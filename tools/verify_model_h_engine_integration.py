#!/usr/bin/env python3
"""Run the supplied engine bundle through the strict Model H integration gate.

The bundle remains external to Git.  This tool records hashes, executes the
provided engine/station/cooling code with the repository's current Model B
module, and evaluates one baseline channel key across the four Model H angles.
"""

from __future__ import annotations

import argparse
import contextlib
import csv
import hashlib
import importlib
import io
import json
from pathlib import Path
import runpy
import re
import sys
import tempfile
import types
from typing import Any
import zipfile


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import cooling_model_b
from model_h_engine_integration import (
    assess_supplied_ipa_states,
    evaluate_candidate_key_by_angle,
)
from model_h_v1 import (
    MODEL_H_HEIGHT_CANDIDATES_MM,
    generate_model_h_candidates,
)


REQUIRED_FILES = {
    "liquid_engine_design_V1.py",
    "cooling_loop_V1.py",
    "ipa_props.py",
    "nozzle_contour_V1.py",
    "stations_v1.py",
    "wall_material_V1.py",
    "hg_candidates_v1.py",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_extract(archive: Path, destination: Path) -> None:
    root = destination.resolve()
    with zipfile.ZipFile(archive) as handle:
        for member in handle.infolist():
            target = (root / member.filename).resolve()
            if root != target and root not in target.parents:
                raise ValueError(f"unsafe ZIP member: {member.filename}")
        handle.extractall(root)


def _find_bundle_root(extracted: Path) -> Path:
    matches = list(extracted.rglob("liquid_engine_design_V1.py"))
    if len(matches) != 1:
        raise ValueError("engine bundle must contain exactly one liquid_engine_design_V1.py")
    root = matches[0].parent
    missing = sorted(name for name in REQUIRED_FILES if not (root / name).is_file())
    if missing:
        raise ValueError(f"engine bundle is missing: {missing}")
    return root


def _install_plot_stub() -> None:
    pyplot = types.ModuleType("matplotlib.pyplot")
    pyplot.show = lambda *args, **kwargs: None
    matplotlib = types.ModuleType("matplotlib")
    matplotlib.pyplot = pyplot
    sys.modules["matplotlib"] = matplotlib
    sys.modules["matplotlib.pyplot"] = pyplot


def _run_supplied_engine(
    bundle_root: Path,
    assignment_overrides: dict[str, str] | None = None,
) -> tuple[dict[str, Any], list[str]]:
    previous_plot_modules = {
        name: sys.modules.get(name)
        for name in ("matplotlib", "matplotlib.pyplot")
    }
    sys.path.insert(0, str(bundle_root))
    for name in (
        "ipa_props",
        "nozzle_contour_V1",
        "stations_v1",
        "cooling_loop_V1",
        "wall_material_V1",
        "hg_candidates_v1",
    ):
        sys.modules.pop(name, None)
    sys.modules["h_l_V1"] = cooling_model_b
    _install_plot_stub()

    nozzle = importlib.import_module("nozzle_contour_V1")
    cooling_loop = importlib.import_module("cooling_loop_V1")
    nozzle.plot_contour = lambda *args, **kwargs: None
    cooling_loop.plot_table = lambda *args, **kwargs: None
    cooling_loop.plot_axial = lambda *args, **kwargs: None

    main_path = bundle_root / "liquid_engine_design_V1.py"
    if assignment_overrides:
        source = main_path.read_text(encoding="utf-8")
        for name, replacement in assignment_overrides.items():
            pattern = rf"(?m)^{re.escape(name)}\s*=\s*[^\r\n]+$"
            source, count = re.subn(pattern, f"{name} = {replacement}", source, count=1)
            if count != 1:
                raise ValueError(f"engine scenario assignment not found exactly once: {name}")
        main_path = bundle_root / "_scenario_liquid_engine_design_V1.py"
        main_path.write_text(source, encoding="utf-8")

    capture = io.StringIO()
    try:
        with contextlib.redirect_stdout(capture), contextlib.redirect_stderr(capture):
            namespace = runpy.run_path(
                str(main_path),
                run_name="__model_h_engine_input__",
            )
        return namespace, capture.getvalue().splitlines()
    finally:
        for name, previous in previous_plot_modules.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        if sys.path and sys.path[0] == str(bundle_root):
            sys.path.pop(0)


def _geometry_summary(geometry: dict[str, Any]) -> dict[str, Any]:
    by_angle: dict[str, dict[str, int]] = {}
    exclusion_counts: dict[str, int] = {}
    for candidate in geometry["candidates"]:
        angle = f"{float(candidate['helix_angle_deg']):g}"
        bucket = by_angle.setdefault(angle, {"total": 0, "valid": 0, "outside": 0})
        bucket["total"] += 1
        bucket["valid" if candidate["valid"] else "outside"] += 1
        if not candidate["valid"]:
            normalized_reasons = {
                reason.split(": ", 1)[-1]
                for reason in candidate["geometry_exclusion_reasons"]
            }
            for normalized in normalized_reasons:
                exclusion_counts[normalized] = exclusion_counts.get(normalized, 0) + 1
    return {
        "template_count": geometry["template_count"],
        "initial_template_angle_combination_count": geometry[
            "initial_template_angle_combination_count"
        ],
        "base_raw_candidate_count": geometry["base_raw_candidate_count"],
        "base_unique_candidate_count": geometry["base_unique_candidate_count"],
        "angle_expanded_candidate_count": geometry["angle_expanded_candidate_count"],
        "valid_candidate_count": geometry["valid_candidate_count"],
        "invalid_candidate_count": geometry["invalid_candidate_count"],
        "counts_by_angle_deg": by_angle,
        "exclusion_reason_candidate_counts": exclusion_counts,
        "three_dimensional_manufacturability_verified": False,
    }


def _write_station_csv(
    path: Path, sensitivities: dict[str, dict[str, Any]]
) -> None:
    columns = [
        "comparison_set",
        "helix_angle_deg",
        "station_index",
        "x_m",
        "mean_channel_radius_m",
        "channel_width_m",
        "hydraulic_diameter_m",
        "total_flow_area_m2",
        "s_B_m",
        "s_H_m",
        "property_validity_status",
        "status",
        "Reynolds",
        "Prandtl",
        "Nusselt",
        "h_l_W_m2K",
        "flow_regime",
        "correlation_id",
        "reason",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for comparison_set, sensitivity in sensitivities.items():
            for angle in sensitivity["angles"]:
                for station in angle["stations"]:
                    writer.writerow(
                        {
                            **{name: station.get(name) for name in columns},
                            "comparison_set": comparison_set,
                            "helix_angle_deg": angle["helix_angle_deg"],
                        }
                    )


def _first_all_angle_valid_key(
    candidates: list[dict[str, Any]],
) -> tuple[int, float, float]:
    groups: dict[tuple[int, float, float], list[dict[str, Any]]] = {}
    for candidate in candidates:
        key = (
            int(candidate["channel_count"]),
            float(candidate["rib_thickness_m"]),
            float(candidate["channel_height_m"]),
        )
        groups.setdefault(key, []).append(candidate)
    eligible = [
        key
        for key, group in groups.items()
        if len(group) == 4 and all(candidate["valid"] for candidate in group)
    ]
    if not eligible:
        raise ValueError("no candidate is valid at all four Model H angles")
    return sorted(eligible)[0]


def run(archive: Path, output_directory: Path) -> dict[str, Any]:
    archive = archive.resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)
    build_root = ROOT / ".codex-build"
    build_root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="space_y_model_h_engine_", dir=build_root
    ) as temporary:
        extracted = Path(temporary)
        _safe_extract(archive, extracted)
        bundle = _find_bundle_root(extracted)
        namespace, captured_log = _run_supplied_engine(bundle)

        stn = namespace["stn"]
        baseline = namespace["res"]
        channel = namespace["ch"]
        cea_row = namespace["cea_row"]
        ipa = sys.modules["ipa_props"]
        station_count = len(stn["x"])
        pressure = float(namespace["P_COOL_IN"])
        pressure_profile = [pressure] * station_count
        temperature_profile = [
            None if value != value else float(value) for value in baseline["T_l"]
        ]
        properties = assess_supplied_ipa_states(
            temperature_K=temperature_profile,
            pressure_Pa_abs=pressure_profile,
            ipa=ipa,
        )

        geometry = generate_model_h_candidates(
            x_m=stn["x"],
            gas_side_radius_m=stn["r"],
            wall_thickness_m=float(namespace["T_WALL"]),
            throat_station_index=int(stn["i_throat"]),
            channel_width_limits_m=tuple(namespace["CH_WIDTH_LIMITS"]),
            minimum_rib_thickness_m=float(namespace["CH_MIN_RIB"]),
            allowed_channel_heights_m=[
                value * 1.0e-3 for value in MODEL_H_HEIGHT_CANDIDATES_MM
            ],
            station_ids=[f"ENG-{index:04d}" for index in range(station_count)],
        )
        candidate_key = (
            int(channel["N"]),
            float(channel["rib"]),
            float(channel["height"]),
        )
        baseline_sensitivity = evaluate_candidate_key_by_angle(
            candidates=geometry["candidates"],
            x_m=stn["x"],
            candidate_key=candidate_key,
            mdot_total_kg_s=float(cea_row["mdot_coolant[kg/s]"]),
            properties=properties,
            roughness_m=float(namespace["ROUGHNESS"]),
            thermal_boundary_condition=str(namespace["THERMAL_BC"]),
            fully_developed=True,
            colebrook_re_range=tuple(namespace["COLEBROOK_RE_RANGE"]),
            colebrook_relative_roughness_range=tuple(namespace["COLEBROOK_RR_RANGE"]),
            laminar_model=str(namespace["LAMINAR_MODEL"]),
        )
        all_angle_reference_key = _first_all_angle_valid_key(geometry["candidates"])
        all_angle_sensitivity = evaluate_candidate_key_by_angle(
            candidates=geometry["candidates"],
            x_m=stn["x"],
            candidate_key=all_angle_reference_key,
            mdot_total_kg_s=float(cea_row["mdot_coolant[kg/s]"]),
            properties=properties,
            roughness_m=float(namespace["ROUGHNESS"]),
            thermal_boundary_condition=str(namespace["THERMAL_BC"]),
            fully_developed=True,
            colebrook_re_range=tuple(namespace["COLEBROOK_RE_RANGE"]),
            colebrook_relative_roughness_range=tuple(namespace["COLEBROOK_RR_RANGE"]),
            laminar_model=str(namespace["LAMINAR_MODEL"]),
        )

        baseline_angle_summaries = [
            {key: value for key, value in item.items() if key != "stations"}
            for item in baseline_sensitivity["angles"]
        ]
        reference_angle_summaries = [
            {key: value for key, value in item.items() if key != "stations"}
            for item in all_angle_sensitivity["angles"]
        ]
        source_hashes = {
            name: _sha256(bundle / name) for name in sorted(REQUIRED_FILES)
        }
        result = {
            "run_classification": "PROVIDED_ENGINE_SCENARIO_MODEL_U_NOT_VALIDATED_DESIGN",
            "source_manifest": {
                "archive_name": archive.name,
                "archive_sha256": _sha256(archive),
                "source_file_sha256": source_hashes,
                "raw_bundle_committed": False,
                "current_repository_model_b_substituted_for_bundle_h_l_V1": True,
            },
            "engine_scenario": {
                "mdot_total_kg_s": float(cea_row["mdot_total[kg/s]"]),
                "mdot_coolant_kg_s": float(cea_row["mdot_coolant[kg/s]"]),
                "chamber_pressure_Pa_abs": float(cea_row["p_c[Pa]"]),
                "of_ratio": float(cea_row["OF[-]"]),
                "coolant_inlet_temperature_K": float(namespace["T_FUEL"]),
                "coolant_inlet_pressure_Pa_abs": pressure,
                "station_count": station_count,
                "throat_station_index": int(stn["i_throat"]),
                "throat_x_m": float(stn["x_t"]),
                "coolant_flow_direction": "NOZZLE_EXIT_TO_CHAMBER",
                "coolant_pressure_profile": "CONSTANT_PLACEHOLDER_NO_PRESSURE_LOSS_MODEL",
                "input_status": {
                    "contraction_ratio": "INPUT_U_SOURCE_MARKED_BLANK",
                    "characteristic_length": "INPUT_U_SOURCE_MARKED_BLANK",
                    "wall_and_manufacturing_limits": "MODEL_U_SOURCE_MARKED_VERIFICATION_ONLY",
                    "coolant_inlet_pressure": "INPUT_U_SOURCE_MARKED_BLANK",
                },
            },
            "provided_model_b_baseline": {
                "selected_candidate_id": channel["id"],
                "candidate_key": {
                    "channel_count": candidate_key[0],
                    "rib_thickness_m": candidate_key[1],
                    "channel_height_m": candidate_key[2],
                },
                "coolant_outlet_temperature_K": float(baseline["T_out"]),
                "maximum_gas_side_wall_temperature_K": float(baseline["T_wg_max"]),
                "cooling_failed": bool(baseline["failed"]),
                "cooling_failure_reason": baseline["fail_reason"],
                "source_reported_wall_ok": bool(baseline["ok_wall"]),
                "source_reported_boil_ok": bool(baseline["ok_boil"]),
                "accepted_as_design_validation": False,
            },
            "ipa_property_gate": {
                key: value
                for key, value in properties.items()
                if key
                not in {
                    "records",
                    "density_kg_m3",
                    "viscosity_Pa_s",
                    "cp_J_kgK",
                    "conductivity_W_mK",
                    "status_by_station",
                }
            },
            "model_h_geometry": _geometry_summary(geometry),
            "selected_candidate_angle_sensitivity": {
                "selection_basis": "PROVIDED_MODEL_B_BASELINE_CANDIDATE",
                "angle_summaries": baseline_angle_summaries,
                "beta_zero_model_b_regression": baseline_sensitivity[
                    "beta_zero_model_b_regression"
                ],
                "stationwise_h_l_relative_to_beta_zero_percent": baseline_sensitivity[
                    "stationwise_h_l_relative_to_beta_zero_percent"
                ],
                "coolant_state_coupling": baseline_sensitivity["coolant_state_coupling"],
                "recoupled_helical_wall_coolant_march": False,
            },
            "all_angle_reference_sensitivity": {
                "selection_basis": "FIRST_DETERMINISTIC_ALL_ANGLE_VALID_REFERENCE_NOT_SELECTED_DESIGN",
                "candidate_key": all_angle_sensitivity["candidate_key"],
                "angle_summaries": reference_angle_summaries,
                "beta_zero_model_b_regression": all_angle_sensitivity[
                    "beta_zero_model_b_regression"
                ],
                "stationwise_h_l_relative_to_beta_zero_percent": all_angle_sensitivity[
                    "stationwise_h_l_relative_to_beta_zero_percent"
                ],
                "coolant_state_coupling": all_angle_sensitivity["coolant_state_coupling"],
                "recoupled_helical_wall_coolant_march": False,
            },
            "interface_findings": {
                "h_l_definition": "LOCAL_COEFFICIENT_ON_ACTUAL_CHANNEL_WETTED_SURFACE",
                "helical_length_applied_in_model_h_path": True,
                "helical_length_applied_in_existing_cooling_loop_area": False,
                "double_length_correction_applied": False,
                "required_follow_up": (
                    "cooling_loop_V1 must accept an explicit station segment length for "
                    "helical wetted area before beta>0 wall/coolant recoupling"
                ),
            },
            "limitations": [
                "The engine source labels several geometry, material, manufacturing, and supply inputs as BLANK or verification-only.",
                "The supplied IPA transport properties are temperature-only saturated-liquid correlations and omit pressure dependence.",
                "Stations above the common 408 K transport limit are OUTSIDE and are not extrapolated by this integration result.",
                "The existing cooling loop holds pressure constant and does not calculate Darcy or helical pressure loss.",
                "Angle sensitivity holds the provided Model B coolant temperature profile fixed; beta>0 is not a recoupled wall/coolant solution.",
                "Dean secondary flow, curvature-shifted transition, 3D interference, manifold distribution, and helical pressure loss are not modelled.",
            ],
            "captured_engine_log_line_count": len(captured_log),
            "experimental_validation": False,
            "validated_engine_prediction": False,
            "physical_validation_v01_v12": "NOT_RUN",
            "safety_approval": False,
        }

        output_directory.mkdir(parents=True, exist_ok=True)
        summary_path = output_directory / "provided_engine_model_h_summary.json"
        summary_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        _write_station_csv(
            output_directory / "provided_engine_selected_candidate_stations.csv",
            {
                "PROVIDED_MODEL_B_BASELINE": baseline_sensitivity,
                "ALL_ANGLE_VALID_REFERENCE": all_angle_sensitivity,
            },
        )
        property_path = output_directory / "provided_engine_ipa_property_gate.csv"
        with property_path.open("w", encoding="utf-8-sig", newline="") as stream:
            writer = csv.DictWriter(
                stream,
                fieldnames=[
                    "station_index",
                    "temperature_K",
                    "pressure_Pa_abs",
                    "saturation_temperature_K",
                    "status",
                    "reason",
                    "pressure_effects_in_transport_properties",
                ],
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(properties["records"])
        return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--engine-zip", required=True, type=Path)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "artifacts" / "model_h_engine_integration",
    )
    args = parser.parse_args()
    result = run(args.engine_zip, args.output_dir)
    print(json.dumps({
        "run_classification": result["run_classification"],
        "property_status_counts": result["ipa_property_gate"]["status_counts"],
        "geometry": result["model_h_geometry"],
        "baseline_angle_summaries": result["selected_candidate_angle_sensitivity"]["angle_summaries"],
        "all_angle_reference_summaries": result["all_angle_reference_sensitivity"]["angle_summaries"],
        "beta_zero_regression": result["selected_candidate_angle_sensitivity"]["beta_zero_model_b_regression"],
        "output_dir": str(args.output_dir.resolve()),
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
