from __future__ import annotations

import copy
import csv
from dataclasses import asdict
import json
import math
from pathlib import Path
from typing import Any

from space_y.coolant import build_friction_domain, build_heat_transfer_domain
from space_y.cooling_channel_pipeline import solve_cooling_channel_pipeline
from space_y.cooling_transition_sensitivity import (
    evaluate_linear_transition_endpoint_sensitivity,
)
from space_y.physics import prandtl_number


ROOT = Path(__file__).resolve().parents[1]
BASE_INPUT = ROOT / "examples/cooling_channel_pipeline_synthetic_80.json"
OUTPUT_DIR = ROOT / "artifacts/cooling_model_b_candidate_analysis"
FLUID_LABEL = "SYNTHETIC_TEST_FLUID_NOT_IPA"
TOTAL_MASS_FLOW_KG_S = 0.4
BOUNDARIES = ("CONSTANT_WALL_TEMPERATURE", "SHAH_LONDON_H1")
MODELS = ("MODEL_A_NULL_2300_3000", "MODEL_B_LINEAR_2300_3000")
STATION_ORDER = {"SYN-INLET": 0, "SYN-THROAT": 1, "SYN-EXIT": 2}


def _run(base: dict[str, Any], model: str, boundary: str) -> dict[str, Any]:
    request = copy.deepcopy(base)
    request["run_id"] = f"SYNTHETIC-0P4-{model}-{boundary}"
    request["coolant_conditions"]["coolant_mass_flow_kg_s"] = TOTAL_MASS_FLOW_KG_S
    request["coolant_conditions"]["fluid_identity"]["classification"] = FLUID_LABEL
    request["model_conditions"]["laminar_thermal_boundary_condition"] = boundary
    request["model_conditions"]["transition_heat_transfer_model"] = model
    return solve_cooling_channel_pipeline(request)


def _regime(reynolds: float | None) -> str:
    if reynolds is None:
        return "NOT_RUN"
    if reynolds < 2300.0:
        return "LAMINAR"
    if reynolds < 3000.0:
        return "TRANSITION"
    return "TURBULENT"


def _rows(result: dict[str, Any], model: str, boundary: str, properties: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    pr = prandtl_number(
        properties["cp_J_kgK"],
        properties["viscosity_Pa_s"],
        properties["conductivity_W_mK"],
    )
    for candidate in result.get("candidates", []):
        if candidate.get("geometric_validity_status") != "PASS":
            continue
        for station in candidate.get("stations", []):
            calculation = station.get("calculation") or {}
            values = calculation.get("results") or {}
            reynolds = values.get("Reynolds")
            heat = values.get("heat_transfer_applicability") or {}
            friction = values.get("friction_applicability") or {}
            correlation = heat.get("correlation_id") or values.get("selected_correlation_id")
            nusselt = values.get("Nusselt")
            h_l = values.get("h_l_W_m2K")
            regime = _regime(reynolds)
            output.append(
                {
                    "fluid_classification": FLUID_LABEL,
                    "actual_engine_prediction": False,
                    "experimental_validation": False,
                    "model": model,
                    "thermal_boundary_condition": boundary,
                    "candidate_id": candidate["candidate_id"],
                    "station_id": station["station_id"],
                    "station_order": STATION_ORDER.get(station["station_id"], 99),
                    "x_m": station["x_m"],
                    "channel_count": candidate["channel_count"],
                    "channel_width_m": station["channel_width_m"],
                    "channel_height_m": candidate["channel_height_m"],
                    "hydraulic_diameter_m": station["hydraulic_diameter_m"],
                    "total_mass_flow_kg_s": TOTAL_MASS_FLOW_KG_S,
                    "channel_mass_flow_kg_s": TOTAL_MASS_FLOW_KG_S / candidate["channel_count"],
                    "Reynolds": reynolds,
                    "Prandtl": values.get("Prandtl", pr if reynolds is not None else None),
                    "flow_regime": regime,
                    "correlation_id": correlation,
                    "Nusselt": nusselt,
                    "h_l_W_m2K": h_l,
                    "heat_transfer_calculable": nusselt is not None and h_l is not None,
                    "calculation_status": calculation.get("status"),
                    "darcy_factor": values.get("darcy_factor"),
                    "friction_pressure_gradient_Pa_m": values.get("friction_pressure_gradient_Pa_m"),
                    "friction_supported": values.get("darcy_factor") is not None,
                    "friction_status": friction.get("status"),
                    "reason": calculation.get("reason"),
                }
            )
    return output


def build_analysis(base: dict[str, Any]) -> dict[str, Any]:
    properties = base["coolant_conditions"]["default_fluid_properties"]
    results: dict[tuple[str, str], dict[str, Any]] = {}
    all_rows: list[dict[str, Any]] = []
    for boundary in BOUNDARIES:
        for model in MODELS:
            result = _run(base, model, boundary)
            results[(boundary, model)] = result
            all_rows.extend(_rows(result, model, boundary, properties))

    paired: list[dict[str, Any]] = []
    lookup = {
        (row["thermal_boundary_condition"], row["model"], row["candidate_id"], row["station_id"]): row
        for row in all_rows
    }
    for boundary in BOUNDARIES:
        for key in sorted(
            {(row["candidate_id"], row["station_id"]) for row in all_rows if row["thermal_boundary_condition"] == boundary},
            key=lambda item: (item[0], STATION_ORDER.get(item[1], 99)),
        ):
            a = lookup[(boundary, MODELS[0], *key)]
            b = lookup[(boundary, MODELS[1], *key)]
            paired.append(
                {
                    "fluid_classification": FLUID_LABEL,
                    "thermal_boundary_condition": boundary,
                    "candidate_id": key[0],
                    "station_id": key[1],
                    "Reynolds": b["Reynolds"],
                    "Prandtl": b["Prandtl"],
                    "flow_regime": b["flow_regime"],
                    "model_a_Nusselt": a["Nusselt"],
                    "model_b_Nusselt": b["Nusselt"],
                    "model_a_h_l_W_m2K": a["h_l_W_m2K"],
                    "model_b_h_l_W_m2K": b["h_l_W_m2K"],
                    "model_b_only": not a["heat_transfer_calculable"] and b["heat_transfer_calculable"],
                    "Nusselt_delta_b_minus_a": (
                        b["Nusselt"] - a["Nusselt"] if a["Nusselt"] is not None and b["Nusselt"] is not None else None
                    ),
                    "h_l_delta_b_minus_a_W_m2K": (
                        b["h_l_W_m2K"] - a["h_l_W_m2K"]
                        if a["h_l_W_m2K"] is not None and b["h_l_W_m2K"] is not None
                        else None
                    ),
                    "model_b_friction_supported": b["friction_supported"],
                }
            )

    availability: list[dict[str, Any]] = []
    for boundary in BOUNDARIES:
        for model in MODELS:
            subset = [r for r in all_rows if r["thermal_boundary_condition"] == boundary and r["model"] == model]
            for regime in ("LAMINAR", "TRANSITION", "TURBULENT", "ALL"):
                group = subset if regime == "ALL" else [r for r in subset if r["flow_regime"] == regime]
                calculable = sum(r["heat_transfer_calculable"] for r in group)
                friction = sum(r["friction_supported"] for r in group)
                availability.append(
                    {
                        "thermal_boundary_condition": boundary,
                        "model": model,
                        "flow_regime": regime,
                        "station_count": len(group),
                        "heat_transfer_calculable_count": calculable,
                        "heat_transfer_calculable_percent": 100.0 * calculable / len(group) if group else 0.0,
                        "friction_supported_count": friction,
                        "friction_supported_percent": 100.0 * friction / len(group) if group else 0.0,
                    }
                )

    heat_domain = build_heat_transfer_domain(base["model_conditions"]["heat_transfer_domain"])
    friction_domain = build_friction_domain(base["model_conditions"]["friction_domain"])
    sensitivity: list[dict[str, Any]] = []
    b_cwt = [r for r in all_rows if r["model"] == MODELS[1] and r["thermal_boundary_condition"] == BOUNDARIES[0]]
    for row in b_cwt:
        re_value = row["Reynolds"]
        if re_value is None or not 2300.0 <= re_value <= 4000.0:
            continue
        for boundary in BOUNDARIES:
            default = lookup[(boundary, MODELS[1], row["candidate_id"], row["station_id"])]
            comparison = evaluate_linear_transition_endpoint_sensitivity(
                reynolds=re_value,
                prandtl=default["Prandtl"],
                width_m=row["channel_width_m"],
                height_m=row["channel_height_m"],
                hydraulic_diameter_m=row["hydraulic_diameter_m"],
                conductivity_W_mK=properties["conductivity_W_mK"],
                equivalent_sand_roughness_m=base["manufacturing_constraints"]["equivalent_sand_roughness_m"],
                closure_geometry=base["model_conditions"]["closure_geometry"],
                fully_developed=True,
                thermal_boundary_condition=boundary,
                heat_transfer_domain=heat_domain,
                friction_domain=friction_domain,
                upper_endpoint_reynolds=4000.0,
            )
            sensitivity.append(
                {
                    "fluid_classification": FLUID_LABEL,
                    "candidate_id": row["candidate_id"],
                    "station_id": row["station_id"],
                    "thermal_boundary_condition": boundary,
                    "Reynolds": re_value,
                    "default_model": "MODEL_B_2300_3000_THEN_GNIELINSKI",
                    "comparison_scope": (
                        "LINEAR_2300_3000_VS_LINEAR_2300_4000"
                        if re_value < 3000.0
                        else "PRODUCTION_GNIELINSKI_VS_LINEAR_2300_4000"
                    ),
                    "default_Nusselt": default["Nusselt"],
                    "default_h_l_W_m2K": default["h_l_W_m2K"],
                    "sensitivity_model": comparison.correlation_id,
                    "sensitivity_Nusselt": comparison.Nusselt,
                    "sensitivity_h_l_W_m2K": comparison.h_l_W_m2K,
                    "Nusselt_difference": comparison.Nusselt - default["Nusselt"],
                    "Nusselt_relative_difference_percent": 100.0 * (comparison.Nusselt - default["Nusselt"]) / default["Nusselt"],
                    "h_l_difference_W_m2K": comparison.h_l_W_m2K - default["h_l_W_m2K"],
                    "h_l_relative_difference_percent": 100.0 * (comparison.h_l_W_m2K - default["h_l_W_m2K"]) / default["h_l_W_m2K"],
                    "experimental_validation": False,
                    "friction_supported": False,
                }
            )

    first = results[(BOUNDARIES[0], MODELS[1])]
    summary = {
        "analysis_id": "SPACE_Y_MODEL_B_CANDIDATE_SENSITIVITY_V1",
        "fluid_classification": FLUID_LABEL,
        "actual_engine_prediction": False,
        "experimental_validation": False,
        "total_coolant_mass_flow_kg_s": TOTAL_MASS_FLOW_KG_S,
        "template_count": first["template_count"],
        "unique_geometry_candidate_count": first["unique_geometry_candidate_count"],
        "geometrically_valid_candidate_count": first["geometrically_valid_candidate_count"],
        "valid_station_count_per_scenario": len([r for r in all_rows if r["model"] == MODELS[1] and r["thermal_boundary_condition"] == BOUNDARIES[0]]),
        "regime_counts_per_scenario": {
            regime: sum(r["flow_regime"] == regime for r in b_cwt)
            for regime in ("LAMINAR", "TRANSITION", "TURBULENT")
        },
        "model_b_only_station_count_per_boundary": {
            boundary: sum(r["model_b_only"] and r["thermal_boundary_condition"] == boundary for r in paired)
            for boundary in BOUNDARIES
        },
        "assumptions": {
            "properties": properties,
            "total_flow_split": "equal distribution over N parallel channels; mdot_channel=0.4/N",
            "geometry": "existing synthetic 80-template input; no geometry optimization",
            "fully_developed": True,
            "entrance_effects_modelled": False,
            "transition_friction": "unsupported/null",
            "re_4000_path": "sensitivity-only; production default unchanged",
        },
    }
    if sensitivity:
        summary["re4000_sensitivity_relative_difference_percent"] = {
            "Nusselt_min": min(r["Nusselt_relative_difference_percent"] for r in sensitivity),
            "Nusselt_max": max(r["Nusselt_relative_difference_percent"] for r in sensitivity),
            "h_l_min": min(r["h_l_relative_difference_percent"] for r in sensitivity),
            "h_l_max": max(r["h_l_relative_difference_percent"] for r in sensitivity),
            "by_thermal_boundary_condition": {
                boundary: {
                    "station_count": sum(
                        r["thermal_boundary_condition"] == boundary
                        for r in sensitivity
                    ),
                    "Nusselt_min": min(
                        r["Nusselt_relative_difference_percent"]
                        for r in sensitivity
                        if r["thermal_boundary_condition"] == boundary
                    ),
                    "Nusselt_max": max(
                        r["Nusselt_relative_difference_percent"]
                        for r in sensitivity
                        if r["thermal_boundary_condition"] == boundary
                    ),
                }
                for boundary in BOUNDARIES
            },
        }
    return {
        "summary": summary,
        "station_rows": all_rows,
        "paired_rows": paired,
        "availability_rows": availability,
        "sensitivity_rows": sensitivity,
    }


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _svg_scatter(path: Path, title: str, series: list[tuple[str, str, list[tuple[float, float]]]], y_label: str) -> None:
    width, height = 1000, 560
    left, right, top, bottom = 90, 35, 55, 75
    points = [point for _, _, data in series for point in data]
    x_min, x_max = min(x for x, _ in points), max(x for x, _ in points)
    raw_y_min = min(y for _, y in points)
    raw_y_max = max(y for _, y in points)
    span = raw_y_max - raw_y_min
    padding = 0.08 * (span if span > 0 else max(abs(raw_y_max), 1.0))
    y_min = min(0.0, raw_y_min - padding)
    y_max = max(0.0, raw_y_max + padding)
    if y_max == y_min:
        y_max = y_min + 1.0
    sx = lambda x: left + (x - x_min) / (x_max - x_min) * (width - left - right)
    sy = lambda y: height - bottom - (y - y_min) / (y_max - y_min) * (height - top - bottom)
    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="white"/>',
        f'<text x="{width/2}" y="28" text-anchor="middle" font-family="Arial" font-size="20">{title}</text>',
        f'<line x1="{left}" y1="{height-bottom}" x2="{width-right}" y2="{height-bottom}" stroke="#222"/>',
        f'<line x1="{left}" y1="{top}" x2="{left}" y2="{height-bottom}" stroke="#222"/>',
        f'<text x="{width/2}" y="{height-20}" text-anchor="middle" font-family="Arial">Reynolds number</text>',
        f'<text transform="translate(22 {height/2}) rotate(-90)" text-anchor="middle" font-family="Arial">{y_label}</text>',
    ]
    for boundary in (2300, 3000, 4000):
        if x_min <= boundary <= x_max:
            lines.append(f'<line x1="{sx(boundary):.2f}" y1="{top}" x2="{sx(boundary):.2f}" y2="{height-bottom}" stroke="#aaa" stroke-dasharray="5 4"/>')
    for index, (label, color, data) in enumerate(series):
        for x, y in data:
            lines.append(f'<circle cx="{sx(x):.2f}" cy="{sy(y):.2f}" r="2.4" fill="{color}" opacity="0.55"/>')
        lx = left + index * 230
        lines.append(f'<circle cx="{lx}" cy="{height-44}" r="5" fill="{color}"/><text x="{lx+10}" y="{height-40}" font-family="Arial" font-size="12">{label}</text>')
    lines.append(f'<text x="{width-right}" y="45" text-anchor="end" font-family="Arial" font-size="11" fill="#a00">{FLUID_LABEL}; not engine validation</text>')
    lines.append("</svg>")
    path.write_text("\n".join(lines), encoding="utf-8")


def _write_graphs(output: Path, data: dict[str, Any]) -> None:
    rows = [r for r in data["station_rows"] if r["model"] == MODELS[1] and r["thermal_boundary_condition"] == BOUNDARIES[0]]
    _svg_scatter(
        output / "reynolds_distribution.svg",
        "Reynolds distribution — 156 valid synthetic candidates",
        [(station, color, [(r["Reynolds"], r["station_order"] + 1.0) for r in rows if r["station_id"] == station])
         for station, color in (("SYN-INLET", "#1f77b4"), ("SYN-THROAT", "#d62728"), ("SYN-EXIT", "#2ca02c"))],
        "Station index (1 inlet, 2 throat, 3 exit)",
    )
    paired = [r for r in data["paired_rows"] if r["thermal_boundary_condition"] == BOUNDARIES[0]]
    _svg_scatter(
        output / "model_a_vs_b_nusselt.svg",
        "Model A vs Model B Nusselt — constant-wall-temperature scenario",
        [
            ("Model A (transition null)", "#7f7f7f", [(r["Reynolds"], r["model_a_Nusselt"]) for r in paired if r["model_a_Nusselt"] is not None]),
            ("Model B default", "#d62728", [(r["Reynolds"], r["model_b_Nusselt"]) for r in paired if r["model_b_Nusselt"] is not None]),
        ],
        "Nusselt number",
    )
    _svg_scatter(
        output / "model_a_vs_b_h_l.svg",
        "Model A vs Model B h_l — constant-wall-temperature scenario",
        [
            ("Model A (transition null)", "#7f7f7f", [(r["Reynolds"], r["model_a_h_l_W_m2K"]) for r in paired if r["model_a_h_l_W_m2K"] is not None]),
            ("Model B default", "#d62728", [(r["Reynolds"], r["model_b_h_l_W_m2K"]) for r in paired if r["model_b_h_l_W_m2K"] is not None]),
        ],
        "h_l (W m^-2 K^-1)",
    )
    sensitivity = data["sensitivity_rows"]
    _svg_scatter(
        output / "upper_endpoint_boundary_sensitivity.svg",
        "Re=4000 sensitivity path vs production default",
        [
            (boundary.replace("CONSTANT_WALL_TEMPERATURE", "Nu_T").replace("SHAH_LONDON_H1", "Nu_H1"), color,
             [(r["Reynolds"], r["Nusselt_relative_difference_percent"]) for r in sensitivity if r["thermal_boundary_condition"] == boundary])
            for boundary, color in ((BOUNDARIES[0], "#1f77b4"), (BOUNDARIES[1], "#ff7f0e"))
        ],
        "Nusselt difference vs default (%)",
    )


def write_analysis(output: Path, data: dict[str, Any]) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "summary.json").write_text(json.dumps(data["summary"], indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    _write_csv(output / "station_comparison.csv", data["station_rows"])
    _write_csv(output / "model_a_vs_b_delta.csv", data["paired_rows"])
    _write_csv(output / "availability_summary.csv", data["availability_rows"])
    _write_csv(output / "upper_endpoint_sensitivity.csv", data["sensitivity_rows"])
    _write_graphs(output, data)


def main() -> None:
    base = json.loads(BASE_INPUT.read_text(encoding="utf-8"))
    data = build_analysis(base)
    write_analysis(OUTPUT_DIR, data)
    print(json.dumps(data["summary"], indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
