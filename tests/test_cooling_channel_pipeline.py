from __future__ import annotations

import copy
import csv
import json
from pathlib import Path
import tempfile
import unittest

import jsonschema

from space_y.coolant import (
    build_friction_domain,
    build_heat_transfer_domain,
    evaluate_rectangular_cooling_hl,
)
from space_y.cooling_applicability import build_cooling_applicability_policy
from space_y.cooling_channel_candidates import (
    write_candidate_results_csv,
    write_candidate_summary_csv,
)
from space_y.cooling_channel_pipeline import (
    solve_cooling_channel_pipeline,
    solve_cooling_channel_pipeline_file,
    write_pipeline_geometry_csv,
)
from space_y.cooling_channel_templates import adjacent_channel_count_candidates
from space_y.cli import build_parser


ROOT = Path(__file__).resolve().parents[1]


class CoolingChannelPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request = json.loads(
            (
                ROOT / "examples/cooling_channel_pipeline_synthetic_80.json"
            ).read_text(encoding="utf-8")
        )
        cls.result = solve_cooling_channel_pipeline(cls.request)

    def test_schema_accepts_synthetic_and_readiness_inputs(self) -> None:
        schema = json.loads(
            (
                ROOT / "schemas/SPACE_Y_cooling_channel_pipeline.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator.check_schema(schema)
        for path in (
            ROOT / "examples/cooling_channel_pipeline_synthetic_80.json",
            ROOT / "config/SPACE_Y_cooling_channel_pipeline_readiness.json",
        ):
            with self.subTest(path=path.name):
                jsonschema.Draft202012Validator(schema).validate(
                    json.loads(path.read_text(encoding="utf-8"))
                )

    def test_cli_command_keeps_existing_sweep_and_adds_pipeline(self) -> None:
        parser = build_parser()
        pipeline = parser.parse_args(
            ["cooling-channel-pipeline", "request.json"]
        )
        sweep = parser.parse_args(["cooling-channel-sweep", "request.json"])
        self.assertEqual(pipeline.command, "cooling-channel-pipeline")
        self.assertEqual(sweep.command, "cooling-channel-sweep")

    def test_full_fixture_generates_exactly_80_templates(self) -> None:
        self.assertEqual(self.result["template_count"], 80)
        self.assertEqual(len(self.result["templates"]), 80)
        self.assertTrue(
            all(item["channel_count"] is None for item in self.result["templates"])
        )

    def test_template_only_state_does_not_invent_geometry_or_hl(self) -> None:
        result = solve_cooling_channel_pipeline_file(
            ROOT / "config/SPACE_Y_cooling_channel_pipeline_readiness.json",
            ROOT / "schemas/SPACE_Y_cooling_channel_pipeline.schema.json",
        )
        self.assertEqual(result["status"], "INPUT_U")
        self.assertEqual(result["pipeline_stage"], "TEMPLATE_ONLY")
        self.assertEqual(result["template_count"], 80)
        self.assertEqual(result["geometry_candidates"], [])
        self.assertEqual(result["candidates"], [])

    def test_geometry_without_manufacturing_or_coolant_stops_before_hl(self) -> None:
        request = copy.deepcopy(self.request)
        for key in ("manufacturing_constraints", "coolant_conditions", "model_conditions"):
            request.pop(key)
        result = solve_cooling_channel_pipeline(request)
        self.assertEqual(result["pipeline_stage"], "GEOMETRY_ONLY")
        self.assertEqual(result["status"], "INPUT_U")
        self.assertTrue(result["geometry_candidates"])
        self.assertTrue(
            all(
                "calculation" not in station
                for candidate in result["geometry_candidates"]
                for station in candidate["stations"]
            )
        )

    def test_manufacturing_geometry_without_coolant_stops_before_hl(self) -> None:
        request = copy.deepcopy(self.request)
        request.pop("coolant_conditions")
        request.pop("model_conditions")
        result = solve_cooling_channel_pipeline(request)
        self.assertEqual(result["pipeline_stage"], "GEOMETRY_ONLY")
        self.assertEqual(result["geometrically_valid_candidate_count"], 156)
        self.assertTrue(
            all(
                "calculation" not in station
                for candidate in result["geometry_candidates"]
                for station in candidate["stations"]
            )
        )

    def test_integer_n_and_target_width_mapping_are_preserved(self) -> None:
        first_template = self.result["templates"][0]
        throat_radius = next(
            item["mean_channel_radius_m"]
            for item in self.request["geometry_input"]["mean_radius_profile"]
            if item["station_id"]
            == self.request["geometry_input"]["throat_station_id"]
        )
        expected = adjacent_channel_count_candidates(first_template, throat_radius)
        linked = [
            item
            for item in self.result["geometry_candidates"]
            if any(
                origin["template_id"] == first_template["template_id"]
                for origin in item["template_origins"]
            )
        ]
        self.assertEqual(
            [item["channel_count"] for item in linked],
            expected["adjacent_integer_channel_counts"],
        )
        for item in linked:
            origin = next(
                origin
                for origin in item["template_origins"]
                if origin["template_id"] == first_template["template_id"]
            )
            self.assertAlmostEqual(
                origin["actual_throat_width_m"]
                - origin["reference_width_m"],
                origin["actual_minus_reference_width_m"],
            )

    def test_no_union_cartesian_product_creates_unlinked_candidates(self) -> None:
        templates = {item["template_id"]: item for item in self.result["templates"]}
        throat_radius = 0.02
        for candidate in self.result["geometry_candidates"]:
            self.assertTrue(candidate["template_origins"])
            for origin in candidate["template_origins"]:
                template = templates[origin["template_id"]]
                mapping = adjacent_channel_count_candidates(template, throat_radius)
                self.assertIn(
                    candidate["channel_count"],
                    mapping["adjacent_integer_channel_counts"],
                )
                self.assertEqual(
                    candidate["rib_thickness_m"], template["rib_thickness_m"]
                )
                self.assertEqual(
                    candidate["channel_height_m"], template["channel_height_m"]
                )

    def test_duplicate_physical_geometry_is_evaluated_once_with_all_origins(self) -> None:
        request = copy.deepcopy(self.request)
        grid = request["template_grid"]
        grid["reference_width_candidates_m"] = [0.0009, 0.0009]
        grid["rib_thickness_candidates_m"] = [0.0009]
        grid["channel_height_candidates_m"] = [0.0012]
        grid["expected_template_count"] = 2
        result = solve_cooling_channel_pipeline(request)
        self.assertEqual(result["raw_geometry_candidate_count"], 4)
        self.assertEqual(result["unique_geometry_candidate_count"], 2)
        self.assertEqual(result["duplicate_geometry_candidate_count"], 2)
        self.assertTrue(
            all(len(item["template_origins"]) == 2 for item in result["candidates"])
        )

    def test_candidate_ids_and_output_are_deterministic(self) -> None:
        repeated = solve_cooling_channel_pipeline(self.request)
        self.assertEqual(
            [item["geometry_candidate_id"] for item in repeated["geometry_candidates"]],
            [item["geometry_candidate_id"] for item in self.result["geometry_candidates"]],
        )
        self.assertEqual(
            json.loads(json.dumps(repeated)), json.loads(json.dumps(self.result))
        )

    def test_geometry_constraints_exclude_before_hl(self) -> None:
        request = copy.deepcopy(self.request)
        request["manufacturing_constraints"]["minimum_rib_thickness_m"] = 0.002
        result = solve_cooling_channel_pipeline(request)
        self.assertEqual(result["status"], "OUTSIDE")
        self.assertEqual(result["geometrically_valid_candidate_count"], 0)
        self.assertEqual(result["successful_station_count"], 0)
        self.assertTrue(
            all(item["decision"] == "EXCLUDE_GEOMETRIC_CONSTRAINT" for item in result["candidates"])
        )

    def test_pipeline_hl_matches_existing_local_core(self) -> None:
        candidate = next(
            item for item in self.result["candidates"] if item["decision"] == "RETAIN_FOR_INTEGRATION_REVIEW"
        )
        station = candidate["stations"][0]
        model = self.request["model_conditions"]
        fluid = self.request["coolant_conditions"]["default_fluid_properties"]
        local = evaluate_rectangular_cooling_hl(
            coolant_mass_flow_kg_s=self.request["coolant_conditions"]["coolant_mass_flow_kg_s"],
            width_m=station["channel_width_m"],
            height_m=station["channel_height_m"],
            channel_count=candidate["channel_count"],
            viscosity_Pa_s=fluid["viscosity_Pa_s"],
            cp_J_kgK=fluid["cp_J_kgK"],
            conductivity_W_mK=fluid["conductivity_W_mK"],
            equivalent_sand_roughness_m=self.request["manufacturing_constraints"]["equivalent_sand_roughness_m"],
            closure_geometry=model["closure_geometry"],
            fully_developed=model["fully_developed"],
            thermal_boundary_condition=model["thermal_boundary_condition"],
            heat_transfer_domain=build_heat_transfer_domain(model["heat_transfer_domain"]),
            heat_transfer_thermal_boundary_condition=model["heat_transfer_thermal_boundary_condition"],
            friction_domain=build_friction_domain(model["friction_domain"]),
            density_kg_m3=fluid["density_kg_m3"],
            applicability_policy=build_cooling_applicability_policy(model["applicability_policy"]),
        )
        values = station["calculation"]["results"]
        self.assertAlmostEqual(values["h_l_W_m2K"], local.h_l_W_m2K)
        self.assertAlmostEqual(values["Reynolds"], local.Reynolds)
        self.assertAlmostEqual(values["Prandtl"], local.Prandtl)

    def test_full_path_applicability_and_recommendation_are_connected(self) -> None:
        self.assertEqual(self.result["status"], "MODEL_U")
        self.assertEqual(self.result["successful_station_count"], 468)
        retained = [
            item for item in self.result["candidates"] if item["decision"] == "RETAIN_FOR_INTEGRATION_REVIEW"
        ]
        self.assertEqual(len(retained), 156)
        self.assertTrue(
            all(
                item["summary"]["applicability_assessment"]["continuous_path_claim"]
                is False
                for item in retained
            )
        )
        recommendation = self.result["candidate_recommendation"]
        self.assertEqual(recommendation["status"], "PRELIMINARY_RELATIVE_RECOMMENDATION")
        self.assertTrue(recommendation["pareto_candidate_ids"])
        self.assertFalse(recommendation["final_design_selection"])

    def test_synthetic_identity_and_safety_flags_are_never_promoted(self) -> None:
        self.assertEqual(self.result["fixture_classification"], "SYNTHETIC_NON_DESIGN")
        self.assertFalse(self.result["validated_engine_prediction"])
        self.assertEqual(self.result["physical_validation_v01_v12"], "NOT_RUN")
        self.assertFalse(self.result["safety_approval"])
        self.assertFalse(self.result["final_design_selection"])

    def test_engine_full_run_requires_evidence_and_exact_input_classifications(self) -> None:
        request = copy.deepcopy(self.request)
        request["purpose"] = "ENGINE_CANDIDATE_SCREENING"
        request["geometry_input"]["classification"] = "TEAM_GEOMETRY_EVIDENCE"
        request["manufacturing_constraints"]["classification"] = (
            "TEAM_APPROVED_MANUFACTURING_CONSTRAINTS"
        )
        request["coolant_conditions"]["fluid_identity"] = {
            "classification": "EXACT_2_PROPANOL",
            "canonical_name": "2-propanol",
            "cas_number": "67-63-0",
        }
        result = solve_cooling_channel_pipeline(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("input_evidence_complete", result["reason"])

    def test_saved_json_and_csv_artifacts_are_reproducible(self) -> None:
        result = solve_cooling_channel_pipeline_file(
            ROOT / "examples/cooling_channel_pipeline_synthetic_80.json",
            ROOT / "schemas/SPACE_Y_cooling_channel_pipeline.schema.json",
        )
        saved = json.loads(
            (
                ROOT / "artifacts/cooling_channel_pipeline/synthetic_80_result.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(json.loads(json.dumps(result)), saved)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            generated = Path(directory)
            geometry = generated / "geometry.csv"
            stations = generated / "stations.csv"
            summary = generated / "summary.csv"
            write_pipeline_geometry_csv(geometry, result)
            write_candidate_results_csv(stations, result)
            write_candidate_summary_csv(summary, result)
            pairs = (
                (geometry, ROOT / "artifacts/cooling_channel_pipeline/synthetic_80_geometry.csv"),
                (stations, ROOT / "artifacts/cooling_channel_pipeline/synthetic_80_stations.csv"),
                (summary, ROOT / "artifacts/cooling_channel_pipeline/synthetic_80_summary.csv"),
            )
            for actual, expected in pairs:
                with self.subTest(file=actual.name):
                    self.assertEqual(
                        actual.read_text(encoding="utf-8"),
                        expected.read_text(encoding="utf-8"),
                    )

    def test_geometry_csv_preserves_all_template_sources(self) -> None:
        path = ROOT / "artifacts/cooling_channel_pipeline/synthetic_80_geometry.csv"
        with path.open(encoding="utf-8") as stream:
            rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), self.result["unique_geometry_candidate_count"])
        self.assertTrue(all(row["template_ids"] for row in rows))


if __name__ == "__main__":
    unittest.main()
