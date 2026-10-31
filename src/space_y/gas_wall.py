from __future__ import annotations

from dataclasses import asdict
import json
import math
from pathlib import Path
from typing import Any

from .bartz import (
    BartzChamberReference,
    BartzStation,
    heat_transfer_coefficient_W_m2K,
    recovery_temperature_K,
    sigma,
)
from .external import sha256_file
from .gas import FORMULA_VERSION, RADIATION_DEFINITION
from .wall import SteadyWallResult, solve_steady_three_resistance_wall


class GasWallStop(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise GasWallStop("INPUT_U", f"{name} must be finite and positive")
    return number


def _source(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GasWallStop("INPUT_U", f"{name} source is required")
    return value


def _station_value(values: Any, index: int, name: str) -> float:
    if not isinstance(values, list) or index < 0 or index >= len(values):
        raise GasWallStop("OUTSIDE", f"CEA {name} station index is outside available data")
    value = float(values[index])
    if not math.isfinite(value):
        raise GasWallStop("OUTSIDE", f"CEA {name} is not finite")
    return value


def _failure(run_id: Any, status: str, reason: str, **diagnostics: Any) -> dict[str, Any]:
    result = {
        "run_id": run_id,
        "status": status,
        "numerical_status": status if status == "NUM_U" else "NOT_RUN",
        "reason": reason,
        "wall_solution": None,
        "coolant_interface": None,
        "physical_validation_v01_v12": "NOT_RUN",
        "validated_engine_prediction": False,
    }
    result.update(diagnostics)
    return result


def _max_energy_residual(result: SteadyWallResult) -> float:
    return max(
        abs(result.gas_balance_residual_W),
        abs(result.wall_balance_residual_W),
        abs(result.coolant_balance_residual_W),
    )


def solve_gas_wall(request: dict[str, Any], cea_record: dict[str, Any]) -> dict[str, Any]:
    """Solve one steady GAS–WALL segment with Bartz/wall-temperature iteration."""
    try:
        if request.get("purpose") != "NON_DESIGN_INTERFACE_VERIFICATION":
            raise GasWallStop("INPUT_U", "only explicitly non-design interface verification is enabled")
        if request.get("model_scope") != "STEADY_SINGLE_SEGMENT_GAS_WALL":
            raise GasWallStop("MODEL_U", "only STEADY_SINGLE_SEGMENT_GAS_WALL is implemented")
        if request.get("formula_version") != FORMULA_VERSION:
            raise GasWallStop("MODEL_U", f"only {FORMULA_VERSION} is implemented")
        _source(request.get("formula_source"), "formula")
        for name in ("run_id", "case_id", "geometry_version", "segment_id", "spatial_basis"):
            _source(request.get(name), name)

        basis = request.get("transport_basis")
        if basis not in {"frozen", "equilibrium"}:
            raise GasWallStop("INPUT_U", "transport_basis must be frozen or equilibrium")
        if cea_record.get("backend") != "NASA_CEA_PYTHON" or cea_record.get("status") != "PASS":
            raise GasWallStop("INPUT_U", "a successful NASA_CEA_PYTHON artifact is required")
        cea = cea_record.get("result")
        if not isinstance(cea, dict) or cea.get("mode") != "rocket" or not cea.get("converged"):
            raise GasWallStop("INPUT_U", "a converged CEA rocket result is required")
        checks = cea.get("reference_checks")
        if not isinstance(checks, list) or not checks or not all(item.get("pass") for item in checks):
            raise GasWallStop("INPUT_U", "all supplied CEA reference checks must pass")
        units = cea.get("station_units")
        transport = cea.get("transport")
        stations = cea.get("stations")
        if not isinstance(units, dict) or units.get("P") != "bar(a)":
            raise GasWallStop("INPUT_U", "CEA pressure unit provenance must be bar(a)")
        if units.get("T") != "K" or units.get("c_star") != "m/s":
            raise GasWallStop("INPUT_U", "unsupported CEA station unit provenance")
        if not isinstance(transport, dict) or basis not in transport.get("basis_available", []):
            raise GasWallStop("INPUT_U", "requested CEA transport basis is unavailable")
        if transport.get("original_units", {}).get("conductivity") != "mW/(cm K)":
            raise GasWallStop("INPUT_U", "CEA transport unit provenance is unsupported")
        si = transport.get("si")
        if not isinstance(stations, dict) or not isinstance(si, dict):
            raise GasWallStop("INPUT_U", "CEA station and SI transport arrays are required")

        chamber_index = int(request["chamber_station_index"])
        station_index = int(request["cea_station_index"])
        if chamber_index != 0:
            raise GasWallStop("INPUT_U", "this interface version requires chamber_station_index=0")
        if station_index == chamber_index:
            raise GasWallStop("INPUT_U", "a flow station, not the chamber station, is required")
        suffix = "fr" if basis == "frozen" else "eq"
        geometry = request["geometry"]
        for field, label in (
            ("throat_diameter_source", "throat diameter"),
            ("bartz_curvature_radius_source", "Bartz curvature radius"),
            ("gas_area_source", "gas area"),
            ("coolant_area_source", "coolant area"),
            ("wall_thickness_source", "wall thickness"),
            ("wall_conductivity_source", "wall conductivity"),
            ("wall_conduction_area_source", "wall conduction area"),
        ):
            _source(geometry.get(field), label)
        throat_diameter = _positive(geometry["throat_diameter_m"], "throat diameter")
        curvature_radius = _positive(
            geometry["bartz_curvature_radius_m"], "Bartz curvature radius"
        )
        gas_area = _positive(geometry["gas_area_m2"], "gas area")
        coolant_area = _positive(geometry["coolant_area_m2"], "coolant area")
        wall_thickness = _positive(geometry["wall_thickness_m"], "wall thickness")
        wall_conductivity = _positive(
            geometry["wall_conductivity_W_mK"], "wall conductivity"
        )
        conduction_area = _positive(geometry["wall_conduction_area_m2"], "wall conduction area")
        wall_resistance = wall_thickness / (wall_conductivity * conduction_area)

        radiation = request["radiation"]
        if radiation.get("definition") != RADIATION_DEFINITION:
            raise GasWallStop("INPUT_U", "explicit net-radiation definition is required")
        if "q_rad_net_W" not in radiation:
            raise GasWallStop("INPUT_U", "net radiation is required and has no default")
        q_rad = float(radiation["q_rad_net_W"])
        if not math.isfinite(q_rad):
            raise GasWallStop("INPUT_U", "net radiation must be finite")
        _source(radiation.get("source"), "net radiation")

        coolant = request["coolant_side_boundary"]
        if coolant.get("mode") != "PRESCRIBED_FILM_COEFFICIENT_AND_BULK_TEMPERATURE":
            raise GasWallStop("MODEL_U", "only a prescribed coolant-side Robin boundary is implemented")
        h_c = _positive(coolant["h_c_W_m2K"], "coolant heat-transfer coefficient")
        t_bulk = _positive(coolant["bulk_temperature_K"], "coolant bulk temperature")
        _source(coolant.get("h_c_source"), "coolant heat-transfer coefficient")
        _source(coolant.get("bulk_temperature_source"), "coolant bulk temperature")

        numerics = request["numerics"]
        max_iterations = int(numerics["max_iterations"])
        relaxation = float(numerics["relaxation"])
        wall_tolerance = _positive(
            numerics["wall_temperature_tolerance_K"], "wall-temperature tolerance"
        )
        energy_tolerance = _positive(
            numerics["energy_residual_tolerance_W"], "energy-residual tolerance"
        )
        wall_temperature = _positive(
            numerics["initial_gas_side_wall_temperature_K"], "initial wall temperature"
        )
        _source(numerics.get("initial_temperature_source"), "initial wall temperature")
        if max_iterations <= 0 or not 0 < relaxation <= 1:
            raise GasWallStop("INPUT_U", "invalid iteration count or relaxation")

        reference = BartzChamberReference(
            temperature_K=_positive(
                _station_value(stations.get("T"), chamber_index, "temperature"),
                "CEA chamber temperature",
            ),
            pressure_Pa_abs=_positive(
                _station_value(stations.get("P"), chamber_index, "pressure") * 1.0e5,
                "CEA chamber pressure",
            ),
            c_star_m_s=_positive(
                _station_value(stations.get("c_star"), chamber_index, "c_star"), "CEA c_star"
            ),
            viscosity_Pa_s=_positive(
                _station_value(si.get("viscosity_Pa_s"), chamber_index, "viscosity"),
                "CEA chamber viscosity",
            ),
            cp_J_kgK=_positive(
                _station_value(si.get(f"cp_{suffix}_J_kgK"), chamber_index, "cp"),
                "CEA chamber cp",
            ),
            Pr=_positive(
                _station_value(si.get(f"Pr_{suffix}"), chamber_index, "Pr"), "CEA chamber Pr"
            ),
            throat_diameter_m=throat_diameter,
            curvature_radius_m=curvature_radius,
        )
        station = BartzStation(
            Mach=_station_value(stations.get("Mach"), station_index, "Mach"),
            gamma=_station_value(stations.get("gamma_s"), station_index, "gamma_s"),
            area_ratio_A_At=_positive(
                _station_value(stations.get("ae_at"), station_index, "ae_at"),
                "CEA area ratio",
            ),
            Pr=_positive(
                _station_value(si.get(f"Pr_{suffix}"), station_index, "Pr"), "CEA local Pr"
            ),
        )
        if station.Mach < 0 or station.gamma <= 1:
            raise GasWallStop("OUTSIDE", "basic Bartz relation requires Mach >= 0 and gamma > 1")
        t_aw = recovery_temperature_K(reference, station)

        converged = False
        last_wall: SteadyWallResult | None = None
        last_sigma = math.nan
        last_h_g = math.nan
        last_fixed_point_residual = math.inf
        for iteration in range(1, max_iterations + 1):
            last_sigma = sigma(reference, station, wall_temperature)
            last_h_g = heat_transfer_coefficient_W_m2K(reference, station, wall_temperature)
            last_wall = solve_steady_three_resistance_wall(
                h_g_W_m2K=last_h_g,
                gas_area_m2=gas_area,
                adiabatic_wall_temperature_K=t_aw,
                q_rad_net_W=q_rad,
                wall_resistance_K_W=wall_resistance,
                h_c_W_m2K=h_c,
                coolant_area_m2=coolant_area,
                coolant_bulk_temperature_K=t_bulk,
            )
            last_fixed_point_residual = (
                last_wall.gas_side_wall_temperature_K - wall_temperature
            )
            if (
                abs(last_fixed_point_residual) <= wall_tolerance
                and _max_energy_residual(last_wall) <= energy_tolerance
            ):
                converged = True
                break
            wall_temperature += relaxation * last_fixed_point_residual
            if not math.isfinite(wall_temperature) or wall_temperature <= 0:
                raise GasWallStop("NUM_U", "wall-temperature iteration produced an invalid state")

        if not converged or last_wall is None:
            return _failure(
                request.get("run_id"),
                "NUM_U",
                "Bartz/wall-temperature fixed-point iteration did not converge",
                iterations=max_iterations,
                last_wall_temperature_update_K=last_fixed_point_residual,
                last_energy_residual_max_W=(
                    _max_energy_residual(last_wall) if last_wall is not None else None
                ),
            )

        wall_data = asdict(last_wall)
        return {
            "run_id": request["run_id"],
            "case_id": request["case_id"],
            "segment_id": request["segment_id"],
            "geometry_version": request["geometry_version"],
            "spatial_basis": request["spatial_basis"],
            "status": "MODEL_U",
            "numerical_status": "PASS",
            "formula_version": FORMULA_VERSION,
            "transport_basis": basis,
            "cea_station_index": station_index,
            "cea_artifact_sha256": request.get("_cea_artifact_sha256"),
            "iterations": iteration,
            "wall_temperature_update_residual_K": last_fixed_point_residual,
            "energy_residual_max_W": _max_energy_residual(last_wall),
            "bartz": {
                "wall_temperature_used_K": wall_temperature,
                "sigma": last_sigma,
                "h_g_W_m2K": last_h_g,
                "adiabatic_wall_temperature_K": t_aw,
                "chamber_reference": asdict(reference),
                "station": asdict(station),
            },
            "geometry": geometry,
            "radiation": radiation,
            "coolant_side_boundary": coolant,
            "wall_conduction": {
                "model": "STEADY_1D_CONSTANT_K_RESISTANCE",
                "resistance_K_W": wall_resistance,
            },
            "wall_solution": wall_data,
            "coolant_interface": {
                "interface_version": "SPACE_Y_WALL_COOLANT_V1",
                "run_id": request["run_id"],
                "case_id": request["case_id"],
                "segment_id": request["segment_id"],
                "spatial_basis": request["spatial_basis"],
                "mode": coolant["mode"],
                "heat_rate_sign_convention": "POSITIVE_WALL_TO_COOLANT",
                "coolant_wetted_area_m2": coolant_area,
                "bulk_temperature_K": t_bulk,
                "h_c_W_m2K": h_c,
                "wall_temperature_coolant_side_K": last_wall.coolant_side_wall_temperature_K,
                "heat_rate_into_coolant_W": last_wall.q_coolant_W,
                "units": {
                    "temperature": "K",
                    "heat_rate": "W",
                    "area": "m2",
                    "heat_transfer_coefficient": "W/(m2 K)"
                },
                "coolant_state_update": "NOT_RUN",
                "pressure_loss_update": "NOT_RUN",
                "applicability_status": "MODEL_U",
                "reason": "prescribed Robin boundary only; no coolant property or closure evaluation",
                "required_before_state_update": [
                    "I02 inlet absolute pressure and source",
                    "I03 inlet thermodynamic state and source",
                    "I04 path mass flow and boundary role",
                    "I05-I06 channel path and geometry",
                    "I09-I12 local flow geometry and area basis",
                    "I15 coolant property model and valid range",
                    "I18 coolant closure version and applicability domain",
                    "I19 supported local-loss inputs",
                    "I20 spatial basis",
                    "I21-I23 time/numerical/residual definitions"
                ]
            },
            "implemented_models": [
                "single-segment steady gas-film/wall/coolant-film resistance network",
                "Bartz sigma fixed-point iteration with computed gas-side wall temperature",
                "explicit net-radiation rate without a default",
                "independent gas, wall-conduction, and coolant energy residuals",
            ],
            "not_implemented": [
                "coolant enthalpy or pressure-loss state update",
                "coolant closure applicability evaluation",
                "temperature-dependent or multidimensional wall conduction",
                "multiple segments or axial conduction",
                "CEA station interpolation",
                "radiation model calculation",
                "uncertainty propagation or safety-allowable assessment",
            ],
            "physical_validation_v01_v12": "NOT_RUN",
            "validated_engine_prediction": False,
        }
    except GasWallStop as error:
        return _failure(request.get("run_id"), error.status, str(error))
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _failure(request.get("run_id"), "INPUT_U", f"invalid or missing input: {error}")


def solve_gas_wall_file(path: str | Path, schema_path: str | Path | None = None) -> dict[str, Any]:
    request_path = Path(path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("GAS-WALL request must be a JSON object")
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
                    "GAS-WALL request schema validation failed",
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
    return solve_gas_wall(request, cea_record)
