from __future__ import annotations

import copy
import csv
import json
import math
from pathlib import Path
import tempfile
import unittest

import jsonschema

from space_y.coolant import (
    build_friction_domain,
    build_heat_transfer_domain,
    evaluate_rectangular_cooling_hl,
)
from space_y.cooling_channel_candidates import (
    generate_channel_geometry_candidates,
    solve_cooling_channel_candidates,
    solve_cooling_channel_candidates_file,
    write_candidate_results_csv,
)


ROOT = Path(__file__).resolve().parents[1]


class CoolingChannelCandidateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request = json.loads(
            (
                ROOT / "examples/cooling_channel_sweep_synthetic_axial.json"
            ).read_text(encoding="utf-8")
        )

    def test_schema_and_file_runner(self) -> None:
        schema = json.loads(
            (
                ROOT / "schemas/SPACE_Y_cooling_channel_sweep.schema.json"
            ).read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(self.request)
        result = solve_cooling_channel_candidates_file(
            ROOT / "examples/cooling_channel_sweep_synthetic_axial.json",
            ROOT / "schemas/SPACE_Y_cooling_channel_sweep.schema.json",
        )
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(len(result["provenance"]["request_sha256"]), 64)
        self.assertEqual(len(result["provenance"]["schema_sha256"]), 64)

    def test_saved_json_and_csv_artifacts_are_reproducible(self) -> None:
        result = solve_cooling_channel_candidates_file(
            ROOT / "examples/cooling_channel_sweep_synthetic_axial.json",
            ROOT / "schemas/SPACE_Y_cooling_channel_sweep.schema.json",
        )
        saved_json = json.loads(
            (
                ROOT
                / "artifacts/cooling_channel_sweep/synthetic_axial_result.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(json.loads(json.dumps(result)), saved_json)

        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            regenerated_csv = Path(directory) / "synthetic_axial_stations.csv"
            write_candidate_results_csv(regenerated_csv, result)
            regenerated_text = regenerated_csv.read_text(encoding="utf-8")
        saved_text = (
            ROOT / "artifacts/cooling_channel_sweep/synthetic_axial_stations.csv"
        ).read_text(encoding="utf-8")
        self.assertEqual(regenerated_text, saved_text)

    def test_cartesian_candidate_generation_is_deterministic(self) -> None:
        candidates = generate_channel_geometry_candidates(
            self.request["candidate_space"]
        )
        self.assertEqual(len(candidates), 4)
        self.assertEqual(
            [
                (
                    item["candidate_id"],
                    item["channel_count"],
                    item["rib_thickness_m"],
                    item["channel_height_m"],
                )
                for item in candidates
            ],
            [
                ("CH-0001", 60, 0.0006, 0.0012),
                ("CH-0002", 60, 0.0009, 0.0012),
                ("CH-0003", 80, 0.0006, 0.0012),
                ("CH-0004", 80, 0.0009, 0.0012),
            ],
        )

    def test_constant_rib_width_pitch_and_width(self) -> None:
        candidate = generate_channel_geometry_candidates(
            self.request["candidate_space"]
        )[2]
        throat = next(
            station for station in candidate["stations"] if station["is_throat"]
        )
        expected_pitch = 2.0 * math.pi * 0.02 / 80.0
        self.assertAlmostEqual(throat["circumferential_pitch_m"], expected_pitch)
        self.assertAlmostEqual(
            throat["channel_width_m"], expected_pitch - 0.0006
        )

    def test_width_constraints_exclude_whole_candidate_before_hl(self) -> None:
        result = solve_cooling_channel_candidates(self.request)
        first, second, third, fourth = result["candidates"]
        self.assertEqual(first["decision"], "EXCLUDE_GEOMETRIC_CONSTRAINT")
        self.assertEqual(second["decision"], "EXCLUDE_GEOMETRIC_CONSTRAINT")
        self.assertEqual(third["geometric_validity_status"], "PASS")
        self.assertEqual(fourth["decision"], "EXCLUDE_GEOMETRIC_CONSTRAINT")
        self.assertTrue(
            all("calculation" not in station for station in first["stations"])
        )

    def test_valid_candidate_has_axial_hl_distribution(self) -> None:
        result = solve_cooling_channel_candidates(self.request)
        candidate = result["candidates"][2]
        self.assertEqual(candidate["decision"], "RETAIN_FOR_INTEGRATION_REVIEW")
        self.assertEqual(candidate["summary"]["valid_calculation_station_count"], 3)
        self.assertIsNotNone(candidate["summary"]["throat_h_l_W_m2K"])
        self.assertLess(
            candidate["summary"]["minimum_h_l_W_m2K"],
            candidate["summary"]["maximum_h_l_W_m2K"],
        )
        self.assertTrue(
            all(
                station["calculation"]["status"] == "MODEL_U"
                for station in candidate["stations"]
            )
        )

    def test_station_result_matches_independent_hl_core(self) -> None:
        result = solve_cooling_channel_candidates(self.request)
        candidate = result["candidates"][2]
        station = candidate["stations"][1]
        model = self.request["model_conditions"]
        fluid = self.request["coolant_conditions"]["default_fluid_properties"]
        direct = evaluate_rectangular_cooling_hl(
            coolant_mass_flow_kg_s=self.request["coolant_conditions"][
                "coolant_mass_flow_kg_s"
            ],
            width_m=station["channel_width_m"],
            height_m=station["channel_height_m"],
            channel_count=candidate["channel_count"],
            viscosity_Pa_s=fluid["viscosity_Pa_s"],
            cp_J_kgK=fluid["cp_J_kgK"],
            conductivity_W_mK=fluid["conductivity_W_mK"],
            equivalent_sand_roughness_m=self.request["candidate_space"][
                "equivalent_sand_roughness_m"
            ],
            closure_geometry=model["closure_geometry"],
            fully_developed=model["fully_developed"],
            thermal_boundary_condition=model["thermal_boundary_condition"],
            heat_transfer_domain=build_heat_transfer_domain(
                model["heat_transfer_domain"]
            ),
            heat_transfer_thermal_boundary_condition=model[
                "heat_transfer_thermal_boundary_condition"
            ],
            friction_domain=build_friction_domain(model["friction_domain"]),
            density_kg_m3=fluid["density_kg_m3"],
        )
        nested = station["calculation"]["results"]
        self.assertAlmostEqual(nested["h_l_W_m2K"], direct.h_l_W_m2K)
        self.assertAlmostEqual(nested["Reynolds"], direct.Reynolds)

    def test_friction_pressure_gradient_identity(self) -> None:
        result = solve_cooling_channel_candidates(self.request)
        station = result["candidates"][2]["stations"][1]
        values = station["calculation"]["results"]
        expected = (
            values["darcy_factor"]
            / station["hydraulic_diameter_m"]
            * 800.0
            * values["velocity_m_s"] ** 2
            / 2.0
        )
        self.assertAlmostEqual(
            values["friction_pressure_gradient_Pa_m"], expected
        )

    def test_noninteger_channel_count_is_input_u(self) -> None:
        request = copy.deepcopy(self.request)
        request["candidate_space"]["channel_count_candidates"] = [80.5]
        result = solve_cooling_channel_candidates(request)
        self.assertEqual(result["status"], "INPUT_U")

    def test_missing_radius_profile_is_input_u(self) -> None:
        request = copy.deepcopy(self.request)
        del request["candidate_space"]["mean_radius_profile"]
        result = solve_cooling_channel_candidates(request)
        self.assertEqual(result["status"], "INPUT_U")

    def test_constant_width_local_mode_does_not_invent_radius(self) -> None:
        request = copy.deepcopy(self.request)
        space = request["candidate_space"]
        space["geometry_policy"] = "CONSTANT_WIDTH_LOCAL"
        space["channel_count_candidates"] = [20]
        space["rib_thickness_candidates_m"] = [0.0009]
        space["channel_height_candidates_m"] = [0.002]
        space["channel_width_limits_m"] = [0.0005, 0.002]
        space["constant_channel_width_m"] = 0.001
        del space["mean_radius_profile"]
        result = solve_cooling_channel_candidates(request)
        candidate = result["candidates"][0]
        station = candidate["stations"][0]
        self.assertEqual(
            candidate["geometric_validity_status"],
            "INPUT_U_CIRCUMFERENTIAL_PLACEMENT",
        )
        self.assertEqual(candidate["decision"], "LOCAL_ARITHMETIC_ONLY")
        self.assertIsNone(station["mean_channel_radius_m"])
        self.assertEqual(station["calculation"]["status"], "MODEL_U")

    def test_correlation_domain_exit_is_preserved(self) -> None:
        request = copy.deepcopy(self.request)
        request["model_conditions"]["heat_transfer_domain"]["re_range"] = [
            100000.0,
            200000.0,
        ]
        result = solve_cooling_channel_candidates(request)
        candidate = result["candidates"][2]
        self.assertEqual(candidate["decision"], "EXCLUDE_CORRELATION_DOMAIN")
        self.assertTrue(
            all(
                station["calculation"]["status"] == "OUTSIDE"
                for station in candidate["stations"]
            )
        )

    def test_explicit_station_properties_are_recomputed_without_interpolation(self) -> None:
        request = copy.deepcopy(self.request)
        request["coolant_conditions"][
            "property_policy"
        ] = "EXPLICIT_BY_STATION_WITH_DEFAULT"
        request["candidate_space"]["mean_radius_profile"][1]["fluid_properties"] = {
            "density_kg_m3": 790.0,
            "viscosity_Pa_s": 0.0008,
            "cp_J_kgK": 2100.0,
            "conductivity_W_mK": 0.14,
            "property_source": "SYNTHETIC_EXPLICIT_STATION_PROPERTY",
            "property_version": "TEST-ONLY-V1",
        }
        result = solve_cooling_channel_candidates(request)
        stations = result["candidates"][2]["stations"]
        self.assertEqual(
            stations[1]["calculation"]["property_basis"], "EXPLICIT_STATION"
        )
        self.assertEqual(
            stations[0]["calculation"]["property_basis"], "CONSTANT_DEFAULT"
        )
        self.assertNotEqual(
            stations[1]["calculation"]["results"]["Prandtl"],
            stations[0]["calculation"]["results"]["Prandtl"],
        )

    def test_csv_contains_every_candidate_station_without_ranking(self) -> None:
        result = solve_cooling_channel_candidates(self.request)
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "sweep.csv"
            write_candidate_results_csv(path, result)
            with path.open(encoding="utf-8", newline="") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 12)
        self.assertIn("friction_pressure_gradient_Pa_m", rows[0])
        self.assertFalse(result["comparison_policy"]["ranking_performed"])
        self.assertFalse(result["final_design_selection"])
        self.assertFalse(result["validated_engine_prediction"])


if __name__ == "__main__":
    unittest.main()
