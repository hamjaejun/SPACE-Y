from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

from space_y.contract import (
    EXPECTED_QOI_IDS,
    load_json,
    propagate_qoi_status,
    validate_semantics,
    validate_structure,
)


ROOT = Path(__file__).resolve().parents[1]


class ContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.contract = load_json(ROOT / "SPACE_Y_M0_model_contract.json")
        cls.dependencies = load_json(ROOT / "config/qoi_dependencies.json")

    def test_v21_fixed_id_sets(self) -> None:
        self.assertEqual(validate_structure(self.contract), [])

    def test_template_is_blocked_without_flow_mode(self) -> None:
        issues = validate_semantics(self.contract)
        self.assertTrue(any(issue.code == "FLOW_MODE_MISSING" for issue in issues))

    def test_zero_is_not_treated_as_null(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["inputs"][1]["value"] = 0
        issues = validate_semantics(mutated)
        self.assertTrue(any(issue.code == "VALUE_STATUS_CONFLICT" for issue in issues))

    def test_missing_inputs_propagate_per_qoi(self) -> None:
        statuses = propagate_qoi_status(self.contract, self.dependencies)
        self.assertEqual(set(statuses), set(EXPECTED_QOI_IDS))
        self.assertTrue(all(value["status"] == "INPUT_U" for value in statuses.values()))

    def test_prescribed_flow_rejects_forced_outlet_pressure(self) -> None:
        mutated = copy.deepcopy(self.contract)
        mutated["analysis_control"]["flow_boundary_mode"] = "PRESCRIBED_MASS_FLOW"
        mutated["analysis_control"]["boundary_roles"] = {"I02": "INPUT", "I04": "INPUT"}
        mutated["analysis_control"]["pressure_driven"]["outlet_pressure_Pa_abs"] = 1.0
        issues = validate_semantics(mutated)
        self.assertTrue(any(issue.code == "OVER_SPECIFIED_BOUNDARY" for issue in issues))


if __name__ == "__main__":
    unittest.main()
