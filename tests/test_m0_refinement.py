from __future__ import annotations

import copy
from pathlib import Path
import unittest

from space_y.contract import load_json
from space_y.m0_refinement import solve_refinement_suite, solve_refinement_suite_file


ROOT = Path(__file__).resolve().parents[1]


class RefinementSuiteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.suite = load_json(ROOT / "examples/m0_refinement_synthetic_n_2n_4n.json")
        cls.cea = load_json(ROOT / "artifacts/phase1/cea_rp1311_example13_rocket_run.json")

    def _records(self, suite: dict) -> dict[str, dict[str, dict]]:
        return {
            level["label"]: {
                segment["segment_id"]: self.cea for segment in level["segments"]
            }
            for level in suite["levels"]
        }

    def test_n_2n_4n_levels_execute_with_explicit_gas_mappings(self) -> None:
        result = solve_refinement_suite(self.suite, self._records(self.suite))
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertEqual(
            [(item["label"], item["cell_count"]) for item in result["levels"]],
            [("N", 1), ("2N", 2), ("4N", 4)],
        )
        for level in self.suite["levels"]:
            for segment in level["segments"]:
                self.assertEqual(
                    segment["cea_station_mapping"]["method"],
                    "EXPLICIT_NO_INTERPOLATION",
                )

    def test_independent_boundary_energy_recalculation(self) -> None:
        result = solve_refinement_suite(self.suite, self._records(self.suite))
        for level in result["levels"]:
            solve = level["result"]
            segment_heat_sum = sum(
                item["solution"]["coolant_solution"]["heat_rate_into_coolant_W"]
                for item in solve["segments"]
            )
            boundary = solve["coolant_path_boundary"]
            enthalpy_rate = boundary["inlet"]["mass_flow_kg_s"] * (
                boundary["outlet"]["enthalpy_J_kg"]
                - boundary["inlet"]["enthalpy_J_kg"]
            )
            self.assertAlmostEqual(segment_heat_sum, enthalpy_rate, places=7)
            self.assertAlmostEqual(
                solve["checks"]["total_energy_residual_W"],
                segment_heat_sum - enthalpy_rate,
                places=9,
            )

    def test_normalized_residuals_and_qoi_changes_are_recorded(self) -> None:
        result = solve_refinement_suite(self.suite, self._records(self.suite))
        for level in result["levels"]:
            normalized = level["normalized_residuals"]
            self.assertLess(normalized["total_energy"], 1e-12)
            self.assertLess(normalized["segment_coolant_energy_max"], 1e-12)
            self.assertLess(normalized["segment_wall_energy_max"], 1e-12)
            self.assertEqual(normalized["pressure_drop_sum"], 0.0)
            self.assertEqual(normalized["mass"], 0.0)
        sensitivity = result["grid_sensitivity"]
        self.assertIn("N_to_2N", sensitivity)
        self.assertIn("2N_to_4N", sensitivity)
        self.assertEqual(sensitivity["asymptotic_regime_evidence"], "NOT_ESTABLISHED")
        self.assertEqual(
            sensitivity["acceptance_status"], "RECORDED_NO_ENGINE_ERROR_BUDGET"
        )
        self.assertTrue(sensitivity["fine_grid_is_reference_not_truth"])

    def test_partition_fraction_mismatch_is_input_u(self) -> None:
        suite = copy.deepcopy(self.suite)
        suite["levels"][1]["segments"][0]["domain_fraction"] = 0.4
        result = solve_refinement_suite(suite, self._records(suite))
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("sum to one", result["reason"])

    def test_interpolated_gas_policy_is_rejected(self) -> None:
        suite = copy.deepcopy(self.suite)
        suite["levels"][0]["segments"][0]["cea_station_mapping"]["method"] = "INTERPOLATED"
        result = solve_refinement_suite(suite, self._records(suite))
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("prohibit interpolation", result["reason"])

    def test_level_nonconvergence_propagates_num_u(self) -> None:
        suite = copy.deepcopy(self.suite)
        suite["segment_template"]["coupled_numerics"]["max_iterations"] = 1
        result = solve_refinement_suite(suite, self._records(suite))
        self.assertEqual(result["status"], "NUM_U")
        self.assertEqual(result["stopped_at_level"], "N")

    def test_file_entrypoint_validates_schema_and_artifact_hashes(self) -> None:
        result = solve_refinement_suite_file(
            ROOT / "examples/m0_refinement_synthetic_n_2n_4n.json",
            ROOT / "schemas/SPACE_Y_M0_refinement_suite.schema.json",
        )
        self.assertEqual(result["numerical_status"], "PASS")
        for level in result["levels"]:
            for segment in level["result"]["segments"]:
                self.assertRegex(
                    segment["solution"]["cea_artifact_sha256"], r"^[0-9a-f]{64}$"
                )
        self.assertEqual(result["physical_validation_v01_v12"], "NOT_RUN")
        self.assertFalse(result["validated_engine_prediction"])


if __name__ == "__main__":
    unittest.main()
