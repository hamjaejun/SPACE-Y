from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

from space_y.coolant import (
    CoolantStop,
    TRANSITION_HEAT_TRANSFER_MODEL_A,
    TRANSITION_HEAT_TRANSFER_MODEL_B,
    build_friction_domain,
    build_heat_transfer_domain,
    build_rectangular_channel_cross_section,
    evaluate_rectangular_cooling_hl,
    evaluate_rectangular_transition_heat_transfer_model_b,
)


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_DIR = ROOT / "artifacts/cooling_transition_model_b"
BOUNDARY_RE = (2299.999, 2300.0, 2300.001, 2500.0, 2800.0, 2999.999, 3000.0, 3000.001)
BOUNDARIES = ("CONSTANT_WALL_TEMPERATURE", "SHAH_LONDON_H1")
ASPECT_GEOMETRIES = (
    ("alpha_1p0", 0.010, 0.010),
    ("alpha_0p5", 0.020, 0.010),
    ("alpha_0p25", 0.040, 0.010),
)


def _domains():
    geometry = "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER"
    return (
        build_heat_transfer_domain(
            {
                "source": "SYNTHETIC_MODEL_B_NUMERICAL_VERIFICATION_DOMAIN",
                "re_range": [3000.0, 5_000_000.0],
                "pr_range": [0.5, 100.0],
                "geometry": geometry,
                "fully_developed_required": True,
            }
        ),
        build_friction_domain(
            {
                "source": "SYNTHETIC_MODEL_B_NUMERICAL_VERIFICATION_DOMAIN",
                "re_range": [3000.0, 5_000_000.0],
                "relative_roughness_range": [0.0, 0.05],
                "geometry": geometry,
            }
        ),
    )


HEAT_DOMAIN, FRICTION_DOMAIN = _domains()


def evaluate(
    reynolds: float,
    boundary: str,
    width_m: float,
    height_m: float,
    *,
    model: str = TRANSITION_HEAT_TRANSFER_MODEL_B,
):
    section = build_rectangular_channel_cross_section(
        {"width_m": width_m, "height_m": height_m, "channel_count": 1}
    )
    viscosity = 0.001
    mass_flow = (
        reynolds
        * section.total_flow_area_m2
        * viscosity
        / section.hydraulic_diameter_m
    )
    return evaluate_rectangular_cooling_hl(
        coolant_mass_flow_kg_s=mass_flow,
        width_m=width_m,
        height_m=height_m,
        channel_count=1,
        viscosity_Pa_s=viscosity,
        cp_J_kgK=2000.0,
        conductivity_W_mK=0.15,
        equivalent_sand_roughness_m=1.0e-6,
        closure_geometry="RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
        fully_developed=True,
        thermal_boundary_condition="SYNTHETIC_UNMAPPED_BOUNDARY",
        heat_transfer_domain=HEAT_DOMAIN,
        heat_transfer_thermal_boundary_condition="SYNTHETIC_UNMAPPED_BOUNDARY",
        friction_domain=FRICTION_DOMAIN,
        density_kg_m3=800.0,
        laminar_thermal_boundary_condition=boundary,
        transition_heat_transfer_model=model,
    )


def evaluate_model_b_endpoint(
    reynolds: float,
    boundary: str,
    width_m: float,
    height_m: float,
):
    section = build_rectangular_channel_cross_section(
        {"width_m": width_m, "height_m": height_m, "channel_count": 1}
    )
    viscosity = 0.001
    mass_flow = (
        reynolds
        * section.total_flow_area_m2
        * viscosity
        / section.hydraulic_diameter_m
    )
    return evaluate_rectangular_transition_heat_transfer_model_b(
        mass_flow_kg_s=mass_flow,
        flow_area_m2=section.total_flow_area_m2,
        hydraulic_diameter_m=section.hydraulic_diameter_m,
        width_m=width_m,
        height_m=height_m,
        viscosity_Pa_s=viscosity,
        cp_J_kgK=2000.0,
        conductivity_W_mK=0.15,
        equivalent_sand_roughness_m=1.0e-6,
        closure_geometry="RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
        fully_developed=True,
        thermal_boundary_condition="SYNTHETIC_UNMAPPED_BOUNDARY",
        heat_transfer_domain=HEAT_DOMAIN,
        heat_transfer_thermal_boundary_condition="SYNTHETIC_UNMAPPED_BOUNDARY",
        friction_domain=FRICTION_DOMAIN,
        density_kg_m3=800.0,
        laminar_thermal_boundary_condition=boundary,
    )


def _model_a_values(reynolds: float, boundary: str) -> tuple[float | None, float | None]:
    try:
        result = evaluate(
            reynolds,
            boundary,
            0.010,
            0.010,
            model=TRANSITION_HEAT_TRANSFER_MODEL_A,
        )
    except CoolantStop:
        return None, None
    return result.Nusselt, result.h_l_W_m2K


def boundary_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for boundary in BOUNDARIES:
        for reynolds in BOUNDARY_RE:
            model_b = evaluate(reynolds, boundary, 0.010, 0.010)
            model_a_nu, model_a_h = _model_a_values(reynolds, boundary)
            rows.append(
                {
                    "thermal_boundary_condition": boundary,
                    "Reynolds_requested": reynolds,
                    "Reynolds_evaluated": model_b.Reynolds,
                    "flow_regime": model_b.applicability_assessment.get("flow_regime", "TURBULENT"),
                    "model_b_Nusselt": model_b.Nusselt,
                    "model_b_h_l_W_m2K": model_b.h_l_W_m2K,
                    "model_b_gamma": model_b.heat_transfer_applicability.get("gamma"),
                    "model_b_darcy_factor": model_b.darcy_factor,
                    "model_b_friction_pressure_gradient_Pa_m": model_b.friction_pressure_gradient_Pa_m,
                    "model_a_Nusselt": model_a_nu,
                    "model_a_h_l_W_m2K": model_a_h,
                    "physical_validation_status": "NOT_RUN",
                    "fixture_classification": "SYNTHETIC_NON_DESIGN",
                }
            )
    return rows


def plot_rows() -> list[dict[str, Any]]:
    reynolds_values = list(range(2000, 3501, 25))
    reynolds_values.extend([2299, 2300, 2301, 2999, 3000, 3001])
    rows: list[dict[str, Any]] = []
    for boundary in BOUNDARIES:
        for reynolds in sorted(set(reynolds_values)):
            result = evaluate(float(reynolds), boundary, 0.010, 0.010)
            rows.append(
                {
                    "thermal_boundary_condition": boundary,
                    "Reynolds": result.Reynolds,
                    "Nusselt": result.Nusselt,
                    "h_l_W_m2K": result.h_l_W_m2K,
                    "flow_regime": result.applicability_assessment.get("flow_regime", "TURBULENT"),
                }
            )
    return rows


def aspect_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for geometry_id, width_m, height_m in ASPECT_GEOMETRIES:
        for boundary in BOUNDARIES:
            values = [
                evaluate(reynolds, boundary, width_m, height_m)
                for reynolds in (2300.0, 2500.0, 2800.0, 2999.999)
            ]
            rows.append(
                {
                    "geometry_id": geometry_id,
                    "width_m": width_m,
                    "height_m": height_m,
                    "aspect_ratio": min(width_m, height_m) / max(width_m, height_m),
                    "thermal_boundary_condition": boundary,
                    "Nu_at_2300": values[0].Nusselt,
                    "Nu_at_2500": values[1].Nusselt,
                    "Nu_at_2800": values[2].Nusselt,
                    "Nu_at_2999p999": values[3].Nusselt,
                    "strictly_monotonic": all(
                        second.Nusselt > first.Nusselt
                        for first, second in zip(values, values[1:])
                    ),
                    "physical_validation_status": "NOT_RUN",
                }
            )
    return rows


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _polyline_points(values: list[tuple[float, float]], frame, x_range, y_range) -> str:
    left, top, width, height = frame
    x_min, x_max = x_range
    y_min, y_max = y_range
    points = []
    for x_value, y_value in values:
        x = left + width * (x_value - x_min) / (x_max - x_min)
        y = top + height * (1.0 - (y_value - y_min) / (y_max - y_min))
        points.append(f"{x:.2f},{y:.2f}")
    return " ".join(points)


def _write_svg(path: Path, rows: list[dict[str, Any]]) -> None:
    width, height = 1200, 760
    frames = ((90, 95, 1020, 250), (90, 440, 1020, 250))
    colors = {"CONSTANT_WALL_TEMPERATURE": "#17365D", "SHAH_LONDON_H1": "#B65F22"}
    x_range = (2000.0, 3500.0)
    nu_max = max(float(row["Nusselt"]) for row in rows) * 1.08
    h_max = max(float(row["h_l_W_m2K"]) for row in rows) * 1.08
    pieces = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        '<rect width="1200" height="760" fill="#ffffff"/>',
        '<text x="90" y="42" font-family="Arial" font-size="25" font-weight="700" fill="#20242a">SPACE Y Model B synthetic continuity check</text>',
        '<text x="90" y="68" font-family="Arial" font-size="14" fill="#5f6973">Fully developed stationwise interpolation; synthetic fluid and geometry; not experimental validation</text>',
    ]
    for frame, y_max, ylabel in zip(frames, (nu_max, h_max), ("Nusselt number", "h_l [W/m²K]")):
        left, top, plot_width, plot_height = frame
        pieces.append(f'<rect x="{left}" y="{top}" width="{plot_width}" height="{plot_height}" fill="#f7f8fa" stroke="#d5dbe1"/>')
        for reynolds_marker in (2300, 3000):
            x = left + plot_width * (reynolds_marker - x_range[0]) / (x_range[1] - x_range[0])
            pieces.append(f'<line x1="{x:.2f}" y1="{top}" x2="{x:.2f}" y2="{top + plot_height}" stroke="#9099a2" stroke-dasharray="6 6"/>')
            pieces.append(f'<text x="{x + 6:.2f}" y="{top + 18}" font-family="Arial" font-size="12" fill="#616b74">Re={reynolds_marker}</text>')
        for tick in range(2000, 3501, 250):
            x = left + plot_width * (tick - x_range[0]) / (x_range[1] - x_range[0])
            pieces.append(f'<text x="{x:.2f}" y="{top + plot_height + 22}" text-anchor="middle" font-family="Arial" font-size="11" fill="#59636c">{tick}</text>')
        for boundary in BOUNDARIES:
            key = "Nusselt" if ylabel.startswith("Nusselt") else "h_l_W_m2K"
            values = [
                (float(row["Reynolds"]), float(row[key]))
                for row in rows
                if row["thermal_boundary_condition"] == boundary
            ]
            points = _polyline_points(values, frame, x_range, (0.0, y_max))
            pieces.append(f'<polyline points="{points}" fill="none" stroke="{colors[boundary]}" stroke-width="3"/>')
        pieces.append(f'<text x="28" y="{top + plot_height / 2}" transform="rotate(-90 28 {top + plot_height / 2})" text-anchor="middle" font-family="Arial" font-size="15" fill="#293038">{ylabel}</text>')
        pieces.append(f'<text x="{left + plot_width / 2}" y="{top + plot_height + 46}" text-anchor="middle" font-family="Arial" font-size="15" fill="#293038">Reynolds number</text>')
    pieces.extend(
        [
            '<line x1="820" y1="42" x2="858" y2="42" stroke="#17365D" stroke-width="3"/>',
            '<text x="868" y="47" font-family="Arial" font-size="13" fill="#293038">Constant wall temperature</text>',
            '<line x1="820" y1="66" x2="858" y2="66" stroke="#B65F22" stroke-width="3"/>',
            '<text x="868" y="71" font-family="Arial" font-size="13" fill="#293038">Shah–London H1</text>',
            '</svg>',
        ]
    )
    path.write_text("\n".join(pieces), encoding="utf-8")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    boundary = boundary_rows()
    plot = plot_rows()
    aspects = aspect_rows()
    _write_csv(OUTPUT_DIR / "boundary_continuity.csv", boundary)
    _write_csv(OUTPUT_DIR / "nu_hl_vs_re.csv", plot)
    _write_csv(OUTPUT_DIR / "aspect_ratio_summary.csv", aspects)
    _write_svg(OUTPUT_DIR / "nu_hl_vs_re.svg", plot)

    summary: dict[str, Any] = {
        "artifact_classification": "SYNTHETIC_NON_DESIGN_NUMERICAL_VERIFICATION",
        "model_id": "SPACE_Y_MODEL_B_LINEAR_2300_3000",
        "default_dispatch_policy": {
            "Re_lt_2300": "SHAH_LONDON_RECTANGULAR_LAMINAR",
            "Re_2300_to_lt_3000": "SPACE_Y_MODEL_B_LINEAR_2300_3000",
            "Re_ge_3000": "GNIELINSKI_COLEBROOK_EXISTING_PATH",
        },
        "model_a_preserved_as": TRANSITION_HEAT_TRANSFER_MODEL_A,
        "assumptions": [
            "FULLY_DEVELOPED_REFERENCE",
            "STATIONWISE_QUASI_LOCAL_APPLICATION",
            "CONSTANT_PROPERTIES_AT_EACH_SYNTHETIC_STATION",
            "RECTANGULAR_DUCT_SHAH_LONDON_LAMINAR_ENDPOINT",
            "GNIELINSKI_COLEBROOK_ENDPOINT_EVALUATED_AT_RE_3000",
        ],
        "unsupported": [
            "TRANSITION_FRICTION_FACTOR",
            "TRANSITION_PRESSURE_GRADIENT",
            "ENTRANCE_REGION",
            "VARIABLE_GEOMETRY_EXPERIMENTAL_VALIDATION",
            "ACTUAL_IPA_OR_ENGINE_VALIDATION",
        ],
        "endpoint_checks": {},
        "boundary_rows": len(boundary),
        "aspect_cases": len(aspects),
        "all_aspect_cases_strictly_monotonic": all(
            bool(row["strictly_monotonic"]) for row in aspects
        ),
        "numerical_adoption_for_preliminary_h_l": True,
        "experimental_validation": False,
        "safety_approval": False,
    }
    for thermal_boundary in BOUNDARIES:
        low = evaluate_model_b_endpoint(2300.0, thermal_boundary, 0.010, 0.010)
        high = evaluate_model_b_endpoint(3000.0, thermal_boundary, 0.010, 0.010)
        turbulent = evaluate(3000.0, thermal_boundary, 0.010, 0.010)
        summary["endpoint_checks"][thermal_boundary] = {
            "Nu_B_2300": low.Nusselt,
            "Nu_laminar_endpoint": low.heat_transfer_applicability[
                "laminar_endpoint_Nusselt"
            ],
            "absolute_residual_at_2300": abs(
                low.Nusselt
                - low.heat_transfer_applicability["laminar_endpoint_Nusselt"]
            ),
            "Nu_B_3000": high.Nusselt,
            "Nu_Gnielinski_3000": turbulent.Nusselt,
            "absolute_residual_at_3000": abs(high.Nusselt - turbulent.Nusselt),
        }
    (OUTPUT_DIR / "summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
