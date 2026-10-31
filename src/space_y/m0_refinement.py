from __future__ import annotations

import copy
import json
import math
from pathlib import Path
from typing import Any

from .external import sha256_file
from .m0_marching import solve_coupled_multisegment


class RefinementStop(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


def _positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise RefinementStop("INPUT_U", f"{name} must be finite and positive")
    return number


def _source(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RefinementStop("INPUT_U", f"{name} source is required")
    return value


def _failure(run_id: Any, status: str, reason: str, **details: Any) -> dict[str, Any]:
    result = {
        "run_id": run_id,
        "status": status,
        "numerical_status": status if status == "NUM_U" else "NOT_RUN",
        "reason": reason,
        "levels": [],
        "grid_sensitivity": None,
        "software_verification": {
            "refinement_execution": status,
            "physical_validation_claim": "NONE",
        },
        "physical_validation_v01_v12": "NOT_RUN",
        "validated_engine_prediction": False,
    }
    result.update(details)
    return result


def _build_level_request(suite: dict[str, Any], level: dict[str, Any]) -> dict[str, Any]:
    base = suite["base_marching_request"]
    template = suite["segment_template"]
    segments = level.get("segments")
    if not isinstance(segments, list) or not segments:
        raise RefinementStop("INPUT_U", "each refinement level needs explicit segments")
    expected_count = int(level["cell_count"])
    if len(segments) != expected_count:
        raise RefinementStop("INPUT_U", "refinement level cell_count differs from segment count")
    fractions = [_positive(item["domain_fraction"], "segment domain fraction") for item in segments]
    fraction_tolerance = _positive(
        suite["partition_checks"]["fraction_sum_tolerance"], "fraction sum tolerance"
    )
    if abs(sum(fractions) - 1.0) > fraction_tolerance:
        raise RefinementStop("INPUT_U", "segment domain fractions must sum to one")
    if float(template["geometry"]["local_loss_coefficient"]) != 0.0:
        raise RefinementStop(
            "INPUT_U",
            "refinement fixture requires zero local-loss coefficient; distributed K scaling is undefined",
        )

    built_segments: list[dict[str, Any]] = []
    for item, fraction in zip(segments, fractions):
        _source(item.get("partition_source"), f"{item.get('segment_id')} partition")
        mapping = item.get("cea_station_mapping")
        if not isinstance(mapping, dict):
            raise RefinementStop("INPUT_U", "every refinement cell needs an explicit gas mapping")
        if mapping.get("method") != "EXPLICIT_NO_INTERPOLATION":
            raise RefinementStop("INPUT_U", "refinement gas mapping must prohibit interpolation")
        _source(mapping.get("source"), f"{item.get('segment_id')} gas mapping")
        geometry = copy.deepcopy(template["geometry"])
        for key in (
            "length_m",
            "gas_area_m2",
            "coolant_area_m2",
            "wall_conduction_area_m2",
        ):
            geometry[key] = float(geometry[key]) * fraction
        radiation = copy.deepcopy(template["radiation"])
        radiation["q_rad_net_W"] = float(radiation["q_rad_net_W"]) * fraction
        built_segments.append(
            {
                "segment_id": item["segment_id"],
                "gas_axial_coordinate_m": item["gas_axial_coordinate_m"],
                "gas_axial_coordinate_source": item["gas_axial_coordinate_source"],
                "cea_station_mapping": copy.deepcopy(mapping),
                "geometry": geometry,
                "radiation": radiation,
                "gas_wall_numerics": copy.deepcopy(template["gas_wall_numerics"]),
                "coupled_numerics": copy.deepcopy(template["coupled_numerics"]),
            }
        )

    return {
        "purpose": "NON_DESIGN_INTERFACE_VERIFICATION",
        "run_id": f"{suite['run_id']}:{level['label']}",
        "case_id": suite["case_id"],
        "geometry_version": suite["geometry_version"],
        "spatial_basis": f"{suite['spatial_basis']}:{level['label']}",
        "model_scope": "STEADY_SINGLE_PATH_MULTISEGMENT_GAS_WALL_COOLANT",
        "flow_boundary_mode": "PRESCRIBED_MASS_FLOW",
        "formula_version": base["formula_version"],
        "formula_source": base["formula_source"],
        "gas_axial_coordinate_reference": suite["gas_axial_coordinate_reference"],
        "gas_axial_order_segment_ids": copy.deepcopy(level["gas_axial_order_segment_ids"]),
        "coolant_flow_direction": level["coolant_flow_direction"],
        "coolant_flow_order_segment_ids": copy.deepcopy(
            level["coolant_flow_order_segment_ids"]
        ),
        "coolant_inlet_segment_id": level["coolant_flow_order_segment_ids"][0],
        "coolant_outlet_segment_id": level["coolant_flow_order_segment_ids"][-1],
        "coolant_boundary": copy.deepcopy(base["coolant_boundary"]),
        "coolant_property_model": copy.deepcopy(base["coolant_property_model"]),
        "single_phase_bulk_domain": copy.deepcopy(base["single_phase_bulk_domain"]),
        "coolant_heat_transfer_closure": copy.deepcopy(
            base["coolant_heat_transfer_closure"]
        ),
        "coolant_friction_closure": copy.deepcopy(base["coolant_friction_closure"]),
        "marching_numerics": copy.deepcopy(base["marching_numerics"]),
        "segments": built_segments,
    }


def _qoi_change(coarse: dict[str, Any], fine: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for qoi_id in ("Y01", "Y02", "Y03", "Y04", "Y05"):
        coarse_value = float(coarse["qoi"][qoi_id]["value"])
        fine_value = float(fine["qoi"][qoi_id]["value"])
        absolute = fine_value - coarse_value
        result[qoi_id] = {
            "coarse_value": coarse_value,
            "fine_value": fine_value,
            "unit": fine["qoi"][qoi_id]["unit"],
            "signed_change": absolute,
            "absolute_change": abs(absolute),
            "relative_change_to_fine": abs(absolute) / max(abs(fine_value), 1.0e-300),
        }
    return result


def solve_refinement_suite(
    suite: dict[str, Any],
    cea_records_by_level: dict[str, dict[str, dict[str, Any]]],
    cea_hashes_by_level: dict[str, dict[str, str]] | None = None,
) -> dict[str, Any]:
    """Run an N/2N/4N non-design manufactured-grid sensitivity suite."""
    level_outputs: list[dict[str, Any]] = []
    try:
        if suite.get("purpose") != "NON_DESIGN_NUMERICAL_VERIFICATION":
            raise RefinementStop("INPUT_U", "only non-design numerical verification is enabled")
        if suite.get("model_scope") != "N_2N_4N_SINGLE_PATH_GRID_SENSITIVITY":
            raise RefinementStop("MODEL_U", "only N/2N/4N single-path sensitivity is implemented")
        if suite.get("gas_state_policy") != "EXPLICIT_PER_CELL_NO_INTERPOLATION":
            raise RefinementStop("INPUT_U", "explicit per-cell gas states are required")
        for name in (
            "run_id",
            "case_id",
            "geometry_version",
            "spatial_basis",
            "gas_axial_coordinate_reference",
            "manufactured_fixture_source",
        ):
            _source(suite.get(name), name)
        levels = suite.get("levels")
        if not isinstance(levels, list) or [item.get("label") for item in levels] != [
            "N",
            "2N",
            "4N",
        ]:
            raise RefinementStop("INPUT_U", "refinement levels must be ordered N, 2N, 4N")
        counts = [int(item["cell_count"]) for item in levels]
        if counts[0] <= 0 or counts != [counts[0], 2 * counts[0], 4 * counts[0]]:
            raise RefinementStop("INPUT_U", "refinement cell counts must follow N, 2N, 4N")

        heat_floor = _positive(
            suite["normalization"]["heat_rate_floor_W"], "heat-rate normalization floor"
        )
        pressure_floor = _positive(
            suite["normalization"]["pressure_drop_floor_Pa"],
            "pressure-drop normalization floor",
        )
        _source(suite["normalization"].get("source"), "normalization")

        solved_levels: list[dict[str, Any]] = []
        for level in levels:
            label = str(level["label"])
            request = _build_level_request(suite, level)
            records = cea_records_by_level.get(label)
            if not isinstance(records, dict):
                raise RefinementStop("INPUT_U", f"CEA records are missing for {label}")
            result = solve_coupled_multisegment(
                request,
                records,
                (cea_hashes_by_level or {}).get(label),
            )
            level_record = {
                "label": label,
                "cell_count": int(level["cell_count"]),
                "gas_state_policy": suite["gas_state_policy"],
                "result": result,
            }
            level_outputs.append(level_record)
            if result.get("numerical_status") != "PASS":
                return _failure(
                    suite.get("run_id"),
                    str(result.get("status", "NUM_U")),
                    f"refinement level {label} failed: {result.get('reason', 'solve did not pass')}",
                    levels=level_outputs,
                    stopped_at_level=label,
                )
            checks = result["checks"]
            heat_denominator = max(abs(float(checks["total_heat_into_coolant_W"])), heat_floor)
            pressure_denominator = max(abs(float(result["qoi"]["Y02"]["value"])), pressure_floor)
            mass_flow = float(result["coolant_path_boundary"]["inlet"]["mass_flow_kg_s"])
            level_record["normalized_residuals"] = {
                "total_energy": abs(float(checks["total_energy_residual_W"]))
                / heat_denominator,
                "segment_coolant_energy_max": abs(
                    float(checks["segment_coolant_energy_residual_max_W"])
                )
                / heat_denominator,
                "segment_wall_energy_max": abs(
                    float(checks["segment_wall_energy_residual_max_W"])
                )
                / heat_denominator,
                "pressure_drop_sum": abs(float(checks["pressure_drop_sum_residual_Pa"]))
                / pressure_denominator,
                "mass": abs(float(checks["mass_residual_max_kg_s"]))
                / max(abs(mass_flow), 1.0e-300),
            }
            solved_levels.append(result)

        changes_n_2n = _qoi_change(solved_levels[0], solved_levels[1])
        changes_2n_4n = _qoi_change(solved_levels[1], solved_levels[2])
        observed_order: dict[str, float | None] = {}
        for qoi_id in ("Y01", "Y02", "Y03", "Y04", "Y05"):
            first = changes_n_2n[qoi_id]["absolute_change"]
            second = changes_2n_4n[qoi_id]["absolute_change"]
            observed_order[qoi_id] = (
                math.log(first / second, 2.0) if first > 0 and second > 0 else None
            )
        asymptotic_regime = (
            "NOT_ESTABLISHED"
            if any(value is None or value <= 0 for value in observed_order.values())
            else "POSITIVE_ORDER_OBSERVED_NOT_PHYSICALLY_VALIDATED"
        )

        return {
            "run_id": suite["run_id"],
            "case_id": suite["case_id"],
            "status": "MODEL_U",
            "numerical_status": "PASS",
            "model_scope": suite["model_scope"],
            "gas_state_policy": suite["gas_state_policy"],
            "levels": level_outputs,
            "grid_sensitivity": {
                "N_to_2N": changes_n_2n,
                "2N_to_4N": changes_2n_4n,
                "observed_order_from_three_levels": observed_order,
                "asymptotic_regime_evidence": asymptotic_regime,
                "fine_grid_is_reference_not_truth": True,
                "acceptance_status": "RECORDED_NO_ENGINE_ERROR_BUDGET",
                "reason": (
                    "synthetic arithmetic refinement has no approved engine QoI error budget "
                    "and does not establish an asymptotic physical-validation regime"
                ),
            },
            "software_verification": {
                "refinement_execution": "PASS",
                "conservation_and_state_transfer": "PASS_FOR_SYNTHETIC_FIXTURE",
                "physical_validation_claim": "NONE",
            },
            "not_implemented": [
                "actual nozzle-coordinate to CEA-station validation",
                "CEA station interpolation",
                "actual IPA property or phase model",
                "parallel flow distribution",
                "axial wall conduction",
                "boiling or two-phase model",
                "design optimization or safety allowables",
            ],
            "physical_validation_v01_v12": "NOT_RUN",
            "validated_engine_prediction": False,
        }
    except RefinementStop as error:
        return _failure(suite.get("run_id"), error.status, str(error), levels=level_outputs)
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _failure(
            suite.get("run_id") if isinstance(suite, dict) else None,
            "INPUT_U",
            f"invalid or missing refinement input: {error}",
            levels=level_outputs,
        )


def solve_refinement_suite_file(
    path: str | Path, schema_path: str | Path | None = None
) -> dict[str, Any]:
    suite_path = Path(path).resolve()
    suite = json.loads(suite_path.read_text(encoding="utf-8"))
    if not isinstance(suite, dict):
        raise ValueError("refinement suite must be a JSON object")
    if schema_path is not None:
        try:
            import jsonschema
        except ImportError:
            pass
        else:
            schema = json.loads(Path(schema_path).read_text(encoding="utf-8"))
            errors = sorted(
                jsonschema.Draft202012Validator(schema).iter_errors(suite),
                key=lambda item: list(item.path),
            )
            if errors:
                return _failure(
                    suite.get("run_id"),
                    "INPUT_U",
                    "refinement suite schema validation failed",
                    schema_errors=[
                        {
                            "path": ".".join(str(part) for part in error.absolute_path) or "$",
                            "message": error.message,
                        }
                        for error in errors
                    ],
                )
    records_by_level: dict[str, dict[str, dict[str, Any]]] = {}
    hashes_by_level: dict[str, dict[str, str]] = {}
    try:
        for level in suite["levels"]:
            label = str(level["label"])
            records_by_level[label] = {}
            hashes_by_level[label] = {}
            for segment in level["segments"]:
                segment_id = str(segment["segment_id"])
                artifact_path = Path(segment["cea_station_mapping"]["cea_run_artifact"])
                if not artifact_path.is_absolute():
                    artifact_path = (suite_path.parent / artifact_path).resolve()
                records_by_level[label][segment_id] = json.loads(
                    artifact_path.read_text(encoding="utf-8")
                )
                hashes_by_level[label][segment_id] = sha256_file(artifact_path)
    except (KeyError, OSError, json.JSONDecodeError) as error:
        return _failure(
            suite.get("run_id"), "INPUT_U", f"cannot read explicit gas artifact: {error}"
        )
    return solve_refinement_suite(suite, records_by_level, hashes_by_level)
