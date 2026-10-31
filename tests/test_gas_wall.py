from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from space_y.contract import load_json
from space_y.gas_wall import solve_gas_wall, solve_gas_wall_file
from space_y.wall import solve_steady_three_resistance_wall


ROOT = Path(__file__).resolve().parents[1]


class WallResistanceTests(unittest.TestCase):
    def test_independent_three_resistance_benchmark(self) -> None:
        result = solve_steady_three_resistance_wall(
            h_g_W_m2K=100.0,
            gas_area_m2=2.0,
            adiabatic_wall_temperature_K=1000.0,
            q_rad_net_W=100.0,
            wall_resistance_K_W=0.01,
            h_c_W_m2K=200.0,
            coolant_area_m2=1.0,
            coolant_bulk_temperature_K=300.0,
        )
        self.assertAlmostEqual(result.q_total_W, 35025.0, places=10)
        self.assertAlmostEqual(result.q_conv_W, 34925.0, places=10)
        self.assertAlmostEqual(result.gas_side_wall_temperature_K, 825.375, places=10)
        self.assertAlmostEqual(result.coolant_side_wall_temperature_K, 475.125, places=10)
        self.assertAlmostEqual(result.q_conduction_W, 35025.0, places=8)
        self.assertAlmostEqual(result.q_coolant_W, 35025.0, places=8)
        self.assertLess(abs(result.gas_balance_residual_W), 1e-10)
        self.assertLess(abs(result.wall_balance_residual_W), 1e-8)
        self.assertLess(abs(result.coolant_balance_residual_W), 1e-8)


class GasWallCouplingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request = load_json(ROOT / "examples/gas_wall_synthetic_single_segment.json")
        cls.cea = load_json(ROOT / "artifacts/phase1/cea_rp1311_example13_rocket_run.json")

    def test_bartz_wall_temperature_iteration_converges(self) -> None:
        result = solve_gas_wall(self.request, self.cea)
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertGreater(result["iterations"], 1)
        self.assertLessEqual(
            abs(result["wall_temperature_update_residual_K"]),
            self.request["numerics"]["wall_temperature_tolerance_K"],
        )
        self.assertLessEqual(
            result["energy_residual_max_W"],
            self.request["numerics"]["energy_residual_tolerance_W"],
        )
        self.assertNotAlmostEqual(
            result["bartz"]["wall_temperature_used_K"],
            self.request["numerics"]["initial_gas_side_wall_temperature_K"],
            places=3,
        )
        self.assertAlmostEqual(
            result["wall_solution"]["gas_side_wall_temperature_K"],
            2376.7624984527242,
            places=7,
        )
        self.assertAlmostEqual(result["bartz"]["sigma"], 0.9278085911142542, places=12)
        self.assertEqual(result["coolant_interface"]["coolant_state_update"], "NOT_RUN")
        schema = json.loads(
            (ROOT / "schemas/SPACE_Y_WALL_COOLANT_interface.schema.json").read_text(
                encoding="utf-8"
            )
        )
        import jsonschema

        jsonschema.Draft202012Validator(schema).validate(result["coolant_interface"])
        self.assertEqual(result["physical_validation_v01_v12"], "NOT_RUN")
        self.assertFalse(result["validated_engine_prediction"])

    def test_missing_radiation_is_not_defaulted_to_zero(self) -> None:
        request = copy.deepcopy(self.request)
        del request["radiation"]["q_rad_net_W"]
        result = solve_gas_wall(request, self.cea)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIsNone(result["wall_solution"])

    def test_nonconvergence_returns_num_u(self) -> None:
        request = copy.deepcopy(self.request)
        request["numerics"]["max_iterations"] = 1
        result = solve_gas_wall(request, self.cea)
        self.assertEqual(result["status"], "NUM_U")
        self.assertEqual(result["numerical_status"], "NUM_U")
        self.assertIsNone(result["wall_solution"])

    def test_invalid_cea_gamma_is_outside(self) -> None:
        request = copy.deepcopy(self.request)
        request["cea_station_index"] = 1
        result = solve_gas_wall(request, self.cea)
        self.assertEqual(result["status"], "OUTSIDE")

    def test_file_entrypoint_validates_schema_and_hashes_cea_artifact(self) -> None:
        result = solve_gas_wall_file(
            ROOT / "examples/gas_wall_synthetic_single_segment.json",
            ROOT / "schemas/SPACE_Y_GAS_WALL_run.schema.json",
        )
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertRegex(result["cea_artifact_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
