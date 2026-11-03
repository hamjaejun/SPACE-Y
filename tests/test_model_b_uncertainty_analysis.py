from __future__ import annotations

import json
import math
import unittest

from tools.analyze_model_b_candidates import BASE_INPUT
from tools.analyze_model_b_uncertainty import (
    BOUNDARIES,
    _percentile,
    _relative_difference,
    build_uncertainty_analysis,
)


class ModelBUncertaintyAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.base = json.loads(BASE_INPUT.read_text(encoding="utf-8"))
        cls.data = build_uncertainty_analysis(cls.base)

    def test_output_denominators_remain_separate_and_complete(self) -> None:
        self.assertEqual(len(self.data["station_rows"]), 936)
        self.assertEqual(len(self.data["candidate_rows"]), 156)
        self.assertEqual(len(self.data["boundary_rows"]), 468)
        self.assertEqual(len(self.data["controlled_rows"]), 602)

    def test_maximum_sensitivity_station_is_reproduced_from_raw_rows(self) -> None:
        summary_max = self.data["summary"]["maximum_absolute_sensitivity_station"]
        raw_max = max(
            self.data["station_rows"],
            key=lambda row: row["h_l_absolute_relative_difference_percent"],
        )
        self.assertEqual(summary_max, raw_max)
        self.assertEqual(raw_max["candidate_id"], "GCH-0111")
        self.assertEqual(raw_max["station_id"], "SYN-INLET")
        self.assertAlmostEqual(raw_max["Reynolds"], 2957.534690478906)
        self.assertAlmostEqual(
            raw_max["h_l_relative_difference_percent"], -34.376707596372064
        )

    def test_reynolds_band_statistics_use_expected_station_counts(self) -> None:
        bands = self.data["summary"]["station_statistics_by_reynolds_band"]
        self.assertEqual(
            bands["RE_2300_3000"]["signed_relative_difference_percent"]["count"],
            236,
        )
        self.assertEqual(
            bands["RE_3000_4000"]["signed_relative_difference_percent"]["count"],
            334,
        )
        self.assertGreater(
            bands["RE_3000_4000"]["absolute_relative_difference_percent"]["maximum"],
            34.0,
        )

    def test_relative_difference_and_percentile_arithmetic(self) -> None:
        self.assertAlmostEqual(_relative_difference(8.0, 10.0), -20.0)
        self.assertIsNone(_relative_difference(8.0, 0.0))
        self.assertIsNone(_relative_difference(None, 10.0))
        self.assertEqual(_percentile([0.0, 10.0, 20.0], 0.5), 10.0)
        self.assertEqual(_percentile([0.0, 10.0], 0.25), 2.5)

    def test_boundary_scenarios_remain_independent(self) -> None:
        rows = self.data["boundary_rows"]
        self.assertTrue(all(row["B_T_Nusselt"] > 0 for row in rows))
        self.assertTrue(all(row["B_H1_Nusselt"] > 0 for row in rows))
        self.assertTrue(all(row["S_T_Nusselt"] > 0 for row in rows))
        self.assertTrue(all(row["S_H1_Nusselt"] > 0 for row in rows))
        self.assertTrue(
            all(not row["boundary_conditions_are_verified_bounds"] for row in rows)
        )

    def test_transition_friction_remains_unsupported(self) -> None:
        active = [
            row
            for row in self.data["station_rows"]
            if 2300.0 <= row["Reynolds"] < 4000.0
        ]
        self.assertTrue(active)
        self.assertTrue(
            all(not row["model_4000_transition_friction_supported"] for row in active)
        )
        model_b_transition = [
            row for row in active if row["Reynolds"] < 3000.0
        ]
        self.assertTrue(
            all(not row["model_3000_friction_supported"] for row in model_b_transition)
        )

    def test_value_continuity_and_expected_slope_changes_are_distinct(self) -> None:
        diagnostics = self.data["boundary_diagnostics"]
        for row in diagnostics:
            self.assertTrue(math.isfinite(row["Nusselt_at_boundary"]))
            self.assertLess(abs(row["left_value_residual"]), 1.0e-3)
            self.assertLess(abs(row["right_value_residual"]), 1.0e-3)
        default_3000 = next(
            row
            for row in diagnostics
            if row["model"] == "MODEL_B_LINEAR_2300_3000"
            and row["thermal_boundary_condition"] == BOUNDARIES[0]
            and row["boundary_Reynolds"] == 3000.0
        )
        sensitivity_3000 = next(
            row
            for row in diagnostics
            if row["model"] == "SPACE_Y_SENSITIVITY_LINEAR_2300_4000"
            and row["thermal_boundary_condition"] == BOUNDARIES[0]
            and row["boundary_Reynolds"] == 3000.0
        )
        self.assertGreater(abs(default_3000["slope_jump_right_minus_left"]), 1.0e-3)
        self.assertLess(abs(sensitivity_3000["slope_jump_right_minus_left"]), 1.0e-8)

    def test_previous_outputs_are_recorded_as_preserved(self) -> None:
        preserved = self.data["summary"]["preserved_previous_outputs"]
        self.assertTrue(all(not item["overwritten"] for item in preserved.values()))
        self.assertTrue(
            all(len(item["sha256_before_followup"]) == 64 for item in preserved.values())
        )

    def test_analysis_is_reproducible_for_identical_input(self) -> None:
        repeated = build_uncertainty_analysis(self.base)
        self.assertEqual(self.data["station_rows"], repeated["station_rows"])
        self.assertEqual(self.data["candidate_rows"], repeated["candidate_rows"])
        self.assertEqual(self.data["summary"], repeated["summary"])


if __name__ == "__main__":
    unittest.main()
