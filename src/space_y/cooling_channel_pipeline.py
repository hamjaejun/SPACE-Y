from __future__ import annotations

import csv
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .coolant import build_rectangular_channel_cross_section
from .cooling_channel_candidates import (
    generate_channel_geometry_candidates,
    solve_cooling_channel_candidates,
)
from .cooling_channel_recommendation import classify_cooling_channel_candidates
from .cooling_channel_templates import (
    adjacent_channel_count_candidates,
    generate_reference_width_templates,
)


INTERFACE_VERSION = "SPACE_Y_COOLING_CHANNEL_PIPELINE_V1"


class CoolingPipelineStop(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoolingPipelineStop("INPUT_U", f"{name} is required")
    return value.strip()


def _bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise CoolingPipelineStop("INPUT_U", f"{name} must be an explicit boolean")
    return value


def _positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise CoolingPipelineStop("INPUT_U", f"{name} must be numeric") from error
    if not math.isfinite(number) or number <= 0:
        raise CoolingPipelineStop("INPUT_U", f"{name} must be finite and positive")
    return number


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise CoolingPipelineStop("INPUT_U", f"{name} must be numeric") from error
    if not math.isfinite(number):
        raise CoolingPipelineStop("INPUT_U", f"{name} must be finite")
    return number


def _nonnegative(value: Any, name: str) -> float:
    number = _finite(value, name)
    if number < 0:
        raise CoolingPipelineStop("INPUT_U", f"{name} must be non-negative")
    return number


def _failure(run_id: Any, status: str, reason: str, **extra: Any) -> dict[str, Any]:
    result = {
        "interface_version": INTERFACE_VERSION,
        "run_id": run_id,
        "status": status,
        "reason": reason,
        "pipeline_stage": "FAILED",
        "templates": [],
        "geometry_candidates": [],
        "candidates": [],
        "validated_engine_prediction": False,
        "physical_validation_v01_v12": "NOT_RUN",
        "safety_approval": False,
        "final_design_selection": False,
    }
    result.update(extra)
    return result


def _parse_geometry_input(spec: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    if not isinstance(spec, dict):
        raise CoolingPipelineStop("INPUT_U", "geometry_input must be an object")
    if _text(spec.get("geometry_policy"), "geometry policy") != "CONSTANT_RIB_WIDTH_AXIAL":
        raise CoolingPipelineStop("OUTSIDE", "only CONSTANT_RIB_WIDTH_AXIAL is implemented")
    if _text(spec.get("path_type"), "path type") != "AXIAL":
        raise CoolingPipelineStop("OUTSIDE", "only AXIAL paths are implemented")
    if spec.get("circuit_passes") != 1:
        raise CoolingPipelineStop("OUTSIDE", "only one-pass channels are implemented")
    throat_id = _text(spec.get("throat_station_id"), "throat station ID")
    _text(spec.get("mean_channel_radius_definition"), "mean channel radius definition")
    _text(spec.get("geometry_source"), "geometry source")
    raw = spec.get("mean_radius_profile")
    if not isinstance(raw, list) or not raw:
        raise CoolingPipelineStop("INPUT_U", "mean_radius_profile is required")
    stations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise CoolingPipelineStop("INPUT_U", "geometry stations must be objects")
        station_id = _text(item.get("station_id"), f"station {index} ID")
        if station_id in seen:
            raise CoolingPipelineStop("INPUT_U", f"duplicate station ID: {station_id}")
        seen.add(station_id)
        stations.append(
            {
                "station_id": station_id,
                "x_m": _finite(item.get("x_m"), f"{station_id} x_m"),
                "mean_channel_radius_m": _positive(
                    item.get("mean_channel_radius_m"),
                    f"{station_id} mean channel radius",
                ),
                "is_throat": station_id == throat_id,
                "geometry_source": _text(
                    item.get("geometry_source"), f"{station_id} geometry source"
                ),
            }
        )
    if throat_id not in seen:
        raise CoolingPipelineStop("INPUT_U", "throat station is absent from radius profile")
    stations.sort(key=lambda item: (item["x_m"], item["station_id"]))
    return stations, throat_id


def _manufacturing_values(spec: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise CoolingPipelineStop("INPUT_U", "manufacturing_constraints must be an object")
    limits = spec.get("channel_width_limits_m")
    if not isinstance(limits, list) or len(limits) != 2:
        raise CoolingPipelineStop("INPUT_U", "channel width limits require [min, max]")
    width_min = _positive(limits[0], "minimum channel width")
    width_max = _positive(limits[1], "maximum channel width")
    if width_max < width_min:
        raise CoolingPipelineStop("INPUT_U", "maximum channel width is below minimum")
    heights_raw = spec.get("allowed_channel_heights_m")
    if not isinstance(heights_raw, list) or not heights_raw:
        raise CoolingPipelineStop("INPUT_U", "allowed channel heights are required")
    heights = [_positive(value, "allowed channel height") for value in heights_raw]
    return {
        "classification": _text(spec.get("classification"), "constraint classification"),
        "channel_width_limits_m": [width_min, width_max],
        "minimum_rib_thickness_m": _positive(
            spec.get("minimum_rib_thickness_m"), "minimum rib thickness"
        ),
        "allowed_channel_heights_m": heights,
        "equivalent_sand_roughness_m": _nonnegative(
            spec.get("equivalent_sand_roughness_m"), "equivalent sand roughness"
        ),
        "constraints_source": _text(spec.get("constraints_source"), "constraint source"),
        "roughness_source": _text(spec.get("roughness_source"), "roughness source"),
    }


def _station_geometry(
    stations: list[dict[str, Any]], count: int, rib: float, height: float
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for station in stations:
        pitch = 2.0 * math.pi * station["mean_channel_radius_m"] / count
        width = pitch - rib
        cross_section = None
        if width > 0:
            cross_section = build_rectangular_channel_cross_section(
                {"width_m": width, "height_m": height, "channel_count": count}
            )
        result.append(
            {
                **station,
                "circumferential_pitch_m": pitch,
                "channel_width_m": width,
                "channel_height_m": height,
                "flow_area_m2": (
                    cross_section.total_flow_area_m2 if cross_section else None
                ),
                "hydraulic_diameter_m": (
                    cross_section.hydraulic_diameter_m if cross_section else None
                ),
                "geometric_validity_status": (
                    "INPUT_U_MANUFACTURING_CONSTRAINTS" if width > 0 else "OUTSIDE"
                ),
                "geometric_reasons": (
                    [] if width > 0 else ["channel width is non-positive"]
                ),
            }
        )
    return result


def _unique_geometry_candidates(
    templates: list[dict[str, Any]],
    stations: list[dict[str, Any]],
    throat_id: str,
    manufacturing: dict[str, Any] | None,
) -> tuple[list[dict[str, Any]], int]:
    throat = next(item for item in stations if item["station_id"] == throat_id)
    records: dict[tuple[int, float, float], dict[str, Any]] = {}
    raw_count = 0
    for template in templates:
        channel_counts = adjacent_channel_count_candidates(
            template, throat["mean_channel_radius_m"]
        )
        for count in channel_counts["adjacent_integer_channel_counts"]:
            raw_count += 1
            rib = float(template["rib_thickness_m"])
            height = float(template["channel_height_m"])
            key = (int(count), rib, height)
            actual_width = channel_counts["recalculated_throat_widths_m"][str(count)]
            origin = {
                "template_id": template["template_id"],
                "reference_width_m": template["reference_width_m"],
                "actual_throat_width_m": actual_width,
                "actual_minus_reference_width_m": (
                    actual_width - template["reference_width_m"]
                ),
            }
            if key in records:
                records[key]["template_origins"].append(origin)
                continue
            geometry_id = f"GCH-{len(records) + 1:04d}"
            if manufacturing is None:
                station_records = _station_geometry(stations, count, rib, height)
                any_nonpositive = any(
                    item["channel_width_m"] <= 0 for item in station_records
                )
                geometry_status = (
                    "OUTSIDE" if any_nonpositive else "INPUT_U_MANUFACTURING_CONSTRAINTS"
                )
                allowed = False
                exclusion_reasons = (
                    ["at least one station has non-positive channel width"]
                    if any_nonpositive
                    else ["manufacturing constraints were not supplied"]
                )
            else:
                generated = generate_channel_geometry_candidates(
                    {
                        "geometry_policy": "CONSTANT_RIB_WIDTH_AXIAL",
                        "path_type": "AXIAL",
                        "circuit_passes": 1,
                        "channel_count_candidates": [count],
                        "rib_thickness_candidates_m": [rib],
                        "channel_height_candidates_m": [height],
                        "channel_width_limits_m": manufacturing[
                            "channel_width_limits_m"
                        ],
                        "candidate_range_source": manufacturing[
                            "constraints_source"
                        ],
                        "geometry_source": throat["geometry_source"],
                        "equivalent_sand_roughness_m": manufacturing[
                            "equivalent_sand_roughness_m"
                        ],
                        "roughness_source": manufacturing["roughness_source"],
                        "assumptions": [
                            "CONSTANT_RIB_WIDTH",
                            "CONSTANT_CHANNEL_HEIGHT",
                            "AXIAL_ONE_PASS",
                        ],
                        "mean_radius_profile": stations,
                    }
                )[0]
                station_records = generated["stations"]
                exclusion_reasons = []
                if rib < manufacturing["minimum_rib_thickness_m"]:
                    exclusion_reasons.append("rib thickness is below the supplied minimum")
                if not any(
                    math.isclose(height, value, rel_tol=0.0, abs_tol=1.0e-12)
                    for value in manufacturing["allowed_channel_heights_m"]
                ):
                    exclusion_reasons.append("channel height is not in the supplied allowed set")
                if generated["geometric_validity_status"] != "PASS":
                    exclusion_reasons.append(
                        "at least one station violates supplied channel-width constraints"
                    )
                allowed = not exclusion_reasons
                geometry_status = "PASS" if allowed else "OUTSIDE"
            records[key] = {
                "geometry_candidate_id": geometry_id,
                "candidate_id": geometry_id,
                "channel_count": count,
                "rib_thickness_m": rib,
                "channel_height_m": height,
                "geometry_policy": "CONSTANT_RIB_WIDTH_AXIAL",
                "path_type": "AXIAL",
                "circuit_passes": 1,
                "template_origins": [origin],
                "target_reference_widths_m": [template["reference_width_m"]],
                "geometric_validity_status": geometry_status,
                "calculation_allowed": allowed,
                "geometry_exclusion_reasons": exclusion_reasons,
                "stations": station_records,
                "design_approved": False,
            }
    for record in records.values():
        record["target_reference_widths_m"] = sorted(
            {item["reference_width_m"] for item in record["template_origins"]}
        )
    return list(records.values()), raw_count


def _coolant_for_sweep(
    coolant: dict[str, Any], stations: list[dict[str, Any]], purpose: str
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    if not isinstance(coolant, dict):
        raise CoolingPipelineStop("INPUT_U", "coolant_conditions must be an object")
    identity = coolant.get("fluid_identity")
    if not isinstance(identity, dict):
        raise CoolingPipelineStop("INPUT_U", "fluid identity is required")
    classification = _text(identity.get("classification"), "fluid classification")
    if purpose == "ENGINE_CANDIDATE_SCREENING":
        if (
            classification != "EXACT_2_PROPANOL"
            or identity.get("canonical_name") != "2-propanol"
            or identity.get("cas_number") != "67-63-0"
        ):
            raise CoolingPipelineStop(
                "INPUT_U", "engine screening requires exact 2-propanol CAS 67-63-0"
            )
    station_properties = coolant.get("station_fluid_properties", [])
    if not isinstance(station_properties, list):
        raise CoolingPipelineStop("INPUT_U", "station fluid properties must be an array")
    property_by_id: dict[str, dict[str, Any]] = {}
    for item in station_properties:
        if not isinstance(item, dict):
            raise CoolingPipelineStop("INPUT_U", "station property entries must be objects")
        station_id = _text(item.get("station_id"), "property station ID")
        if station_id in property_by_id:
            raise CoolingPipelineStop("INPUT_U", f"duplicate property station: {station_id}")
        property_by_id[station_id] = {
            key: value for key, value in item.items() if key != "station_id"
        }
    known_ids = {item["station_id"] for item in stations}
    if not set(property_by_id).issubset(known_ids):
        raise CoolingPipelineStop("INPUT_U", "station properties reference unknown geometry stations")
    augmented = []
    for station in stations:
        item = dict(station)
        if station["station_id"] in property_by_id:
            item["fluid_properties"] = property_by_id[station["station_id"]]
        augmented.append(item)
    stripped = {
        key: value
        for key, value in coolant.items()
        if key not in {"fluid_identity", "station_fluid_properties"}
    }
    return stripped, augmented


def _unresolved_inputs(request: dict[str, Any]) -> list[dict[str, str]]:
    mapping = (
        ("geometry_input", "PIPE-I01", "actual station coordinates and mean channel radii"),
        ("manufacturing_constraints", "PIPE-I02", "approved or synthetic geometry constraints and roughness"),
        ("coolant_conditions", "PIPE-I03", "regenerative coolant mass flow and fluid properties"),
        ("model_conditions", "PIPE-I04", "correlation domains and applicability evidence"),
        ("comparison", "PIPE-I05", "common-station candidate comparison policy"),
    )
    return [
        {"id": input_id, "status": "INPUT_U", "requirement": requirement}
        for key, input_id, requirement in mapping
        if request.get(key) is None
    ]


def solve_cooling_channel_pipeline(request: dict[str, Any]) -> dict[str, Any]:
    run_id = request.get("run_id") if isinstance(request, dict) else None
    try:
        if not isinstance(request, dict):
            raise CoolingPipelineStop("INPUT_U", "pipeline request must be an object")
        if request.get("schema_version") != INTERFACE_VERSION:
            raise CoolingPipelineStop("INPUT_U", "unsupported pipeline schema version")
        purpose = request.get("purpose")
        if purpose not in {
            "NON_DESIGN_ARITHMETIC_VERIFICATION",
            "ENGINE_CANDIDATE_SCREENING",
        }:
            raise CoolingPipelineStop("INPUT_U", "unsupported pipeline purpose")
        evidence_complete = _bool(
            request.get("input_evidence_complete"), "input_evidence_complete"
        )
        template_spec = request.get("template_grid")
        templates = generate_reference_width_templates(template_spec)
        expected = template_spec.get("expected_template_count")
        if expected is not None and int(expected) != len(templates):
            raise CoolingPipelineStop(
                "INPUT_U", "template grid count differs from expected_template_count"
            )
        unresolved = _unresolved_inputs(request)
        common = {
            "interface_version": INTERFACE_VERSION,
            "run_id": run_id,
            "fixture_classification": (
                "SYNTHETIC_NON_DESIGN"
                if purpose == "NON_DESIGN_ARITHMETIC_VERIFICATION"
                else "ENGINE_INPUT_UNVALIDATED"
            ),
            "templates": templates,
            "template_count": len(templates),
            "unresolved_external_inputs": unresolved,
            "validated_engine_prediction": False,
            "physical_validation_v01_v12": "NOT_RUN",
            "safety_approval": False,
            "final_design_selection": False,
        }
        geometry_spec = request.get("geometry_input")
        if geometry_spec is None:
            return {
                **common,
                "status": "INPUT_U",
                "pipeline_stage": "TEMPLATE_ONLY",
                "stage_statuses": {
                    "template_generation": "MODEL_U",
                    "geometry_generation": "INPUT_U",
                    "heat_transfer_evaluation": "NOT_RUN",
                    "candidate_comparison": "NOT_RUN",
                },
                "raw_geometry_candidate_count": 0,
                "unique_geometry_candidate_count": 0,
                "duplicate_geometry_candidate_count": 0,
                "geometry_candidates": [],
                "candidates": [],
                "candidate_recommendation": {
                    "status": "NOT_RUN",
                    "reason": "geometry input is not available",
                    "final_design_selection": False,
                },
            }
        stations, throat_id = _parse_geometry_input(geometry_spec)
        geometry_classification = _text(
            geometry_spec.get("classification"), "geometry classification"
        )
        if (
            purpose == "ENGINE_CANDIDATE_SCREENING"
            and geometry_classification != "TEAM_GEOMETRY_EVIDENCE"
        ):
            raise CoolingPipelineStop(
                "INPUT_U", "engine screening requires team geometry evidence"
            )
        manufacturing_spec = request.get("manufacturing_constraints")
        manufacturing = (
            _manufacturing_values(manufacturing_spec)
            if manufacturing_spec is not None
            else None
        )
        if (
            purpose == "ENGINE_CANDIDATE_SCREENING"
            and manufacturing is not None
            and manufacturing["classification"]
            != "TEAM_APPROVED_MANUFACTURING_CONSTRAINTS"
        ):
            raise CoolingPipelineStop(
                "INPUT_U", "engine screening requires approved manufacturing constraints"
            )
        geometry_candidates, raw_count = _unique_geometry_candidates(
            templates, stations, throat_id, manufacturing
        )
        counts = {
            "raw_geometry_candidate_count": raw_count,
            "unique_geometry_candidate_count": len(geometry_candidates),
            "duplicate_geometry_candidate_count": raw_count
            - len(geometry_candidates),
            "geometrically_valid_candidate_count": sum(
                item["geometric_validity_status"] == "PASS"
                for item in geometry_candidates
            ),
        }
        coolant = request.get("coolant_conditions")
        model = request.get("model_conditions")
        if manufacturing is None or coolant is None or model is None:
            return {
                **common,
                **counts,
                "status": "INPUT_U",
                "pipeline_stage": "GEOMETRY_ONLY",
                "stage_statuses": {
                    "template_generation": "MODEL_U",
                    "geometry_generation": (
                        "MODEL_U" if manufacturing is not None else "INPUT_U"
                    ),
                    "heat_transfer_evaluation": "NOT_RUN",
                    "candidate_comparison": "NOT_RUN",
                },
                "geometry_candidates": geometry_candidates,
                "candidates": geometry_candidates,
                "candidate_recommendation": {
                    "status": "NOT_RUN",
                    "reason": "coolant/model inputs or manufacturing constraints are unavailable",
                    "final_design_selection": False,
                },
            }

        if purpose == "ENGINE_CANDIDATE_SCREENING" and not evidence_complete:
            raise CoolingPipelineStop(
                "INPUT_U", "full engine screening requires input_evidence_complete=true"
            )

        sweep_coolant, augmented_stations = _coolant_for_sweep(
            coolant, stations, purpose
        )
        evaluated: list[dict[str, Any]] = []
        successful_station_count = 0
        for geometry in geometry_candidates:
            if not geometry["calculation_allowed"]:
                candidate = dict(geometry)
                candidate["decision"] = "EXCLUDE_GEOMETRIC_CONSTRAINT"
                candidate["summary"] = {
                    "valid_calculation_station_count": 0,
                    "geometric_validity_status": "OUTSIDE",
                    "correlation_applicability_status": "NOT_RUN",
                    "applicability_assessment": {
                        "status": "NOT_EVALUATED",
                        "reason": "geometry constraints failed before heat-transfer evaluation",
                        "continuous_path_claim": False,
                    },
                }
                evaluated.append(candidate)
                continue
            single_request = {
                "schema_version": "SPACE_Y_COOLING_CHANNEL_SWEEP_V1",
                "purpose": purpose,
                "run_id": f"{run_id}:{geometry['geometry_candidate_id']}",
                "input_evidence_complete": evidence_complete,
                "candidate_space": {
                    "geometry_policy": "CONSTANT_RIB_WIDTH_AXIAL",
                    "path_type": "AXIAL",
                    "circuit_passes": 1,
                    "channel_count_candidates": [geometry["channel_count"]],
                    "rib_thickness_candidates_m": [geometry["rib_thickness_m"]],
                    "channel_height_candidates_m": [geometry["channel_height_m"]],
                    "channel_width_limits_m": manufacturing[
                        "channel_width_limits_m"
                    ],
                    "candidate_range_source": template_spec["source"],
                    "geometry_source": geometry_spec["geometry_source"],
                    "equivalent_sand_roughness_m": manufacturing[
                        "equivalent_sand_roughness_m"
                    ],
                    "roughness_source": manufacturing["roughness_source"],
                    "assumptions": [
                        "CONSTANT_RIB_WIDTH",
                        "CONSTANT_CHANNEL_HEIGHT",
                        "AXIAL_ONE_PASS",
                        "EXPLICIT_TEMPLATE_TO_INTEGER_N_MAPPING",
                    ],
                    "mean_radius_profile": augmented_stations,
                },
                "coolant_conditions": sweep_coolant,
                "model_conditions": model,
            }
            solved = solve_cooling_channel_candidates(single_request)
            if not solved.get("candidates"):
                return {
                    **common,
                    **counts,
                    "status": solved.get("status", "INPUT_U"),
                    "reason": solved.get("reason", "candidate evaluation failed"),
                    "pipeline_stage": "HEAT_TRANSFER_EVALUATION_FAILED",
                    "geometry_candidates": geometry_candidates,
                    "candidates": [],
                }
            candidate = solved["candidates"][0]
            candidate["candidate_id"] = geometry["geometry_candidate_id"]
            candidate["geometry_candidate_id"] = geometry["geometry_candidate_id"]
            candidate["template_origins"] = geometry["template_origins"]
            candidate["target_reference_widths_m"] = geometry[
                "target_reference_widths_m"
            ]
            successful_station_count += candidate.get("summary", {}).get(
                "valid_calculation_station_count", 0
            )
            evaluated.append(candidate)

        recommendation = classify_cooling_channel_candidates(
            evaluated,
            request.get("comparison"),
            request.get("baseline_reference"),
        )
        return {
            **common,
            **counts,
            "status": "MODEL_U" if successful_station_count else "OUTSIDE",
            "pipeline_stage": "FULL_CANDIDATE_EVALUATION",
            "stage_statuses": {
                "template_generation": "MODEL_U",
                "geometry_generation": "MODEL_U",
                "heat_transfer_evaluation": (
                    "MODEL_U" if successful_station_count else "OUTSIDE"
                ),
                "candidate_comparison": recommendation["status"],
            },
            "geometry_candidates": geometry_candidates,
            "candidates": evaluated,
            "successful_station_count": successful_station_count,
            "candidate_recommendation": recommendation,
            "not_implemented": [
                "CFD or three-dimensional flow",
                "coolant axial state marching",
                "gas-side heat transfer or wall-temperature solution",
                "full-path pressure-loss integration",
                "boiling or phase-change analysis",
                "automatic final geometry selection",
            ],
        }
    except CoolingPipelineStop as error:
        return _failure(run_id, error.status, str(error))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _failure(run_id, "INPUT_U", f"invalid or missing input: {error}")


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def solve_cooling_channel_pipeline_file(
    path: str | Path, schema_path: str | Path | None = None
) -> dict[str, Any]:
    request = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        return _failure(None, "INPUT_U", "pipeline request must be an object")
    schema = None
    if schema_path is not None:
        schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
        try:
            import jsonschema
        except ImportError:
            pass
        else:
            errors = sorted(
                jsonschema.Draft202012Validator(schema).iter_errors(request),
                key=lambda item: list(item.path),
            )
            if errors:
                return _failure(
                    request.get("run_id"),
                    "INPUT_U",
                    "cooling-channel pipeline schema validation failed",
                    schema_errors=[
                        {
                            "path": ".".join(str(part) for part in error.absolute_path)
                            or "$",
                            "message": error.message,
                        }
                        for error in errors
                    ],
                )
    result = solve_cooling_channel_pipeline(request)
    result["provenance"] = {
        "request_sha256": _sha256_json(request),
        "schema_sha256": _sha256_json(schema) if schema is not None else None,
    }
    return result


def write_pipeline_geometry_csv(path: str | Path, result: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "geometry_candidate_id",
        "channel_count",
        "rib_thickness_m",
        "channel_height_m",
        "geometric_validity_status",
        "calculation_allowed",
        "template_ids",
        "reference_widths_m",
        "actual_throat_width_m",
        "actual_minus_reference_widths_m",
        "geometry_exclusion_reasons",
        "design_approved",
    ]
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fields)
        writer.writeheader()
        for item in result.get("geometry_candidates", []):
            origins = item.get("template_origins", [])
            actual_widths = sorted(
                {origin.get("actual_throat_width_m") for origin in origins}
            )
            writer.writerow(
                {
                    "geometry_candidate_id": item.get("geometry_candidate_id"),
                    "channel_count": item.get("channel_count"),
                    "rib_thickness_m": item.get("rib_thickness_m"),
                    "channel_height_m": item.get("channel_height_m"),
                    "geometric_validity_status": item.get(
                        "geometric_validity_status"
                    ),
                    "calculation_allowed": item.get("calculation_allowed"),
                    "template_ids": "|".join(
                        origin.get("template_id", "") for origin in origins
                    ),
                    "reference_widths_m": "|".join(
                        str(origin.get("reference_width_m")) for origin in origins
                    ),
                    "actual_throat_width_m": "|".join(
                        str(value) for value in actual_widths
                    ),
                    "actual_minus_reference_widths_m": "|".join(
                        str(origin.get("actual_minus_reference_width_m"))
                        for origin in origins
                    ),
                    "geometry_exclusion_reasons": "|".join(
                        item.get("geometry_exclusion_reasons", [])
                    ),
                    "design_approved": item.get("design_approved", False),
                }
            )
