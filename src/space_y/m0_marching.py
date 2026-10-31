from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from .external import sha256_file
from .m0 import M0Stop, build_m0_property_adapter
from .m0_coupled import solve_coupled_single_segment


class MarchingStop(RuntimeError):
    def __init__(self, status: str, message: str, *, segment_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.segment_id = segment_id


def _source(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise MarchingStop("INPUT_U", f"{name} source is required")
    return value


def _positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise MarchingStop("INPUT_U", f"{name} must be finite and positive")
    return number


def _empty_qoi(status: str) -> dict[str, dict[str, Any]]:
    return {f"Y{index:02d}": {"value": None, "status": status} for index in range(1, 6)}


def _failure(
    run_id: Any,
    status: str,
    reason: str,
    *,
    segments: list[dict[str, Any]] | None = None,
    stopped_at_segment: str | None = None,
    **diagnostics: Any,
) -> dict[str, Any]:
    result = {
        "run_id": run_id,
        "status": status,
        "numerical_status": status if status == "NUM_U" else "NOT_RUN",
        "reason": reason,
        "stopped_at_segment": stopped_at_segment,
        "segments": segments or [],
        "qoi": _empty_qoi(status),
        "physical_validation_v01_v12": "NOT_RUN",
        "validated_engine_prediction": False,
    }
    result.update(diagnostics)
    return result


def _validate_orders(request: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    segments = request.get("segments")
    if not isinstance(segments, list) or not segments:
        raise MarchingStop("INPUT_U", "at least one segment is required")
    by_id: dict[str, dict[str, Any]] = {}
    coordinates: dict[str, float] = {}
    for segment in segments:
        if not isinstance(segment, dict):
            raise MarchingStop("INPUT_U", "each segment must be an object")
        segment_id = segment.get("segment_id")
        if not isinstance(segment_id, str) or not segment_id.strip():
            raise MarchingStop("INPUT_U", "segment_id is required")
        if segment_id in by_id:
            raise MarchingStop("INPUT_U", "segment IDs must be unique")
        coordinate = float(segment["gas_axial_coordinate_m"])
        if not math.isfinite(coordinate):
            raise MarchingStop("INPUT_U", "gas axial coordinates must be finite")
        _source(segment.get("gas_axial_coordinate_source"), f"{segment_id} gas axial coordinate")
        by_id[segment_id] = segment
        coordinates[segment_id] = coordinate

    ids = set(by_id)
    gas_order = request.get("gas_axial_order_segment_ids")
    coolant_order = request.get("coolant_flow_order_segment_ids")
    for order, name in ((gas_order, "gas axial order"), (coolant_order, "coolant flow order")):
        if not isinstance(order, list) or len(order) != len(ids) or set(order) != ids:
            raise MarchingStop("INPUT_U", f"{name} must contain every segment exactly once")
        if len(order) != len(set(order)):
            raise MarchingStop("INPUT_U", f"{name} contains duplicate segment IDs")

    gas_coordinates = [coordinates[segment_id] for segment_id in gas_order]
    if any(right <= left for left, right in zip(gas_coordinates, gas_coordinates[1:])):
        raise MarchingStop("INPUT_U", "gas axial order must have strictly increasing coordinates")

    coolant_coordinates = [coordinates[segment_id] for segment_id in coolant_order]
    direction = request.get("coolant_flow_direction")
    if direction == "INCREASING_GAS_AXIAL_COORDINATE":
        valid = all(right > left for left, right in zip(coolant_coordinates, coolant_coordinates[1:]))
    elif direction == "DECREASING_GAS_AXIAL_COORDINATE":
        valid = all(right < left for left, right in zip(coolant_coordinates, coolant_coordinates[1:]))
    else:
        raise MarchingStop("INPUT_U", "coolant flow direction must be explicitly increasing or decreasing gas axial coordinate")
    if not valid:
        raise MarchingStop("INPUT_U", "coolant flow order is inconsistent with declared direction")
    if request.get("coolant_inlet_segment_id") != coolant_order[0]:
        raise MarchingStop("INPUT_U", "coolant inlet segment must be first in coolant flow order")
    if request.get("coolant_outlet_segment_id") != coolant_order[-1]:
        raise MarchingStop("INPUT_U", "coolant outlet segment must be last in coolant flow order")
    return by_id, list(coolant_order)


def _segment_request(
    request: dict[str, Any],
    segment: dict[str, Any],
    *,
    inlet_temperature_K: float,
    inlet_pressure_Pa_abs: float,
    inlet_enthalpy_J_kg: float | None,
    upstream_segment_id: str | None,
    cea_sha256: str | None,
) -> dict[str, Any]:
    mapping = segment["cea_station_mapping"]
    if mapping.get("method") != "EXPLICIT_NO_INTERPOLATION":
        raise MarchingStop(
            "INPUT_U",
            "each CEA station mapping must explicitly prohibit interpolation",
            segment_id=segment["segment_id"],
        )
    _source(mapping.get("source"), f"{segment['segment_id']} CEA station mapping")
    boundary = request["coolant_boundary"]
    if upstream_segment_id is None:
        temperature_source = boundary["inlet_temperature_source"]
        pressure_source = boundary["inlet_pressure_source"]
    else:
        temperature_source = f"propagated outlet temperature from {upstream_segment_id}"
        pressure_source = f"propagated outlet pressure from {upstream_segment_id}"
    result = {
        "purpose": request["purpose"],
        "run_id": f"{request['run_id']}:{segment['segment_id']}",
        "case_id": request["case_id"],
        "geometry_version": request["geometry_version"],
        "segment_id": segment["segment_id"],
        "spatial_basis": request["spatial_basis"],
        "model_scope": "STEADY_SINGLE_SEGMENT_GAS_WALL_COOLANT",
        "flow_boundary_mode": request["flow_boundary_mode"],
        "formula_version": request["formula_version"],
        "formula_source": request["formula_source"],
        "chamber_station_index": mapping["chamber_station_index"],
        "cea_station_index": mapping["cea_station_index"],
        "transport_basis": mapping["transport_basis"],
        "_cea_artifact_sha256": cea_sha256,
        "coolant_boundary": {
            "mass_flow_kg_s": boundary["mass_flow_kg_s"],
            "mass_flow_source": boundary["mass_flow_source"],
            "inlet_pressure_Pa_abs": inlet_pressure_Pa_abs,
            "inlet_pressure_source": pressure_source,
            "inlet_temperature_K": inlet_temperature_K,
            "inlet_temperature_source": temperature_source,
        },
        "coolant_property_model": request["coolant_property_model"],
        "single_phase_bulk_domain": request["single_phase_bulk_domain"],
        "coolant_heat_transfer_closure": request["coolant_heat_transfer_closure"],
        "coolant_friction_closure": request["coolant_friction_closure"],
        "geometry": segment["geometry"],
        "radiation": segment["radiation"],
        "gas_wall_numerics": segment["gas_wall_numerics"],
        "coupled_numerics": segment["coupled_numerics"],
    }
    if inlet_enthalpy_J_kg is not None:
        result["_propagated_inlet_enthalpy_J_kg"] = inlet_enthalpy_J_kg
    return result


def solve_coupled_multisegment(
    request: dict[str, Any],
    cea_records_by_segment: dict[str, dict[str, Any]],
    cea_hashes_by_segment: dict[str, str] | None = None,
) -> dict[str, Any]:
    """March one prescribed-flow coolant path through explicit GAS-WALL segments."""
    segment_results: list[dict[str, Any]] = []
    try:
        if request.get("purpose") != "NON_DESIGN_INTERFACE_VERIFICATION":
            raise MarchingStop("INPUT_U", "only explicitly non-design verification is enabled")
        if request.get("model_scope") != "STEADY_SINGLE_PATH_MULTISEGMENT_GAS_WALL_COOLANT":
            raise MarchingStop("MODEL_U", "only steady single-path multisegment marching is implemented")
        if request.get("flow_boundary_mode") != "PRESCRIBED_MASS_FLOW":
            raise MarchingStop("MODEL_U", "only prescribed mass flow is implemented")
        for name in (
            "run_id",
            "case_id",
            "geometry_version",
            "spatial_basis",
            "formula_source",
            "gas_axial_coordinate_reference",
        ):
            _source(request.get(name), name)
        by_id, coolant_order = _validate_orders(request)
        for segment_id in coolant_order:
            mapping = by_id[segment_id].get("cea_station_mapping")
            if not isinstance(mapping, dict):
                raise MarchingStop(
                    "INPUT_U", "CEA station mapping is required", segment_id=segment_id
                )
            if mapping.get("method") != "EXPLICIT_NO_INTERPOLATION":
                raise MarchingStop(
                    "INPUT_U",
                    "each CEA station mapping must explicitly prohibit interpolation",
                    segment_id=segment_id,
                )
            _source(mapping.get("source"), f"{segment_id} CEA station mapping")
            if segment_id not in cea_records_by_segment:
                raise MarchingStop(
                    "INPUT_U", "CEA artifact record is missing", segment_id=segment_id
                )

        boundary = request["coolant_boundary"]
        mass_flow = _positive(boundary["mass_flow_kg_s"], "mass flow")
        inlet_pressure = _positive(boundary["inlet_pressure_Pa_abs"], "inlet pressure")
        inlet_temperature = _positive(boundary["inlet_temperature_K"], "inlet temperature")
        for key, label in (
            ("mass_flow_source", "mass flow"),
            ("inlet_pressure_source", "inlet pressure"),
            ("inlet_temperature_source", "inlet temperature"),
        ):
            _source(boundary.get(key), label)
        table = build_m0_property_adapter(request["coolant_property_model"])
        initial_state = table.state_pt(inlet_pressure, inlet_temperature)

        numerics = request["marching_numerics"]
        total_energy_tolerance = _positive(
            numerics["total_energy_residual_tolerance_W"], "total energy tolerance"
        )
        enthalpy_continuity_tolerance = _positive(
            numerics["enthalpy_continuity_tolerance_J_kg"], "enthalpy continuity tolerance"
        )
        pressure_continuity_tolerance = _positive(
            numerics["pressure_continuity_tolerance_Pa"], "pressure continuity tolerance"
        )
        mass_tolerance = _positive(
            numerics["mass_residual_tolerance_kg_s"], "mass residual tolerance"
        )

        current_temperature = initial_state.temperature_K
        current_pressure = initial_state.pressure_Pa_abs
        current_enthalpy: float | None = None
        previous_outlet: dict[str, Any] | None = None
        previous_segment_id: str | None = None
        total_heat = 0.0
        total_pressure_drop = 0.0
        min_pressure = current_pressure
        continuity_checks: list[dict[str, Any]] = []

        for path_index, segment_id in enumerate(coolant_order):
            segment = by_id[segment_id]
            mapping = segment["cea_station_mapping"]
            segment_request = _segment_request(
                request,
                segment,
                inlet_temperature_K=current_temperature,
                inlet_pressure_Pa_abs=current_pressure,
                inlet_enthalpy_J_kg=current_enthalpy,
                upstream_segment_id=previous_segment_id,
                cea_sha256=(cea_hashes_by_segment or {}).get(segment_id),
            )
            result = solve_coupled_single_segment(
                segment_request, cea_records_by_segment[segment_id]
            )
            wrapper = {
                "segment_id": segment_id,
                "coolant_path_index": path_index,
                "gas_axial_coordinate_m": float(segment["gas_axial_coordinate_m"]),
                "gas_axial_coordinate_source": segment["gas_axial_coordinate_source"],
                "cea_station_mapping": copy.deepcopy(mapping),
                "status": result.get("status"),
                "execution_status": "EXECUTED",
                "upstream_segment_id": previous_segment_id,
                "solution": result,
            }
            segment_results.append(wrapper)
            if result.get("numerical_status") != "PASS":
                failure_status = str(result.get("status", "NUM_U"))
                for blocked_index, blocked_id in enumerate(
                    coolant_order[path_index + 1 :], start=path_index + 1
                ):
                    blocked = by_id[blocked_id]
                    segment_results.append(
                        {
                            "segment_id": blocked_id,
                            "coolant_path_index": blocked_index,
                            "gas_axial_coordinate_m": float(blocked["gas_axial_coordinate_m"]),
                            "gas_axial_coordinate_source": blocked["gas_axial_coordinate_source"],
                            "cea_station_mapping": copy.deepcopy(blocked.get("cea_station_mapping")),
                            "status": failure_status,
                            "execution_status": "BLOCKED_BY_UPSTREAM",
                            "upstream_failure_segment_id": segment_id,
                            "solution": None,
                        }
                    )
                return _failure(
                    request.get("run_id"),
                    failure_status,
                    f"segment {segment_id} failed: {result.get('reason', 'segment solve did not pass')}",
                    segments=segment_results,
                    stopped_at_segment=segment_id,
                    coolant_flow_direction=request["coolant_flow_direction"],
                    coolant_flow_order_segment_ids=coolant_order,
                )

            coolant = result["coolant_solution"]
            segment_inlet = coolant["inlet"]
            segment_outlet = coolant["outlet"]
            if previous_outlet is not None:
                continuity = {
                    "from_segment_id": previous_segment_id,
                    "to_segment_id": segment_id,
                    "enthalpy_residual_J_kg": segment_inlet["enthalpy_J_kg"]
                    - previous_outlet["enthalpy_J_kg"],
                    "pressure_residual_Pa": segment_inlet["pressure_Pa_abs"]
                    - previous_outlet["pressure_Pa_abs"],
                    "temperature_residual_K": segment_inlet["temperature_K"]
                    - previous_outlet["temperature_K"],
                }
                continuity_checks.append(continuity)
            wrapper["checks"] = {
                "mass_residual_kg_s": 0.0,
                "coolant_energy_residual_W": coolant["coolant_energy_residual_W"],
                "wall_energy_residual_max_W": result["checks"]["wall_energy_residual_max_W"],
            }
            total_heat += float(coolant["heat_rate_into_coolant_W"])
            segment_dp = float(segment_inlet["pressure_Pa_abs"]) - float(
                segment_outlet["pressure_Pa_abs"]
            )
            total_pressure_drop += segment_dp
            min_pressure = min(
                min_pressure,
                float(segment_inlet["pressure_Pa_abs"]),
                float(segment_outlet["pressure_Pa_abs"]),
            )
            current_temperature = float(segment_outlet["temperature_K"])
            current_pressure = float(segment_outlet["pressure_Pa_abs"])
            current_enthalpy = float(segment_outlet["enthalpy_J_kg"])
            previous_outlet = segment_outlet
            previous_segment_id = segment_id

        if previous_outlet is None:
            raise MarchingStop("INPUT_U", "no segment was executed")
        boundary_energy_rise = mass_flow * (
            float(previous_outlet["enthalpy_J_kg"]) - initial_state.enthalpy_J_kg
        )
        total_energy_residual = total_heat - boundary_energy_rise
        pressure_sum_residual = (
            initial_state.pressure_Pa_abs - float(previous_outlet["pressure_Pa_abs"])
        ) - total_pressure_drop
        max_enthalpy_continuity = max(
            (abs(item["enthalpy_residual_J_kg"]) for item in continuity_checks), default=0.0
        )
        max_pressure_continuity = max(
            (abs(item["pressure_residual_Pa"]) for item in continuity_checks), default=0.0
        )
        max_mass_residual = max(
            abs(item["checks"]["mass_residual_kg_s"]) for item in segment_results
        )
        max_segment_energy_residual = max(
            abs(item["checks"]["coolant_energy_residual_W"]) for item in segment_results
        )
        max_wall_energy_residual = max(
            abs(item["checks"]["wall_energy_residual_max_W"]) for item in segment_results
        )
        if (
            abs(total_energy_residual) > total_energy_tolerance
            or max_enthalpy_continuity > enthalpy_continuity_tolerance
            or max_pressure_continuity > pressure_continuity_tolerance
            or max_mass_residual > mass_tolerance
        ):
            return _failure(
                request.get("run_id"),
                "NUM_U",
                "multisegment conservation or continuity residual exceeds tolerance",
                segments=segment_results,
                stopped_at_segment=coolant_order[-1],
                total_energy_residual_W=total_energy_residual,
                max_enthalpy_continuity_residual_J_kg=max_enthalpy_continuity,
                max_pressure_continuity_residual_Pa=max_pressure_continuity,
                max_mass_residual_kg_s=max_mass_residual,
            )

        status = "MODEL_U"
        hottest = max(
            (
                float(item["solution"]["qoi"]["Y01"]["value"]),
                item["segment_id"],
            )
            for item in segment_results
        )
        return {
            "run_id": request["run_id"],
            "case_id": request["case_id"],
            "geometry_version": request["geometry_version"],
            "spatial_basis": request["spatial_basis"],
            "model_scope": request["model_scope"],
            "status": status,
            "numerical_status": "PASS",
            "coolant_flow_direction": request["coolant_flow_direction"],
            "gas_axial_order_segment_ids": request["gas_axial_order_segment_ids"],
            "coolant_flow_order_segment_ids": coolant_order,
            "segments": segment_results,
            "coolant_path_boundary": {
                "inlet": {
                    "segment_id": coolant_order[0],
                    "temperature_K": initial_state.temperature_K,
                    "pressure_Pa_abs": initial_state.pressure_Pa_abs,
                    "enthalpy_J_kg": initial_state.enthalpy_J_kg,
                    "mass_flow_kg_s": mass_flow,
                },
                "outlet": {
                    "segment_id": coolant_order[-1],
                    "temperature_K": previous_outlet["temperature_K"],
                    "pressure_Pa_abs": previous_outlet["pressure_Pa_abs"],
                    "enthalpy_J_kg": previous_outlet["enthalpy_J_kg"],
                    "mass_flow_kg_s": mass_flow,
                },
            },
            "qoi": {
                "Y01": {
                    "value": hottest[0],
                    "unit": "K",
                    "location": hottest[1],
                    "status": status,
                },
                "Y02": {
                    "value": initial_state.pressure_Pa_abs
                    - float(previous_outlet["pressure_Pa_abs"]),
                    "unit": "Pa",
                    "status": status,
                },
                "Y03": {"value": total_heat, "unit": "W", "status": status},
                "Y04": {"value": min_pressure, "unit": "Pa(a)", "status": status},
                "Y05": {
                    "value": previous_outlet["temperature_K"],
                    "unit": "K",
                    "status": status,
                },
            },
            "checks": {
                "segment_count": len(segment_results),
                "mass_residual_max_kg_s": max_mass_residual,
                "segment_coolant_energy_residual_max_W": max_segment_energy_residual,
                "segment_wall_energy_residual_max_W": max_wall_energy_residual,
                "total_heat_into_coolant_W": total_heat,
                "boundary_coolant_enthalpy_rise_W": boundary_energy_rise,
                "total_energy_residual_W": total_energy_residual,
                "pressure_drop_sum_Pa": total_pressure_drop,
                "pressure_drop_sum_residual_Pa": pressure_sum_residual,
                "enthalpy_continuity_residual_max_J_kg": max_enthalpy_continuity,
                "pressure_continuity_residual_max_Pa": max_pressure_continuity,
                "continuity": continuity_checks,
                "cea_station_mapping": "EXPLICIT_NO_INTERPOLATION_PER_SEGMENT",
            },
            "implemented_models": [
                "single-path axial marching in explicit coolant-flow order",
                "explicit increasing or decreasing gas-coordinate coolant direction",
                "segment outlet enthalpy and pressure propagation to downstream inlet",
                "per-segment explicit CEA station mapping without interpolation",
                "single-segment GAS-WALL-COOLANT solver reuse",
                "per-segment and whole-path conservation residuals",
                "upstream failure blocking and status propagation",
            ],
            "not_implemented": [
                "parallel-channel distribution or manifold network",
                "axial wall conduction",
                "CEA station interpolation",
                "wall boiling, phase change, or two-phase flow",
                "acceleration pressure drop",
                "uncertainty propagation, safety allowables, or engine optimization",
            ],
            "physical_validation_v01_v12": "NOT_RUN",
            "validated_engine_prediction": False,
        }
    except (MarchingStop, M0Stop) as error:
        status = error.status
        stopped = getattr(error, "segment_id", None)
        return _failure(
            request.get("run_id") if isinstance(request, dict) else None,
            status,
            str(error),
            segments=segment_results,
            stopped_at_segment=stopped,
        )
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _failure(
            request.get("run_id") if isinstance(request, dict) else None,
            "INPUT_U",
            f"invalid or missing input: {error}",
            segments=segment_results,
        )


def solve_coupled_multisegment_file(
    path: str | Path, schema_path: str | Path | None = None
) -> dict[str, Any]:
    request_path = Path(path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("multisegment M0 request must be a JSON object")
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
                    request.get("run_id"),
                    "INPUT_U",
                    "multisegment M0 request schema validation failed",
                    schema_errors=[
                        {
                            "path": ".".join(str(part) for part in error.absolute_path) or "$",
                            "message": error.message,
                        }
                        for error in errors
                    ],
                )
    records: dict[str, dict[str, Any]] = {}
    hashes: dict[str, str] = {}
    try:
        for segment in request["segments"]:
            segment_id = str(segment["segment_id"])
            mapping = segment["cea_station_mapping"]
            artifact_path = Path(mapping["cea_run_artifact"])
            if not artifact_path.is_absolute():
                artifact_path = (request_path.parent / artifact_path).resolve()
            records[segment_id] = json.loads(artifact_path.read_text(encoding="utf-8"))
            hashes[segment_id] = sha256_file(artifact_path)
    except (KeyError, OSError, json.JSONDecodeError) as error:
        return _failure(
            request.get("run_id"), "INPUT_U", f"cannot read mapped CEA artifact: {error}"
        )
    return solve_coupled_multisegment(request, records, hashes)
