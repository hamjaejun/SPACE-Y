from __future__ import annotations

import copy
from pathlib import Path
import unittest

from space_y.contract import load_json
from space_y.m0 import solve_m0


ROOT = Path(__file__).resolve().parents[1]


class M0SolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.fixture = load_json(ROOT / "examples/m0_synthetic_single_path.json")

    def test_synthetic_single_path_converges(self) -> None:
        result = solve_m0(self.fixture)
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertEqual(len(result["segments"]), 2)
        self.assertGreater(result["qoi"]["Y05"]["value"], self.fixture["boundary"]["inlet_temperature_K"])
        self.assertGreater(result["qoi"]["Y02"]["value"], 0)
        self.assertFalse(result["validated_engine_prediction"])

    def test_closure_outside_stops_without_qoi(self) -> None:
        request = copy.deepcopy(self.fixture)
        request["coolant_closure"]["re_range"] = [10.0, 20.0]
        result = solve_m0(request)
        self.assertEqual(result["status"], "OUTSIDE")
        self.assertTrue(all(item["value"] is None for item in result["qoi"].values()))

    def test_property_extrapolation_is_prohibited(self) -> None:
        request = copy.deepcopy(self.fixture)
        request["boundary"]["inlet_temperature_K"] = 600.0
        result = solve_m0(request)
        self.assertEqual(result["status"], "OUTSIDE")

    def test_pressure_driven_mode_is_not_silently_approximated(self) -> None:
        request = copy.deepcopy(self.fixture)
        request["flow_boundary_mode"] = "PRESSURE_DRIVEN"
        result = solve_m0(request)
        self.assertEqual(result["status"], "MODEL_U")

    def test_missing_required_input_returns_input_u(self) -> None:
        request = copy.deepcopy(self.fixture)
        del request["boundary"]["mass_flow_kg_s"]
        result = solve_m0(request)
        self.assertEqual(result["status"], "INPUT_U")

    def test_enthalpy_reference_may_be_negative(self) -> None:
        request = copy.deepcopy(self.fixture)
        for row in request["coolant_property_model"]["rows"]:
            row["enthalpy_J_kg"] -= 700000.0
        result = solve_m0(request)
        self.assertEqual(result["numerical_status"], "PASS")


if __name__ == "__main__":
    unittest.main()
