from __future__ import annotations

import csv
from dataclasses import asdict
import hashlib
from itertools import product
import json
import math
from pathlib import Path
from typing import Any

from .cooling_applicability import (
    assess_candidate_station_path,
    build_cooling_applicability_policy,
)
from .coolant import (
    CoolantStop,
    build_friction_domain,
    build_heat_transfer_domain,
    build_rectangular_channel_cross_section,
    evaluate_rectangular_cooling_hl,
)
from .cooling_channel_recommendation import classify_cooling_channel_candidates


INTERFACE_VERSION = "SPACE_Y_COOLING_CHANNEL_SWEEP_V1"


class CandidateSweepStop(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CandidateSweepStop("INPUT_U", f"{name} is required")
    return value.strip()


def _required_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise CandidateSweepStop("INPUT_U", f"{name} must be an explicit boolean")
    return value


def _positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise CandidateSweepStop("INPUT_U", f"{name} must be numeric") from error
    if not math.isfinite(number) or number <= 0:
        raise CandidateSweepStop("INPUT_U", f"{name} must be finite and positive")
    return number


def _nonnegative(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise CandidateSweepStop("INPUT_U", f"{name} must be numeric") from error
    if not math.isfinite(number) or number < 0:
        raise CandidateSweepStop("INPUT_U", f"{name} must be finite and non-negative")
    return number


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise CandidateSweepStop("INPUT_U", f"{name} must be numeric") from error
    if not math.isfinite(number):
        raise CandidateSweepStop("INPUT_U", f"{name} must be finite")
    return number


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise CandidateSweepStop("INPUT_U", f"{name} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise CandidateSweepStop("INPUT_U", f"{name} must be a positive integer") from error
    if number <= 0 or float(value) != number:
        raise CandidateSweepStop("INPUT_U", f"{name} must be a positive integer")
    return number


def _positive_array(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise CandidateSweepStop("INPUT_U", f"{name} must be a non-empty array")
    return [_positive(item, f"{name}[{index}]") for index, item in enumerate(value)]


def _integer_array(value: Any, name: str) -> list[int]:
    if not isinstance(value, list) or not value:
        raise CandidateSweepStop("INPUT_U", f"{name} must be a non-empty array")
    return [
        _positive_integer(item, f"{name}[{index}]")
        for index, item in enumerate(value)
    ]


def _failure(run_id: Any, status: str, reason: str, **extra: Any) -> dict[str, Any]:
    result = {
        "interface_version": INTERFACE_VERSION,
        "run_id": run_id,
        "status": status,
        "reason": reason,
        "candidates": [],
        "validated_engine_prediction": False,
        "physical_validation_v01_v12": "NOT_RUN",
        "safety_approval": False,
        "final_design_selection": False,
    }
    result.update(extra)
    return result


def _width_limits(spec: dict[str, Any]) -> tuple[float, float]:
    value = spec.get("channel_width_limits_m")
    if not isinstance(value, list) or len(value) != 2:
        raise CandidateSweepStop(
            "INPUT_U", "channel_width_limits_m must contain [minimum, maximum]"
        )
    minimum = _positive(value[0], "minimum channel width")
    maximum = _positive(value[1], "maximum channel width")
    if maximum < minimum:
        raise CandidateSweepStop(
            "INPUT_U", "maximum channel width must not be below minimum"
        )
    return minimum, maximum


def _radius_profile(spec: dict[str, Any]) -> list[dict[str, Any]]:
    raw = spec.get("mean_radius_profile")
    if not isinstance(raw, list) or not raw:
        raise CandidateSweepStop(
            "INPUT_U",
            "CONSTANT_RIB_WIDTH_AXIAL requires an explicit mean-radius profile",
        )
    stations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, dict):
            raise CandidateSweepStop("INPUT_U", "radius profile entries must be objects")
        station_id = _required_text(item.get("station_id"), "station_id")
        if station_id in seen:
            raise CandidateSweepStop("INPUT_U", f"duplicate station_id: {station_id}")
        seen.add(station_id)
        is_throat = _required_bool(item.get("is_throat", False), "is_throat")
        station = {
            "station_id": station_id,
            "x_m": _finite(item.get("x_m"), f"{station_id} x_m"),
            "mean_channel_radius_m": _positive(
                item.get("mean_channel_radius_m"),
                f"{station_id} mean channel radius",
            ),
            "is_throat": is_throat,
            "geometry_source": _required_text(
                item.get("geometry_source"), f"{station_id} geometry source"
            ),
        }
        if item.get("fluid_properties") is not None:
            if not isinstance(item["fluid_properties"], dict):
                raise CandidateSweepStop(
                    "INPUT_U", f"{station_id} fluid_properties must be an object"
                )
            station["fluid_properties"] = item["fluid_properties"]
        stations.append(station)
    stations.sort(key=lambda item: (item["x_m"], item["station_id"]))
    return stations


def _local_station(spec: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "station_id": _required_text(
                spec.get("local_station_id", "LOCAL-SECTION"), "local station id"
            ),
            "x_m": _finite(spec.get("local_x_m", 0.0), "local x_m"),
            "mean_channel_radius_m": None,
            "is_throat": False,
            "geometry_source": _required_text(
                spec.get("geometry_source"), "local geometry source"
            ),
        }
    ]


def generate_channel_geometry_candidates(
    candidate_space: dict[str, Any],
) -> list[dict[str, Any]]:
    """Generate deterministic N/b/h candidates without selecting an optimum."""

    if not isinstance(candidate_space, dict):
        raise CandidateSweepStop("INPUT_U", "candidate_space must be an object")
    mode = _required_text(candidate_space.get("geometry_policy"), "geometry policy")
    if mode not in {"CONSTANT_RIB_WIDTH_AXIAL", "CONSTANT_WIDTH_LOCAL"}:
        raise CandidateSweepStop("INPUT_U", f"unsupported geometry policy: {mode}")
    if _required_text(candidate_space.get("path_type"), "path type") != "AXIAL":
        raise CandidateSweepStop("OUTSIDE", "only AXIAL channel paths are implemented")
    if _positive_integer(candidate_space.get("circuit_passes"), "circuit passes") != 1:
        raise CandidateSweepStop("OUTSIDE", "only one-pass channels are implemented")

    channel_counts = _integer_array(
        candidate_space.get("channel_count_candidates"), "channel count candidates"
    )
    rib_thicknesses = _positive_array(
        candidate_space.get("rib_thickness_candidates_m"),
        "rib thickness candidates",
    )
    channel_heights = _positive_array(
        candidate_space.get("channel_height_candidates_m"),
        "channel height candidates",
    )
    width_min, width_max = _width_limits(candidate_space)
    range_source = _required_text(
        candidate_space.get("candidate_range_source"), "candidate range source"
    )
    assumptions = candidate_space.get("assumptions", [])
    if not isinstance(assumptions, list) or not all(
        isinstance(item, str) and item.strip() for item in assumptions
    ):
        raise CandidateSweepStop("INPUT_U", "candidate assumptions must be strings")

    if mode == "CONSTANT_RIB_WIDTH_AXIAL":
        base_stations = _radius_profile(candidate_space)
        constant_width = None
    else:
        base_stations = _local_station(candidate_space)
        constant_width = _positive(
            candidate_space.get("constant_channel_width_m"),
            "constant local channel width",
        )

    candidates: list[dict[str, Any]] = []
    for candidate_index, (count, rib, height) in enumerate(
        product(channel_counts, rib_thicknesses, channel_heights), start=1
    ):
        station_geometries: list[dict[str, Any]] = []
        hard_geometry_failure = False
        for station in base_stations:
            if mode == "CONSTANT_RIB_WIDTH_AXIAL":
                pitch = 2.0 * math.pi * station["mean_channel_radius_m"] / count
                width = pitch - rib
            else:
                pitch = None
                width = constant_width

            reasons: list[str] = []
            if width <= 0:
                reasons.append("channel width is non-positive")
            if width < width_min:
                reasons.append("channel width is below the supplied minimum")
            if width > width_max:
                reasons.append("channel width is above the supplied maximum")
            if reasons:
                hard_geometry_failure = True
                cross_section = None
                if width > 0:
                    cross_section = build_rectangular_channel_cross_section(
                        {
                            "width_m": width,
                            "height_m": height,
                            "channel_count": count,
                        }
                    )
                geometry_status = "OUTSIDE"
            else:
                cross_section = build_rectangular_channel_cross_section(
                    {
                        "width_m": width,
                        "height_m": height,
                        "channel_count": count,
                    }
                )
                geometry_status = (
                    "PASS"
                    if mode == "CONSTANT_RIB_WIDTH_AXIAL"
                    else "LOCAL_ONLY_RADIUS_INPUT_U"
                )

            station_geometry = {
                "station_id": station["station_id"],
                "x_m": station["x_m"],
                "mean_channel_radius_m": station["mean_channel_radius_m"],
                "is_throat": station["is_throat"],
                "circumferential_pitch_m": pitch,
                "channel_width_m": width,
                "channel_height_m": height,
                "flow_area_m2": (
                    None if cross_section is None else cross_section.total_flow_area_m2
                ),
                "hydraulic_diameter_m": (
                    None if cross_section is None else cross_section.hydraulic_diameter_m
                ),
                "geometric_validity_status": geometry_status,
                "geometric_reasons": reasons,
                "geometry_source": station["geometry_source"],
            }
            if station.get("fluid_properties") is not None:
                station_geometry["fluid_properties"] = station["fluid_properties"]
            station_geometries.append(station_geometry)

        geometry_status = (
            "OUTSIDE"
            if hard_geometry_failure
            else (
                "PASS"
                if mode == "CONSTANT_RIB_WIDTH_AXIAL"
                else "INPUT_U_CIRCUMFERENTIAL_PLACEMENT"
            )
        )
        candidates.append(
            {
                "candidate_id": f"CH-{candidate_index:04d}",
                "channel_count": count,
                "rib_thickness_m": rib,
                "channel_height_m": height,
                "geometry_policy": mode,
                "path_type": "AXIAL",
                "circuit_passes": 1,
                "candidate_range_source": range_source,
                "assumptions": list(assumptions),
                "design_approved": False,
                "geometric_validity_status": geometry_status,
                "calculation_allowed": not hard_geometry_failure,
                "stations": station_geometries,
            }
        )
    return candidates


def _fluid_properties(spec: dict[str, Any], name: str) -> dict[str, Any]:
    if not isinstance(spec, dict):
        raise CandidateSweepStop("INPUT_U", f"{name} must be an object")
    return {
        "density_kg_m3": _positive(spec.get("density_kg_m3"), f"{name} density"),
        "viscosity_Pa_s": _positive(
            spec.get("viscosity_Pa_s"), f"{name} viscosity"
        ),
        "cp_J_kgK": _positive(spec.get("cp_J_kgK"), f"{name} cp"),
        "conductivity_W_mK": _positive(
            spec.get("conductivity_W_mK"), f"{name} conductivity"
        ),
        "property_source": _required_text(
            spec.get("property_source"), f"{name} property source"
        ),
        "property_version": _required_text(
            spec.get("property_version"), f"{name} property version"
        ),
    }


def solve_cooling_channel_candidates(request: dict[str, Any]) -> dict[str, Any]:
    """Evaluate candidate geometry stations without ranking or final selection."""

    run_id = request.get("run_id") if isinstance(request, dict) else None
    try:
        if not isinstance(request, dict):
            raise CandidateSweepStop("INPUT_U", "request must be an object")
        if request.get("schema_version") != INTERFACE_VERSION:
            raise CandidateSweepStop("INPUT_U", "unsupported candidate-sweep version")
        purpose = request.get("purpose")
        if purpose not in {
            "NON_DESIGN_ARITHMETIC_VERIFICATION",
            "ENGINE_CANDIDATE_SCREENING",
        }:
            raise CandidateSweepStop("INPUT_U", "unsupported or missing purpose")
        evidence_complete = _required_bool(
            request.get("input_evidence_complete"), "input_evidence_complete"
        )
        if purpose == "ENGINE_CANDIDATE_SCREENING" and not evidence_complete:
            raise CandidateSweepStop(
                "INPUT_U", "engine screening requires input_evidence_complete=true"
            )

        candidate_space = request.get("candidate_space")
        candidates = generate_channel_geometry_candidates(candidate_space)
        roughness = _nonnegative(
            candidate_space.get("equivalent_sand_roughness_m"),
            "equivalent sand roughness",
        )
        roughness_source = _required_text(
            candidate_space.get("roughness_source"), "roughness source"
        )

        coolant = request.get("coolant_conditions")
        if not isinstance(coolant, dict):
            raise CandidateSweepStop("INPUT_U", "coolant_conditions must be an object")
        mass_flow = _positive(
            coolant.get("coolant_mass_flow_kg_s"), "coolant mass flow"
        )
        mass_flow_source = _required_text(
            coolant.get("coolant_mass_flow_source"), "coolant mass-flow source"
        )
        default_fluid = _fluid_properties(
            coolant.get("default_fluid_properties"), "default fluid properties"
        )
        property_policy = _required_text(
            coolant.get("property_policy"), "property policy"
        )
        if property_policy not in {
            "CONSTANT_SUPPLIED_PROPERTIES",
            "EXPLICIT_BY_STATION_WITH_DEFAULT",
        }:
            raise CandidateSweepStop("INPUT_U", "unsupported property policy")

        model = request.get("model_conditions")
        if not isinstance(model, dict):
            raise CandidateSweepStop("INPUT_U", "model_conditions must be an object")
        fully_developed = _required_bool(
            model.get("fully_developed"), "fully developed condition"
        )
        closure_geometry = _required_text(
            model.get("closure_geometry"), "closure geometry"
        )
        thermal_boundary = _required_text(
            model.get("thermal_boundary_condition"), "thermal boundary condition"
        )
        correlation_boundary = _required_text(
            model.get("heat_transfer_thermal_boundary_condition"),
            "heat-transfer correlation thermal boundary",
        )
        heat_domain = build_heat_transfer_domain(model.get("heat_transfer_domain"))
        friction_domain = build_friction_domain(model.get("friction_domain"))
        applicability_policy = (
            build_cooling_applicability_policy(model["applicability_policy"])
            if model.get("applicability_policy") is not None
            else None
        )

        successful_station_count = 0
        outside_station_count = 0
        input_unknown_station_count = 0
        for candidate in candidates:
            if not candidate["calculation_allowed"]:
                candidate["decision"] = "EXCLUDE_GEOMETRIC_CONSTRAINT"
                candidate["summary"] = {
                    "valid_calculation_station_count": 0,
                    "geometric_validity_status": "OUTSIDE",
                    "correlation_applicability_status": "NOT_RUN",
                    "throat_h_l_W_m2K": None,
                    "minimum_h_l_W_m2K": None,
                    "maximum_h_l_W_m2K": None,
                    "maximum_friction_pressure_gradient_Pa_m": None,
                    "applicability_assessment": assess_candidate_station_path(
                        candidate["stations"], applicability_policy
                    ),
                    "limitations": [
                        "At least one station violates supplied channel-width constraints."
                    ],
                }
                continue

            h_values: list[float] = []
            gradient_values: list[float] = []
            throat_h = None
            candidate_station_statuses: list[str] = []
            for station in candidate["stations"]:
                fluid_raw = station.pop("fluid_properties", None)
                if property_policy == "EXPLICIT_BY_STATION_WITH_DEFAULT" and fluid_raw:
                    fluid = _fluid_properties(
                        fluid_raw, f"{station['station_id']} fluid properties"
                    )
                    property_basis = "EXPLICIT_STATION"
                else:
                    fluid = default_fluid
                    property_basis = "CONSTANT_DEFAULT"
                try:
                    local = evaluate_rectangular_cooling_hl(
                        coolant_mass_flow_kg_s=mass_flow,
                        width_m=station["channel_width_m"],
                        height_m=station["channel_height_m"],
                        channel_count=candidate["channel_count"],
                        viscosity_Pa_s=fluid["viscosity_Pa_s"],
                        cp_J_kgK=fluid["cp_J_kgK"],
                        conductivity_W_mK=fluid["conductivity_W_mK"],
                        equivalent_sand_roughness_m=roughness,
                        closure_geometry=closure_geometry,
                        fully_developed=fully_developed,
                        thermal_boundary_condition=thermal_boundary,
                        heat_transfer_domain=heat_domain,
                        heat_transfer_thermal_boundary_condition=correlation_boundary,
                        friction_domain=friction_domain,
                        density_kg_m3=fluid["density_kg_m3"],
                        applicability_policy=applicability_policy,
                    )
                except CoolantStop as error:
                    station["calculation"] = {
                        "status": error.status,
                        "reason": str(error),
                        "results": None,
                        "property_basis": property_basis,
                        "property_source": fluid["property_source"],
                        "property_version": fluid["property_version"],
                    }
                    candidate_station_statuses.append(error.status)
                    if error.status == "OUTSIDE":
                        outside_station_count += 1
                    else:
                        input_unknown_station_count += 1
                    continue
                except RuntimeError as error:
                    station["calculation"] = {
                        "status": "NUM_U",
                        "reason": str(error),
                        "results": None,
                        "property_basis": property_basis,
                        "property_source": fluid["property_source"],
                        "property_version": fluid["property_version"],
                    }
                    candidate_station_statuses.append("NUM_U")
                    continue

                local_result = asdict(local)
                station["calculation"] = {
                    "status": "MODEL_U",
                    "reason": "arithmetic completed within supplied correlation domains",
                    "results": local_result,
                    "property_basis": property_basis,
                    "property_source": fluid["property_source"],
                    "property_version": fluid["property_version"],
                }
                candidate_station_statuses.append("MODEL_U")
                successful_station_count += 1
                h_values.append(local.h_l_W_m2K)
                if local.friction_pressure_gradient_Pa_m is not None:
                    gradient_values.append(local.friction_pressure_gradient_Pa_m)
                if station["is_throat"]:
                    throat_h = local.h_l_W_m2K

            valid_count = candidate_station_statuses.count("MODEL_U")
            path_assessment = assess_candidate_station_path(
                candidate["stations"], applicability_policy
            )
            if valid_count == len(candidate["stations"]):
                applicability = "APPLICABLE_TO_SUPPLIED_DOMAINS"
                decision = (
                    "LOCAL_ARITHMETIC_ONLY"
                    if candidate["geometric_validity_status"].startswith("INPUT_U")
                    else "RETAIN_FOR_INTEGRATION_REVIEW"
                )
            elif "NUM_U" in candidate_station_statuses:
                applicability = "NUM_U"
                decision = "HOLD_NUMERICAL"
            elif "INPUT_U" in candidate_station_statuses:
                applicability = "INPUT_U"
                decision = "HOLD_INPUT"
            else:
                applicability = "OUTSIDE"
                decision = "EXCLUDE_CORRELATION_DOMAIN"
            candidate["decision"] = decision
            candidate["summary"] = {
                "valid_calculation_station_count": valid_count,
                "geometric_validity_status": candidate[
                    "geometric_validity_status"
                ],
                "correlation_applicability_status": applicability,
                "throat_h_l_W_m2K": throat_h,
                "minimum_h_l_W_m2K": min(h_values) if h_values else None,
                "maximum_h_l_W_m2K": max(h_values) if h_values else None,
                "maximum_friction_pressure_gradient_Pa_m": (
                    max(gradient_values) if gradient_values else None
                ),
                "applicability_assessment": path_assessment,
                "limitations": [
                    "No heat-load, wall-temperature, phase-change, manufacturing, structural, or supply-pressure approval is performed.",
                    "No scalar score or final optimum is assigned.",
                ],
            }

        if successful_station_count:
            overall_status = "MODEL_U"
        elif outside_station_count:
            overall_status = "OUTSIDE"
        elif input_unknown_station_count:
            overall_status = "INPUT_U"
        else:
            overall_status = "OUTSIDE"

        recommendation = classify_cooling_channel_candidates(
            candidates,
            request.get("comparison"),
            request.get("baseline_reference"),
        )

        return {
            "interface_version": INTERFACE_VERSION,
            "run_id": run_id,
            "status": overall_status,
            "fixture_classification": (
                "SYNTHETIC_NON_DESIGN"
                if purpose == "NON_DESIGN_ARITHMETIC_VERIFICATION"
                else "ENGINE_INPUT_UNVALIDATED"
            ),
            "comparison_policy": {
                "basis": "same supplied coolant flow and property conditions",
                "ranking_performed": False,
                "scalar_weighting_performed": False,
                "pareto_final_design_claim": False,
                "statement": (
                    "High h_l alone is not a final design criterion; pressure-gradient, "
                    "whole-engine thermal, phase, structural, manufacturing, and supply "
                    "constraints remain external."
                ),
            },
            "inputs_consumed": {
                "coolant_mass_flow_kg_s": mass_flow,
                "coolant_mass_flow_source": mass_flow_source,
                "property_policy": property_policy,
                "default_property_source": default_fluid["property_source"],
                "default_property_version": default_fluid["property_version"],
                "equivalent_sand_roughness_m": roughness,
                "roughness_source": roughness_source,
                "applicability_policy_id": (
                    applicability_policy.policy_id
                    if applicability_policy is not None
                    else None
                ),
            },
            "counts": {
                "candidate_count": len(candidates),
                "successful_station_count": successful_station_count,
                "outside_station_count": outside_station_count,
                "input_unknown_station_count": input_unknown_station_count,
            },
            "candidate_recommendation": recommendation,
            "candidates": candidates,
            "not_implemented": [
                "helical channels",
                "coolant axial energy/state marching",
                "wall temperature or heat-load calculation",
                "minor, manifold, inlet, or outlet losses",
                "full-path pressure-loss integration",
                "automatic optimum or final design selection",
            ],
            "validated_engine_prediction": False,
            "physical_validation_v01_v12": "NOT_RUN",
            "safety_approval": False,
            "final_design_selection": False,
        }
    except CandidateSweepStop as error:
        return _failure(run_id, error.status, str(error))
    except CoolantStop as error:
        return _failure(run_id, error.status, str(error))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _failure(run_id, "INPUT_U", f"invalid or missing input: {error}")


def _sha256_json(value: Any) -> str:
    payload = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def solve_cooling_channel_candidates_file(
    path: str | Path, schema_path: str | Path | None = None
) -> dict[str, Any]:
    request = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        return _failure(None, "INPUT_U", "candidate-sweep request must be an object")
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
                    "candidate-sweep schema validation failed",
                    schema_errors=[
                        {
                            "path": ".".join(
                                str(part) for part in error.absolute_path
                            )
                            or "$",
                            "message": error.message,
                        }
                        for error in errors
                    ],
                )
    result = solve_cooling_channel_candidates(request)
    result["provenance"] = {
        "request_sha256": _sha256_json(request),
        "schema_sha256": _sha256_json(schema) if schema is not None else None,
    }
    return result


def write_candidate_results_csv(path: str | Path, result: dict[str, Any]) -> None:
    """Write one row per candidate/station without changing result status."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "candidate_id",
        "candidate_decision",
        "recommendation_tags",
        "channel_count",
        "rib_thickness_m",
        "channel_height_m",
        "station_id",
        "x_m",
        "mean_channel_radius_m",
        "channel_width_m",
        "flow_area_m2",
        "hydraulic_diameter_m",
        "geometric_validity_status",
        "calculation_status",
        "velocity_m_s",
        "Reynolds",
        "Prandtl",
        "darcy_factor",
        "smooth_darcy_factor",
        "roughness_friction_ratio",
        "roughness_warning_status",
        "Nusselt",
        "h_l_W_m2K",
        "friction_pressure_gradient_Pa_m",
        "channel_aspect_ratio_h_over_w",
        "gnielinski_correlation_status",
        "re_design_recommendation_status",
        "transition_region_warning",
    ]
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in result.get("candidates", []):
            for station in candidate.get("stations", []):
                calculation = station.get("calculation") or {}
                values = calculation.get("results") or {}
                writer.writerow(
                    {
                        "candidate_id": candidate.get("candidate_id"),
                        "candidate_decision": candidate.get("decision"),
                        "recommendation_tags": "|".join(
                            candidate.get("recommendation_tags", [])
                        ),
                        "channel_count": candidate.get("channel_count"),
                        "rib_thickness_m": candidate.get("rib_thickness_m"),
                        "channel_height_m": candidate.get("channel_height_m"),
                        "station_id": station.get("station_id"),
                        "x_m": station.get("x_m"),
                        "mean_channel_radius_m": station.get(
                            "mean_channel_radius_m"
                        ),
                        "channel_width_m": station.get("channel_width_m"),
                        "flow_area_m2": station.get("flow_area_m2"),
                        "hydraulic_diameter_m": station.get(
                            "hydraulic_diameter_m"
                        ),
                        "geometric_validity_status": station.get(
                            "geometric_validity_status"
                        ),
                        "calculation_status": calculation.get("status", "NOT_RUN"),
                        "velocity_m_s": values.get("velocity_m_s"),
                        "Reynolds": values.get("Reynolds"),
                        "Prandtl": values.get("Prandtl"),
                        "darcy_factor": values.get("darcy_factor"),
                        "smooth_darcy_factor": values.get(
                            "smooth_darcy_factor"
                        ),
                        "roughness_friction_ratio": values.get(
                            "roughness_friction_ratio"
                        ),
                        "roughness_warning_status": (
                            values.get("roughness_assessment") or {}
                        ).get("roughness_heat_transfer_warning_status"),
                        "Nusselt": values.get("Nusselt"),
                        "h_l_W_m2K": values.get("h_l_W_m2K"),
                        "friction_pressure_gradient_Pa_m": values.get(
                            "friction_pressure_gradient_Pa_m"
                        ),
                        "channel_aspect_ratio_h_over_w": values.get(
                            "channel_aspect_ratio_h_over_w"
                        ),
                        "gnielinski_correlation_status": (
                            values.get("applicability_assessment") or {}
                        ).get("correlation_applicability_status"),
                        "re_design_recommendation_status": (
                            values.get("applicability_assessment") or {}
                        ).get("re_design_recommendation_status"),
                        "transition_region_warning": (
                            values.get("applicability_assessment") or {}
                        ).get("transition_region_warning"),
                    }
                )


def write_candidate_summary_csv(path: str | Path, result: dict[str, Any]) -> None:
    """Write one comparison row per candidate for downstream integration review."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "candidate_id",
        "channel_count",
        "rib_thickness_m",
        "channel_height_m",
        "candidate_decision",
        "geometric_validity_status",
        "model_applicability",
        "reference_station_id",
        "h_l_at_reference_W_m2K",
        "friction_pressure_gradient_at_reference_Pa_m",
        "minimum_h_l_W_m2K",
        "maximum_h_l_W_m2K",
        "maximum_friction_pressure_gradient_Pa_m",
        "minimum_reynolds",
        "minimum_reynolds_station_id",
        "minimum_reynolds_x_m",
        "re_design_minimum",
        "re_design_margin",
        "re_design_recommendation_status",
        "transition_warning_station_ids",
        "maximum_roughness_friction_ratio",
        "maximum_roughness_ratio_station_id",
        "roughness_warning_status",
        "gnielinski_correlation_domain_status",
        "continuous_path_claim",
        "pareto_status",
        "recommendation_tags",
        "exclusion_reasons",
        "design_approved",
    ]
    with output.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=fieldnames)
        writer.writeheader()
        for candidate in result.get("candidates", []):
            comparison = candidate.get("comparison") or {}
            summary = candidate.get("summary") or {}
            applicability = summary.get("applicability_assessment") or {}
            writer.writerow(
                {
                    "candidate_id": candidate.get("candidate_id"),
                    "channel_count": candidate.get("channel_count"),
                    "rib_thickness_m": candidate.get("rib_thickness_m"),
                    "channel_height_m": candidate.get("channel_height_m"),
                    "candidate_decision": candidate.get("decision"),
                    "geometric_validity_status": candidate.get(
                        "geometric_validity_status"
                    ),
                    "model_applicability": comparison.get(
                        "model_applicability"
                    ),
                    "reference_station_id": comparison.get(
                        "reference_station_id"
                    ),
                    "h_l_at_reference_W_m2K": comparison.get(
                        "h_l_at_reference_W_m2K"
                    ),
                    "friction_pressure_gradient_at_reference_Pa_m": comparison.get(
                        "friction_pressure_gradient_at_reference_Pa_m"
                    ),
                    "minimum_h_l_W_m2K": summary.get("minimum_h_l_W_m2K"),
                    "maximum_h_l_W_m2K": summary.get("maximum_h_l_W_m2K"),
                    "maximum_friction_pressure_gradient_Pa_m": summary.get(
                        "maximum_friction_pressure_gradient_Pa_m"
                    ),
                    "minimum_reynolds": applicability.get("minimum_reynolds"),
                    "minimum_reynolds_station_id": applicability.get(
                        "minimum_reynolds_station_id"
                    ),
                    "minimum_reynolds_x_m": applicability.get(
                        "minimum_reynolds_x_m"
                    ),
                    "re_design_minimum": applicability.get(
                        "re_design_minimum"
                    ),
                    "re_design_margin": applicability.get("re_design_margin"),
                    "re_design_recommendation_status": applicability.get(
                        "re_design_recommendation_status"
                    ),
                    "transition_warning_station_ids": "|".join(
                        applicability.get("transition_warning_station_ids", [])
                    ),
                    "maximum_roughness_friction_ratio": applicability.get(
                        "maximum_roughness_friction_ratio"
                    ),
                    "maximum_roughness_ratio_station_id": applicability.get(
                        "maximum_roughness_ratio_station_id"
                    ),
                    "roughness_warning_status": applicability.get(
                        "roughness_warning_status"
                    ),
                    "gnielinski_correlation_domain_status": applicability.get(
                        "correlation_domain_status"
                    ),
                    "continuous_path_claim": applicability.get(
                        "continuous_path_claim", False
                    ),
                    "pareto_status": comparison.get("pareto_status"),
                    "recommendation_tags": "|".join(
                        candidate.get("recommendation_tags", [])
                    ),
                    "exclusion_reasons": "|".join(
                        candidate.get("exclusion_reasons", [])
                    ),
                    "design_approved": candidate.get("design_approved", False),
                }
            )
