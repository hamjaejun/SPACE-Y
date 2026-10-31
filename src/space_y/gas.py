from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from .bartz import (
    BartzChamberReference,
    BartzStation,
    heat_transfer_coefficient_W_m2K,
    recovery_temperature_K,
    sigma as bartz_sigma,
)
from .external import sha256_file


FORMULA_VERSION = "RPA_2012_BASIC_BARTZ"
RADIATION_DEFINITION = "EXPLICIT_NET_RATE_GAS_TO_WALL_POSITIVE"


class GasMapStop(RuntimeError):
    def __init__(self, status: str, message: str, *, segment_id: str | None = None):
        super().__init__(message)
        self.status = status
        self.segment_id = segment_id


def _positive(value: Any, name: str, segment_id: str | None = None) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise GasMapStop("INPUT_U", f"{name} must be finite and positive", segment_id=segment_id)
    return number


def _source(value: Any, name: str, segment_id: str | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GasMapStop("INPUT_U", f"{name} source is required", segment_id=segment_id)
    return value


def _array_value(values: Any, index: int, name: str, segment_id: str | None = None) -> float:
    if not isinstance(values, list) or index < 0 or index >= len(values):
        raise GasMapStop("OUTSIDE", f"CEA {name} station index is outside available data", segment_id=segment_id)
    value = float(values[index])
    if not math.isfinite(value):
        raise GasMapStop("OUTSIDE", f"CEA {name} is not finite", segment_id=segment_id)
    return value


def _failure(run_id: Any, error: GasMapStop) -> dict[str, Any]:
    return {
        "run_id": run_id,
        "status": error.status,
        "stopped_at_segment": error.segment_id,
        "reason": str(error),
        "gas_boundaries": [],
        "physical_validation_v01_v12": "NOT_RUN",
        "validated_engine_prediction": False,
    }


def solve_gas_map(request: dict[str, Any], cea_record: dict[str, Any]) -> dict[str, Any]:
    """Map an explicit CEA station selection through the RPA 2012 basic Bartz form.

    This is a one-way interface calculation. Because sigma depends on prescribed wall
    temperature, it is not yet the coupled Bartz/M0 wall-temperature iteration.
    """
    try:
        if request.get("purpose") != "NON_DESIGN_INTERFACE_VERIFICATION":
            raise GasMapStop("INPUT_U", "only an explicitly non-design interface verification is enabled")
        if request.get("formula_version") != FORMULA_VERSION:
            raise GasMapStop("MODEL_U", f"only {FORMULA_VERSION} is implemented")
        _source(request.get("formula_source"), "formula")
        basis = request.get("transport_basis")
        if basis not in {"frozen", "equilibrium"}:
            raise GasMapStop("INPUT_U", "transport_basis must be frozen or equilibrium")

        if cea_record.get("backend") != "NASA_CEA_PYTHON" or cea_record.get("status") != "PASS":
            raise GasMapStop("INPUT_U", "CEA artifact must be a successful NASA_CEA_PYTHON run record")
        result = cea_record.get("result")
        if not isinstance(result, dict) or result.get("mode") != "rocket" or not result.get("converged"):
            raise GasMapStop("INPUT_U", "CEA artifact must contain a converged rocket result")
        reference_checks = result.get("reference_checks")
        if not isinstance(reference_checks, list) or not reference_checks or not all(
            item.get("pass") for item in reference_checks
        ):
            raise GasMapStop("INPUT_U", "all supplied CEA reference checks must pass")
        stations = result.get("stations")
        station_units = result.get("station_units")
        transport = result.get("transport")
        if not isinstance(stations, dict) or not isinstance(transport, dict):
            raise GasMapStop("INPUT_U", "CEA station and transport output are required")
        if not isinstance(station_units, dict) or station_units.get("P") != "bar(a)":
            raise GasMapStop("INPUT_U", "CEA station pressure unit provenance is missing or unsupported")
        if station_units.get("T") != "K" or station_units.get("c_star") != "m/s":
            raise GasMapStop("INPUT_U", "CEA station temperature/c_star unit provenance is unsupported")
        if basis not in transport.get("basis_available", []):
            raise GasMapStop("INPUT_U", "requested CEA transport basis is not available")
        if transport.get("original_units", {}).get("conductivity") != "mW/(cm K)":
            raise GasMapStop("INPUT_U", "CEA conductivity unit provenance is missing or unsupported")
        si = transport.get("si")
        if not isinstance(si, dict):
            raise GasMapStop("INPUT_U", "CEA SI transport output is required")

        geometry = request["geometry"]
        throat_diameter = _positive(geometry["throat_diameter_m"], "throat diameter")
        _source(geometry.get("throat_diameter_source"), "throat diameter")
        curvature_radius = _positive(geometry["bartz_curvature_radius_m"], "Bartz curvature radius")
        _source(geometry.get("bartz_curvature_radius_source"), "Bartz curvature radius")

        chamber_index = int(request.get("chamber_station_index", 0))
        if chamber_index != 0:
            raise GasMapStop("INPUT_U", "this interface version requires explicit chamber_station_index=0")
        chamber_temperature = _positive(
            _array_value(stations.get("T"), chamber_index, "temperature"), "CEA chamber temperature"
        )
        chamber_pressure = _positive(
            _array_value(stations.get("P"), chamber_index, "pressure") * 1.0e5,
            "CEA chamber pressure",
        )
        c_star = _positive(_array_value(stations.get("c_star"), chamber_index, "c_star"), "CEA c_star")
        chamber_viscosity = _positive(
            _array_value(si.get("viscosity_Pa_s"), chamber_index, "viscosity"), "CEA chamber viscosity"
        )
        suffix = "fr" if basis == "frozen" else "eq"
        chamber_cp = _positive(
            _array_value(si.get(f"cp_{suffix}_J_kgK"), chamber_index, "cp"), "CEA chamber cp"
        )
        chamber_pr = _positive(
            _array_value(si.get(f"Pr_{suffix}"), chamber_index, "Pr"), "CEA chamber Pr"
        )
        bartz_reference = BartzChamberReference(
            temperature_K=chamber_temperature,
            pressure_Pa_abs=chamber_pressure,
            c_star_m_s=c_star,
            viscosity_Pa_s=chamber_viscosity,
            cp_J_kgK=chamber_cp,
            Pr=chamber_pr,
            throat_diameter_m=throat_diameter,
            curvature_radius_m=curvature_radius,
        )

        segments = request.get("segments")
        if not isinstance(segments, list) or not segments:
            raise GasMapStop("INPUT_U", "at least one explicit segment mapping is required")
        seen: set[str] = set()
        mapped: list[dict[str, Any]] = []
        for segment in segments:
            segment_id = str(segment.get("id", ""))
            if not segment_id or segment_id in seen:
                raise GasMapStop("INPUT_U", "segment IDs must be present and unique", segment_id=segment_id or None)
            seen.add(segment_id)
            station_index = int(segment["cea_station_index"])
            if station_index == chamber_index:
                raise GasMapStop("INPUT_U", "a flow station, not the chamber station, is required", segment_id=segment_id)
            wall_temperature = _positive(segment["wall_temperature_K"], "wall temperature", segment_id)
            _source(segment.get("wall_temperature_source"), "wall temperature", segment_id)
            heated_area = _positive(segment["gas_heated_area_m2"], "gas heated area", segment_id)
            _source(segment.get("gas_heated_area_source"), "gas heated area", segment_id)
            if segment.get("radiation_definition") != RADIATION_DEFINITION:
                raise GasMapStop("INPUT_U", "explicit net-radiation definition is required", segment_id=segment_id)
            q_rad = float(segment["q_rad_net_W"])
            if not math.isfinite(q_rad):
                raise GasMapStop("INPUT_U", "net radiation must be finite", segment_id=segment_id)
            _source(segment.get("q_rad_source"), "net radiation", segment_id)

            mach = _array_value(stations.get("Mach"), station_index, "Mach", segment_id)
            gamma = _array_value(stations.get("gamma_s"), station_index, "gamma_s", segment_id)
            area_ratio = _positive(
                _array_value(stations.get("ae_at"), station_index, "ae_at", segment_id),
                "CEA area ratio",
                segment_id,
            )
            local_pr = _positive(
                _array_value(si.get(f"Pr_{suffix}"), station_index, "Pr", segment_id),
                "CEA local Pr",
                segment_id,
            )
            if mach < 0 or gamma <= 1.0:
                raise GasMapStop("OUTSIDE", "basic Bartz recovery relation requires Mach >= 0 and gamma > 1", segment_id=segment_id)
            bartz_station = BartzStation(
                Mach=mach,
                gamma=gamma,
                area_ratio_A_At=area_ratio,
                Pr=local_pr,
            )
            recovery_temperature = recovery_temperature_K(bartz_reference, bartz_station)
            sigma = bartz_sigma(bartz_reference, bartz_station, wall_temperature)
            h_g = heat_transfer_coefficient_W_m2K(
                bartz_reference, bartz_station, wall_temperature
            )
            q_conv = h_g * heated_area * (recovery_temperature - wall_temperature)
            mapped.append(
                {
                    "segment_id": segment_id,
                    "cea_station_index": station_index,
                    "cea_station": {
                        "Mach": mach,
                        "gamma_s": gamma,
                        "Ae_At": area_ratio,
                        "Pr": local_pr,
                    },
                    "prescribed_wall_temperature_K": wall_temperature,
                    "sigma": sigma,
                    "h_g_W_m2K": h_g,
                    "adiabatic_wall_temperature_K": recovery_temperature,
                    "q_conv_at_prescribed_wall_temperature_W": q_conv,
                    "q_rad_net_W": q_rad,
                    "q_total_at_prescribed_wall_temperature_W": q_conv + q_rad,
                    "gas_boundary": {
                        "h_g_W_m2K": h_g,
                        "adiabatic_wall_temperature_K": recovery_temperature,
                        "radiation_definition": RADIATION_DEFINITION,
                        "q_rad_net_W": q_rad,
                    },
                    "status": "MODEL_U",
                }
            )

        return {
            "run_id": request.get("run_id"),
            "status": "MODEL_U",
            "formula_version": FORMULA_VERSION,
            "transport_basis": basis,
            "cea_artifact_sha256": request.get("_cea_artifact_sha256"),
            "chamber_reference": {
                "station_index": chamber_index,
                "T0_K": chamber_temperature,
                "p0_Pa_abs": chamber_pressure,
                "c_star_m_s": c_star,
                "viscosity_Pa_s": chamber_viscosity,
                "cp_J_kgK": chamber_cp,
                "Pr": chamber_pr,
            },
            "geometry": geometry,
            "gas_boundaries": mapped,
            "implemented_models": [
                "explicit CEA rocket-station mapping without spatial interpolation",
                "CEA transport conversion to SI with unit provenance",
                "RPA 2012 basic Bartz heat-transfer coefficient and sigma correction",
                "Prandtl recovery-temperature relation",
                "explicit net-radiation pass-through",
            ],
            "not_implemented": [
                "coupled iteration of Bartz sigma with M0-computed wall temperature",
                "station interpolation from nozzle contour coordinates",
                "original Bartz correlation validation against reference data",
                "radiation model calculation",
                "uncertainty propagation and safety-allowable assessment",
            ],
            "physical_validation_v01_v12": "NOT_RUN",
            "validated_engine_prediction": False,
        }
    except GasMapStop as error:
        return _failure(request.get("run_id"), error)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _failure(
            request.get("run_id") if isinstance(request, dict) else None,
            GasMapStop("INPUT_U", f"invalid or missing input: {error}"),
        )


def solve_gas_map_file(path: str | Path, schema_path: str | Path | None = None) -> dict[str, Any]:
    request_path = Path(path).resolve()
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if not isinstance(request, dict):
        raise ValueError("GAS mapping request must be a JSON object")
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
                return {
                    "run_id": request.get("run_id"),
                    "status": "INPUT_U",
                    "reason": "GAS mapping request schema validation failed",
                    "schema_errors": [
                        {
                            "path": ".".join(str(part) for part in error.absolute_path) or "$",
                            "message": error.message,
                        }
                        for error in errors
                    ],
                    "gas_boundaries": [],
                    "physical_validation_v01_v12": "NOT_RUN",
                    "validated_engine_prediction": False,
                }
    artifact_path = Path(request["cea_run_artifact"])
    if not artifact_path.is_absolute():
        artifact_path = (request_path.parent / artifact_path).resolve()
    cea_record = json.loads(artifact_path.read_text(encoding="utf-8"))
    request["_cea_artifact_sha256"] = sha256_file(artifact_path)
    return solve_gas_map(request, cea_record)
