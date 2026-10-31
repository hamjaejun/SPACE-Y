from __future__ import annotations

from dataclasses import asdict, dataclass
import json
from pathlib import Path
from typing import Any, Iterable


EXPECTED_INPUT_IDS = tuple(f"I{i:02d}" for i in range(1, 26))
EXPECTED_QOI_IDS = tuple(f"Y{i:02d}" for i in range(1, 6))
EXPECTED_INTERFACE_IDS = ("GAS", "WALL", "COOLANT", "NETWORK", "ASSESS")
EXPECTED_VALIDATION_IDS = tuple(f"V{i:02d}" for i in range(1, 13))

ALLOWED_INPUT_STATUS = {"MISSING", "PROVIDED", "INVALID", "OUTSIDE"}
ALLOWED_QOI_STATUS = {
    "NOT_RUN",
    "INPUT_U",
    "NUM_U",
    "OUTSIDE",
    "MODEL_U",
    "PDR_CONDITIONAL",
    "HARDWARE_U",
}
ALLOWED_VALIDATION_STATUS = {"NOT_RUN", "PASS", "FAIL", "BLOCKED"}


@dataclass(frozen=True)
class Issue:
    code: str
    level: str
    path: str
    message: str
    affects: tuple[str, ...] = ()


def _issue(
    code: str,
    level: str,
    path: str,
    message: str,
    affects: Iterable[str] = (),
) -> Issue:
    return Issue(code, level, path, message, tuple(affects))


def load_json(path: str | Path) -> dict[str, Any]:
    with Path(path).open(encoding="utf-8") as stream:
        value = json.load(stream)
    if not isinstance(value, dict):
        raise ValueError("top-level JSON value must be an object")
    return value


def _ids(items: Any) -> list[str]:
    if not isinstance(items, list):
        return []
    return [item.get("id") for item in items if isinstance(item, dict)]


def _check_exact_ids(
    issues: list[Issue], path: str, actual: list[str], expected: tuple[str, ...]
) -> None:
    duplicates = sorted({item for item in actual if actual.count(item) > 1})
    if duplicates:
        issues.append(
            _issue("DUPLICATE_ID", "ERROR", path, f"duplicate IDs: {duplicates}")
        )
    missing = sorted(set(expected) - set(actual))
    extra = sorted(set(actual) - set(expected))
    if missing or extra:
        issues.append(
            _issue(
                "ID_SET_MISMATCH",
                "ERROR",
                path,
                f"missing={missing}, extra={extra}",
            )
        )


def validate_structure(contract: dict[str, Any]) -> list[Issue]:
    """Validate the fixed v2.1 ID contract without claiming physical validation."""
    issues: list[Issue] = []
    for key in (
        "document_version",
        "solver_run",
        "selected_case",
        "can_use_as_validated_engine_prediction",
        "inputs",
        "qoi",
        "interfaces",
        "validation",
    ):
        if key not in contract:
            issues.append(_issue("MISSING_FIELD", "ERROR", key, "required field is absent"))

    if contract.get("document_version") != "v2.1":
        issues.append(
            _issue(
                "VERSION_MISMATCH",
                "ERROR",
                "document_version",
                "Phase 1 implementation is pinned to the v2.1 contract",
            )
        )
    if not isinstance(contract.get("can_use_as_validated_engine_prediction"), bool):
        issues.append(
            _issue(
                "TYPE_ERROR",
                "ERROR",
                "can_use_as_validated_engine_prediction",
                "must be boolean",
            )
        )

    _check_exact_ids(issues, "inputs", _ids(contract.get("inputs")), EXPECTED_INPUT_IDS)
    _check_exact_ids(issues, "qoi", _ids(contract.get("qoi")), EXPECTED_QOI_IDS)
    _check_exact_ids(
        issues, "interfaces", _ids(contract.get("interfaces")), EXPECTED_INTERFACE_IDS
    )
    _check_exact_ids(
        issues, "validation", _ids(contract.get("validation")), EXPECTED_VALIDATION_IDS
    )

    for index, item in enumerate(contract.get("inputs", [])):
        if not isinstance(item, dict):
            issues.append(_issue("TYPE_ERROR", "ERROR", f"inputs[{index}]", "must be object"))
            continue
        for key in ("id", "key", "name", "unit", "classification", "status"):
            if key not in item:
                issues.append(
                    _issue("MISSING_FIELD", "ERROR", f"inputs[{index}].{key}", "required")
                )
        if item.get("status") not in ALLOWED_INPUT_STATUS:
            issues.append(
                _issue(
                    "STATUS_ENUM",
                    "ERROR",
                    f"inputs[{index}].status",
                    f"must be one of {sorted(ALLOWED_INPUT_STATUS)}",
                )
            )

    for index, item in enumerate(contract.get("qoi", [])):
        if not isinstance(item, dict):
            continue
        if item.get("status") not in ALLOWED_QOI_STATUS:
            issues.append(
                _issue(
                    "STATUS_ENUM",
                    "ERROR",
                    f"qoi[{index}].status",
                    f"must be one of {sorted(ALLOWED_QOI_STATUS)}",
                )
            )

    for index, item in enumerate(contract.get("validation", [])):
        if not isinstance(item, dict):
            continue
        if item.get("status") not in ALLOWED_VALIDATION_STATUS:
            issues.append(
                _issue(
                    "STATUS_ENUM",
                    "ERROR",
                    f"validation[{index}].status",
                    f"must be one of {sorted(ALLOWED_VALIDATION_STATUS)}",
                )
            )
    return issues


def validate_semantics(contract: dict[str, Any]) -> list[Issue]:
    """Check null/status consistency and mutually exclusive flow boundary modes."""
    issues: list[Issue] = []
    for index, item in enumerate(contract.get("inputs", [])):
        if not isinstance(item, dict):
            continue
        value = item.get("value")
        status = item.get("status")
        if status == "MISSING" and value is not None:
            issues.append(
                _issue(
                    "VALUE_STATUS_CONFLICT",
                    "ERROR",
                    f"inputs[{index}]",
                    "MISSING input must have value=null; zero is not null",
                )
            )
        if status == "PROVIDED" and value is None:
            issues.append(
                _issue(
                    "VALUE_STATUS_CONFLICT",
                    "ERROR",
                    f"inputs[{index}]",
                    "PROVIDED input must not have value=null",
                )
            )

    for index, item in enumerate(contract.get("qoi", [])):
        if not isinstance(item, dict):
            continue
        if item.get("status") == "NOT_RUN" and item.get("value") is not None:
            issues.append(
                _issue(
                    "RESULT_STATUS_CONFLICT",
                    "ERROR",
                    f"qoi[{index}]",
                    "NOT_RUN QoI must have value=null",
                )
            )

    for index, item in enumerate(contract.get("validation", [])):
        if not isinstance(item, dict):
            continue
        if item.get("status") == "NOT_RUN" and item.get("result") is not None:
            issues.append(
                _issue(
                    "VALIDATION_STATUS_CONFLICT",
                    "ERROR",
                    f"validation[{index}]",
                    "NOT_RUN validation must have result=null",
                )
            )

    control = contract.get("analysis_control")
    if not isinstance(control, dict):
        issues.append(
            _issue(
                "FLOW_MODE_MISSING",
                "BLOCKER",
                "analysis_control",
                "flow boundary mode is not selected; I02 and I04 roles remain ambiguous",
                EXPECTED_QOI_IDS,
            )
        )
        return issues

    mode = control.get("flow_boundary_mode")
    roles = control.get("boundary_roles", {})
    pressure_boundary = control.get("pressure_driven", {})
    if mode is None:
        issues.append(
            _issue(
                "FLOW_MODE_MISSING",
                "BLOCKER",
                "analysis_control.flow_boundary_mode",
                "select PRESCRIBED_MASS_FLOW or PRESSURE_DRIVEN; no default is assumed",
                ("Y02", "Y04", "Y05", "Y01", "Y03"),
            )
        )
    elif mode == "PRESCRIBED_MASS_FLOW":
        if roles.get("I02") != "INPUT" or roles.get("I04") != "INPUT":
            issues.append(
                _issue(
                    "FLOW_ROLE_CONFLICT",
                    "ERROR",
                    "analysis_control.boundary_roles",
                    "prescribed-flow mode requires I02=INPUT and I04=INPUT",
                    ("Y02", "Y04", "Y05"),
                )
            )
        if pressure_boundary.get("outlet_pressure_Pa_abs") is not None:
            issues.append(
                _issue(
                    "OVER_SPECIFIED_BOUNDARY",
                    "ERROR",
                    "analysis_control.pressure_driven.outlet_pressure_Pa_abs",
                    "do not force an outlet pressure in prescribed-flow mode",
                    ("Y02", "Y04"),
                )
            )
    elif mode == "PRESSURE_DRIVEN":
        if roles.get("I02") != "INPUT" or roles.get("I04") != "SOLVED":
            issues.append(
                _issue(
                    "FLOW_ROLE_CONFLICT",
                    "ERROR",
                    "analysis_control.boundary_roles",
                    "pressure-driven mode requires I02=INPUT and I04=SOLVED",
                    ("Y02", "Y04", "Y05"),
                )
            )
        for key in ("outlet_pressure_Pa_abs", "outlet_node_id", "source"):
            if pressure_boundary.get(key) is None:
                issues.append(
                    _issue(
                        "PRESSURE_BOUNDARY_MISSING",
                        "BLOCKER",
                        f"analysis_control.pressure_driven.{key}",
                        "pressure-driven mode requires an explicit downstream absolute-pressure boundary",
                        ("Y02", "Y04", "Y05"),
                    )
                )
    else:
        issues.append(
            _issue(
                "FLOW_MODE_ENUM",
                "ERROR",
                "analysis_control.flow_boundary_mode",
                "unsupported flow mode",
            )
        )
    return issues


def validate_json_schema(
    contract: dict[str, Any], schema_path: str | Path
) -> tuple[str, list[Issue]]:
    """Run the published JSON Schema when jsonschema is installed."""
    try:
        import jsonschema
    except ImportError:
        return "NOT_RUN", [
            _issue(
                "JSONSCHEMA_UNAVAILABLE",
                "INFO",
                "$",
                "install the 'validation' extra to execute JSON Schema validation",
            )
        ]

    schema = load_json(schema_path)
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(contract), key=lambda item: list(item.path))
    issues = [
        _issue(
            "JSON_SCHEMA",
            "ERROR",
            ".".join(str(part) for part in error.absolute_path) or "$",
            error.message,
        )
        for error in errors
    ]
    return ("PASS" if not issues else "FAIL"), issues


def propagate_qoi_status(
    contract: dict[str, Any], dependency_spec: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    """Propagate missing/invalid upstream inputs without inventing output values."""
    input_by_id = {
        item.get("id"): item
        for item in contract.get("inputs", [])
        if isinstance(item, dict) and item.get("id")
    }
    result: dict[str, dict[str, Any]] = {}
    for qoi_id, spec in dependency_spec.get("qoi", {}).items():
        blocking = []
        outside = []
        for input_id in spec.get("required_inputs", []):
            item = input_by_id.get(input_id)
            if item is None or item.get("value") is None or item.get("status") in {"MISSING", "INVALID"}:
                blocking.append(input_id)
            elif item.get("status") == "OUTSIDE":
                outside.append(input_id)
        status = "OUTSIDE" if outside else ("INPUT_U" if blocking else "NOT_RUN")
        result[qoi_id] = {
            "status": status,
            "blocking_inputs": blocking,
            "outside_inputs": outside,
            "reason": spec.get("reason"),
        }
    return result


def build_validation_report(
    contract_path: str | Path,
    schema_path: str | Path,
    dependency_path: str | Path,
) -> dict[str, Any]:
    contract = load_json(contract_path)
    structure = validate_structure(contract)
    semantics = validate_semantics(contract)
    schema_status, schema_issues = validate_json_schema(contract, schema_path)
    dependencies = load_json(dependency_path)
    all_issues = structure + semantics + schema_issues
    return {
        "contract_path": str(Path(contract_path)),
        "document_version": contract.get("document_version"),
        "checks": {
            "json_syntax": "PASS",
            "json_schema": schema_status,
            "fixed_id_sets": "PASS" if not structure else "FAIL",
            "semantic_consistency": "PASS"
            if not [issue for issue in semantics if issue.level == "ERROR"]
            else "FAIL",
            "run_readiness": "BLOCKED"
            if any(issue.level in {"ERROR", "BLOCKER"} for issue in all_issues)
            or contract.get("solver_run") is None
            else "READY",
        },
        "issues": [asdict(issue) for issue in all_issues],
        "qoi_dependency_status": propagate_qoi_status(contract, dependencies),
        "physical_validation_v01_v12": "UNCHANGED_NOT_RUN",
        "validated_engine_prediction": False,
    }
