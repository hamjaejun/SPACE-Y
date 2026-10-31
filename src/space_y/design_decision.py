from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from .external import sha256_file


SAFETY_DOMAINS = ("cooling", "supply", "structural", "combustion")
UPSTREAM_UNKNOWN_STATES = {"UNKNOWN", "INPUT_U", "OUTSIDE", "NUM_U", "MODEL_U"}
VALID_CRITERION_STATES = {"PASS", "FAIL", *UPSTREAM_UNKNOWN_STATES}
VALIDATION_RANK = {
    "NOT_VALIDATED": 0,
    "SYNTHETIC_ARITHMETIC": 1,
    "SOFTWARE_VERIFIED": 2,
    "SOURCE_REVIEWED": 3,
    "PHYSICAL_BENCHMARK": 4,
    "TEST_VALIDATED": 5,
}
OUTCOME_PRIORITY = {
    "ELIGIBLE_FOR_PDR_SELECTION": 0,
    "CONDITIONAL_REVIEW": 1,
    "UNKNOWN": 2,
    "EXCLUDED": 3,
}


class DecisionStop(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _failure(decision_id: Any, status: str, reason: str, **details: Any) -> dict[str, Any]:
    result = {
        "decision_id": decision_id,
        "status": status,
        "reason": reason,
        "candidates": [],
        "comparison_table": [],
        "priority_candidate_id": None,
        "priority_rationale": None,
        "cad_handoff": None,
        "safe_design_claim": False,
        "validated_engine_prediction": False,
        "physical_validation_v01_v12": "NOT_RUN",
    }
    result.update(details)
    return result


def _require_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DecisionStop("INPUT_U", f"{name} must be a non-empty string")
    return value.strip()


def _unique_id_map(items: Any, name: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise DecisionStop("INPUT_U", f"{name} must be a list")
    mapped: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise DecisionStop("INPUT_U", f"{name}[{index}] must be an object")
        item_id = _require_text(item.get("id"), f"{name}[{index}].id")
        if item_id in mapped:
            raise DecisionStop("INPUT_U", f"duplicate {name} ID: {item_id}")
        mapped[item_id] = item
    return mapped


def _check_references(
    values: Any,
    known: dict[str, Any],
    name: str,
    *,
    allow_empty: bool = True,
) -> list[str]:
    if not isinstance(values, list) or (not values and not allow_empty):
        suffix = "non-empty list" if not allow_empty else "list"
        raise DecisionStop("INPUT_U", f"{name} must be a {suffix}")
    result: list[str] = []
    for value in values:
        item_id = _require_text(value, name)
        if item_id not in known:
            raise DecisionStop("INPUT_U", f"{name} references unknown ID: {item_id}")
        result.append(item_id)
    return result


def _effective_validation_level(
    criterion: dict[str, Any],
    model_map: dict[str, dict[str, Any]],
    source_map: dict[str, dict[str, Any]],
) -> tuple[str, list[str]]:
    claimed = str(criterion.get("validation_level"))
    if claimed not in VALIDATION_RANK:
        raise DecisionStop("INPUT_U", f"invalid validation_level: {claimed}")
    levels = [claimed]
    downgrades: list[str] = []
    for model_id in criterion["model_ids"]:
        model = model_map[model_id]
        level = str(model.get("validation_level"))
        if level not in VALIDATION_RANK:
            raise DecisionStop("INPUT_U", f"model {model_id} has invalid validation_level")
        levels.append(level)
        if model.get("status") != "AVAILABLE":
            downgrades.append(f"model {model_id} status={model.get('status')}")
    for source_id in criterion["source_ids"]:
        source = source_map[source_id]
        level = str(source.get("validation_level"))
        if level not in VALIDATION_RANK:
            raise DecisionStop("INPUT_U", f"source {source_id} has invalid validation_level")
        levels.append(level)
        if source.get("status") != "AVAILABLE":
            downgrades.append(f"source {source_id} status={source.get('status')}")
    effective = min(levels, key=lambda item: VALIDATION_RANK[item])
    if VALIDATION_RANK[effective] < VALIDATION_RANK[claimed]:
        downgrades.append(f"claimed {claimed} reduced to {effective} by referenced evidence")
    return effective, downgrades


def _prepare_criterion(
    criterion: dict[str, Any],
    *,
    model_map: dict[str, dict[str, Any]],
    source_map: dict[str, dict[str, Any]],
    assumption_map: dict[str, dict[str, Any]],
    accepted_levels: set[str],
) -> dict[str, Any]:
    item = copy.deepcopy(criterion)
    item["id"] = _require_text(item.get("id"), "criterion.id")
    item["name"] = _require_text(item.get("name"), f"criterion {item['id']}.name")
    status = str(item.get("status"))
    if status not in VALID_CRITERION_STATES:
        raise DecisionStop("INPUT_U", f"criterion {item['id']} has invalid status={status}")
    if not isinstance(item.get("mandatory"), bool):
        raise DecisionStop("INPUT_U", f"criterion {item['id']}.mandatory must be boolean")
    item["source_ids"] = _check_references(
        item.get("source_ids"),
        source_map,
        f"criterion {item['id']}.source_ids",
        allow_empty=status != "PASS",
    )
    item["model_ids"] = _check_references(
        item.get("model_ids"), model_map, f"criterion {item['id']}.model_ids"
    )
    item["assumption_ids"] = _check_references(
        item.get("assumption_ids"),
        assumption_map,
        f"criterion {item['id']}.assumption_ids",
    )
    effective, downgrades = _effective_validation_level(item, model_map, source_map)
    unconfirmed_assumptions = [
        assumption_id
        for assumption_id in item["assumption_ids"]
        if assumption_map[assumption_id].get("status") != "CONFIRMED"
    ]
    item["effective_validation_level"] = effective
    item["validation_downgrades"] = downgrades
    item["unconfirmed_assumption_ids"] = unconfirmed_assumptions
    item["evidence_accepted"] = (
        status == "PASS"
        and effective in accepted_levels
        and not downgrades
        and not unconfirmed_assumptions
    )
    item["traceability"] = {
        "source_ids": list(item["source_ids"]),
        "model_ids": list(item["model_ids"]),
        "assumption_ids": list(item["assumption_ids"]),
    }
    return item


def _evaluate_constraint_group(
    criteria: list[dict[str, Any]],
    *,
    unresolved_risks: list[str],
    missing_inputs: list[str],
) -> dict[str, Any]:
    mandatory = [item for item in criteria if item["mandatory"]]
    failed = [item["id"] for item in mandatory if item["status"] == "FAIL"]
    unknown = [
        item["id"]
        for item in mandatory
        if item["status"] in UPSTREAM_UNKNOWN_STATES
        or item["unconfirmed_assumption_ids"]
        or item["validation_downgrades"]
    ]
    conditional = [
        item["id"]
        for item in mandatory
        if item["status"] == "PASS" and not item["evidence_accepted"]
    ]
    if failed:
        status = "VIOLATED"
    elif unknown or missing_inputs:
        status = "UNKNOWN"
    elif conditional or unresolved_risks:
        status = "CONDITIONAL_REVIEW"
    else:
        status = "SATISFIED"
    return {
        "status": status,
        "criteria": criteria,
        "failed_mandatory_criterion_ids": failed,
        "unknown_mandatory_criterion_ids": sorted(set(unknown)),
        "conditional_mandatory_criterion_ids": conditional,
        "unresolved_risks": unresolved_risks,
        "missing_required_inputs": missing_inputs,
    }


def _candidate_outcome(
    performance: dict[str, Any], safety: dict[str, dict[str, Any]], purpose: str
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    violated_domains = [name for name, value in safety.items() if value["status"] == "VIOLATED"]
    unknown_domains = [name for name, value in safety.items() if value["status"] == "UNKNOWN"]
    conditional_domains = [
        name for name, value in safety.items() if value["status"] == "CONDITIONAL_REVIEW"
    ]
    if performance["status"] == "VIOLATED":
        reasons.append("mandatory performance constraint violated")
    if violated_domains:
        reasons.append(f"mandatory safety constraint violated in {violated_domains}")
    if reasons:
        return "EXCLUDED", reasons
    if performance["status"] == "UNKNOWN" or unknown_domains:
        if performance["status"] == "UNKNOWN":
            reasons.append("mandatory performance evidence is incomplete")
        if unknown_domains:
            reasons.append(f"mandatory safety evidence is incomplete in {unknown_domains}")
        return "UNKNOWN", reasons
    if performance["status"] == "CONDITIONAL_REVIEW" or conditional_domains:
        reasons.append("one or more mandatory results require conditional review")
        if conditional_domains:
            reasons.append(f"conditional safety domains={conditional_domains}")
        return "CONDITIONAL_REVIEW", reasons
    if purpose == "SYNTHETIC_DECISION_LOGIC_TEST":
        return (
            "CONDITIONAL_REVIEW",
            ["synthetic logic evidence cannot qualify an actual design candidate"],
        )
    return (
        "ELIGIBLE_FOR_PDR_SELECTION",
        ["all declared mandatory constraints have accepted evidence for PDR comparison"],
    )


def _build_cad_handoff(
    request: dict[str, Any], candidate_results: list[dict[str, Any]]
) -> dict[str, Any]:
    handoff_request = request["cad_handoff_request"]
    requested_baseline = handoff_request.get("provisional_baseline_candidate_id")
    by_id = {item["candidate_id"]: item for item in candidate_results}
    blockers: list[str] = []
    baseline_id: str | None = None
    if requested_baseline is not None:
        if requested_baseline not in by_id:
            raise DecisionStop("INPUT_U", "CAD handoff baseline references unknown candidate")
        candidate = by_id[requested_baseline]
        if request["purpose"] == "SYNTHETIC_DECISION_LOGIC_TEST":
            blockers.append("synthetic candidate cannot be a CAD baseline")
        elif candidate["overall_outcome"] in {"UNKNOWN", "EXCLUDED"}:
            blockers.append("requested baseline is unknown or excluded")
        else:
            baseline_id = requested_baseline
    else:
        blockers.append("no provisional baseline candidate has been approved")

    parameters: list[dict[str, Any]] = []
    if baseline_id is not None:
        source_candidate = next(
            item for item in request["candidates"] if item["id"] == baseline_id
        )
        parameters = [
            copy.deepcopy(item)
            for item in source_candidate["parameters"]
            if item["status"] in {"DECIDED", "PROVISIONAL"}
        ]

    missing = sorted(
        {
            *request.get("global_missing_inputs", []),
            *(
                item
                for candidate in candidate_results
                if candidate["overall_outcome"] != "EXCLUDED"
                for domain in candidate["safety_assessments"].values()
                for item in domain["missing_required_inputs"]
            ),
        }
    )
    risks = sorted(
        {
            item
            for candidate in candidate_results
            if candidate["overall_outcome"] != "EXCLUDED"
            for domain in candidate["safety_assessments"].values()
            for item in domain["unresolved_risks"]
        }
    )
    if missing:
        blockers.append("required design inputs remain unresolved")
    return {
        "schema_version": "SPACE_Y_CAD_HANDOFF_V1",
        "purpose": "PDR_PRELIMINARY_NOT_FOR_MANUFACTURE_OR_TEST",
        "decision_id": request["decision_id"],
        "status": "PDR_CONDITIONAL" if baseline_id and not missing else "INPUT_U",
        "provisional_baseline_candidate_id": baseline_id,
        "decided_requirements": copy.deepcopy(handoff_request["decided_requirements"]),
        "provisional_parameters": parameters,
        "parameter_selection_reasons": copy.deepcopy(
            handoff_request["parameter_selection_reasons"]
        ),
        "models_and_sources": {
            "model_ids": [item["id"] for item in request["models"]],
            "source_ids": [item["id"] for item in request["sources"]],
        },
        "unresolved_inputs": missing,
        "change_triggers": copy.deepcopy(handoff_request["change_triggers"]),
        "pre_manufacture_and_test_risks": sorted(
            set(risks + handoff_request["pre_manufacture_and_test_risks"])
        ),
        "handoff_blockers": blockers,
        "manufacture_release": False,
        "test_safety_approval": False,
        "validated_engine_prediction": False,
    }


def solve_design_decision(request: dict[str, Any]) -> dict[str, Any]:
    try:
        if request.get("schema_version") != "SPACE_Y_PDR_DECISION_V1":
            raise DecisionStop("INPUT_U", "unsupported PDR decision schema version")
        purpose = str(request.get("purpose"))
        if purpose not in {"SYNTHETIC_DECISION_LOGIC_TEST", "PDR_CANDIDATE_ASSESSMENT"}:
            raise DecisionStop("INPUT_U", "unsupported PDR decision purpose")
        decision_id = _require_text(request.get("decision_id"), "decision_id")
        model_map = _unique_id_map(request.get("models"), "models")
        source_map = _unique_id_map(request.get("sources"), "sources")
        assumption_map = _unique_id_map(request.get("assumptions"), "assumptions")
        candidate_map = _unique_id_map(request.get("candidates"), "candidates")
        for model_id, model in model_map.items():
            model["source_ids"] = _check_references(
                model.get("source_ids"),
                source_map,
                f"model {model_id}.source_ids",
                allow_empty=False,
            )
        for assumption_id, assumption in assumption_map.items():
            assumption["source_ids"] = _check_references(
                assumption.get("source_ids"),
                source_map,
                f"assumption {assumption_id}.source_ids",
            )
        if not candidate_map:
            raise DecisionStop("INPUT_U", "at least one candidate is required")
        accepted_levels = set(request["status_policy"]["accepted_validation_levels"])
        if not accepted_levels or not accepted_levels <= set(VALIDATION_RANK):
            raise DecisionStop("INPUT_U", "accepted_validation_levels are invalid")
        if request["status_policy"].get("safety_is_hard_constraint") is not True:
            raise DecisionStop("INPUT_U", "safety_is_hard_constraint must be true")
        if request["status_policy"].get("allow_weighted_safety_tradeoff") is not False:
            raise DecisionStop("INPUT_U", "weighted safety tradeoff must be prohibited")
        for requirement in request["cad_handoff_request"]["decided_requirements"]:
            requirement["source_ids"] = _check_references(
                requirement.get("source_ids"),
                source_map,
                f"CAD requirement {requirement.get('id')}.source_ids",
                allow_empty=requirement.get("value") is None,
            )

        candidate_results: list[dict[str, Any]] = []
        for candidate_id, candidate in candidate_map.items():
            parameters = candidate.get("parameters")
            if not isinstance(parameters, list):
                raise DecisionStop("INPUT_U", f"candidate {candidate_id} parameters missing")
            parameter_ids: set[str] = set()
            for parameter in parameters:
                parameter_id = _require_text(
                    parameter.get("id"), f"candidate {candidate_id} parameter.id"
                )
                if parameter_id in parameter_ids:
                    raise DecisionStop(
                        "INPUT_U",
                        f"candidate {candidate_id} has duplicate parameter ID: {parameter_id}",
                    )
                parameter_ids.add(parameter_id)
                status = parameter.get("status")
                if status not in {"DECIDED", "PROVISIONAL", "UNKNOWN"}:
                    raise DecisionStop("INPUT_U", f"candidate {candidate_id} parameter status invalid")
                source_ids = _check_references(
                    parameter.get("source_ids"),
                    source_map,
                    f"candidate {candidate_id} parameter source_ids",
                    allow_empty=parameter.get("value") is None,
                )
                parameter["source_ids"] = source_ids

            prepared_performance = [
                _prepare_criterion(
                    item,
                    model_map=model_map,
                    source_map=source_map,
                    assumption_map=assumption_map,
                    accepted_levels=accepted_levels,
                )
                for item in candidate["performance_criteria"]
            ]
            performance = _evaluate_constraint_group(
                prepared_performance,
                unresolved_risks=list(candidate["performance_unresolved_risks"]),
                missing_inputs=list(candidate["performance_missing_inputs"]),
            )
            safety_input = candidate.get("safety_assessments")
            if not isinstance(safety_input, dict) or set(safety_input) != set(SAFETY_DOMAINS):
                raise DecisionStop(
                    "INPUT_U",
                    f"candidate {candidate_id} must define exactly {list(SAFETY_DOMAINS)}",
                )
            safety: dict[str, dict[str, Any]] = {}
            for domain in SAFETY_DOMAINS:
                domain_input = safety_input[domain]
                prepared = [
                    _prepare_criterion(
                        item,
                        model_map=model_map,
                        source_map=source_map,
                        assumption_map=assumption_map,
                        accepted_levels=accepted_levels,
                    )
                    for item in domain_input["criteria"]
                ]
                safety[domain] = _evaluate_constraint_group(
                    prepared,
                    unresolved_risks=list(domain_input["unresolved_risks"]),
                    missing_inputs=list(domain_input["missing_required_inputs"]),
                )
            criterion_ids = [item["id"] for item in prepared_performance]
            criterion_ids.extend(
                item["id"]
                for domain_result in safety.values()
                for item in domain_result["criteria"]
            )
            if len(criterion_ids) != len(set(criterion_ids)):
                raise DecisionStop(
                    "INPUT_U", f"candidate {candidate_id} has duplicate criterion IDs"
                )
            outcome, reasons = _candidate_outcome(performance, safety, purpose)
            notes = candidate["decision_notes"]
            candidate_results.append(
                {
                    "candidate_id": candidate_id,
                    "label": candidate["label"],
                    "input_parameters": copy.deepcopy(parameters),
                    "performance": performance,
                    "safety_assessments": safety,
                    "overall_outcome": outcome,
                    "outcome_reasons": reasons,
                    "declared_advantages": copy.deepcopy(notes["advantages"]),
                    "declared_limitations": copy.deepcopy(notes["limitations"]),
                    "additional_verification": copy.deepcopy(
                        notes["additional_verification"]
                    ),
                    "safe_design_claim": False,
                    "actual_design_selection_eligible": (
                        outcome == "ELIGIBLE_FOR_PDR_SELECTION"
                        and purpose == "PDR_CANDIDATE_ASSESSMENT"
                    ),
                }
            )

        candidate_results.sort(
            key=lambda item: (OUTCOME_PRIORITY[item["overall_outcome"]], item["candidate_id"])
        )
        retained = [
            item
            for item in candidate_results
            if item["overall_outcome"]
            in {"ELIGIBLE_FOR_PDR_SELECTION", "CONDITIONAL_REVIEW"}
        ]
        priority = retained[0] if retained else None
        comparison = [
            {
                "candidate_id": item["candidate_id"],
                "label": item["label"],
                "performance": item["performance"]["status"],
                "cooling": item["safety_assessments"]["cooling"]["status"],
                "supply": item["safety_assessments"]["supply"]["status"],
                "structural": item["safety_assessments"]["structural"]["status"],
                "combustion": item["safety_assessments"]["combustion"]["status"],
                "overall_outcome": item["overall_outcome"],
                "reason": "; ".join(item["outcome_reasons"]),
            }
            for item in candidate_results
        ]
        if priority:
            priority_rationale = (
                f"{priority['label']} is the highest retained decision class without a known "
                "hard-constraint violation; this is a follow-up priority, not a safety approval. "
                f"Advantages={priority['declared_advantages']}; "
                f"limitations={priority['declared_limitations']}."
            )
        else:
            priority_rationale = "No candidate is retained for priority review."
        cad_handoff = _build_cad_handoff(request, candidate_results)
        overall_status = (
            "PDR_CONDITIONAL"
            if retained
            else ("INPUT_U" if any(item["overall_outcome"] == "UNKNOWN" for item in candidate_results) else "FAIL")
        )
        return {
            "schema_version": "SPACE_Y_PDR_DECISION_RESULT_V1",
            "decision_id": decision_id,
            "purpose": purpose,
            "status": overall_status,
            "status_policy": copy.deepcopy(request["status_policy"]),
            "evidence_catalog": {
                "sources": copy.deepcopy(request["sources"]),
                "models": copy.deepcopy(request["models"]),
                "assumptions": copy.deepcopy(request["assumptions"]),
            },
            "candidates": candidate_results,
            "comparison_table": comparison,
            "priority_candidate_id": priority["candidate_id"] if priority else None,
            "priority_rationale": priority_rationale,
            "cad_handoff": cad_handoff,
            "decision_method": "HARD_CONSTRAINTS_FIRST_NO_WEIGHTED_SAFETY_SUM",
            "safe_design_claim": False,
            "validated_engine_prediction": False,
            "physical_validation_v01_v12": "NOT_RUN",
        }
    except (DecisionStop, KeyError, TypeError, ValueError) as error:
        status = error.status if isinstance(error, DecisionStop) else "INPUT_U"
        return _failure(request.get("decision_id"), status, f"invalid PDR decision input: {error}")


def solve_design_decision_file(
    path: str | Path, schema_path: str | Path | None = None
) -> dict[str, Any]:
    request_path = Path(path).resolve()
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return _failure(None, "INPUT_U", f"cannot read PDR decision request: {error}")
    if not isinstance(request, dict):
        return _failure(None, "INPUT_U", "PDR decision request must be an object")
    if schema_path is not None:
        try:
            import jsonschema
        except ImportError:
            pass
        else:
            schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
            errors = sorted(
                jsonschema.Draft202012Validator(schema).iter_errors(request),
                key=lambda item: list(item.path),
            )
            if errors:
                return _failure(
                    request.get("decision_id"),
                    "INPUT_U",
                    "PDR decision schema validation failed",
                    schema_errors=[
                        {
                            "path": ".".join(str(part) for part in error.absolute_path) or "$",
                            "message": error.message,
                        }
                        for error in errors
                    ],
                )
    result = solve_design_decision(request)
    result["request_sha256"] = sha256_file(request_path)
    if schema_path is not None:
        result["schema_sha256"] = sha256_file(schema_path)
    return result
