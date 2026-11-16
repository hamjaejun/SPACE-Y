from __future__ import annotations

from collections import defaultdict
import csv
from dataclasses import asdict
import hashlib
import json
import math
from pathlib import Path
from statistics import mean, median
from typing import Any, Callable

try:
    from tools.analyze_model_b_candidates import (
        BASE_INPUT,
        BOUNDARIES,
        FLUID_LABEL,
        MODELS,
        TOTAL_MASS_FLOW_KG_S,
        build_analysis as build_candidate_analysis,
    )
except ModuleNotFoundError:  # Direct execution from the tools directory.
    from analyze_model_b_candidates import (
        BASE_INPUT,
        BOUNDARIES,
        FLUID_LABEL,
        MODELS,
        TOTAL_MASS_FLOW_KG_S,
        build_analysis as build_candidate_analysis,
    )
from space_y.coolant import (
    TRANSITION_HEAT_TRANSFER_MODEL_B,
    build_friction_domain,
    build_heat_transfer_domain,
    build_rectangular_channel_cross_section,
    evaluate_rectangular_cooling_hl,
)
from space_y.cooling_transition_sensitivity import (
    evaluate_linear_transition_endpoint_sensitivity,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "artifacts/cooling_model_b_uncertainty_analysis"
PRESERVED_ANALYSIS_DIR = ROOT / "artifacts/cooling_model_b_candidate_analysis"
GEOMETRY = "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER"
MODEL_3000 = "MODEL_B_LINEAR_2300_3000"
MODEL_4000 = "SPACE_Y_SENSITIVITY_LINEAR_2300_4000"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _percentile(values: list[float], probability: float) -> float:
    if not values:
        raise ValueError("percentile requires data")
    if not 0.0 <= probability <= 1.0:
        raise ValueError("probability must be in [0, 1]")
    ordered = sorted(values)
    position = probability * (len(ordered) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _statistics(values: list[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0}
    return {
        "count": len(values),
        "minimum": min(values),
        "p05": _percentile(values, 0.05),
        "p25": _percentile(values, 0.25),
        "median": median(values),
        "mean": mean(values),
        "p75": _percentile(values, 0.75),
        "p95": _percentile(values, 0.95),
        "maximum": max(values),
    }


def _pearson(x_values: list[float], y_values: list[float]) -> float | None:
    if len(x_values) != len(y_values) or len(x_values) < 2:
        return None
    x_mean = mean(x_values)
    y_mean = mean(y_values)
    numerator = sum(
        (x - x_mean) * (y - y_mean) for x, y in zip(x_values, y_values)
    )
    x_scale = math.sqrt(sum((x - x_mean) ** 2 for x in x_values))
    y_scale = math.sqrt(sum((y - y_mean) ** 2 for y in y_values))
    if x_scale == 0.0 or y_scale == 0.0:
        return None
    return numerator / (x_scale * y_scale)


def _relative_difference(comparison: float | None, baseline: float | None) -> float | None:
    if comparison is None or baseline is None:
        return None
    if not all(math.isfinite(value) for value in (comparison, baseline)) or baseline <= 0:
        return None
    return (comparison - baseline) / baseline * 100.0


def _domains(base: dict[str, Any]):
    return (
        build_heat_transfer_domain(base["model_conditions"]["heat_transfer_domain"]),
        build_friction_domain(base["model_conditions"]["friction_domain"]),
    )


def _production_at_re(
    *,
    reynolds: float,
    width_m: float,
    height_m: float,
    channel_count: int,
    boundary: str,
    properties: dict[str, Any],
    roughness_m: float,
    heat_domain,
    friction_domain,
) -> dict[str, Any]:
    section = build_rectangular_channel_cross_section(
        {"width_m": width_m, "height_m": height_m, "channel_count": channel_count}
    )
    mass_flow = (
        reynolds
        * section.total_flow_area_m2
        * properties["viscosity_Pa_s"]
        / section.hydraulic_diameter_m
    )
    result = evaluate_rectangular_cooling_hl(
        coolant_mass_flow_kg_s=mass_flow,
        width_m=width_m,
        height_m=height_m,
        channel_count=channel_count,
        viscosity_Pa_s=properties["viscosity_Pa_s"],
        cp_J_kgK=properties["cp_J_kgK"],
        conductivity_W_mK=properties["conductivity_W_mK"],
        equivalent_sand_roughness_m=roughness_m,
        closure_geometry=GEOMETRY,
        fully_developed=True,
        thermal_boundary_condition="SYNTHETIC_UNIFORM_HEAT_FLUX",
        heat_transfer_domain=heat_domain,
        heat_transfer_thermal_boundary_condition="SYNTHETIC_UNIFORM_HEAT_FLUX",
        friction_domain=friction_domain,
        density_kg_m3=properties["density_kg_m3"],
        laminar_thermal_boundary_condition=boundary,
        transition_heat_transfer_model=TRANSITION_HEAT_TRANSFER_MODEL_B,
    )
    return {
        "Nusselt": result.Nusselt,
        "h_l_W_m2K": result.h_l_W_m2K,
        "correlation_id": result.heat_transfer_applicability.get("correlation_id")
        or result.applicability_assessment.get("selected_correlation_id")
        or result.heat_transfer_applicability.get("status"),
    }


def _sensitivity_4000_at_re(
    *,
    reynolds: float,
    width_m: float,
    height_m: float,
    channel_count: int,
    boundary: str,
    properties: dict[str, Any],
    roughness_m: float,
    heat_domain,
    friction_domain,
) -> dict[str, Any]:
    if reynolds < 2300.0 or reynolds >= 4000.0:
        return _production_at_re(
            reynolds=reynolds,
            width_m=width_m,
            height_m=height_m,
            channel_count=channel_count,
            boundary=boundary,
            properties=properties,
            roughness_m=roughness_m,
            heat_domain=heat_domain,
            friction_domain=friction_domain,
        )
    section = build_rectangular_channel_cross_section(
        {"width_m": width_m, "height_m": height_m, "channel_count": channel_count}
    )
    result = evaluate_linear_transition_endpoint_sensitivity(
        reynolds=reynolds,
        prandtl=(
            properties["cp_J_kgK"]
            * properties["viscosity_Pa_s"]
            / properties["conductivity_W_mK"]
        ),
        width_m=width_m,
        height_m=height_m,
        hydraulic_diameter_m=section.hydraulic_diameter_m,
        conductivity_W_mK=properties["conductivity_W_mK"],
        equivalent_sand_roughness_m=roughness_m,
        closure_geometry=GEOMETRY,
        fully_developed=True,
        thermal_boundary_condition=boundary,
        heat_transfer_domain=heat_domain,
        friction_domain=friction_domain,
        upper_endpoint_reynolds=4000.0,
    )
    return {
        "Nusselt": result.Nusselt,
        "h_l_W_m2K": result.h_l_W_m2K,
        "correlation_id": result.correlation_id,
    }


def _station_comparison(
    candidate_data: dict[str, Any], base: dict[str, Any]
) -> list[dict[str, Any]]:
    properties = base["coolant_conditions"]["default_fluid_properties"]
    roughness = base["manufacturing_constraints"]["equivalent_sand_roughness_m"]
    heat_domain, friction_domain = _domains(base)
    base_rows = candidate_data["station_rows"]
    lookup = {
        (row["thermal_boundary_condition"], row["model"], row["candidate_id"], row["station_id"]): row
        for row in base_rows
    }
    output: list[dict[str, Any]] = []
    keys = sorted(
        {
            (row["candidate_id"], row["station_id"])
            for row in base_rows
            if row["model"] == MODELS[1]
            and row["thermal_boundary_condition"] == BOUNDARIES[0]
        }
    )
    for boundary in BOUNDARIES:
        for candidate_id, station_id in keys:
            row = lookup[(boundary, MODELS[1], candidate_id, station_id)]
            re_value = row["Reynolds"]
            comparison = _sensitivity_4000_at_re(
                reynolds=re_value,
                width_m=row["channel_width_m"],
                height_m=row["channel_height_m"],
                channel_count=row["channel_count"],
                boundary=boundary,
                properties=properties,
                roughness_m=roughness,
                heat_domain=heat_domain,
                friction_domain=friction_domain,
            )
            relative = _relative_difference(
                comparison["h_l_W_m2K"], row["h_l_W_m2K"]
            )
            aspect = min(row["channel_width_m"], row["channel_height_m"]) / max(
                row["channel_width_m"], row["channel_height_m"]
            )
            output.append(
                {
                    "fluid_classification": FLUID_LABEL,
                    "actual_engine_prediction": False,
                    "experimental_validation": False,
                    "candidate_id": candidate_id,
                    "station_id": station_id,
                    "x_m": row["x_m"],
                    "thermal_boundary_condition": boundary,
                    "Reynolds": re_value,
                    "Prandtl": row["Prandtl"],
                    "channel_width_m": row["channel_width_m"],
                    "channel_height_m": row["channel_height_m"],
                    "aspect_ratio": aspect,
                    "hydraulic_diameter_m": row["hydraulic_diameter_m"],
                    "channel_count": row["channel_count"],
                    "model_3000_correlation_id": row["correlation_id"],
                    "model_3000_Nusselt": row["Nusselt"],
                    "model_3000_h_l_W_m2K": row["h_l_W_m2K"],
                    "model_4000_correlation_id": comparison["correlation_id"],
                    "model_4000_Nusselt": comparison["Nusselt"],
                    "model_4000_h_l_W_m2K": comparison["h_l_W_m2K"],
                    "Nusselt_absolute_difference": comparison["Nusselt"] - row["Nusselt"],
                    "Nusselt_relative_difference_percent": _relative_difference(
                        comparison["Nusselt"], row["Nusselt"]
                    ),
                    "h_l_absolute_difference_W_m2K": (
                        comparison["h_l_W_m2K"] - row["h_l_W_m2K"]
                    ),
                    "h_l_relative_difference_percent": relative,
                    "h_l_absolute_relative_difference_percent": abs(relative),
                    "comparison_band": (
                        "BELOW_2300_SAME_SHAH_LONDON"
                        if re_value < 2300.0
                        else "RE_2300_3000_BOTH_INTERPOLATED"
                        if re_value < 3000.0
                        else "RE_3000_4000_GNIELINSKI_VS_INTERPOLATED"
                        if re_value < 4000.0
                        else "AT_OR_ABOVE_4000_SAME_GNIELINSKI"
                    ),
                    "model_3000_friction_supported": row["friction_supported"],
                    "model_4000_transition_friction_supported": (
                        False if 2300.0 <= re_value < 4000.0 else row["friction_supported"]
                    ),
                }
            )
    return output


def _candidate_summary(station_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in station_rows:
        grouped[row["candidate_id"]].append(row)
    output: list[dict[str, Any]] = []
    for candidate_id, rows in sorted(grouped.items()):
        unique_stations = {
            row["station_id"]: row
            for row in rows
            if row["thermal_boundary_condition"] == BOUNDARIES[0]
        }
        maximum = max(rows, key=lambda row: row["h_l_absolute_relative_difference_percent"])
        by_boundary = {
            boundary: [
                row["h_l_absolute_relative_difference_percent"]
                for row in rows
                if row["thermal_boundary_condition"] == boundary
            ]
            for boundary in BOUNDARIES
        }
        output.append(
            {
                "fluid_classification": FLUID_LABEL,
                "candidate_id": candidate_id,
                "station_count": len(unique_stations),
                "max_absolute_relative_difference_percent": maximum[
                    "h_l_absolute_relative_difference_percent"
                ],
                "mean_absolute_relative_difference_percent": mean(
                    row["h_l_absolute_relative_difference_percent"] for row in rows
                ),
                "max_sensitivity_station_id": maximum["station_id"],
                "max_sensitivity_boundary_condition": maximum[
                    "thermal_boundary_condition"
                ],
                "max_sensitivity_Reynolds": maximum["Reynolds"],
                "max_sensitivity_aspect_ratio": maximum["aspect_ratio"],
                "transition_2300_3000_station_count": sum(
                    2300.0 <= row["Reynolds"] < 3000.0
                    for row in unique_stations.values()
                ),
                "re_3000_4000_station_count": sum(
                    3000.0 <= row["Reynolds"] < 4000.0
                    for row in unique_stations.values()
                ),
                "Nu_T_max_absolute_relative_difference_percent": max(
                    by_boundary[BOUNDARIES[0]]
                ),
                "Nu_H1_max_absolute_relative_difference_percent": max(
                    by_boundary[BOUNDARIES[1]]
                ),
                "boundary_max_sensitivity_difference_percentage_points": (
                    max(by_boundary[BOUNDARIES[1]])
                    - max(by_boundary[BOUNDARIES[0]])
                ),
                "both_models_heat_transfer_calculable": True,
                "transition_friction_complete": not any(
                    2300.0 <= row["Reynolds"] < 4000.0
                    for row in unique_stations.values()
                ),
                "design_ranking_performed": False,
                "experimental_validation": False,
            }
        )
    return output


def _boundary_comparison(station_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    lookup = {
        (row["candidate_id"], row["station_id"], row["thermal_boundary_condition"]): row
        for row in station_rows
    }
    keys = sorted({(row["candidate_id"], row["station_id"]) for row in station_rows})
    output: list[dict[str, Any]] = []
    for candidate_id, station_id in keys:
        t = lookup[(candidate_id, station_id, BOUNDARIES[0])]
        h1 = lookup[(candidate_id, station_id, BOUNDARIES[1])]
        output.append(
            {
                "fluid_classification": FLUID_LABEL,
                "candidate_id": candidate_id,
                "station_id": station_id,
                "Reynolds": t["Reynolds"],
                "Prandtl": t["Prandtl"],
                "aspect_ratio": t["aspect_ratio"],
                "B_T_Nusselt": t["model_3000_Nusselt"],
                "B_H1_Nusselt": h1["model_3000_Nusselt"],
                "S_T_Nusselt": t["model_4000_Nusselt"],
                "S_H1_Nusselt": h1["model_4000_Nusselt"],
                "B_T_h_l_W_m2K": t["model_3000_h_l_W_m2K"],
                "B_H1_h_l_W_m2K": h1["model_3000_h_l_W_m2K"],
                "S_T_h_l_W_m2K": t["model_4000_h_l_W_m2K"],
                "S_H1_h_l_W_m2K": h1["model_4000_h_l_W_m2K"],
                "model_3000_boundary_effect_percent": _relative_difference(
                    h1["model_3000_h_l_W_m2K"], t["model_3000_h_l_W_m2K"]
                ),
                "model_4000_boundary_effect_percent": _relative_difference(
                    h1["model_4000_h_l_W_m2K"], t["model_4000_h_l_W_m2K"]
                ),
                "Nu_T_endpoint_effect_percent": t[
                    "h_l_relative_difference_percent"
                ],
                "Nu_H1_endpoint_effect_percent": h1[
                    "h_l_relative_difference_percent"
                ],
                "boundary_conditions_are_verified_bounds": False,
            }
        )
    return output


def _controlled_curve(
    reference: dict[str, Any], base: dict[str, Any]
) -> list[dict[str, Any]]:
    properties = base["coolant_conditions"]["default_fluid_properties"]
    roughness = base["manufacturing_constraints"]["equivalent_sand_roughness_m"]
    heat_domain, friction_domain = _domains(base)
    output: list[dict[str, Any]] = []
    for boundary in BOUNDARIES:
        for reynolds in (float(value) for value in range(2000, 5001, 10)):
            model_3000 = _production_at_re(
                reynolds=reynolds,
                width_m=reference["channel_width_m"],
                height_m=reference["channel_height_m"],
                channel_count=reference["channel_count"],
                boundary=boundary,
                properties=properties,
                roughness_m=roughness,
                heat_domain=heat_domain,
                friction_domain=friction_domain,
            )
            model_4000 = _sensitivity_4000_at_re(
                reynolds=reynolds,
                width_m=reference["channel_width_m"],
                height_m=reference["channel_height_m"],
                channel_count=reference["channel_count"],
                boundary=boundary,
                properties=properties,
                roughness_m=roughness,
                heat_domain=heat_domain,
                friction_domain=friction_domain,
            )
            output.append(
                {
                    "fluid_classification": FLUID_LABEL,
                    "reference_candidate_id": reference["candidate_id"],
                    "reference_station_id": reference["station_id"],
                    "thermal_boundary_condition": boundary,
                    "Reynolds": reynolds,
                    "Prandtl": reference["Prandtl"],
                    "channel_width_m": reference["channel_width_m"],
                    "channel_height_m": reference["channel_height_m"],
                    "aspect_ratio": reference["aspect_ratio"],
                    "hydraulic_diameter_m": reference["hydraulic_diameter_m"],
                    "model_3000_Nusselt": model_3000["Nusselt"],
                    "model_4000_Nusselt": model_4000["Nusselt"],
                    "model_3000_h_l_W_m2K": model_3000["h_l_W_m2K"],
                    "model_4000_h_l_W_m2K": model_4000["h_l_W_m2K"],
                    "Nusselt_relative_difference_percent": _relative_difference(
                        model_4000["Nusselt"], model_3000["Nusselt"]
                    ),
                    "h_l_relative_difference_percent": _relative_difference(
                        model_4000["h_l_W_m2K"], model_3000["h_l_W_m2K"]
                    ),
                    "dNu_dRe_model_3000": None,
                    "dNu_dRe_model_4000": None,
                    "dh_l_dRe_model_3000": None,
                    "dh_l_dRe_model_4000": None,
                }
            )
    for boundary in BOUNDARIES:
        indices = [
            index
            for index, row in enumerate(output)
            if row["thermal_boundary_condition"] == boundary
        ]
        for position, index in enumerate(indices):
            if position == 0 or position == len(indices) - 1:
                continue
            previous = output[indices[position - 1]]
            following = output[indices[position + 1]]
            delta_re = following["Reynolds"] - previous["Reynolds"]
            for field, slope_field in (
                ("model_3000_Nusselt", "dNu_dRe_model_3000"),
                ("model_4000_Nusselt", "dNu_dRe_model_4000"),
                ("model_3000_h_l_W_m2K", "dh_l_dRe_model_3000"),
                ("model_4000_h_l_W_m2K", "dh_l_dRe_model_4000"),
            ):
                output[index][slope_field] = (
                    following[field] - previous[field]
                ) / delta_re
    return output


def _boundary_diagnostics(reference: dict[str, Any], base: dict[str, Any]) -> list[dict[str, Any]]:
    properties = base["coolant_conditions"]["default_fluid_properties"]
    roughness = base["manufacturing_constraints"]["equivalent_sand_roughness_m"]
    heat_domain, friction_domain = _domains(base)
    epsilon = 0.001
    output: list[dict[str, Any]] = []
    functions: tuple[tuple[str, Callable[..., dict[str, Any]]], ...] = (
        (MODEL_3000, _production_at_re),
        (MODEL_4000, _sensitivity_4000_at_re),
    )
    for boundary in BOUNDARIES:
        for model_id, function in functions:
            for transition in (2300.0, 3000.0, 4000.0):
                values = []
                for re_value in (transition - epsilon, transition, transition + epsilon):
                    values.append(
                        function(
                            reynolds=re_value,
                            width_m=reference["channel_width_m"],
                            height_m=reference["channel_height_m"],
                            channel_count=reference["channel_count"],
                            boundary=boundary,
                            properties=properties,
                            roughness_m=roughness,
                            heat_domain=heat_domain,
                            friction_domain=friction_domain,
                        )
                    )
                left, center, right = values
                output.append(
                    {
                        "model": model_id,
                        "thermal_boundary_condition": boundary,
                        "boundary_Reynolds": transition,
                        "epsilon_Reynolds": epsilon,
                        "Nusselt_left": left["Nusselt"],
                        "Nusselt_at_boundary": center["Nusselt"],
                        "Nusselt_right": right["Nusselt"],
                        "left_value_residual": center["Nusselt"] - left["Nusselt"],
                        "right_value_residual": right["Nusselt"] - center["Nusselt"],
                        "left_slope_dNu_dRe": (
                            center["Nusselt"] - left["Nusselt"]
                        ) / epsilon,
                        "right_slope_dNu_dRe": (
                            right["Nusselt"] - center["Nusselt"]
                        ) / epsilon,
                        "slope_jump_right_minus_left": (
                            right["Nusselt"]
                            - 2.0 * center["Nusselt"]
                            + left["Nusselt"]
                        ) / epsilon,
                        "value_continuity_interpretation": (
                            "CONTINUOUS_WITH_FINITE_NEIGHBORHOOD_RESIDUAL"
                        ),
                        "slope_continuity_claim": False,
                    }
                )
    return output


def _endpoint_decomposition(maximum: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    properties = base["coolant_conditions"]["default_fluid_properties"]
    roughness = base["manufacturing_constraints"]["equivalent_sand_roughness_m"]
    heat_domain, friction_domain = _domains(base)
    section = build_rectangular_channel_cross_section(
        {
            "width_m": maximum["channel_width_m"],
            "height_m": maximum["channel_height_m"],
            "channel_count": maximum["channel_count"],
        }
    )
    common = {
        "prandtl": maximum["Prandtl"],
        "width_m": maximum["channel_width_m"],
        "height_m": maximum["channel_height_m"],
        "hydraulic_diameter_m": section.hydraulic_diameter_m,
        "conductivity_W_mK": properties["conductivity_W_mK"],
        "equivalent_sand_roughness_m": roughness,
        "closure_geometry": GEOMETRY,
        "fully_developed": True,
        "thermal_boundary_condition": maximum["thermal_boundary_condition"],
        "heat_transfer_domain": heat_domain,
        "friction_domain": friction_domain,
    }
    endpoint_3000 = evaluate_linear_transition_endpoint_sensitivity(
        reynolds=min(max(maximum["Reynolds"], 2300.0), 3000.0),
        upper_endpoint_reynolds=3000.0,
        **common,
    )
    endpoint_4000 = evaluate_linear_transition_endpoint_sensitivity(
        reynolds=maximum["Reynolds"],
        upper_endpoint_reynolds=4000.0,
        **common,
    )
    return {
        "gamma_3000": endpoint_3000.interpolation_fraction,
        "gamma_4000": endpoint_4000.interpolation_fraction,
        "laminar_endpoint_Nusselt": endpoint_3000.laminar_endpoint_Nusselt,
        "turbulent_endpoint_Nusselt_at_3000": endpoint_3000.turbulent_endpoint_Nusselt,
        "turbulent_endpoint_Nusselt_at_4000": endpoint_4000.turbulent_endpoint_Nusselt,
        "laminar_to_turbulent_gap_at_3000": (
            endpoint_3000.turbulent_endpoint_Nusselt
            - endpoint_3000.laminar_endpoint_Nusselt
        ),
        "laminar_to_turbulent_gap_at_4000": (
            endpoint_4000.turbulent_endpoint_Nusselt
            - endpoint_4000.laminar_endpoint_Nusselt
        ),
        "arithmetic_interpretation": (
            "Different interpolation fractions and turbulent endpoints produce the "
            "reported model-selection sensitivity; this is not a measured error."
        ),
    }


def build_uncertainty_analysis(base: dict[str, Any]) -> dict[str, Any]:
    candidate_data = build_candidate_analysis(base)
    stations = _station_comparison(candidate_data, base)
    candidates = _candidate_summary(stations)
    boundaries = _boundary_comparison(stations)
    maximum = max(stations, key=lambda row: row["h_l_absolute_relative_difference_percent"])
    controlled = _controlled_curve(maximum, base)
    diagnostics = _boundary_diagnostics(maximum, base)
    active = [row for row in stations if 2300.0 <= row["Reynolds"] < 4000.0]
    signed = [row["h_l_relative_difference_percent"] for row in active]
    absolute = [row["h_l_absolute_relative_difference_percent"] for row in active]
    band_stats = {}
    for label, low, high in (
        ("RE_2300_3000", 2300.0, 3000.0),
        ("RE_3000_4000", 3000.0, 4000.0),
    ):
        subset = [
            row["h_l_relative_difference_percent"]
            for row in stations
            if low <= row["Reynolds"] < high
        ]
        band_stats[label] = {
            "signed_relative_difference_percent": _statistics(subset),
            "absolute_relative_difference_percent": _statistics(
                [abs(value) for value in subset]
            ),
        }
    boundary_effect_3000 = [
        abs(row["model_3000_boundary_effect_percent"]) for row in boundaries
    ]
    boundary_effect_4000 = [
        abs(row["model_4000_boundary_effect_percent"]) for row in boundaries
    ]
    sensitivity_by_boundary = {
        boundary: [
            row["h_l_absolute_relative_difference_percent"]
            for row in active
            if row["thermal_boundary_condition"] == boundary
        ]
        for boundary in BOUNDARIES
    }
    station_type_statistics = {
        station_id: _statistics(
            [
                row["h_l_absolute_relative_difference_percent"]
                for row in active
                if row["station_id"] == station_id
            ]
        )
        for station_id in ("SYN-INLET", "SYN-THROAT", "SYN-EXIT")
    }
    preserved = {}
    for name in ("summary.json", "upper_endpoint_sensitivity.csv"):
        path = PRESERVED_ANALYSIS_DIR / name
        preserved[name] = {
            "path": f"artifacts/cooling_model_b_candidate_analysis/{name}",
            "sha256_before_followup": _sha256(path) if path.exists() else None,
            "overwritten": False,
        }
    summary = {
        "analysis_id": "SPACE_Y_MODEL_B_UNCERTAINTY_ANALYSIS_V1",
        "fluid_classification": FLUID_LABEL,
        "actual_engine_prediction": False,
        "experimental_validation": False,
        "measurement_uncertainty_or_confidence_interval": False,
        "sensitivity_definition": (
            "correlation/model-selection sensitivity, not measured error"
        ),
        "inputs": {
            "total_coolant_mass_flow_kg_s": TOTAL_MASS_FLOW_KG_S,
            "template_count": candidate_data["summary"]["template_count"],
            "unique_geometry_candidate_count": candidate_data["summary"][
                "unique_geometry_candidate_count"
            ],
            "geometrically_valid_candidate_count": candidate_data["summary"][
                "geometrically_valid_candidate_count"
            ],
            "station_count_per_boundary": candidate_data["summary"][
                "valid_station_count_per_scenario"
            ],
            "properties": base["coolant_conditions"]["default_fluid_properties"],
        },
        "preserved_previous_outputs": preserved,
        "maximum_absolute_sensitivity_station": maximum,
        "maximum_endpoint_decomposition": _endpoint_decomposition(maximum, base),
        "station_statistics_active_2300_4000": {
            "signed_relative_difference_percent": _statistics(signed),
            "absolute_relative_difference_percent": _statistics(absolute),
        },
        "station_statistics_by_reynolds_band": band_stats,
        "station_statistics_by_thermal_boundary_condition": {
            boundary: _statistics(values)
            for boundary, values in sensitivity_by_boundary.items()
        },
        "station_statistics_by_station_id": station_type_statistics,
        "sensitivity_correlations_active_2300_4000": {
            "pearson_aspect_ratio_vs_absolute_relative_difference": _pearson(
                [row["aspect_ratio"] for row in active],
                [row["h_l_absolute_relative_difference_percent"] for row in active],
            ),
            "pearson_reynolds_vs_absolute_relative_difference": _pearson(
                [row["Reynolds"] for row in active],
                [row["h_l_absolute_relative_difference_percent"] for row in active],
            ),
            "interpretation_limit": (
                "descriptive association in the synthetic candidate set; not causal evidence"
            ),
        },
        "candidate_statistics": {
            "maximum_absolute_relative_difference_percent": _statistics(
                [row["max_absolute_relative_difference_percent"] for row in candidates]
            ),
            "mean_absolute_relative_difference_percent": _statistics(
                [row["mean_absolute_relative_difference_percent"] for row in candidates]
            ),
        },
        "thermal_boundary_condition_statistics": {
            "model_3000_absolute_boundary_effect_percent": _statistics(
                boundary_effect_3000
            ),
            "model_4000_absolute_boundary_effect_percent": _statistics(
                boundary_effect_4000
            ),
            "verified_physical_bounds": False,
            "comparison_to_endpoint_choice": {
                "mean_absolute_endpoint_effect_active_percent": mean(absolute),
                "mean_absolute_boundary_effect_model_3000_percent": mean(
                    boundary_effect_3000
                ),
                "mean_absolute_boundary_effect_model_4000_percent": mean(
                    boundary_effect_4000
                ),
                "descriptive_result": (
                    "Endpoint-choice sensitivity is larger on average for this synthetic set"
                ),
            },
        },
        "evidence_classification": {
            "literature_based": [
                "Shah-London fully developed rectangular-duct laminar correlations",
                "Gnielinski turbulent Nusselt correlation within supplied domain",
                "Colebrook Darcy friction factor used for turbulent endpoint",
            ],
            "space_y_assumptions": [
                "fully developed quasi-local station application",
                "linear interpolation between laminar and turbulent endpoints",
                "Re=3000 production connection endpoint",
                "constant station properties and equal parallel-channel distribution",
            ],
            "unverified": [
                "actual IPA p,T properties and phase envelope",
                "hydrodynamic and thermal entrance regions",
                "variable-area local heat transfer",
                "nonuniform heating boundary condition",
                "transition friction and pressure loss",
                "experimental accuracy",
            ],
        },
    }
    return {
        "summary": summary,
        "station_rows": stations,
        "candidate_rows": candidates,
        "controlled_rows": controlled,
        "boundary_rows": boundaries,
        "boundary_diagnostics": diagnostics,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _svg_chart(
    path: Path,
    title: str,
    x_label: str,
    y_label: str,
    series: list[tuple[str, str, list[tuple[float, float]], bool]],
) -> None:
    width, height = 1100, 620
    left, right, top, bottom = 95, 35, 60, 95
    points = [point for _, _, data, _ in series for point in data]
    x_values = [point[0] for point in points]
    y_values = [point[1] for point in points]
    x_min, x_max = min(x_values), max(x_values)
    y_min_raw, y_max_raw = min(y_values), max(y_values)
    x_span = x_max - x_min or 1.0
    y_span = y_max_raw - y_min_raw or max(abs(y_max_raw), 1.0)
    x_min -= 0.02 * x_span
    x_max += 0.02 * x_span
    y_min = min(0.0, y_min_raw - 0.08 * y_span)
    y_max = max(0.0, y_max_raw + 0.08 * y_span)
    if y_max == y_min:
        y_max = y_min + 1.0
    sx = lambda value: left + (value - x_min) / (x_max - x_min) * (width - left - right)
    sy = lambda value: height - bottom - (value - y_min) / (y_max - y_min) * (height - top - bottom)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2}" y="30" text-anchor="middle" font-family="Arial" font-size="20">{title}</text>',
        f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#222"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#222"/>',
        f'<text x="{width/2}" y="{height-28}" text-anchor="middle" font-family="Arial">{x_label}</text>',
        f'<text transform="translate(25 {height/2}) rotate(-90)" text-anchor="middle" font-family="Arial">{y_label}</text>',
    ]
    for boundary in (2300.0, 3000.0, 4000.0):
        if x_min <= boundary <= x_max and "Reynolds" in x_label:
            svg.append(
                f'<line x1="{sx(boundary):.2f}" y1="{top}" x2="{sx(boundary):.2f}" y2="{height-bottom}" stroke="#aaa" stroke-dasharray="5 4"/>'
            )
    for index, (label, color, data, connect) in enumerate(series):
        ordered = sorted(data)
        if connect and len(ordered) > 1:
            coordinates = " ".join(f"{sx(x):.2f},{sy(y):.2f}" for x, y in ordered)
            svg.append(
                f'<polyline points="{coordinates}" fill="none" stroke="{color}" stroke-width="2"/>'
            )
        else:
            for x, y in ordered:
                svg.append(
                    f'<circle cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="2.6" fill="{color}" opacity="0.55"/>'
                )
        legend_x = left + (index % 4) * 240
        legend_y = height - 60 + (index // 4) * 18
        svg.append(
            f'<line x1="{legend_x}" y1="{legend_y}" x2="{legend_x+20}" y2="{legend_y}" stroke="{color}" stroke-width="3"/>'
            f'<text x="{legend_x+27}" y="{legend_y+4}" font-family="Arial" font-size="11">{label}</text>'
        )
    svg.append(
        f'<text x="{width-right}" y="48" text-anchor="end" font-family="Arial" font-size="11" fill="#a00">{FLUID_LABEL}; model sensitivity, not measured uncertainty</text>'
    )
    svg.append("</svg>")
    path.write_text("\n".join(svg), encoding="utf-8")


def _graphs(output: Path, data: dict[str, Any]) -> None:
    controlled = data["controlled_rows"]
    boundary = BOUNDARIES[0]
    curve = [row for row in controlled if row["thermal_boundary_condition"] == boundary]
    _svg_chart(
        output / "controlled_nusselt_vs_re.svg",
        "Controlled Nusselt comparison",
        "Reynolds number",
        "Nusselt number",
        [
            ("Model B Re=3000", "#1f77b4", [(r["Reynolds"], r["model_3000_Nusselt"]) for r in curve], True),
            ("Sensitivity Re=4000", "#d62728", [(r["Reynolds"], r["model_4000_Nusselt"]) for r in curve], True),
        ],
    )
    _svg_chart(
        output / "controlled_h_l_vs_re.svg",
        "Controlled h_l comparison",
        "Reynolds number",
        "h_l (W m^-2 K^-1)",
        [
            ("Model B Re=3000", "#1f77b4", [(r["Reynolds"], r["model_3000_h_l_W_m2K"]) for r in curve], True),
            ("Sensitivity Re=4000", "#d62728", [(r["Reynolds"], r["model_4000_h_l_W_m2K"]) for r in curve], True),
        ],
    )
    _svg_chart(
        output / "controlled_relative_difference_vs_re.svg",
        "Controlled relative model difference",
        "Reynolds number",
        "(h4000-h3000)/h3000 (%)",
        [("Relative difference", "#9467bd", [(r["Reynolds"], r["h_l_relative_difference_percent"]) for r in curve], True)],
    )
    _svg_chart(
        output / "controlled_slope_vs_re.svg",
        "Controlled Nusselt slope comparison",
        "Reynolds number",
        "dNu/dRe",
        [
            ("Model B Re=3000", "#1f77b4", [(r["Reynolds"], r["dNu_dRe_model_3000"]) for r in curve if r["dNu_dRe_model_3000"] is not None], True),
            ("Sensitivity Re=4000", "#d62728", [(r["Reynolds"], r["dNu_dRe_model_4000"]) for r in curve if r["dNu_dRe_model_4000"] is not None], True),
        ],
    )
    candidates = sorted(data["candidate_rows"], key=lambda row: row["max_absolute_relative_difference_percent"])
    _svg_chart(
        output / "candidate_max_sensitivity.svg",
        "Candidate maximum model sensitivity",
        "Candidate rank by sensitivity",
        "Maximum absolute relative difference (%)",
        [("156 valid candidates", "#2ca02c", [(i + 1, row["max_absolute_relative_difference_percent"]) for i, row in enumerate(candidates)], True)],
    )
    stations = data["station_rows"]
    _svg_chart(
        output / "station_sensitivity_vs_re.svg",
        "Station model sensitivity",
        "Reynolds number",
        "h_l relative difference (%)",
        [
            ("Nu_T", "#1f77b4", [(r["Reynolds"], r["h_l_relative_difference_percent"]) for r in stations if r["thermal_boundary_condition"] == BOUNDARIES[0]], False),
            ("Nu_H1", "#ff7f0e", [(r["Reynolds"], r["h_l_relative_difference_percent"]) for r in stations if r["thermal_boundary_condition"] == BOUNDARIES[1]], False),
        ],
    )
    _svg_chart(
        output / "aspect_ratio_sensitivity.svg",
        "Aspect-ratio model sensitivity",
        "Rectangular aspect ratio min(w,h)/max(w,h)",
        "Absolute relative difference (%)",
        [
            ("Nu_T", "#1f77b4", [(r["aspect_ratio"], r["h_l_absolute_relative_difference_percent"]) for r in stations if r["thermal_boundary_condition"] == BOUNDARIES[0]], False),
            ("Nu_H1", "#ff7f0e", [(r["aspect_ratio"], r["h_l_absolute_relative_difference_percent"]) for r in stations if r["thermal_boundary_condition"] == BOUNDARIES[1]], False),
        ],
    )
    boundary_rows = data["boundary_rows"]
    _svg_chart(
        output / "boundary_condition_sensitivity.svg",
        "Thermal-boundary-condition sensitivity",
        "Reynolds number",
        "(H1-T)/T (%)",
        [
            ("Model B Re=3000", "#1f77b4", [(r["Reynolds"], r["model_3000_boundary_effect_percent"]) for r in boundary_rows], False),
            ("Sensitivity Re=4000", "#d62728", [(r["Reynolds"], r["model_4000_boundary_effect_percent"]) for r in boundary_rows], False),
        ],
    )


def write_analysis(output: Path, data: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "uncertainty_summary.json").write_text(
        json.dumps(data["summary"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (output / "boundary_continuity.json").write_text(
        json.dumps(data["boundary_diagnostics"], indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    _write_csv(output / "station_model_comparison.csv", data["station_rows"])
    _write_csv(output / "candidate_sensitivity_summary.csv", data["candidate_rows"])
    _write_csv(output / "reynolds_controlled_comparison.csv", data["controlled_rows"])
    _write_csv(output / "boundary_condition_comparison.csv", data["boundary_rows"])
    _graphs(output, data)


def main() -> None:
    base = json.loads(BASE_INPUT.read_text(encoding="utf-8"))
    data = build_uncertainty_analysis(base)
    write_analysis(OUTPUT_DIR, data)
    print(json.dumps(data["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
