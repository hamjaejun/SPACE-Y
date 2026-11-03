from __future__ import annotations

import json
import math
import unittest

from tools.analyze_model_b_candidates import BASE_INPUT, BOUNDARIES, FLUID_LABEL, MODELS, build_analysis


class ModelBCandidateAnalysisTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        base = json.loads(BASE_INPUT.read_text(encoding="utf-8"))
        cls.data = build_analysis(base)

    def test_candidate_and_regime_counts_come_from_existing_pipeline(self) -> None:
        summary = self.data["summary"]
        self.assertEqual(summary["template_count"], 80)
        self.assertEqual(summary["unique_geometry_candidate_count"], 160)
        self.assertEqual(summary["geometrically_valid_candidate_count"], 156)
        self.assertEqual(summary["valid_station_count_per_scenario"], 468)
        self.assertEqual(
            summary["regime_counts_per_scenario"],
            {"LAMINAR": 14, "TRANSITION": 118, "TURBULENT": 336},
        )

    def test_model_b_adds_only_transition_heat_transfer_results(self) -> None:
        for boundary in BOUNDARIES:
            subset = [r for r in self.data["paired_rows"] if r["thermal_boundary_condition"] == boundary]
            added = [r for r in subset if r["model_b_only"]]
            unchanged = [r for r in subset if r["flow_regime"] != "TRANSITION"]
            self.assertEqual(len(added), 118)
            self.assertTrue(all(r["flow_regime"] == "TRANSITION" for r in added))
            self.assertTrue(all(not r["model_b_friction_supported"] for r in added))
            self.assertTrue(all(r["Nusselt_delta_b_minus_a"] == 0.0 for r in unchanged))

    def test_total_flow_is_split_equally_and_conserved(self) -> None:
        rows = [
            r for r in self.data["station_rows"]
            if r["model"] == MODELS[1] and r["thermal_boundary_condition"] == BOUNDARIES[0]
        ]
        for row in rows:
            self.assertAlmostEqual(
                row["channel_mass_flow_kg_s"] * row["channel_count"],
                row["total_mass_flow_kg_s"],
                places=14,
            )

    def test_model_a_null_and_model_b_positive_in_transition(self) -> None:
        transition = [r for r in self.data["station_rows"] if r["flow_regime"] == "TRANSITION"]
        for row in transition:
            self.assertEqual(row["fluid_classification"], FLUID_LABEL)
            self.assertFalse(row["experimental_validation"])
            if row["model"] == MODELS[0]:
                self.assertIsNone(row["Nusselt"])
                self.assertIsNone(row["h_l_W_m2K"])
            else:
                self.assertTrue(math.isfinite(row["Nusselt"]) and row["Nusselt"] > 0)
                self.assertTrue(math.isfinite(row["h_l_W_m2K"]) and row["h_l_W_m2K"] > 0)
                self.assertFalse(row["friction_supported"])

    def test_upper_endpoint_sensitivity_is_finite_and_separate(self) -> None:
        rows = self.data["sensitivity_rows"]
        self.assertGreater(len(rows), 0)
        for row in rows:
            self.assertEqual(row["sensitivity_model"], "SPACE_Y_SENSITIVITY_LINEAR_2300_4000")
            self.assertTrue(math.isfinite(row["sensitivity_Nusselt"]))
            self.assertTrue(math.isfinite(row["sensitivity_h_l_W_m2K"]))
            self.assertFalse(row["friction_supported"])
            self.assertFalse(row["experimental_validation"])


if __name__ == "__main__":
    unittest.main()
