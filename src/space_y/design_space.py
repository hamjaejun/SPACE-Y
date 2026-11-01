from __future__ import annotations

import copy
import itertools
import json
import math
from pathlib import Path
from typing import Any

from .external import sha256_file


DOMAINS = (
    "performance",
    "supply",
    "cooling",
    "structural",
    "combustion",
    "operations",
)
UNKNOWN_METRIC_STATES = {"NOT_RUN", "INPUT_U", "OUTSIDE", "NUM_U", "MODEL_U"}
RESULT_CLASSES = {
    "HISTORICAL_RESULT",
    "REPRODUCED_RESULT",
    "SYNTHETIC_RESULT",
    "NOT_RUN",
}
OPERATORS = {
    "LT": lambda value, limit: value < limit,
    "LE": lambda value, limit: value <= limit,
    "GT": lambda value, limit: value > limit,
    "GE": lambda value, limit: value >= limit,
    "EQ": lambda value, limit: value == limit,
}


class ExplorationStop(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ExplorationStop("INPUT_U", f"{name} must be a non-empty string")
    return value.strip()


def _finite(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _indexed(items: Any, name: str) -> dict[str, dict[str, Any]]:
    if not isinstance(items, list):
        raise ExplorationStop("INPUT_U", f"{name} must be a list")
    result: dict[str, dict[str, Any]] = {}
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ExplorationStop("INPUT_U", f"{name}[{index}] must be an object")
        item_id = _text(item.get("id"), f"{name}[{index}].id")
        if item_id in result:
            raise ExplorationStop("INPUT_U", f"duplicate {name} ID: {item_id}")
        result[item_id] = item
    return result


def _source_refs(values: Any, sources: dict[str, dict[str, Any]], name: str) -> list[str]:
    if not isinstance(values, list):
        raise ExplorationStop("INPUT_U", f"{name} must be a list")
    refs: list[str] = []
    for value in values:
        source_id = _text(value, name)
        if source_id not in sources:
            raise ExplorationStop("INPUT_U", f"{name} references unknown source: {source_id}")
        refs.append(source_id)
    return refs


def _generation_approval(purpose: str) -> str:
    return (
        "APPROVED_FOR_SYNTHETIC_TEST"
        if purpose == "SYNTHETIC_EXPLORATION_TEST"
        else "APPROVED_FOR_EXPLORATION"
    )


def _blocked_result(request: dict[str, Any], blockers: list[str]) -> dict[str, Any]:
    return {
        "schema_version": "SPACE_Y_DESIGN_SPACE_RESULT_V1",
        "exploration_id": request.get("exploration_id"),
        "status": "BLOCKED_BY_INPUT",
        "reason": "approved exploration definition is incomplete",
        "generation_blockers": sorted(set(blockers)),
        "candidate_count": 0,
        "candidates": [],
        "comparison_table": [],
        "tradeoff_analysis": {"status": "NOT_RUN", "findings": []},
        "pareto_analysis": {
            "status": "NOT_RUN",
            "candidate_ids": [],
            "scope_warning": "No Pareto claim is available without comparable evaluated metrics.",
        },
        "relative_shortlist_candidate_ids": [],
        "reference_points": copy.deepcopy(request.get("reference_points", [])),
        "safe_design_claim": False,
        "validated_engine_prediction": False,
        "physical_validation_v01_v12": "NOT_RUN",
    }


def _parameter_signature(
    parameters: dict[str, dict[str, Any]], variable_order: list[str]
) -> str:
    compact = [
        [variable_id, parameters[variable_id]["value"], parameters[variable_id]["unit"]]
        for variable_id in variable_order
    ]
    return json.dumps(compact, ensure_ascii=False, separators=(",", ":"), sort_keys=False)


def _validate_metric(metric: dict[str, Any], sources: dict[str, dict[str, Any]]) -> dict[str, Any]:
    item = copy.deepcopy(metric)
    item["id"] = _text(item.get("id"), "metric.id")
    if item.get("domain") not in DOMAINS:
        raise ExplorationStop("INPUT_U", f"metric {item['id']} has invalid domain")
    status = item.get("status")
    if status not in {"AVAILABLE", *UNKNOWN_METRIC_STATES}:
        raise ExplorationStop("INPUT_U", f"metric {item['id']} has invalid status")
    item["source_ids"] = _source_refs(
        item.get("source_ids"), sources, f"metric {item['id']}.source_ids"
    )
    item["evidence_available"] = bool(item["source_ids"]) and all(
        sources[source_id].get("status") == "AVAILABLE" for source_id in item["source_ids"]
    )
    if status == "AVAILABLE":
        if not _finite(item.get("value")):
            raise ExplorationStop("INPUT_U", f"metric {item['id']} AVAILABLE value must be finite")
        _text(item.get("unit"), f"metric {item['id']}.unit")
        if not item["source_ids"]:
            raise ExplorationStop("INPUT_U", f"metric {item['id']} AVAILABLE requires a source")
    return item


def _constraint_assessment(
    constraint: dict[str, Any], metric: dict[str, Any] | None, approved_status: str
) -> dict[str, Any]:
    result = {
        "constraint_id": constraint["id"],
        "metric_id": constraint["metric_id"],
        "domain": constraint["domain"],
        "mandatory": constraint["mandatory"],
        "status": "UNKNOWN",
        "reason": None,
        "actual_value": None,
        "limit": constraint["limit"],
        "unit": constraint["unit"],
    }
    if constraint["approval_status"] != approved_status:
        result["reason"] = "constraint is not approved for this exploration purpose"
        return result
    if not constraint["_source_ready"]:
        result["reason"] = "constraint source is missing or unavailable"
        return result
    if metric is None:
        result["reason"] = "metric was not produced under the approved comparison basis"
        return result
    if metric["status"] != "AVAILABLE":
        result["reason"] = f"metric status={metric['status']}"
        return result
    if not metric["evidence_available"]:
        result["reason"] = "metric source is missing or unavailable"
        return result
    if metric["unit"] != constraint["unit"]:
        result["reason"] = (
            f"unit mismatch: metric={metric['unit']} constraint={constraint['unit']}"
        )
        return result
    result["actual_value"] = metric["value"]
    result["status"] = (
        "PASS"
        if OPERATORS[constraint["operator"]](metric["value"], constraint["limit"])
        else "FAIL"
    )
    result["reason"] = "direct numeric comparison using identical explicit units"
    return result


def _domain_assessments(assessments: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for domain in DOMAINS:
        items = [item for item in assessments if item["domain"] == domain]
        mandatory = [item for item in items if item["mandatory"]]
        if any(item["status"] == "FAIL" for item in mandatory):
            status = "VIOLATED"
        elif not mandatory or any(item["status"] == "UNKNOWN" for item in mandatory):
            status = "UNKNOWN"
        else:
            status = "SATISFIED_FOR_INCLUDED_METRICS"
        output[domain] = {
            "status": status,
            "constraint_assessments": items,
            "missing_mandatory_evidence": [
                item["constraint_id"] for item in mandatory if item["status"] == "UNKNOWN"
            ],
        }
    return output


def _overall_outcome(domains: dict[str, dict[str, Any]]) -> tuple[str, list[str]]:
    violated = [name for name, item in domains.items() if item["status"] == "VIOLATED"]
    unknown = [name for name, item in domains.items() if item["status"] == "UNKNOWN"]
    if violated:
        return "EXCLUDED", [f"mandatory hard constraint violated in {violated}"]
    if unknown:
        return "UNKNOWN", [f"mandatory evidence incomplete in {unknown}"]
    return (
        "CONDITIONAL_REVIEW",
        ["exploration screening passed only for the included metrics and evidence"],
    )


def _pareto(
    candidates: list[dict[str, Any]], objectives: list[dict[str, Any]]
) -> tuple[str, list[str], list[dict[str, Any]]]:
    eligible = [item for item in candidates if item["overall_outcome"] == "CONDITIONAL_REVIEW"]
    if not objectives or not eligible:
        return "NOT_RUN", [], []
    vectors: dict[str, list[float]] = {}
    findings: list[dict[str, Any]] = []
    for objective in objectives:
        metric_id = objective["metric_id"]
        units = set()
        values: list[tuple[str, float]] = []
        for candidate in eligible:
            metric = candidate["metrics"].get(metric_id)
            if metric is None or metric["status"] != "AVAILABLE" or not _finite(metric["value"]):
                return "BLOCKED_BY_INPUT", [], []
            units.add(metric["unit"])
            values.append((candidate["candidate_id"], metric["value"]))
        if len(units) != 1:
            return "BLOCKED_BY_INPUT", [], []
        for candidate_id, value in values:
            normalized = value if objective["direction"] == "MAX" else -value
            vectors.setdefault(candidate_id, []).append(normalized)
        extrema = max(values, key=lambda item: item[1]) if objective["direction"] == "MAX" else min(values, key=lambda item: item[1])
        findings.append(
            {
                "metric_id": metric_id,
                "direction": objective["direction"],
                "best_candidate_id": extrema[0],
                "best_value": extrema[1],
                "unit": next(iter(units)),
            }
        )
    nondominated: list[str] = []
    for candidate_id, vector in vectors.items():
        dominated = any(
            other_id != candidate_id
            and all(a >= b for a, b in zip(other_vector, vector))
            and any(a > b for a, b in zip(other_vector, vector))
            for other_id, other_vector in vectors.items()
        )
        if not dominated:
            nondominated.append(candidate_id)
    return "RELATIVE_ONLY", sorted(nondominated), findings


def explore_design_space(request: dict[str, Any]) -> dict[str, Any]:
    try:
        if request.get("schema_version") != "SPACE_Y_DESIGN_SPACE_V1":
            raise ExplorationStop("INPUT_U", "unsupported design-space schema version")
        purpose = request.get("purpose")
        if purpose not in {"SYNTHETIC_EXPLORATION_TEST", "PDR_DESIGN_SPACE_EXPLORATION"}:
            raise ExplorationStop("INPUT_U", "unsupported exploration purpose")
        exploration_id = _text(request.get("exploration_id"), "exploration_id")
        sources = _indexed(request.get("sources"), "sources")
        variables = _indexed(request.get("design_variables"), "design_variables")
        constraints = _indexed(request.get("constraints"), "constraints")
        if not variables:
            return _blocked_result(request, ["no design variables are defined"])
        approved_status = _generation_approval(purpose)
        blockers: list[str] = []
        basis = request.get("comparison_basis")
        if not isinstance(basis, dict):
            raise ExplorationStop("INPUT_U", "comparison_basis must be an object")
        basis_id = _text(basis.get("id"), "comparison_basis.id")
        basis_sources = _source_refs(
            basis.get("source_ids"), sources, "comparison_basis.source_ids"
        )
        if basis.get("approval_status") != approved_status:
            blockers.append("comparison basis is not approved for this exploration purpose")
        if not basis_sources or any(
            sources[source_id].get("status") != "AVAILABLE" for source_id in basis_sources
        ):
            blockers.append("comparison basis source is missing or unavailable")
        required_basis_fields = (
            "performance_target_and_tolerance",
            "propellant_and_inlet_state",
            "ambient_condition",
            "sizing_basis",
            "program_and_version",
            "thermochemistry_and_loss_model",
            "common_geometry_assumptions",
        )
        for field in required_basis_fields:
            if not isinstance(basis.get(field), str) or not basis[field].strip():
                blockers.append(f"comparison basis missing {field}")

        variable_order = list(variables)
        grids: list[list[Any]] = []
        for variable_id, variable in variables.items():
            _text(variable.get("unit"), f"design variable {variable_id}.unit")
            source_ids = _source_refs(
                variable.get("source_ids"), sources, f"design variable {variable_id}.source_ids"
            )
            values = variable.get("grid_values")
            if not isinstance(values, list):
                raise ExplorationStop("INPUT_U", f"design variable {variable_id}.grid_values must be a list")
            if variable.get("range_approval_status") != approved_status:
                blockers.append(f"design variable {variable_id} range is not approved")
            if not values:
                blockers.append(f"design variable {variable_id} has no approved grid values")
            if len(values) != len({json.dumps(value, sort_keys=True) for value in values}):
                raise ExplorationStop("INPUT_U", f"design variable {variable_id} has duplicate grid values")
            if not source_ids:
                blockers.append(f"design variable {variable_id} has no range source")
            elif any(sources[source_id].get("status") != "AVAILABLE" for source_id in source_ids):
                blockers.append(f"design variable {variable_id} range source is unavailable")
            grids.append(values)

        for condition in request.get("fixed_conditions", []):
            condition_id = _text(condition.get("id"), "fixed condition.id")
            condition_sources = _source_refs(
                condition.get("source_ids"), sources, f"fixed condition {condition_id}.source_ids"
            )
            if condition.get("approval_status") != approved_status:
                blockers.append(f"fixed condition {condition_id} is not approved")
            if not condition_sources or any(
                sources[source_id].get("status") != "AVAILABLE"
                for source_id in condition_sources
            ):
                blockers.append(f"fixed condition {condition_id} source is missing or unavailable")

        if purpose == "PDR_DESIGN_SPACE_EXPLORATION":
            synthetic_sources = [
                source_id
                for source_id, source in sources.items()
                if str(source.get("type", "")).upper().startswith("SYNTHETIC")
            ]
            if synthetic_sources:
                raise ExplorationStop(
                    "INPUT_U", f"actual exploration cannot use synthetic sources: {synthetic_sources}"
                )
        if blockers:
            return _blocked_result(request, blockers)

        combination_count = math.prod(len(values) for values in grids)
        maximum = request.get("max_candidates")
        if not isinstance(maximum, int) or isinstance(maximum, bool) or maximum < 1:
            raise ExplorationStop("INPUT_U", "max_candidates must be a positive integer")
        if combination_count > maximum:
            raise ExplorationStop(
                "INPUT_U",
                f"grid creates {combination_count} candidates, exceeding max_candidates={maximum}",
            )

        for constraint_id, constraint in constraints.items():
            if constraint.get("domain") not in DOMAINS:
                raise ExplorationStop("INPUT_U", f"constraint {constraint_id} has invalid domain")
            constraint["id"] = constraint_id
            constraint["metric_id"] = _text(
                constraint.get("metric_id"), f"constraint {constraint_id}.metric_id"
            )
            if constraint.get("operator") not in OPERATORS:
                raise ExplorationStop("INPUT_U", f"constraint {constraint_id} has invalid operator")
            if not _finite(constraint.get("limit")):
                raise ExplorationStop("INPUT_U", f"constraint {constraint_id}.limit must be finite")
            _text(constraint.get("unit"), f"constraint {constraint_id}.unit")
            if not isinstance(constraint.get("mandatory"), bool):
                raise ExplorationStop("INPUT_U", f"constraint {constraint_id}.mandatory must be boolean")
            constraint_sources = _source_refs(
                constraint.get("source_ids"), sources, f"constraint {constraint_id}.source_ids"
            )
            constraint["_source_ready"] = bool(constraint_sources) and all(
                sources[source_id].get("status") == "AVAILABLE"
                for source_id in constraint_sources
            )

        records_by_signature: dict[str, list[dict[str, Any]]] = {}
        for record in request.get("result_records", []):
            record_id = _text(record.get("id"), "result record.id")
            result_class = record.get("result_class")
            if result_class not in RESULT_CLASSES:
                raise ExplorationStop("INPUT_U", f"result record {record_id} has invalid result_class")
            if purpose == "PDR_DESIGN_SPACE_EXPLORATION" and result_class == "SYNTHETIC_RESULT":
                raise ExplorationStop("INPUT_U", "synthetic result cannot enter actual exploration")
            record_sources = _source_refs(
                record.get("source_ids"), sources, f"result record {record_id}.source_ids"
            )
            parameters = record.get("candidate_parameters")
            if not isinstance(parameters, dict) or set(parameters) != set(variable_order):
                raise ExplorationStop(
                    "INPUT_U", f"result record {record_id} parameter set does not match design variables"
                )
            for variable_id in variable_order:
                if parameters[variable_id].get("unit") != variables[variable_id]["unit"]:
                    raise ExplorationStop(
                        "INPUT_U", f"result record {record_id} parameter unit mismatch for {variable_id}"
                    )
            prepared_metrics: dict[str, dict[str, Any]] = {}
            for metric in record.get("metrics", []):
                prepared = _validate_metric(metric, sources)
                if prepared["id"] in prepared_metrics:
                    raise ExplorationStop("INPUT_U", f"result record {record_id} has duplicate metric")
                prepared_metrics[prepared["id"]] = prepared
            record_copy = copy.deepcopy(record)
            record_copy["metrics"] = prepared_metrics
            record_copy["_source_ready"] = bool(record_sources) and all(
                sources[source_id].get("status") == "AVAILABLE"
                for source_id in record_sources
            )
            signature = _parameter_signature(parameters, variable_order)
            records_by_signature.setdefault(signature, []).append(record_copy)

        candidates: list[dict[str, Any]] = []
        for index, values in enumerate(itertools.product(*grids), start=1):
            parameters = {
                variable_id: {"value": value, "unit": variables[variable_id]["unit"]}
                for variable_id, value in zip(variable_order, values)
            }
            signature = _parameter_signature(parameters, variable_order)
            records = records_by_signature.get(signature, [])
            metrics: dict[str, dict[str, Any]] = {}
            linked: list[dict[str, Any]] = []
            for record in records:
                same_basis = record.get("comparison_basis_id") == basis_id
                comparable = same_basis and record["_source_ready"]
                linked.append(
                    {
                        "result_id": record["id"],
                        "result_class": record["result_class"],
                        "comparison_basis_id": record.get("comparison_basis_id"),
                        "comparability": (
                            "COMPARABLE"
                            if comparable
                            else (
                                "NOT_COMPARABLE_EVIDENCE"
                                if same_basis and not record["_source_ready"]
                                else "NOT_COMPARABLE_BOUNDARY"
                            )
                        ),
                    }
                )
                if not comparable or record["result_class"] == "NOT_RUN":
                    continue
                for metric_id, metric in record["metrics"].items():
                    if metric_id in metrics:
                        raise ExplorationStop(
                            "INPUT_U",
                            f"candidate parameter set has duplicate comparable metric {metric_id}",
                        )
                    metrics[metric_id] = copy.deepcopy(metric)
                    metrics[metric_id]["result_id"] = record["id"]
                    metrics[metric_id]["result_class"] = record["result_class"]
            assessments = [
                _constraint_assessment(constraint, metrics.get(constraint["metric_id"]), approved_status)
                for constraint in constraints.values()
            ]
            domains = _domain_assessments(assessments)
            outcome, reasons = _overall_outcome(domains)
            candidates.append(
                {
                    "candidate_id": f"DSE-{index:04d}",
                    "candidate_class": "EXPLORATION_CANDIDATE",
                    "parameters": parameters,
                    "linked_results": linked,
                    "metrics": metrics,
                    "domain_assessments": domains,
                    "overall_outcome": outcome,
                    "outcome_reasons": reasons,
                    "pdr_candidate": False,
                    "team_provisional_baseline": False,
                    "validated_design": False,
                }
            )

        objectives = request.get("pareto_objectives", [])
        for objective in objectives:
            _text(objective.get("metric_id"), "pareto objective.metric_id")
            if objective.get("direction") not in {"MIN", "MAX"}:
                raise ExplorationStop("INPUT_U", "pareto objective direction must be MIN or MAX")
        pareto_status, pareto_ids, findings = _pareto(candidates, objectives)
        result = {
            "schema_version": "SPACE_Y_DESIGN_SPACE_RESULT_V1",
            "exploration_id": exploration_id,
            "status": "PASS",
            "reason": "deterministic grid generated under an explicitly approved comparison definition",
            "generation_blockers": [],
            "candidate_count": len(candidates),
            "candidates": candidates,
            "comparison_table": [
                {
                    "candidate_id": item["candidate_id"],
                    "parameters": item["parameters"],
                    "performance": item["domain_assessments"]["performance"]["status"],
                    "supply": item["domain_assessments"]["supply"]["status"],
                    "cooling": item["domain_assessments"]["cooling"]["status"],
                    "structural": item["domain_assessments"]["structural"]["status"],
                    "combustion": item["domain_assessments"]["combustion"]["status"],
                    "operations": item["domain_assessments"]["operations"]["status"],
                    "overall_outcome": item["overall_outcome"],
                }
                for item in candidates
            ],
            "tradeoff_analysis": {
                "status": pareto_status,
                "findings": findings,
                "limitations": [
                    "Only explicit comparable metrics are included.",
                    "Unknown safety domains are not assigned zero and are not omitted.",
                ],
            },
            "pareto_analysis": {
                "status": pareto_status,
                "candidate_ids": pareto_ids,
                "scope_warning": (
                    "Relative dominance applies only to listed objectives; it is not a global optimum or safety approval."
                ),
            },
            "relative_shortlist_candidate_ids": pareto_ids,
            "reference_points": copy.deepcopy(request.get("reference_points", [])),
            "safe_design_claim": False,
            "validated_engine_prediction": False,
            "physical_validation_v01_v12": "NOT_RUN",
        }
        if purpose == "SYNTHETIC_EXPLORATION_TEST":
            result["reason"] += "; synthetic results cannot be promoted to actual design evidence"
        return result
    except (ExplorationStop, KeyError, TypeError, ValueError) as exc:
        status = exc.status if isinstance(exc, ExplorationStop) else "INPUT_U"
        return {
            "schema_version": "SPACE_Y_DESIGN_SPACE_RESULT_V1",
            "exploration_id": request.get("exploration_id"),
            "status": status,
            "reason": str(exc),
            "generation_blockers": [str(exc)],
            "candidate_count": 0,
            "candidates": [],
            "comparison_table": [],
            "tradeoff_analysis": {"status": "NOT_RUN", "findings": []},
            "pareto_analysis": {"status": "NOT_RUN", "candidate_ids": []},
            "relative_shortlist_candidate_ids": [],
            "reference_points": copy.deepcopy(request.get("reference_points", [])),
            "safe_design_claim": False,
            "validated_engine_prediction": False,
            "physical_validation_v01_v12": "NOT_RUN",
        }


def explore_design_space_file(
    request_path: str | Path,
    schema_path: str | Path = "schemas/SPACE_Y_design_space.schema.json",
) -> dict[str, Any]:
    request_file = Path(request_path)
    schema_file = Path(schema_path)
    request = json.loads(request_file.read_text(encoding="utf-8"))
    try:
        import jsonschema

        schema = json.loads(schema_file.read_text(encoding="utf-8"))
        errors = sorted(
            jsonschema.Draft202012Validator(schema).iter_errors(request),
            key=lambda item: list(item.absolute_path),
        )
        if errors:
            path = ".".join(str(item) for item in errors[0].absolute_path)
            return {
                "schema_version": "SPACE_Y_DESIGN_SPACE_RESULT_V1",
                "exploration_id": request.get("exploration_id"),
                "status": "INPUT_U",
                "reason": f"schema validation failed at {path or '<root>'}: {errors[0].message}",
                "candidate_count": 0,
                "candidates": [],
                "safe_design_claim": False,
                "validated_engine_prediction": False,
            }
    except ImportError:
        pass
    result = explore_design_space(request)
    result["request_sha256"] = sha256_file(request_file)
    result["schema_sha256"] = sha256_file(schema_file)
    return result
