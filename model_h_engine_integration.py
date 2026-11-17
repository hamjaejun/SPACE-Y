"""Strict bridge from supplied engine station snapshots to Model H V1.

The bridge does not create an engine condition or an IPA property model.  It
accepts station temperatures and pressures produced by an external engine
calculation, applies the documented range and phase gates of the supplied
``ipa_props.py`` correlations, and calls the existing Model H/Model B code.

The supplied IPA correlations are temperature-only saturated-liquid fits.
Pressure effects on ``rho``, ``mu``, ``cp`` and ``k`` are therefore not
modelled.  A successful call is ``MODEL_U`` and never experimental validation.
"""

from __future__ import annotations

import math
import statistics
from contextlib import contextmanager
from typing import Any, Protocol, Sequence

from cooling_model_b import (
    CoolingModelBError,
    LAMINAR_MODEL_THERMAL_ENTRY,
    evaluate_model_b_hl,
)
from model_h_v1 import (
    MODEL_H_HELIX_ANGLES_DEG,
    evaluate_model_h_hl,
    helical_coolant_path_distances,
)


IPA_FLUID_NAME = "2-propanol (IPA)"
IPA_CAS = "67-63-0"
IPA_TRANSPORT_OVERLAP_K = (187.35, 408.0)
STOP_STATUSES = {"INPUT_U", "OUTSIDE", "NUM_U"}
PROPERTY_POLICY_STRICT = "STRICT_IN_RANGE"
PROPERTY_POLICY_ALLOW_EXTRAPOLATION = "ALLOW_SUPPLIED_EXTRAPOLATION"


class IPAPropertyModule(Protocol):
    T_CRIT: float

    def rho(self, temperature_K: float) -> Any: ...
    def mu(self, temperature_K: float) -> Any: ...
    def cp(self, temperature_K: float) -> Any: ...
    def k(self, temperature_K: float) -> Any: ...
    def T_sat(self, pressure_Pa: float) -> float | None: ...


class _MathProxy:
    """Forward ``math`` except for the coolant-path segment length."""

    def __init__(self, original: Any, path_length_factor: float) -> None:
        self._original = original
        self._path_length_factor = path_length_factor

    def __getattr__(self, name: str) -> Any:
        return getattr(self._original, name)

    def hypot(self, x: float, y: float) -> float:
        return self._original.hypot(x, y) * self._path_length_factor


@contextmanager
def existing_loop_segment_length_factor(
    cooling_loop_module: Any, path_length_factor: float
):
    """Apply helical length once inside the unchanged existing cooling loop.

    ``cooling_loop_V1.march`` obtains every coolant segment length from its one
    ``math.hypot(dx, dr)`` call.  Replacing only the module's math reference
    keeps its wall iteration, fin efficiency, heat-resistance network and
    coolant energy equation unchanged.
    """

    factor = _finite(path_length_factor, "path length factor")
    if factor <= 0.0:
        raise ValueError("path length factor must be positive")
    original = cooling_loop_module.math
    cooling_loop_module.math = _MathProxy(original, factor)
    try:
        yield
    finally:
        cooling_loop_module.math = original


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(number):
        raise ValueError(f"{name} must be finite")
    return number


def evaluate_supplied_ipa_state(
    *,
    temperature_K: float | None,
    pressure_Pa_abs: float | None,
    ipa: IPAPropertyModule,
    policy: str = PROPERTY_POLICY_STRICT,
) -> dict[str, Any]:
    """Evaluate one supplied IPA state with explicit range/extrapolation status."""

    if policy not in {PROPERTY_POLICY_STRICT, PROPERTY_POLICY_ALLOW_EXTRAPOLATION}:
        raise ValueError("unsupported IPA property policy")
    if temperature_K is None or pressure_Pa_abs is None:
        return {
            "status": "INPUT_U",
            "calculation_status": "INPUT_U",
            "reason": "coolant temperature or pressure is unavailable",
            "temperature_K": temperature_K,
            "pressure_Pa_abs": pressure_Pa_abs,
        }
    try:
        temperature = _finite(temperature_K, "temperature")
        pressure = _finite(pressure_Pa_abs, "pressure")
    except ValueError as error:
        return {
            "status": "INPUT_U",
            "calculation_status": "INPUT_U",
            "reason": str(error),
            "temperature_K": temperature_K,
            "pressure_Pa_abs": pressure_Pa_abs,
        }
    if temperature <= 0.0 or pressure <= 0.0:
        return {
            "status": "INPUT_U",
            "calculation_status": "INPUT_U",
            "reason": "temperature and pressure must be positive",
            "temperature_K": temperature,
            "pressure_Pa_abs": pressure,
        }
    try:
        saturation_temperature = ipa.T_sat(pressure)
    except (RuntimeError, ValueError) as error:
        return {
            "status": "OUTSIDE",
            "calculation_status": "OUTSIDE",
            "reason": f"saturation gate failed: {error}",
            "temperature_K": temperature,
            "pressure_Pa_abs": pressure,
        }
    if temperature >= float(ipa.T_CRIT):
        return {
            "status": "OUTSIDE",
            "calculation_status": "OUTSIDE",
            "reason": "temperature is at or above the supplied critical temperature",
            "temperature_K": temperature,
            "pressure_Pa_abs": pressure,
            "saturation_temperature_K": saturation_temperature,
        }
    if saturation_temperature is not None and temperature >= saturation_temperature:
        return {
            "status": "OUTSIDE",
            "calculation_status": "OUTSIDE",
            "reason": "bulk state is at or above the supplied saturation boundary",
            "temperature_K": temperature,
            "pressure_Pa_abs": pressure,
            "saturation_temperature_K": saturation_temperature,
        }

    lower, upper = IPA_TRANSPORT_OVERLAP_K
    in_range = lower <= temperature <= upper
    if not in_range and policy == PROPERTY_POLICY_STRICT:
        return {
            "status": "OUTSIDE",
            "calculation_status": "OUTSIDE",
            "reason": (
                f"T={temperature:.6g} K is outside the common rho/mu/cp/k "
                f"correlation interval {lower}..{upper} K; no extrapolation"
            ),
            "temperature_K": temperature,
            "pressure_Pa_abs": pressure,
            "saturation_temperature_K": saturation_temperature,
        }
    try:
        values = {
            "density_kg_m3": float(ipa.rho(temperature)),
            "viscosity_Pa_s": float(ipa.mu(temperature)),
            "cp_J_kgK": float(ipa.cp(temperature)),
            "conductivity_W_mK": float(ipa.k(temperature)),
        }
    except (ArithmeticError, RuntimeError, ValueError) as error:
        return {
            "status": "OUTSIDE",
            "calculation_status": "NUM_U",
            "reason": f"property evaluation failed: {error}",
            "temperature_K": temperature,
            "pressure_Pa_abs": pressure,
            "saturation_temperature_K": saturation_temperature,
        }
    if not all(math.isfinite(value) and value > 0.0 for value in values.values()):
        return {
            "status": "OUTSIDE",
            "calculation_status": "NUM_U",
            "reason": "property module returned a non-positive or non-finite value",
            "temperature_K": temperature,
            "pressure_Pa_abs": pressure,
            "saturation_temperature_K": saturation_temperature,
        }
    status = "IN_RANGE" if in_range else "EXTRAPOLATED"
    reason = (
        "within the common supplied transport-correlation interval"
        if in_range
        else "supplied temperature-only correlations extrapolated by explicit policy"
    )
    return {
        "status": status,
        "calculation_status": "MODEL_U",
        "reason": reason,
        "temperature_K": temperature,
        "pressure_Pa_abs": pressure,
        "saturation_temperature_K": saturation_temperature,
        "pressure_effects_in_transport_properties": False,
        **values,
    }


def assess_supplied_ipa_states(
    *,
    temperature_K: Sequence[float | None],
    pressure_Pa_abs: Sequence[float | None],
    ipa: IPAPropertyModule,
) -> dict[str, Any]:
    """Return strict, non-extrapolated station properties and stop statuses."""

    temperatures = list(temperature_K)
    pressures = list(pressure_Pa_abs)
    if not temperatures or len(temperatures) != len(pressures):
        raise ValueError("temperature and pressure arrays must have equal non-zero length")

    records: list[dict[str, Any]] = []
    arrays: dict[str, list[float | None]] = {
        "density_kg_m3": [],
        "viscosity_Pa_s": [],
        "cp_J_kgK": [],
        "conductivity_W_mK": [],
    }
    counts = {"MODEL_U": 0, "OUTSIDE": 0, "INPUT_U": 0, "NUM_U": 0}
    range_counts = {"IN_RANGE": 0, "EXTRAPOLATED": 0, "OUTSIDE": 0, "INPUT_U": 0}
    lower, upper = IPA_TRANSPORT_OVERLAP_K

    for index, (raw_temperature, raw_pressure) in enumerate(zip(temperatures, pressures)):
        record: dict[str, Any] = {
            "station_index": index,
            "temperature_K": raw_temperature,
            "pressure_Pa_abs": raw_pressure,
            "fluid_name": IPA_FLUID_NAME,
            "cas": IPA_CAS,
            "pressure_effects_in_transport_properties": False,
            "experimental_validation": False,
        }
        values: tuple[float, float, float, float] | None = None
        if raw_temperature is None or raw_pressure is None:
            status = "INPUT_U"
            range_status = "INPUT_U"
            reason = "coolant temperature or pressure is unavailable"
        else:
            try:
                temperature = _finite(raw_temperature, "temperature")
                pressure = _finite(raw_pressure, "pressure")
                if temperature <= 0.0 or pressure <= 0.0:
                    raise ValueError("temperature and pressure must be positive")
                if not lower <= temperature <= upper:
                    status = "OUTSIDE"
                    range_status = "OUTSIDE"
                    reason = (
                        f"T={temperature:.6g} K is outside the common rho/mu/cp/k "
                        f"correlation interval {lower}..{upper} K; no extrapolation"
                    )
                else:
                    saturation_temperature = ipa.T_sat(pressure)
                    record["saturation_temperature_K"] = saturation_temperature
                    if saturation_temperature is not None and temperature >= saturation_temperature:
                        status = "OUTSIDE"
                        range_status = "OUTSIDE"
                        reason = "bulk state is at or above the supplied saturation boundary"
                    else:
                        values = (
                            float(ipa.rho(temperature)),
                            float(ipa.mu(temperature)),
                            float(ipa.cp(temperature)),
                            float(ipa.k(temperature)),
                        )
                        if not all(math.isfinite(item) and item > 0.0 for item in values):
                            raise ArithmeticError("property module returned a non-positive value")
                        status = "MODEL_U"
                        range_status = "IN_RANGE"
                        reason = (
                            "within supplied temperature correlation and saturation gates; "
                            "temperature-only saturated-liquid fits remain model uncertainty"
                        )
            except (ArithmeticError, RuntimeError, ValueError) as error:
                status = "NUM_U"
                range_status = "OUTSIDE"
                reason = str(error)

        counts[status] += 1
        range_counts[range_status] += 1
        record.update(
            {"status": status, "property_range_status": range_status, "reason": reason}
        )
        records.append(record)
        for key, value in zip(arrays, values or (None, None, None, None)):
            arrays[key].append(value)

    return {
        "fluid_name": IPA_FLUID_NAME,
        "cas": IPA_CAS,
        "property_source_classification": "SUPPLIED_T_ONLY_CORRELATIONS_MODEL_U",
        "transport_common_temperature_range_K": [lower, upper],
        "pressure_effects_in_transport_properties": False,
        "single_phase_gate": "SUPPLIED_T_SAT_P_CORRELATION",
        "status_counts": counts,
        "property_range_status_counts": range_counts,
        "status_by_station": [item["status"] for item in records],
        "property_range_status_by_station": [
            item["property_range_status"] for item in records
        ],
        "records": records,
        **arrays,
        "experimental_validation": False,
        "validated_engine_prediction": False,
    }


def evaluate_candidate_key_by_angle(
    *,
    candidates: Sequence[dict[str, Any]],
    x_m: Sequence[float],
    candidate_key: tuple[int, float, float],
    mdot_total_kg_s: float,
    properties: dict[str, Any],
    roughness_m: float,
    thermal_boundary_condition: str,
    fully_developed: bool,
    colebrook_re_range: tuple[float, float],
    colebrook_relative_roughness_range: tuple[float, float],
    laminar_model: str = LAMINAR_MODEL_THERMAL_ENTRY,
) -> dict[str, Any]:
    """Evaluate one shared ``(N, rib, height)`` geometry at all helix angles.

    The coolant state snapshot is held fixed across angles.  This is an
    h_l/geometry sensitivity calculation, not a recoupled wall/coolant march.
    """

    count, rib, height = candidate_key
    selected = [
        item
        for item in candidates
        if item["channel_count"] == count
        and math.isclose(item["rib_thickness_m"], rib, rel_tol=0.0, abs_tol=1.0e-12)
        and math.isclose(item["channel_height_m"], height, rel_tol=0.0, abs_tol=1.0e-12)
    ]
    if not selected:
        raise ValueError("selected engine channel key is absent from Model H candidates")

    station_count = len(x_m)
    statuses = list(properties["status_by_station"])
    if len(statuses) != station_count:
        raise ValueError("property and geometry station counts differ")
    results: list[dict[str, Any]] = []
    beta_zero_residuals = {key: 0.0 for key in ("Reynolds", "Prandtl", "Nusselt", "h_l_W_m2K")}
    beta_zero_comparisons = 0

    for candidate in sorted(selected, key=lambda item: float(item["helix_angle_deg"])):
        angle = float(candidate["helix_angle_deg"])
        radii = [float(station["mean_channel_radius_m"]) for station in candidate["stations"]]
        path = helical_coolant_path_distances(x_m, radii, angle)
        station_results: list[dict[str, Any]] = []
        for station in candidate["stations"]:
            index = int(station["station_index"])
            status = statuses[index]
            base = {
                **station,
                "s_B_m": path["s_B_m"][index],
                "s_H_m": path["s_H_m"][index],
                "segment_length_H_m": path["segment_length_H_m"][index],
                "property_validity_status": status,
            }
            if not candidate["valid"]:
                station_results.append({**base, "status": "OUTSIDE", "reason": "geometry outside supplied constraints", "Reynolds": None, "Prandtl": None, "Nusselt": None, "h_l_W_m2K": None})
                continue
            if status in STOP_STATUSES:
                station_results.append({**base, "status": status, "reason": "property gate stopped calculation", "Reynolds": None, "Prandtl": None, "Nusselt": None, "h_l_W_m2K": None})
                continue
            try:
                local = evaluate_model_h_hl(
                    mdot_total_kg_s=mdot_total_kg_s,
                    density_kg_m3=properties["density_kg_m3"][index],
                    mean_channel_radius_m=station["mean_channel_radius_m"],
                    channel_count=count,
                    rib_thickness_m=rib,
                    channel_height_m=height,
                    helix_angle_deg=angle,
                    viscosity_Pa_s=properties["viscosity_Pa_s"][index],
                    cp_J_kgK=properties["cp_J_kgK"][index],
                    conductivity_W_mK=properties["conductivity_W_mK"][index],
                    roughness_m=roughness_m,
                    thermal_boundary_condition=thermal_boundary_condition,
                    fully_developed=fully_developed,
                    colebrook_re_range=colebrook_re_range,
                    colebrook_relative_roughness_range=colebrook_relative_roughness_range,
                    laminar_model=laminar_model,
                    s_H_m=path["s_H_m"][index],
                    first_segment_length_H_m=path["entry_segment_length_H_m"][index],
                )
            except CoolingModelBError as error:
                station_results.append({**base, "status": error.status, "reason": str(error), "Reynolds": error.details.get("Reynolds"), "Prandtl": error.details.get("Prandtl"), "Nusselt": None, "h_l_W_m2K": None})
                continue

            station_results.append({**base, **local, "reason": "frozen-state Model H sensitivity completed"})
            if angle == 0.0:
                reference = evaluate_model_b_hl(
                    mdot_total_kg_s=mdot_total_kg_s,
                    channel_count=count,
                    width_m=station["channel_width_m"],
                    height_m=height,
                    viscosity_Pa_s=properties["viscosity_Pa_s"][index],
                    cp_J_kgK=properties["cp_J_kgK"][index],
                    conductivity_W_mK=properties["conductivity_W_mK"][index],
                    roughness_m=roughness_m,
                    thermal_boundary_condition=thermal_boundary_condition,
                    fully_developed=fully_developed,
                    colebrook_re_range=colebrook_re_range,
                    colebrook_relative_roughness_range=colebrook_relative_roughness_range,
                    laminar_model=laminar_model,
                    s_m=path["s_B_m"][index],
                    first_segment_length_m=path["entry_segment_length_B_m"][index],
                )
                beta_zero_comparisons += 1
                for key in beta_zero_residuals:
                    beta_zero_residuals[key] = max(
                        beta_zero_residuals[key], abs(float(local[key]) - float(reference[key]))
                    )

        computed = [row for row in station_results if row.get("h_l_W_m2K") is not None]
        status_counts: dict[str, int] = {}
        regime_counts: dict[str, int] = {}
        for row in station_results:
            row_status = str(row["status"])
            status_counts[row_status] = status_counts.get(row_status, 0) + 1
            if row.get("flow_regime") is not None:
                regime = str(row["flow_regime"])
                regime_counts[regime] = regime_counts.get(regime, 0) + 1
        widths = [
            float(row["channel_width_m"])
            for row in station_results
            if row.get("channel_width_m") is not None
        ]
        diameters = [
            float(row["hydraulic_diameter_m"])
            for row in station_results
            if row.get("hydraulic_diameter_m") is not None
        ]
        results.append(
            {
                "candidate_id": candidate["candidate_id"],
                "base_model_b_candidate_id": candidate["base_model_b_candidate_id"],
                "helix_angle_deg": angle,
                "channel_count": count,
                "rib_thickness_m": rib,
                "channel_height_m": height,
                "geometry_valid": candidate["valid"],
                "geometry_exclusion_reason_types": sorted(
                    {
                        reason.split(": ", 1)[-1]
                        for reason in candidate["geometry_exclusion_reasons"]
                    }
                ),
                "computed_station_count": len(computed),
                "stopped_station_count": station_count - len(computed),
                "status_counts": status_counts,
                "flow_regime_counts": regime_counts,
                "path_length_m": path["s_H_m"][0],
                "minimum_channel_width_m": min(widths) if widths else None,
                "maximum_channel_width_m": max(widths) if widths else None,
                "minimum_hydraulic_diameter_m": min(diameters) if diameters else None,
                "maximum_hydraulic_diameter_m": max(diameters) if diameters else None,
                "minimum_Reynolds": min((row["Reynolds"] for row in computed), default=None),
                "maximum_Reynolds": max((row["Reynolds"] for row in computed), default=None),
                "minimum_Nusselt": min((row["Nusselt"] for row in computed), default=None),
                "maximum_Nusselt": max((row["Nusselt"] for row in computed), default=None),
                "minimum_h_l_W_m2K": min((row["h_l_W_m2K"] for row in computed), default=None),
                "maximum_h_l_W_m2K": max((row["h_l_W_m2K"] for row in computed), default=None),
                "stations": station_results,
            }
        )

    beta_zero = next(
        (item for item in results if item["helix_angle_deg"] == 0.0), None
    )
    base_h = (
        {
            int(row["station_index"]): float(row["h_l_W_m2K"])
            for row in beta_zero["stations"]
            if row.get("h_l_W_m2K") is not None
        }
        if beta_zero is not None
        else {}
    )
    relative_sensitivity: dict[str, dict[str, float | int]] = {}
    for item in results:
        angle = float(item["helix_angle_deg"])
        if angle == 0.0:
            continue
        differences = [
            100.0
            * (float(row["h_l_W_m2K"]) / base_h[int(row["station_index"])] - 1.0)
            for row in item["stations"]
            if row.get("h_l_W_m2K") is not None
            and int(row["station_index"]) in base_h
        ]
        if differences:
            relative_sensitivity[f"{angle:g}"] = {
                "paired_station_count": len(differences),
                "mean_percent": statistics.mean(differences),
                "median_percent": statistics.median(differences),
                "minimum_percent": min(differences),
                "maximum_percent": max(differences),
            }

    return {
        "candidate_key": {
            "channel_count": count,
            "rib_thickness_m": rib,
            "channel_height_m": height,
        },
        "angles": results,
        "beta_zero_model_b_regression": {
            "comparison_count": beta_zero_comparisons,
            "maximum_absolute_residuals": beta_zero_residuals,
            "pass": beta_zero_comparisons > 0 and all(value == 0.0 for value in beta_zero_residuals.values()),
        },
        "stationwise_h_l_relative_to_beta_zero_percent": relative_sensitivity,
        "coolant_state_coupling": "FROZEN_PROVIDED_MODEL_B_BASELINE_PROFILE",
        "recoupled_helical_wall_coolant_march": False,
        "experimental_validation": False,
        "validated_engine_prediction": False,
    }


__all__ = [
    "IPA_CAS",
    "IPA_FLUID_NAME",
    "IPA_TRANSPORT_OVERLAP_K",
    "PROPERTY_POLICY_ALLOW_EXTRAPOLATION",
    "PROPERTY_POLICY_STRICT",
    "assess_supplied_ipa_states",
    "evaluate_supplied_ipa_state",
    "evaluate_candidate_key_by_angle",
    "existing_loop_segment_length_factor",
]
