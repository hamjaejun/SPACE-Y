#!/usr/bin/env python3
"""Full provided-engine verification for the existing SPACE Y Model H V1."""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import csv
import json
import math
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from typing import Any
import warnings


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from cooling_model_b import (
    LAMINAR_MODEL_THERMAL_ENTRY,
    coolant_path_distances,
    evaluate_model_b_hl,
    generate_channel_candidates,
)
from model_h_engine_integration import (
    PROPERTY_POLICY_ALLOW_EXTRAPOLATION,
    assess_supplied_ipa_states,
    evaluate_candidate_key_by_angle,
    evaluate_supplied_ipa_state,
    existing_loop_segment_length_factor,
)
from model_h_v1 import (
    MODEL_H_HEIGHT_CANDIDATES_MM,
    MODEL_H_HELIX_ANGLES_DEG,
    evaluate_model_h_candidates,
    evaluate_model_h_hl,
    generate_model_h_candidates,
)
from verify_model_h_engine_integration import (
    REQUIRED_FILES,
    _find_bundle_root,
    _run_supplied_engine,
    _safe_extract,
    _sha256,
)


H_L_ONLY_SENSITIVITY = "H_L_ONLY_SENSITIVITY"
H_GEOMETRY_COUPLED_EXISTING_LOOP = "H_GEOMETRY_COUPLED_EXISTING_LOOP"
COOLING_HL_MODELS = {"B", "H"}
EXECUTION_MODES = {H_L_ONLY_SENSITIVITY, H_GEOMETRY_COUPLED_EXISTING_LOOP}


def _run_repository_tests() -> dict[str, Any]:
    command = [sys.executable, "-m", "unittest", "discover", "-s", "tests", "-q"]
    completed = subprocess.run(
        command,
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    combined = "\n".join(part for part in (completed.stdout, completed.stderr) if part)
    match = re.search(r"Ran\s+(\d+)\s+tests?", combined)
    return {
        "command": ".venv\\Scripts\\python.exe -m unittest discover -s tests -q",
        "exit_code": completed.returncode,
        "test_count": int(match.group(1)) if match else None,
        "passed": completed.returncode == 0 and "OK" in combined,
    }


def _candidate_key(candidate: dict[str, Any]) -> tuple[int, float, float]:
    return (
        int(candidate["channel_count"]),
        float(candidate["rib_thickness_m"]),
        float(candidate["channel_height_m"]),
    )


def _candidate_for(
    candidates: list[dict[str, Any]],
    key: tuple[int, float, float],
    angle_deg: float,
) -> dict[str, Any]:
    count, rib, height = key
    for candidate in candidates:
        if (
            int(candidate["channel_count"]) == count
            and math.isclose(float(candidate["rib_thickness_m"]), rib, abs_tol=1.0e-12)
            and math.isclose(float(candidate["channel_height_m"]), height, abs_tol=1.0e-12)
            and math.isclose(float(candidate["helix_angle_deg"]), angle_deg, abs_tol=1.0e-12)
        ):
            return candidate
    raise ValueError(f"candidate {key} at beta={angle_deg:g} deg was not generated")


def _first_all_angle_valid_key(candidates: list[dict[str, Any]]) -> tuple[int, float, float]:
    groups: dict[tuple[int, float, float], list[dict[str, Any]]] = defaultdict(list)
    for candidate in candidates:
        groups[_candidate_key(candidate)].append(candidate)
    required = set(MODEL_H_HELIX_ANGLES_DEG)
    eligible = [
        key
        for key, group in groups.items()
        if {float(item["helix_angle_deg"]) for item in group} == required
        and all(item["valid"] for item in group)
    ]
    if not eligible:
        raise ValueError("no candidate passes the supplied 1D geometry limits at all angles")
    return sorted(eligible)[0]


def _channel_for_cooling_loop(candidate: dict[str, Any]) -> dict[str, Any]:
    stations = candidate["stations"]
    return {
        "id": candidate["candidate_id"],
        "N": int(candidate["channel_count"]),
        "rib": float(candidate["rib_thickness_m"]),
        "height": float(candidate["channel_height_m"]),
        "r_m": np.array([float(row["mean_channel_radius_m"]) for row in stations]),
        "width": np.array([float(row["channel_width_m"]) for row in stations]),
        "area": np.array([float(row["total_flow_area_m2"]) for row in stations]),
        "D_h": np.array([float(row["hydraulic_diameter_m"]) for row in stations]),
    }


def _run_existing_cooling_loop_case(
    *,
    scenario_id: str,
    namespace: dict[str, Any],
    candidate: dict[str, Any],
    cooling_hl_model: str,
    helix_angle_deg: float,
    execution_mode: str,
) -> dict[str, Any]:
    """Call the supplied cooling loop without changing any heat-balance equation."""

    if cooling_hl_model not in COOLING_HL_MODELS:
        raise ValueError("COOLING_HL_MODEL must be B or H")
    if execution_mode not in EXECUTION_MODES:
        raise ValueError("unsupported cooling-loop execution mode")
    if cooling_hl_model == "B" and helix_angle_deg != 0.0:
        raise ValueError("Model B comparison is defined at beta=0 deg")
    if not candidate["valid"]:
        return {
            "scenario_id": scenario_id,
            "cooling_hl_model": cooling_hl_model,
            "helix_angle_deg": helix_angle_deg,
            "execution_mode": execution_mode,
            "status": "OUTSIDE",
            "reason": "candidate violates supplied 1D geometry constraints",
            "station_rows": [],
        }

    stn = namespace["stn"]
    ipa = namespace["ipa"]
    cooling_loop = namespace["cl"]
    channel = _channel_for_cooling_loop(candidate)
    paths = namespace["model_h_paths"][float(helix_angle_deg)]
    pressure = float(namespace["P_COOL_IN"])
    local_by_station: dict[int, dict[str, Any]] = {}

    def calc_h_l(index: int, coolant_temperature_K: float) -> float:
        state = evaluate_supplied_ipa_state(
            temperature_K=coolant_temperature_K,
            pressure_Pa_abs=pressure,
            ipa=ipa,
            policy=PROPERTY_POLICY_ALLOW_EXTRAPOLATION,
        )
        if state["calculation_status"] != "MODEL_U":
            raise RuntimeError(f"IPA {state['status']}: {state['reason']}")
        if cooling_hl_model == "B":
            local = evaluate_model_b_hl(
                mdot_total_kg_s=float(namespace["mdot_cool"]),
                channel_count=channel["N"],
                width_m=float(channel["width"][index]),
                height_m=channel["height"],
                viscosity_Pa_s=state["viscosity_Pa_s"],
                cp_J_kgK=state["cp_J_kgK"],
                conductivity_W_mK=state["conductivity_W_mK"],
                roughness_m=float(namespace["ROUGHNESS"]),
                thermal_boundary_condition=str(namespace["THERMAL_BC"]),
                fully_developed=True,
                colebrook_re_range=tuple(namespace["COLEBROOK_RE_RANGE"]),
                colebrook_relative_roughness_range=tuple(namespace["COLEBROOK_RR_RANGE"]),
                laminar_model=str(namespace["LAMINAR_MODEL"]),
                s_m=paths["s_B_m"][index],
                first_segment_length_m=paths["entry_segment_length_B_m"][index],
            )
        else:
            local = evaluate_model_h_hl(
                mdot_total_kg_s=float(namespace["mdot_cool"]),
                density_kg_m3=state["density_kg_m3"],
                mean_channel_radius_m=float(channel["r_m"][index]),
                channel_count=channel["N"],
                rib_thickness_m=channel["rib"],
                channel_height_m=channel["height"],
                helix_angle_deg=helix_angle_deg,
                viscosity_Pa_s=state["viscosity_Pa_s"],
                cp_J_kgK=state["cp_J_kgK"],
                conductivity_W_mK=state["conductivity_W_mK"],
                roughness_m=float(namespace["ROUGHNESS"]),
                thermal_boundary_condition=str(namespace["THERMAL_BC"]),
                fully_developed=True,
                colebrook_re_range=tuple(namespace["COLEBROOK_RE_RANGE"]),
                colebrook_relative_roughness_range=tuple(namespace["COLEBROOK_RR_RANGE"]),
                laminar_model=str(namespace["LAMINAR_MODEL"]),
                s_H_m=paths["s_H_m"][index],
                first_segment_length_H_m=paths["entry_segment_length_H_m"][index],
            )
        local_by_station[index] = {
            **state,
            **local,
            "property_range_status": state["status"],
        }
        return float(local["h_l_W_m2K"])

    path_factor = (
        float(paths["path_length_factor"])
        if execution_mode == H_GEOMETRY_COUPLED_EXISTING_LOOP
        else 1.0
    )
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            with existing_loop_segment_length_factor(cooling_loop, path_factor):
                result = cooling_loop.run_cooling(
                    stn,
                    channel,
                    namespace["calc_h_g"],
                    calc_h_l,
                    mdot_cool=float(namespace["mdot_cool"]),
                    T_in=float(namespace["T_FUEL"]),
                    p_in=pressure,
                    t_wall=float(namespace["T_WALL"]),
                    k_wall=namespace["K_WALL"],
                    T_wall_limit=float(namespace["T_WALL_MAX"]),
                    sat_margin=float(namespace["SAT_MARGIN"]),
                    verbose=False,
                )
    except Exception as error:
        return {
            "scenario_id": scenario_id,
            "cooling_hl_model": cooling_hl_model,
            "helix_angle_deg": helix_angle_deg,
            "execution_mode": execution_mode,
            "status": "NUM_U",
            "reason": str(error),
            "path_length_factor_in_area": path_factor,
            "station_rows": [],
        }

    station_rows: list[dict[str, Any]] = []
    array_names = [
        "T_wg",
        "T_wl",
        "T_l",
        "q",
        "h_g",
        "h_l",
        "h_l_eff",
        "eta_fin",
        "k_wall",
    ]
    for index, x_value in enumerate(stn["x"]):
        local = local_by_station.get(index, {})
        row = {
            "scenario_id": scenario_id,
            "execution_mode": execution_mode,
            "cooling_hl_model": cooling_hl_model,
            "candidate_id": candidate["candidate_id"],
            "station_index": index,
            "x_m": float(x_value),
            "helix_angle_deg": helix_angle_deg,
            "segment_length_B_m": paths["segment_length_B_m"][index],
            "segment_length_H_m": paths["segment_length_H_m"][index],
            "s_B_m": paths["s_B_m"][index],
            "s_H_m": paths["s_H_m"][index],
            "property_range_status": local.get("property_range_status"),
            "Reynolds": local.get("Reynolds"),
            "Prandtl": local.get("Prandtl"),
            "Nusselt": local.get("Nusselt"),
            "flow_regime": local.get("flow_regime"),
            "correlation_id": local.get("correlation_id"),
        }
        for name in array_names:
            value = result[name][index]
            row[name] = None if not np.isfinite(value) else float(value)
        station_rows.append(row)

    extrapolated_count = sum(
        row["property_range_status"] == "EXTRAPOLATED" for row in station_rows
    )
    applicability_failed = bool(
        result["failed"]
        or not result["ok_wall"]
        or not result["ok_boil"]
        or result["local_boiling"]
    )
    reason = str(result["fail_reason"] or "")
    if applicability_failed and not reason:
        reason = "existing cooling-loop wall-temperature or boiling check failed"
    return {
        "scenario_id": scenario_id,
        "cooling_hl_model": cooling_hl_model,
        "helix_angle_deg": helix_angle_deg,
        "execution_mode": execution_mode,
        "status": "OUTSIDE" if applicability_failed else "MODEL_U",
        "reason": reason,
        "path_length_factor_in_area": path_factor,
        "h_l_length_multiplier_applied": False,
        "coolant_outlet_temperature_K": float(result["T_out"]),
        "maximum_gas_side_wall_temperature_K": float(result["T_wg_max"]),
        "maximum_wall_temperature_x_m": float(result["x_T_wg_max"]),
        "total_heat_W": float(result["Q_total"]),
        "source_wall_ok": bool(result["ok_wall"]),
        "source_boil_ok": bool(result["ok_boil"]),
        "source_local_boiling_flag": bool(result["local_boiling"]),
        "extrapolated_property_station_count": extrapolated_count,
        "station_rows": station_rows,
        "experimental_validation": False,
        "validated_engine_prediction": False,
    }


def _write_full_candidate_csvs(
    *,
    output_directory: Path,
    result: dict[str, Any],
    temperatures: list[float | None],
    pressure_Pa_abs: float,
    properties: dict[str, Any],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    station_columns = [
        "candidate_id",
        "base_model_b_candidate_id",
        "helix_angle_deg",
        "station_id",
        "station_index",
        "x_m",
        "channel_count",
        "rib_thickness_m",
        "channel_height_m",
        "mean_channel_radius_m",
        "channel_width_m",
        "total_flow_area_m2",
        "hydraulic_diameter_m",
        "s_H_m",
        "coolant_temperature_K",
        "coolant_pressure_Pa_abs",
        "density_kg_m3",
        "viscosity_Pa_s",
        "cp_J_kgK",
        "conductivity_W_mK",
        "Reynolds",
        "Prandtl",
        "Nusselt",
        "h_l_W_m2K",
        "flow_regime",
        "correlation_id",
        "geometric_validity_status",
        "property_validity_status",
        "property_range_status",
        "status",
        "reason",
    ]
    station_path = output_directory / "model_h_station_results.csv"
    summary_rows: list[dict[str, Any]] = []
    global_status_counts: Counter[str] = Counter()
    with station_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=station_columns)
        writer.writeheader()
        for candidate in result["candidates"]:
            h_values: list[float] = []
            re_values: list[float] = []
            regimes: Counter[str] = Counter()
            statuses: Counter[str] = Counter()
            for station in candidate["stations"]:
                index = int(station["station_index"])
                status = str(station["status"])
                statuses[status] += 1
                global_status_counts[status] += 1
                if station.get("h_l_W_m2K") is not None:
                    h_values.append(float(station["h_l_W_m2K"]))
                if station.get("Reynolds") is not None:
                    re_values.append(float(station["Reynolds"]))
                if station.get("flow_regime") is not None:
                    regimes[str(station["flow_regime"])] += 1
                writer.writerow(
                    {
                        **{name: station.get(name) for name in station_columns},
                        "candidate_id": candidate["candidate_id"],
                        "base_model_b_candidate_id": candidate["base_model_b_candidate_id"],
                        "helix_angle_deg": candidate["helix_angle_deg"],
                        "channel_count": candidate["channel_count"],
                        "rib_thickness_m": candidate["rib_thickness_m"],
                        "channel_height_m": candidate["channel_height_m"],
                        "coolant_temperature_K": temperatures[index],
                        "coolant_pressure_Pa_abs": pressure_Pa_abs,
                        "density_kg_m3": properties["density_kg_m3"][index],
                        "viscosity_Pa_s": properties["viscosity_Pa_s"][index],
                        "cp_J_kgK": properties["cp_J_kgK"][index],
                        "conductivity_W_mK": properties["conductivity_W_mK"][index],
                        "property_range_status": properties[
                            "property_range_status_by_station"
                        ][index],
                    }
                )
            summary_rows.append(
                {
                    "candidate_id": candidate["candidate_id"],
                    "base_model_b_candidate_id": candidate["base_model_b_candidate_id"],
                    "helix_angle_deg": candidate["helix_angle_deg"],
                    "channel_count": candidate["channel_count"],
                    "rib_thickness_m": candidate["rib_thickness_m"],
                    "channel_height_m": candidate["channel_height_m"],
                    "geometry_valid": candidate["valid"],
                    "candidate_status": candidate["status"],
                    "total_station_count": len(candidate["stations"]),
                    "calculated_station_count": len(h_values),
                    "outside_station_count": statuses["OUTSIDE"],
                    "input_u_station_count": statuses["INPUT_U"],
                    "num_u_station_count": statuses["NUM_U"],
                    "minimum_h_l_W_m2K": min(h_values) if h_values else None,
                    "maximum_h_l_W_m2K": max(h_values) if h_values else None,
                    "mean_h_l_W_m2K": float(np.mean(h_values)) if h_values else None,
                    "minimum_Reynolds": min(re_values) if re_values else None,
                    "maximum_Reynolds": max(re_values) if re_values else None,
                    "laminar_station_count": regimes["LAMINAR"],
                    "transition_station_count": regimes["TRANSITION_MODEL_B"],
                    "turbulent_station_count": regimes["TURBULENT"],
                    "geometry_exclusion_reasons": " | ".join(
                        sorted(
                            {
                                reason.split(": ", 1)[-1]
                                for reason in candidate["geometry_exclusion_reasons"]
                            }
                        )
                    ),
                }
            )
    summary_path = output_directory / "model_h_candidate_summary.csv"
    with summary_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(summary_rows[0]))
        writer.writeheader()
        writer.writerows(summary_rows)
    return summary_rows, dict(global_status_counts)


def _write_wall_csv(path: Path, cases: list[dict[str, Any]]) -> None:
    rows = [row for case in cases for row in case.get("station_rows", [])]
    if not rows:
        return
    columns: list[str] = []
    for row in rows:
        columns.extend(name for name in row if name not in columns)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def _beta_zero_residuals(
    *,
    namespace: dict[str, Any],
    geometry: dict[str, Any],
    key: tuple[int, float, float],
    properties: dict[str, Any],
) -> dict[str, Any]:
    h_candidate = _candidate_for(geometry["candidates"], key, 0.0)
    base = generate_channel_candidates(
        x_m=namespace["stn"]["x"],
        gas_side_radius_m=namespace["stn"]["r"],
        wall_thickness_m=float(namespace["T_WALL"]),
        throat_station_index=int(namespace["stn"]["i_throat"]),
        channel_width_limits_m=tuple(namespace["CH_WIDTH_LIMITS"]),
        minimum_rib_thickness_m=float(namespace["CH_MIN_RIB"]),
        allowed_channel_heights_m=[value * 1.0e-3 for value in MODEL_H_HEIGHT_CANDIDATES_MM],
        width_candidates_mm=[0.8, 0.9, 1.2, 1.5, 2.0, 2.7, 3.0],
        rib_candidates_mm=[0.9, 1.0, 1.2, 1.5],
        height_candidates_mm=[1.2, 1.5, 2.0, 3.0, 4.0, 5.0],
    )
    base_candidate = next(item for item in base["candidates"] if _candidate_key(item) == key)
    names = {
        "channel_width_m": "channel_width_m",
        "hydraulic_diameter_m": "hydraulic_diameter_m",
        "total_flow_area_m2": "flow_area_m2",
    }
    residuals = {name: 0.0 for name in names}
    for h_station, b_station in zip(h_candidate["stations"], base_candidate["stations"]):
        for output_name, base_name in names.items():
            residuals[output_name] = max(
                residuals[output_name],
                abs(float(h_station[output_name]) - float(b_station[base_name])),
            )
    radii = [float(row["mean_channel_radius_m"]) for row in h_candidate["stations"]]
    path_h = namespace["model_h_paths"][0.0]
    path_b = coolant_path_distances(namespace["stn"]["x"], radii)
    residuals["coolant_path_distance_m"] = max(
        abs(float(a) - float(b)) for a, b in zip(path_h["s_H_m"], path_b["s_m"])
    )
    sensitivity = evaluate_candidate_key_by_angle(
        candidates=geometry["candidates"],
        x_m=namespace["stn"]["x"],
        candidate_key=key,
        mdot_total_kg_s=float(namespace["mdot_cool"]),
        properties=properties,
        roughness_m=float(namespace["ROUGHNESS"]),
        thermal_boundary_condition=str(namespace["THERMAL_BC"]),
        fully_developed=True,
        colebrook_re_range=tuple(namespace["COLEBROOK_RE_RANGE"]),
        colebrook_relative_roughness_range=tuple(namespace["COLEBROOK_RR_RANGE"]),
        laminar_model=str(namespace["LAMINAR_MODEL"]),
    )
    residuals.update(sensitivity["beta_zero_model_b_regression"]["maximum_absolute_residuals"])
    return {
        "comparison_count": sensitivity["beta_zero_model_b_regression"]["comparison_count"],
        "maximum_absolute_residuals": residuals,
        "pass": all(value == 0.0 for value in residuals.values()),
    }


def _plot_line_cases(
    *,
    output_directory: Path,
    scenario_id: str,
    cases: list[dict[str, Any]],
    field: str,
    ylabel: str,
    filename: str,
    execution_mode: str = H_GEOMETRY_COUPLED_EXISTING_LOOP,
) -> None:
    selected = [
        case
        for case in cases
        if case.get("scenario_id") == scenario_id
        and case.get("cooling_hl_model") == "H"
        and case.get("execution_mode") == execution_mode
        and case.get("station_rows")
    ]
    if not selected:
        return
    fig, ax = plt.subplots(figsize=(8.5, 4.8))
    for case in selected:
        rows = case["station_rows"]
        x = [row["x_m"] * 1.0e3 for row in rows]
        y = [row.get(field) for row in rows]
        ax.plot(x, y, label=f"β={case['helix_angle_deg']:g}°")
    ax.set_xlabel("Axial position x [mm]")
    ax.set_ylabel(ylabel)
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_directory / filename, dpi=180)
    plt.close(fig)


def _plot_candidate_h_summary(output_directory: Path, rows: list[dict[str, Any]]) -> None:
    valid = [row for row in rows if row["mean_h_l_W_m2K"] is not None]
    fig, ax = plt.subplots(figsize=(9.0, 5.0))
    colors = {0.0: "#1f77b4", 20.0: "#2ca02c", 30.0: "#ff7f0e", 35.0: "#d62728"}
    for angle in MODEL_H_HELIX_ANGLES_DEG:
        group = [row for row in valid if float(row["helix_angle_deg"]) == angle]
        ax.scatter(
            [row["mean_h_l_W_m2K"] for row in group],
            [row["maximum_h_l_W_m2K"] for row in group],
            s=10,
            alpha=0.65,
            label=f"β={angle:g}°",
            color=colors[angle],
        )
    ax.set_xlabel("Candidate mean h_l [W/m²K]")
    ax.set_ylabel("Candidate maximum h_l [W/m²K]")
    ax.grid(True, alpha=0.25)
    ax.legend()
    fig.tight_layout()
    fig.savefig(output_directory / "candidate_mean_vs_max_h_l.png", dpi=180)
    plt.close(fig)


def _scenario_summary(namespace: dict[str, Any], scenario_id: str) -> dict[str, Any]:
    row = namespace["cea_row"]
    baseline = namespace["res"]
    return {
        "scenario_id": scenario_id,
        "mdot_total_kg_s": float(row["mdot_total[kg/s]"]),
        "mdot_coolant_kg_s": float(row["mdot_coolant[kg/s]"]),
        "chamber_pressure_bar_abs": float(row["p_c[bar]"]),
        "of_ratio": float(row["OF[-]"]),
        "fuel_temperature_K": float(namespace["T_FUEL"]),
        "oxidizer_temperature_K": float(namespace["T_OXIDIZER"]),
        "station_count": len(namespace["stn"]["x"]),
        "throat_station_index": int(namespace["stn"]["i_throat"]),
        "provided_model_b_coolant_outlet_temperature_K": float(baseline["T_out"]),
        "provided_model_b_maximum_wall_temperature_K": float(baseline["T_wg_max"]),
        "validated_design_condition": False,
    }


def run(engine_zip: Path, output_directory: Path) -> dict[str, Any]:
    engine_zip = engine_zip.resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    build_root = ROOT / ".codex-build"
    build_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="model_h_full_", dir=build_root) as temporary:
        extracted = Path(temporary)
        _safe_extract(engine_zip, extracted)
        bundle = _find_bundle_root(extracted)
        source_hashes = {name: _sha256(bundle / name) for name in sorted(REQUIRED_FILES)}

        baseline_ns, baseline_log = _run_supplied_engine(bundle)
        ppt_ns, ppt_log = _run_supplied_engine(
            bundle,
            assignment_overrides={"P_C": "[30]", "OF": "[3.0]", "M_DOT": "[1.6]"},
        )
        scenarios = {
            "PROVIDED_ENGINE_25BAR_OF2P5": baseline_ns,
            "PPT_PARTIAL_30BAR_OF3P0": ppt_ns,
        }

        all_wall_cases: list[dict[str, Any]] = []
        scenario_records: dict[str, Any] = {}
        full_result: dict[str, Any] | None = None
        full_summary_rows: list[dict[str, Any]] = []
        full_station_status_counts: dict[str, int] = {}
        beta_zero_regression: dict[str, Any] | None = None

        for scenario_id, namespace in scenarios.items():
            stn = namespace["stn"]
            geometry = generate_model_h_candidates(
                x_m=stn["x"],
                gas_side_radius_m=stn["r"],
                wall_thickness_m=float(namespace["T_WALL"]),
                throat_station_index=int(stn["i_throat"]),
                channel_width_limits_m=tuple(namespace["CH_WIDTH_LIMITS"]),
                minimum_rib_thickness_m=float(namespace["CH_MIN_RIB"]),
                allowed_channel_heights_m=[value * 1.0e-3 for value in MODEL_H_HEIGHT_CANDIDATES_MM],
                station_ids=[f"{scenario_id}-S{index:04d}" for index in range(len(stn["x"]))],
            )
            requested_key = (34, 1.5e-3, 1.5e-3)
            try:
                requested_candidates = [
                    _candidate_for(geometry["candidates"], requested_key, angle)
                    for angle in MODEL_H_HELIX_ANGLES_DEG
                ]
                requested_valid = all(item["valid"] for item in requested_candidates)
            except ValueError:
                requested_valid = False
            reference_key = requested_key if requested_valid else _first_all_angle_valid_key(geometry["candidates"])
            reference_candidates = [
                _candidate_for(geometry["candidates"], reference_key, angle)
                for angle in MODEL_H_HELIX_ANGLES_DEG
            ]
            reference_radii = [
                float(row["mean_channel_radius_m"])
                for row in reference_candidates[0]["stations"]
            ]
            namespace["model_h_paths"] = {
                float(angle): __import__("model_h_v1").helical_coolant_path_distances(
                    stn["x"], reference_radii, float(angle)
                )
                for angle in MODEL_H_HELIX_ANGLES_DEG
            }

            cases = [
                _run_existing_cooling_loop_case(
                    scenario_id=scenario_id,
                    namespace=namespace,
                    candidate=reference_candidates[0],
                    cooling_hl_model="B",
                    helix_angle_deg=0.0,
                    execution_mode=H_L_ONLY_SENSITIVITY,
                )
            ]
            for mode in (H_L_ONLY_SENSITIVITY, H_GEOMETRY_COUPLED_EXISTING_LOOP):
                for candidate in reference_candidates:
                    cases.append(
                        _run_existing_cooling_loop_case(
                            scenario_id=scenario_id,
                            namespace=namespace,
                            candidate=candidate,
                            cooling_hl_model="H",
                            helix_angle_deg=float(candidate["helix_angle_deg"]),
                            execution_mode=mode,
                        )
                    )
            all_wall_cases.extend(cases)
            scenario_records[scenario_id] = {
                **_scenario_summary(namespace, scenario_id),
                "requested_reference_candidate_key": {
                    "channel_count": requested_key[0],
                    "rib_thickness_m": requested_key[1],
                    "channel_height_m": requested_key[2],
                },
                "requested_reference_valid_at_all_angles": requested_valid,
                "used_reference_candidate_key": {
                    "channel_count": reference_key[0],
                    "rib_thickness_m": reference_key[1],
                    "channel_height_m": reference_key[2],
                },
                "geometry_counts": {
                    "template_count": geometry["template_count"],
                    "base_unique_candidate_count": geometry["base_unique_candidate_count"],
                    "angle_expanded_candidate_count": geometry["angle_expanded_candidate_count"],
                    "valid_candidate_count": geometry["valid_candidate_count"],
                    "invalid_candidate_count": geometry["invalid_candidate_count"],
                },
                "wall_case_summaries": [
                    {key: value for key, value in case.items() if key != "station_rows"}
                    for case in cases
                ],
            }

            if scenario_id == "PROVIDED_ENGINE_25BAR_OF2P5":
                temperature_profile = [
                    None if value != value else float(value) for value in namespace["res"]["T_l"]
                ]
                properties = assess_supplied_ipa_states(
                    temperature_K=temperature_profile,
                    pressure_Pa_abs=[float(namespace["P_COOL_IN"])] * len(temperature_profile),
                    ipa=namespace["ipa"],
                )
                full_result = evaluate_model_h_candidates(
                    x_m=stn["x"],
                    gas_side_radius_m=stn["r"],
                    wall_thickness_m=float(namespace["T_WALL"]),
                    throat_station_index=int(stn["i_throat"]),
                    mdot_total_kg_s=float(namespace["mdot_cool"]),
                    density_kg_m3=properties["density_kg_m3"],
                    viscosity_Pa_s=properties["viscosity_Pa_s"],
                    cp_J_kgK=properties["cp_J_kgK"],
                    conductivity_W_mK=properties["conductivity_W_mK"],
                    roughness_m=float(namespace["ROUGHNESS"]),
                    thermal_boundary_condition=str(namespace["THERMAL_BC"]),
                    fully_developed=True,
                    colebrook_re_range=tuple(namespace["COLEBROOK_RE_RANGE"]),
                    colebrook_relative_roughness_range=tuple(namespace["COLEBROOK_RR_RANGE"]),
                    channel_width_limits_m=tuple(namespace["CH_WIDTH_LIMITS"]),
                    minimum_rib_thickness_m=float(namespace["CH_MIN_RIB"]),
                    allowed_channel_heights_m=[value * 1.0e-3 for value in MODEL_H_HEIGHT_CANDIDATES_MM],
                    station_ids=[f"ENG-S{index:04d}" for index in range(len(stn["x"]))],
                    laminar_model=LAMINAR_MODEL_THERMAL_ENTRY,
                    fluid_identity="2-propanol (IPA), CAS 67-63-0",
                    property_data_classification="SUPPLIED_T_ONLY_CORRELATIONS_MODEL_U",
                    property_validity_status=properties["status_by_station"],
                )
                full_summary_rows, full_station_status_counts = _write_full_candidate_csvs(
                    output_directory=output_directory,
                    result=full_result,
                    temperatures=temperature_profile,
                    pressure_Pa_abs=float(namespace["P_COOL_IN"]),
                    properties=properties,
                )
                beta_zero_regression = _beta_zero_residuals(
                    namespace=namespace,
                    geometry=geometry,
                    key=reference_key,
                    properties=properties,
                )

            for field, ylabel, filename in (
                ("h_l", "Coolant-side h_l [W/m²K]", f"{scenario_id}_h_l_by_angle.png"),
                ("Reynolds", "Reynolds number [-]", f"{scenario_id}_Re_by_angle.png"),
                ("Nusselt", "Nusselt number [-]", f"{scenario_id}_Nu_by_angle.png"),
                ("T_wg", "Gas-side wall temperature [K]", f"{scenario_id}_wall_temperature_by_angle.png"),
                ("T_l", "Coolant bulk temperature [K]", f"{scenario_id}_coolant_temperature_by_angle.png"),
            ):
                _plot_line_cases(
                    output_directory=output_directory,
                    scenario_id=scenario_id,
                    cases=cases,
                    field=field,
                    ylabel=ylabel,
                    filename=filename,
                )

        _write_wall_csv(output_directory / "model_h_wall_temperature_results.csv", all_wall_cases)
        _plot_candidate_h_summary(output_directory, full_summary_rows)

        ppt_reference_rows = [
            {
                "source": "SPACE_Y_R2S_Model_B_Entry_V1_Briefing.pptx slide 4",
                "location": "throat",
                "x_mm": 387.5,
                "reported_h_l_W_m2K": 12292,
                "reported_T_wg_K": 698,
                "reproduction_status": "INPUT_U",
                "reason": "complete source geometry, channel and thermal inputs are not embedded in the deck",
            },
            {
                "source": "SPACE_Y_R2S_Model_B_Entry_V1_Briefing.pptx slide 4",
                "location": "maximum wall temperature",
                "x_mm": 420.0,
                "reported_h_l_W_m2K": 2391,
                "reported_T_wg_K": 736,
                "reproduction_status": "INPUT_U",
                "reason": "complete source geometry, channel and thermal inputs are not embedded in the deck",
            },
            {
                "source": "SPACE_Y_R2S_Model_B_Entry_V1_Briefing.pptx slide 4",
                "location": "nozzle exit",
                "x_mm": 439.5,
                "reported_h_l_W_m2K": 3702,
                "reported_T_wg_K": 575,
                "reproduction_status": "INPUT_U",
                "reason": "complete source geometry, channel and thermal inputs are not embedded in the deck",
            },
        ]
        with (output_directory / "ppt_reference_comparison.csv").open(
            "w", encoding="utf-8-sig", newline=""
        ) as stream:
            writer = csv.DictWriter(stream, fieldnames=list(ppt_reference_rows[0]))
            writer.writeheader()
            writer.writerows(ppt_reference_rows)

        generated_files = {
            path.name for path in output_directory.iterdir() if path.is_file()
        }
        generated_files.add("model_h_verification_summary.json")
        verification = {
            "run_classification": "PROVIDED_ENGINE_AND_PPT_PARTIAL_SCENARIOS_MODEL_U",
            "source_manifest": {
                "engine_zip_name": engine_zip.name,
                "engine_zip_sha256": _sha256(engine_zip),
                "source_file_sha256": source_hashes,
                "raw_engine_bundle_committed": False,
                "current_repository_model_b_and_model_h_reused": True,
            },
            "model_switch": {
                "supported_values": sorted(COOLING_HL_MODELS),
                "adapter_setting": "COOLING_HL_MODEL",
            },
            "execution_modes": {
                H_L_ONLY_SENSITIVITY: "Model H h_l with the existing meridional heat-transfer area",
                H_GEOMETRY_COUPLED_EXISTING_LOOP: "Model H h_l plus ds_H in the existing loop area exactly once",
            },
            "scenarios": scenario_records,
            "full_candidate_run": {
                "scenario_id": "PROVIDED_ENGINE_25BAR_OF2P5",
                "template_count": full_result["template_count"] if full_result else None,
                "angle_expanded_candidate_count": full_result["angle_expanded_candidate_count"] if full_result else None,
                "valid_candidate_count": sum(row["geometry_valid"] for row in full_summary_rows),
                "invalid_candidate_count": sum(not row["geometry_valid"] for row in full_summary_rows),
                "station_row_count": sum(row["total_station_count"] for row in full_summary_rows),
                "station_status_counts": full_station_status_counts,
            },
            "beta_zero_model_b_regression": beta_zero_regression,
            "ppt_input_assessment": {
                "parameter_ppt_reproduced_fields": {
                    "chamber_pressure_bar_abs": 30.0,
                    "of_ratio": 3.0,
                    "mdot_total_kg_s": 1.6,
                },
                "parameter_ppt_unreproduced_or_conflicting_fields": [
                    "the deck also states 1.2 kg/s on a different slide",
                    "the deck states IPA 298.15 K and N2O 288.15 K, but the supplied engine source locks the liquid IPA enthalpy treatment to 293.15 K",
                ],
                "briefing_case_reproduction_status": "INPUT_U",
                "briefing_case_reason": "only three output points are present; complete inputs are absent",
            },
            "cooling_loop_interface": {
                "source_equations_changed": False,
                "segment_length_adapter": "module-local math.hypot factor with guaranteed restoration",
                "source_math_hypot_occurrence_count": (bundle / "cooling_loop_V1.py").read_text(encoding="utf-8").count("math.hypot"),
                "h_l_length_multiplier_applied": False,
            },
            "engine_log_line_counts": {
                "provided_scenario": len(baseline_log),
                "ppt_partial_scenario": len(ppt_log),
            },
            "generated_files": sorted(generated_files),
            "automated_tests": _run_repository_tests(),
            "experimental_validation": False,
            "validated_engine_prediction": False,
            "physical_validation_v01_v12": "NOT_RUN",
            "safety_approval": False,
            "limitations": [
                "The supplied engine code marks contraction ratio, characteristic length and coolant inlet pressure as BLANK or provisional.",
                "The supplied IPA correlations are temperature-only saturated-liquid fits and omit pressure effects.",
                "Wall-loop completion above 408 K uses explicit extrapolation and remains MODEL_U.",
                "The PPT partial scenario changes only Pc, O/F and total mass flow; it does not reproduce the deck's liquid inlet temperatures.",
                "Dean flow, curvature-dependent transition, helical pressure loss, manifold distribution and 3D manufacturability remain unmodelled.",
            ],
        }
        (output_directory / "model_h_verification_summary.json").write_text(
            json.dumps(verification, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        return verification


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-zip", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "model_h_engine_full",
    )
    args = parser.parse_args()
    result = run(args.engine_zip, args.output_dir)
    print(
        json.dumps(
            {
                "run_classification": result["run_classification"],
                "engine_zip_sha256": result["source_manifest"]["engine_zip_sha256"],
                "full_candidate_run": result["full_candidate_run"],
                "beta_zero_regression": result["beta_zero_model_b_regression"],
                "generated_files": result["generated_files"],
                "output_directory": str(args.output_dir.resolve()),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
