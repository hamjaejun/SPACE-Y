from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
from typing import Any

from .coolant import (
    CoolantGeometry,
    CoolantStop,
    build_friction_domain,
    build_heat_transfer_domain,
    build_single_phase_domain,
    check_single_phase_bulk_state,
    evaluate_local_coolant_closures,
)
from .external import sha256_file
from .gas import FORMULA_VERSION, RADIATION_DEFINITION
from .gas_wall import solve_gas_wall
from .m0 import M0Stop, build_m0_property_adapter
from .physics import coolant_outlet_enthalpy_J_kg


class CoupledStop(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise CoupledStop("INPUT_U", f"{name} must be finite and positive")
    return number


def _source(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoupledStop("INPUT_U", f"{name} source is required")
    return value


def _empty_qoi(status: str) -> dict[str, dict[str, Any]]:
    return {f"Y{index:02d}": {"value": None, "status": status} for index in range(1, 6)}


def _failure(run_id: Any, status: str, reason: str, **diagnostics: Any) -> dict[str, Any]:
    result = {
        "run_id": run_id,
        "status": status,
        "numerical_status": status if status == "NUM_U" else "NOT_RUN",
        "reason": reason,
        "gas_wall_solution": None,
        "coolant_solution": None,
        "qoi": _empty_qoi(status),
        "physical_validation_v01_v12": "NOT_RUN",
        "validated_engine_prediction": False,
    }
    result.update(diagnostics)
    return result


def _gas_wall_request(
    request: dict[str, Any],
    *,
    h_c_W_m2K: float,
    coolant_bulk_temperature_K: float,
    closure_source: str,
) -> dict[str, Any]:
    geometry = request["geometry"]
    return {
        "purpose": "NON_DESIGN_INTERFACE_VERIFICATION",
        "run_id": request["run_id"],
        "case_id": request["case_id"],
        "geometry_version": request["geometry_version"],
        "segment_id": request["segment_id"],
        "spatial_basis": request["spatial_basis"],
        "model_scope": "STEADY_SINGLE_SEGMENT_GAS_WALL",
        "formula_version": request["formula_version"],
        "formula_source": request["formula_source"],
        "chamber_station_index": request["chamber_station_index"],
        "cea_station_index": request["cea_station_index"],
        "transport_basis": request["transport_basis"],
        "_cea_artifact_sha256": request.get("_cea_artifact_sha256"),
        "geometry": {
            key: geometry[key]
            for key in (
                "throat_diameter_m",
                "throat_diameter_source",
                "bartz_curvature_radius_m",
                "bartz_curvature_radius_source",
                "gas_area_m2",
                "gas_area_source",
                "coolant_area_m2",
                "coolant_area_source",
                "wall_thickness_m",
                "wall_thickness_source",
                "wall_conductivity_W_mK",
                "wall_conductivity_source",
                "wall_conduction_area_m2",
                "wall_conduction_area_source",
            )
        },
        "radiation": request["radiation"],
        "coolant_side_boundary": {
            "mode": "PRESCRIBED_FILM_COEFFICIENT_AND_BULK_TEMPERATURE",
            "h_c_W_m2K": h_c_W_m2K,
            "h_c_source": f"computed from supplied coolant closure: {closure_source}",
            "bulk_temperature_K": coolant_bulk_temperature_K,
            "bulk_temperature_source": "current segment mean bulk state in coupled iteration",
        },
        "numerics": request["gas_wall_numerics"],
    }


def solve_coupled_single_segment(
    request: dict[str, Any], cea_record: dict[str, Any]
) -> dict[str, Any]:
    """Couple one Bartz GAS-WALL segment to bounded single-phase coolant marching."""
    try:
        if request.get("purpose") != "NON_DESIGN_INTERFACE_VERIFICATION":
            raise CoupledStop("INPUT_U", "only explicitly non-design verification is enabled")
        if request.get("model_scope") != "STEADY_SINGLE_SEGMENT_GAS_WALL_COOLANT":
            raise CoupledStop("MODEL_U", "only the single-segment coupled scope is implemented")
        if request.get("flow_boundary_mode") != "PRESCRIBED_MASS_FLOW":
            raise CoupledStop("MODEL_U", "only prescribed mass flow is implemented")
        if request.get("formula_version") != FORMULA_VERSION:
            raise CoupledStop("MODEL_U", f"only {FORMULA_VERSION} is implemented")
        for name in (
            "run_id",
            "case_id",
            "geometry_version",
            "segment_id",
            "spatial_basis",
            "formula_source",
        ):
            _source(request.get(name), name)

        table = build_m0_property_adapter(request["coolant_property_model"])
        phase_domain = build_single_phase_domain(request["single_phase_bulk_domain"])
        heat_spec = request["coolant_heat_transfer_closure"]
        friction_spec = request["coolant_friction_closure"]
        heat_domain = build_heat_transfer_domain(heat_spec)
        friction_domain = build_friction_domain(friction_spec)
        heat_boundary_condition = _source(
            heat_spec.get("thermal_boundary_condition"), "heat-transfer thermal boundary condition"
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
        inlet = table.state_pt(inlet_pressure, inlet_temperature)
        propagated_enthalpy = request.get("_propagated_inlet_enthalpy_J_kg")
        if propagated_enthalpy is not None:
            propagated_state = table.state_ph(inlet_pressure, float(propagated_enthalpy))
            if not math.isclose(
                propagated_state.temperature_K,
                inlet_temperature,
                rel_tol=0.0,
                abs_tol=1.0e-9,
            ):
                raise CoupledStop(
                    "NUM_U",
                    "propagated inlet enthalpy is inconsistent with propagated inlet temperature",
                )
            inlet = propagated_state
        check_single_phase_bulk_state(phase_domain, inlet.temperature_K, inlet.pressure_Pa_abs)

        geometry_spec = request["geometry"]
        for key, label in (
            ("flow_geometry_source", "flow geometry"),
            ("equivalent_sand_roughness_source", "equivalent sand roughness"),
            ("local_loss_source", "local loss"),
        ):
            _source(geometry_spec.get(key), label)
        coolant_geometry = CoolantGeometry(
            length_m=_positive(geometry_spec["length_m"], "segment length"),
            flow_area_m2=_positive(geometry_spec["flow_area_m2"], "flow area"),
            hydraulic_diameter_m=_positive(
                geometry_spec["hydraulic_diameter_m"], "hydraulic diameter"
            ),
            coolant_wetted_area_m2=_positive(
                geometry_spec["coolant_area_m2"], "coolant wetted area"
            ),
            equivalent_sand_roughness_m=float(
                geometry_spec["equivalent_sand_roughness_m"]
            ),
            local_loss_coefficient=float(geometry_spec["local_loss_coefficient"]),
            closure_geometry=str(geometry_spec["closure_geometry"]),
            fully_developed=bool(geometry_spec["fully_developed"]),
            thermal_boundary_condition=str(geometry_spec["thermal_boundary_condition"]),
        )

        radiation = request["radiation"]
        if radiation.get("definition") != RADIATION_DEFINITION:
            raise CoupledStop("INPUT_U", "explicit net-radiation definition is required")
        if "q_rad_net_W" not in radiation:
            raise CoupledStop("INPUT_U", "net radiation is required and has no default")
        _source(radiation.get("source"), "net radiation")

        numerics = request["coupled_numerics"]
        max_iterations = int(numerics["max_iterations"])
        relaxation = float(numerics["relaxation"])
        temperature_tolerance = _positive(
            numerics["outlet_temperature_tolerance_K"], "outlet-temperature tolerance"
        )
        pressure_tolerance = _positive(
            numerics["outlet_pressure_tolerance_Pa"], "outlet-pressure tolerance"
        )
        energy_tolerance = _positive(
            numerics["energy_residual_tolerance_W"], "energy-residual tolerance"
        )
        outlet_temperature = _positive(
            numerics["initial_outlet_temperature_K"], "initial outlet temperature"
        )
        outlet_pressure = _positive(
            numerics["initial_outlet_pressure_Pa_abs"], "initial outlet pressure"
        )
        _source(numerics.get("initial_state_source"), "initial outlet iterate")
        if max_iterations <= 0 or not 0 < relaxation <= 1:
            raise CoupledStop("INPUT_U", "invalid coupled iteration count or relaxation")

        last_gas_wall: dict[str, Any] | None = None
        last_local = None
        last_outlet = None
        last_temperature_update = math.inf
        last_pressure_update = math.inf
        last_energy_residual = math.inf
        converged = False
        for iteration in range(1, max_iterations + 1):
            mean_temperature = 0.5 * (inlet_temperature + outlet_temperature)
            mean_pressure = 0.5 * (inlet_pressure + outlet_pressure)
            check_single_phase_bulk_state(phase_domain, mean_temperature, mean_pressure)
            mean_state = table.state_pt(mean_pressure, mean_temperature)
            last_local = evaluate_local_coolant_closures(
                state=mean_state,
                mass_flow_kg_s=mass_flow,
                geometry=coolant_geometry,
                heat_transfer_domain=heat_domain,
                heat_transfer_thermal_boundary_condition=heat_boundary_condition,
                friction_domain=friction_domain,
            )
            gas_wall_request = _gas_wall_request(
                request,
                h_c_W_m2K=last_local.h_c_W_m2K,
                coolant_bulk_temperature_K=mean_temperature,
                closure_source=heat_domain.source,
            )
            last_gas_wall = solve_gas_wall(gas_wall_request, cea_record)
            if last_gas_wall.get("numerical_status") != "PASS":
                raise CoupledStop(
                    str(last_gas_wall.get("status", "NUM_U")),
                    f"GAS-WALL inner solve failed: {last_gas_wall.get('reason')}",
                )

            q_coolant = float(last_gas_wall["wall_solution"]["q_coolant_W"])
            outlet_enthalpy = coolant_outlet_enthalpy_J_kg(
                inlet.enthalpy_J_kg, q_coolant, mass_flow
            )
            candidate_pressure = inlet_pressure - last_local.pressure_drop_Pa
            if candidate_pressure <= 0:
                raise CoupledStop("OUTSIDE", "computed coolant outlet pressure is non-positive")
            last_outlet = table.state_ph(candidate_pressure, outlet_enthalpy)
            check_single_phase_bulk_state(
                phase_domain, last_outlet.temperature_K, last_outlet.pressure_Pa_abs
            )
            last_temperature_update = last_outlet.temperature_K - outlet_temperature
            last_pressure_update = last_outlet.pressure_Pa_abs - outlet_pressure
            last_energy_residual = q_coolant - mass_flow * (
                last_outlet.enthalpy_J_kg - inlet.enthalpy_J_kg
            )
            wall_energy_residual = float(last_gas_wall["energy_residual_max_W"])
            if (
                abs(last_temperature_update) <= temperature_tolerance
                and abs(last_pressure_update) <= pressure_tolerance
                and abs(last_energy_residual) <= energy_tolerance
                and wall_energy_residual <= energy_tolerance
            ):
                converged = True
                break
            outlet_temperature += relaxation * last_temperature_update
            outlet_pressure += relaxation * last_pressure_update
            if not math.isfinite(outlet_temperature) or not math.isfinite(outlet_pressure):
                raise CoupledStop("NUM_U", "coupled iteration produced a non-finite state")

        if not converged or last_gas_wall is None or last_local is None or last_outlet is None:
            return _failure(
                request.get("run_id"),
                "NUM_U",
                "GAS-WALL-COOLANT iteration did not converge",
                iterations=max_iterations,
                last_outlet_temperature_update_K=last_temperature_update,
                last_outlet_pressure_update_Pa=last_pressure_update,
                last_energy_residual_W=last_energy_residual,
            )

        q_total = float(last_gas_wall["wall_solution"]["q_total_W"])
        max_wall_temperature = max(
            float(last_gas_wall["wall_solution"]["gas_side_wall_temperature_K"]),
            float(last_gas_wall["wall_solution"]["coolant_side_wall_temperature_K"]),
        )
        status = "MODEL_U"
        return {
            "run_id": request["run_id"],
            "case_id": request["case_id"],
            "segment_id": request["segment_id"],
            "geometry_version": request["geometry_version"],
            "spatial_basis": request["spatial_basis"],
            "status": status,
            "numerical_status": "PASS",
            "iterations": iteration,
            "cea_artifact_sha256": request.get("_cea_artifact_sha256"),
            "interface_consumption": {
                "interface_version": "SPACE_Y_WALL_COOLANT_V1",
                "status": "CONSUMED_BY_OUTER_COUPLED_COOLANT_SOLVER",
                "note": (
                    "the nested GAS-WALL record retains its local NOT_RUN marker; "
                    "the outer solver performs the coolant state and pressure update"
                ),
            },
            "gas_wall_solution": last_gas_wall,
            "coolant_solution": {
                "inlet": asdict(inlet),
                "mean_evaluation_state": asdict(mean_state),
                "outlet": asdict(last_outlet),
                "local_closures": asdict(last_local),
                "mass_flow_kg_s": mass_flow,
                "heat_rate_into_coolant_W": q_total,
                "coolant_energy_residual_W": last_energy_residual,
                "outlet_temperature_update_residual_K": last_temperature_update,
                "outlet_pressure_update_residual_Pa": last_pressure_update,
                "bulk_single_phase_status": "APPLICABLE_TO_SUPPLIED_DOMAIN",
                "bulk_single_phase_domain": asdict(phase_domain),
                "wall_phase_change_status": "NOT_EVALUATED",
                "wall_phase_change_reason": (
                    "no saturation, ONB, CHF, dryout, or two-phase closure was supplied"
                ),
                "status": status,
            },
            "qoi": {
                "Y01": {"value": max_wall_temperature, "unit": "K", "status": status},
                "Y02": {
                    "value": inlet_pressure - last_outlet.pressure_Pa_abs,
                    "unit": "Pa",
                    "status": status,
                },
                "Y03": {"value": q_total, "unit": "W", "status": status},
                "Y04": {
                    "value": min(inlet_pressure, last_outlet.pressure_Pa_abs),
                    "unit": "Pa(a)",
                    "status": status,
                },
                "Y05": {"value": last_outlet.temperature_K, "unit": "K", "status": status},
            },
            "checks": {
                "coupled_temperature_update_K": last_temperature_update,
                "coupled_pressure_update_Pa": last_pressure_update,
                "coolant_energy_residual_W": last_energy_residual,
                "wall_energy_residual_max_W": last_gas_wall["energy_residual_max_W"],
                "heat_transfer_closure": "APPLICABLE_TO_SUPPLIED_DOMAIN",
                "friction_closure": "APPLICABLE_TO_SUPPLIED_DOMAIN",
                "bulk_single_phase": "APPLICABLE_TO_SUPPLIED_DOMAIN",
                "wall_phase_change": "NOT_EVALUATED",
            },
            "implemented_models": [
                "single-segment nested GAS-WALL and coolant-state fixed-point iteration",
                "bounded temperature-tabulated variable coolant properties without extrapolation",
                "conditional supplied-domain Gnielinski heat transfer",
                "conditional supplied-domain Colebrook Darcy friction",
                "Darcy-Weisbach friction and explicit local loss",
                "coolant enthalpy energy balance and pressure update",
            ],
            "not_implemented": [
                "wall boiling, ONB, CHF, dryout, or two-phase flow",
                "pressure-dependent coolant properties within the supplied table",
                "acceleration pressure drop",
                "multiple segments, parallel distribution, or axial conduction",
                "CEA station interpolation",
                "uncertainty propagation or safety-allowable assessment",
            ],
            "physical_validation_v01_v12": "NOT_RUN",
            "validated_engine_prediction": False,
        }
    except (CoupledStop, CoolantStop, M0Stop) as error:
        return _failure(request.get("run_id"), error.status, str(error))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _failure(request.get("run_id"), "INPUT_U", f"invalid or missing input: {error}")


def solve_coupled_single_segment_file(
    path: str | Path, schema_path: str | Path | None = None
) -> dict[str, Any]:
    request_path = Path(path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("coupled M0 request must be a JSON object")
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
                    "coupled M0 request schema validation failed",
                    schema_errors=[
                        {
                            "path": ".".join(str(part) for part in error.absolute_path) or "$",
                            "message": error.message,
                        }
                        for error in errors
                    ],
                )
    try:
        artifact_path = Path(request["cea_run_artifact"])
        if not artifact_path.is_absolute():
            artifact_path = (request_path.parent / artifact_path).resolve()
        cea_record = json.loads(artifact_path.read_text(encoding="utf-8"))
    except (KeyError, OSError, json.JSONDecodeError) as error:
        return _failure(request.get("run_id"), "INPUT_U", f"cannot read CEA artifact: {error}")
    request["_cea_artifact_sha256"] = sha256_file(artifact_path)
    return solve_coupled_single_segment(request, cea_record)
