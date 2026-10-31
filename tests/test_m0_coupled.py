from __future__ import annotations

import copy
from pathlib import Path
import unittest

from space_y.contract import load_json
from space_y.m0_coupled import solve_coupled_single_segment, solve_coupled_single_segment_file


ROOT = Path(__file__).resolve().parents[1]


class CoupledSingleSegmentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request = load_json(ROOT / "examples/m0_coupled_synthetic_single_segment.json")
        cls.cea = load_json(ROOT / "artifacts/phase1/cea_rp1311_example13_rocket_run.json")

    def test_variable_property_coupled_fixture_converges(self) -> None:
        result = solve_coupled_single_segment(self.request, self.cea)
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertGreater(result["iterations"], 1)
        coolant = result["coolant_solution"]
        self.assertGreater(coolant["outlet"]["temperature_K"], coolant["inlet"]["temperature_K"])
        self.assertLess(coolant["outlet"]["pressure_Pa_abs"], coolant["inlet"]["pressure_Pa_abs"])
        self.assertNotEqual(
            coolant["mean_evaluation_state"]["viscosity_Pa_s"],
            coolant["inlet"]["viscosity_Pa_s"],
        )
        self.assertAlmostEqual(coolant["outlet"]["temperature_K"], 321.5602427037746, places=7)
        self.assertAlmostEqual(
            coolant["local_closures"]["pressure_drop_Pa"], 516.5334523344754, places=8
        )
        self.assertLess(
            abs(coolant["coolant_energy_residual_W"]),
            self.request["coupled_numerics"]["energy_residual_tolerance_W"],
        )
        self.assertEqual(coolant["wall_phase_change_status"], "NOT_EVALUATED")
        self.assertEqual(result["physical_validation_v01_v12"], "NOT_RUN")
        self.assertFalse(result["validated_engine_prediction"])

    def test_property_extrapolation_stops_as_outside(self) -> None:
        request = copy.deepcopy(self.request)
        request["coolant_boundary"]["inlet_temperature_K"] = 600.0
        result = solve_coupled_single_segment(request, self.cea)
        self.assertEqual(result["status"], "OUTSIDE")
        self.assertTrue(all(item["value"] is None for item in result["qoi"].values()))

    def test_supplied_single_phase_domain_is_enforced(self) -> None:
        request = copy.deepcopy(self.request)
        request["single_phase_bulk_domain"]["temperature_range_K"] = [250.0, 310.0]
        result = solve_coupled_single_segment(request, self.cea)
        self.assertEqual(result["status"], "OUTSIDE")

    def test_heat_transfer_domain_exit_is_not_extrapolated(self) -> None:
        request = copy.deepcopy(self.request)
        request["coolant_heat_transfer_closure"]["re_range"] = [10.0, 20.0]
        result = solve_coupled_single_segment(request, self.cea)
        self.assertEqual(result["status"], "OUTSIDE")
        self.assertIsNone(result["coolant_solution"])

    def test_thermal_boundary_mismatch_is_outside(self) -> None:
        request = copy.deepcopy(self.request)
        request["geometry"]["thermal_boundary_condition"] = "DIFFERENT_BOUNDARY"
        result = solve_coupled_single_segment(request, self.cea)
        self.assertEqual(result["status"], "OUTSIDE")

    def test_missing_closure_source_is_input_u(self) -> None:
        request = copy.deepcopy(self.request)
        del request["coolant_friction_closure"]["source"]
        result = solve_coupled_single_segment(request, self.cea)
        self.assertEqual(result["status"], "INPUT_U")

    def test_missing_radiation_is_not_defaulted(self) -> None:
        request = copy.deepcopy(self.request)
        del request["radiation"]["q_rad_net_W"]
        result = solve_coupled_single_segment(request, self.cea)
        self.assertEqual(result["status"], "INPUT_U")

    def test_coupled_nonconvergence_is_num_u(self) -> None:
        request = copy.deepcopy(self.request)
        request["coupled_numerics"]["max_iterations"] = 1
        result = solve_coupled_single_segment(request, self.cea)
        self.assertEqual(result["status"], "NUM_U")
        self.assertEqual(result["numerical_status"], "NUM_U")

    def test_file_entrypoint_schema_and_artifact_hash(self) -> None:
        result = solve_coupled_single_segment_file(
            ROOT / "examples/m0_coupled_synthetic_single_segment.json",
            ROOT / "schemas/SPACE_Y_M0_coupled_single_segment.schema.json",
        )
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertRegex(result["cea_artifact_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
