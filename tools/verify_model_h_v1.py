from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from cooling_model_b import (  # noqa: E402
    LAMINAR_MODEL_THERMAL_ENTRY,
    evaluate_cooling_candidates,
)
from model_h_v1 import (  # noqa: E402
    MODEL_H_HELIX_ANGLES_DEG,
    MODEL_H_HEIGHT_CANDIDATES_MM,
    MODEL_H_RIB_CANDIDATES_MM,
    MODEL_H_WIDTH_CANDIDATES_MM,
    evaluate_model_h_candidates,
    write_model_h_result_json,
    write_model_h_station_csv,
)


def synthetic_inputs() -> dict[str, Any]:
    """Return arithmetic-only inputs; these are not SPACE Y or IPA design data."""

    return {
        "x_m": [0.0, 0.18, 0.30, 0.42],
        "mean_channel_radius_m": [0.055, 0.040, 0.025, 0.070],
        "throat_station_index": 2,
        "station_ids": ["SYN-CHAMBER", "SYN-CONVERGENT", "SYN-THROAT", "SYN-EXIT"],
        "mdot_total_kg_s": 0.4,
        "density_kg_m3": [800.0, 790.0, 780.0, 770.0],
        "viscosity_Pa_s": [0.0030, 0.0026, 0.0022, 0.0019],
        "cp_J_kgK": [2400.0, 2420.0, 2440.0, 2460.0],
        "conductivity_W_mK": [0.145, 0.143, 0.141, 0.139],
        "roughness_m": 2.0e-6,
        "thermal_boundary_condition": "SHAH_LONDON_H1",
        "fully_developed": True,
        "colebrook_re_range": (3000.0, 10_000_000.0),
        "colebrook_relative_roughness_range": (0.0, 0.1),
        "channel_width_limits_m": (0.00005, 0.020),
        "minimum_rib_thickness_m": 0.0009,
        "allowed_channel_heights_m": [item * 1.0e-3 for item in MODEL_H_HEIGHT_CANDIDATES_MM],
        "width_candidates_mm": MODEL_H_WIDTH_CANDIDATES_MM,
        "rib_candidates_mm": MODEL_H_RIB_CANDIDATES_MM,
        "height_candidates_mm": MODEL_H_HEIGHT_CANDIDATES_MM,
        "fluid_identity": "SYNTHETIC_TEST_FLUID_NOT_IPA",
        "property_data_classification": "SYNTHETIC_NON_DESIGN_ARITHMETIC_VERIFICATION",
        "property_validity_status": "SYNTHETIC_DOMAIN_ONLY",
    }


def beta_zero_regression(model_h: dict[str, Any], inputs: dict[str, Any]) -> dict[str, Any]:
    excluded = {
        "density_kg_m3",
        "fluid_identity",
        "property_data_classification",
        "property_validity_status",
    }
    model_b = evaluate_cooling_candidates(
        **{key: value for key, value in inputs.items() if key not in excluded},
        laminar_model=LAMINAR_MODEL_THERMAL_ENTRY,
    )
    base_by_id = {item["candidate_id"]: item for item in model_b["candidates"]}
    beta_zero = [item for item in model_h["candidates"] if item["helix_angle_deg"] == 0.0]
    metrics = {
        "channel_width_m": 0.0,
        "total_flow_area_m2": 0.0,
        "hydraulic_diameter_m": 0.0,
        "s_m": 0.0,
        "Reynolds": 0.0,
        "Prandtl": 0.0,
        "Nusselt": 0.0,
        "h_l_W_m2K": 0.0,
    }
    mismatches: list[dict[str, Any]] = []
    compared = 0
    for helical_candidate in beta_zero:
        base = base_by_id[helical_candidate["base_model_b_candidate_id"]]
        if helical_candidate["valid"] != base["valid"]:
            mismatches.append(
                {
                    "candidate_id": helical_candidate["candidate_id"],
                    "reason": "candidate validity differs",
                }
            )
        for actual, expected in zip(helical_candidate["stations"], base["stations"]):
            pairs = {
                "channel_width_m": (actual.get("channel_width_m"), expected.get("channel_width_m")),
                "total_flow_area_m2": (actual.get("total_flow_area_m2"), expected.get("flow_area_m2")),
                "hydraulic_diameter_m": (
                    actual.get("hydraulic_diameter_m"),
                    expected.get("hydraulic_diameter_m"),
                ),
                "s_m": (actual.get("s_H_m"), expected.get("s_m")),
                "Reynolds": (actual.get("Reynolds"), expected.get("Reynolds")),
                "Prandtl": (actual.get("Prandtl"), expected.get("Prandtl")),
                "Nusselt": (actual.get("Nusselt"), expected.get("Nusselt")),
                "h_l_W_m2K": (actual.get("h_l_W_m2K"), expected.get("h_l_W_m2K")),
            }
            if actual.get("status") != expected.get("status"):
                mismatches.append(
                    {
                        "candidate_id": helical_candidate["candidate_id"],
                        "station_id": actual.get("station_id"),
                        "reason": "station status differs",
                        "model_h": actual.get("status"),
                        "model_b": expected.get("status"),
                    }
                )
            for name, (left, right) in pairs.items():
                if left is None and right is None:
                    continue
                if left is None or right is None:
                    mismatches.append(
                        {
                            "candidate_id": helical_candidate["candidate_id"],
                            "station_id": actual.get("station_id"),
                            "reason": f"{name} null mismatch",
                        }
                    )
                    continue
                difference = abs(float(left) - float(right))
                metrics[name] = max(metrics[name], difference)
            compared += 1
    return {
        "status": "PASS" if not mismatches and all(value == 0.0 for value in metrics.values()) else "FAIL",
        "beta_zero_candidate_count": len(beta_zero),
        "compared_station_count": compared,
        "maximum_absolute_difference": metrics,
        "mismatches": mismatches,
    }


def angle_counts(result: dict[str, Any]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for angle in MODEL_H_HELIX_ANGLES_DEG:
        candidates = [item for item in result["candidates"] if item["helix_angle_deg"] == angle]
        stations = [station for item in candidates for station in item["stations"]]
        outside_reasons: dict[str, int] = {}
        for station in stations:
            if station.get("status") == "OUTSIDE":
                reason = str(station.get("reason", "unspecified OUTSIDE reason"))
                outside_reasons[reason] = outside_reasons.get(reason, 0) + 1
        summary[str(int(angle))] = {
            "candidate_count": len(candidates),
            "valid_candidate_count": sum(item["valid"] for item in candidates),
            "invalid_candidate_count": sum(not item["valid"] for item in candidates),
            "model_u_station_count": sum(item.get("status") == "MODEL_U" for item in stations),
            "outside_station_count": sum(item.get("status") == "OUTSIDE" for item in stations),
            "input_u_station_count": sum(item.get("status") == "INPUT_U" for item in stations),
            "num_u_station_count": sum(item.get("status") == "NUM_U" for item in stations),
            "outside_reason_counts": outside_reasons,
        }
    return summary


def representative_group(result: dict[str, Any]) -> list[dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for candidate in result["candidates"]:
        groups.setdefault(candidate["base_model_b_candidate_id"], []).append(candidate)
    eligible = [
        sorted(group, key=lambda item: item["helix_angle_deg"])
        for group in groups.values()
        if len(group) == len(MODEL_H_HELIX_ANGLES_DEG)
        and all(item["valid"] and item["status"] == "MODEL_U" for item in group)
    ]
    if not eligible:
        raise RuntimeError("no all-angle representative candidate completed")
    eligible.sort(key=lambda group: (group[0]["channel_count"], group[0]["channel_height_m"]))
    return eligible[len(eligible) // 2]


def write_representative_csv(path: Path, group: list[dict[str, Any]]) -> None:
    fields = [
        "base_model_b_candidate_id",
        "candidate_id",
        "helix_angle_deg",
        "station_id",
        "x_m",
        "channel_count",
        "rib_thickness_m",
        "channel_height_m",
        "channel_width_m",
        "hydraulic_diameter_m",
        "total_flow_area_m2",
        "s_H_m",
        "velocity_m_s",
        "Reynolds",
        "Prandtl",
        "Nusselt",
        "h_l_W_m2K",
        "flow_regime",
    ]
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for candidate in group:
            for station in candidate["stations"]:
                writer.writerow(
                    {
                        **{key: station.get(key) for key in fields},
                        "base_model_b_candidate_id": candidate[
                            "base_model_b_candidate_id"
                        ],
                        "candidate_id": candidate["candidate_id"],
                        "helix_angle_deg": candidate["helix_angle_deg"],
                        "channel_count": candidate["channel_count"],
                        "rib_thickness_m": candidate["rib_thickness_m"],
                        "channel_height_m": candidate["channel_height_m"],
                    }
                )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "model_h_v1",
    )
    parser.add_argument(
        "--write-full",
        action="store_true",
        help="also write the large full JSON and all-station CSV outputs",
    )
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)

    inputs = synthetic_inputs()
    result = evaluate_model_h_candidates(
        **inputs,
        helix_angles_deg=MODEL_H_HELIX_ANGLES_DEG,
        laminar_model=LAMINAR_MODEL_THERMAL_ENTRY,
    )
    regression = beta_zero_regression(result, inputs)
    counts = angle_counts(result)
    group = representative_group(result)
    representative = {
        "base_model_b_candidate_id": group[0]["base_model_b_candidate_id"],
        "channel_count": group[0]["channel_count"],
        "rib_thickness_m": group[0]["rib_thickness_m"],
        "channel_height_m": group[0]["channel_height_m"],
        "angles": {
            str(int(item["helix_angle_deg"])): {
                "minimum_Reynolds": item["summary"]["minimum_Reynolds"],
                "maximum_Reynolds": item["summary"]["maximum_Reynolds"],
                "minimum_h_l_W_m2K": item["summary"]["minimum_h_l_W_m2K"],
                "maximum_h_l_W_m2K": item["summary"]["maximum_h_l_W_m2K"],
                "path_length_factor": 1.0
                / math.cos(math.radians(item["helix_angle_deg"])),
            }
            for item in group
        },
    }
    summary = {
        "fixture_classification": "SYNTHETIC_NON_DESIGN_ARITHMETIC_VERIFICATION",
        "fluid_identity": "SYNTHETIC_TEST_FLUID_NOT_IPA",
        "status": "PASS" if regression["status"] == "PASS" else "FAIL",
        "template_count": result["template_count"],
        "initial_template_angle_combination_count": result[
            "initial_template_angle_combination_count"
        ],
        "base_raw_candidate_count": result["base_raw_candidate_count"],
        "base_unique_candidate_count": result["base_unique_candidate_count"],
        "angle_expanded_candidate_count": result["angle_expanded_candidate_count"],
        "valid_candidate_count": result["valid_candidate_count"],
        "invalid_candidate_count": result["invalid_candidate_count"],
        "successful_station_count": result["successful_station_count"],
        "failed_station_count": result["failed_station_count"],
        "flow_regime_counts": result["flow_regime_counts"],
        "angle_counts": counts,
        "beta_zero_model_b_regression": regression,
        "representative_angle_comparison": representative,
        "experimental_validation": False,
        "validated_engine_prediction": False,
        "physical_validation_v01_v12": "NOT_RUN",
        "limitations": result["limitations"],
    }
    if args.write_full:
        write_model_h_result_json(args.output_dir / "synthetic_full_result.json", result)
        write_model_h_station_csv(args.output_dir / "synthetic_all_stations.csv", result)
    write_representative_csv(
        args.output_dir / "synthetic_representative_angle_comparison.csv", group
    )
    (args.output_dir / "synthetic_verification_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
