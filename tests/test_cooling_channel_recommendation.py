from __future__ import annotations

import csv
import json
from pathlib import Path
import tempfile
import unittest

from space_y.cooling_channel_candidates import (
    solve_cooling_channel_candidates_file,
    write_candidate_summary_csv,
)
from space_y.cooling_channel_recommendation import (
    classify_cooling_channel_candidates,
)


ROOT = Path(__file__).resolve().parents[1]


def comparison(reference_type: str = "THROAT") -> dict:
    value = {
        "reference_station_type": reference_type,
        "relative_tolerance": 1.0e-9,
        "h_l_absolute_tolerance_W_m2K": 1.0e-6,
        "friction_gradient_absolute_tolerance_Pa_m": 1.0e-3,
    }
    if reference_type == "STATION_ID":
        value["reference_station_id"] = "REF"
    return value


def candidate(
    candidate_id: str,
    h_l: float,
    gradient: float,
    *,
    exit_h_l: float | None = None,
    decision: str = "RETAIN_FOR_INTEGRATION_REVIEW",
    geometry: str = "PASS",
    applicability: str = "APPLICABLE_TO_SUPPLIED_DOMAINS",
    exit_status: str = "MODEL_U",
) -> dict:
    def station(station_id: str, is_throat: bool, h_value: float, g_value: float, status: str) -> dict:
        return {
            "station_id": station_id,
            "is_throat": is_throat,
            "calculation": {
                "status": status,
                "results": (
                    {
                        "h_l_W_m2K": h_value,
                        "friction_pressure_gradient_Pa_m": g_value,
                    }
                    if status == "MODEL_U"
                    else None
                ),
            },
        }

    return {
        "candidate_id": candidate_id,
        "decision": decision,
        "geometric_validity_status": geometry,
        "design_approved": False,
        "summary": {"correlation_applicability_status": applicability},
        "stations": [
            station("REF", True, h_l, gradient, "MODEL_U"),
            station(
                "EXIT",
                False,
                exit_h_l if exit_h_l is not None else h_l,
                gradient,
                exit_status,
            ),
        ],
    }


class CoolingChannelRecommendationTests(unittest.TestCase):
    def test_multi_candidate_fixture_and_schema(self) -> None:
        result = solve_cooling_channel_candidates_file(
            ROOT / "examples/cooling_channel_recommendation_synthetic_multi.json",
            ROOT / "schemas/SPACE_Y_cooling_channel_sweep.schema.json",
        )
        recommendation = result["candidate_recommendation"]
        self.assertEqual(result["fixture_classification"], "SYNTHETIC_NON_DESIGN")
        self.assertEqual(
            recommendation["status"], "PRELIMINARY_RELATIVE_RECOMMENDATION"
        )
        self.assertEqual(recommendation["reference_station_id"], "SYN-THROAT")
        self.assertGreaterEqual(len(recommendation["eligible_candidate_ids"]), 3)
        self.assertEqual(recommendation["highest_h_l_candidate_ids"], ["CH-0004"])
        self.assertEqual(
            recommendation["lowest_friction_gradient_candidate_ids"], ["CH-0005"]
        )
        self.assertFalse(recommendation["final_design_selection"])
        self.assertFalse(result["validated_engine_prediction"])

    def test_baseline_is_not_automatically_selected_or_recommended(self) -> None:
        items = [candidate("A", 100.0, 20.0), candidate("B", 120.0, 30.0)]
        baseline = {
            "baseline_id": "REFERENCE-ONLY",
            "source": "synthetic test reference",
            "geometry_policy": "CONSTANT_WIDTH_LOCAL",
            "settings": {"width_m": 0.001},
            "geometric_applicability_status": "REFERENCE_ONLY_NOT_EVALUATED",
            "design_approved": False,
            "limitations": ["not an engine design"],
        }
        result = classify_cooling_channel_candidates(
            items, comparison(), baseline
        )
        self.assertFalse(result["baseline_reference"]["automatically_selected"])
        self.assertNotIn("BASELINE_REFERENCE", items[0]["recommendation_tags"])
        self.assertEqual(result["highest_h_l_candidate_ids"], ["B"])

    def test_explicit_baseline_candidate_tag_does_not_change_optimum_tags(self) -> None:
        items = [candidate("A", 100.0, 20.0), candidate("B", 120.0, 30.0)]
        baseline = {
            "baseline_id": "EXPLICIT-A",
            "source": "synthetic test reference",
            "geometry_policy": "TEST",
            "settings": {},
            "geometric_applicability_status": "PASS",
            "candidate_id": "A",
            "design_approved": False,
            "limitations": [],
        }
        result = classify_cooling_channel_candidates(items, comparison(), baseline)
        self.assertIn("BASELINE_REFERENCE", items[0]["recommendation_tags"])
        self.assertEqual(result["highest_h_l_candidate_ids"], ["B"])

    def test_comparison_uses_same_reference_not_each_candidate_maximum(self) -> None:
        items = [
            candidate("A", 100.0, 20.0, exit_h_l=1000.0),
            candidate("B", 120.0, 30.0, exit_h_l=121.0),
        ]
        result = classify_cooling_channel_candidates(items, comparison())
        self.assertEqual(result["highest_h_l_candidate_ids"], ["B"])

    def test_highest_and_lowest_are_independent(self) -> None:
        items = [candidate("A", 130.0, 50.0), candidate("B", 100.0, 10.0)]
        result = classify_cooling_channel_candidates(items, comparison())
        self.assertEqual(result["highest_h_l_candidate_ids"], ["A"])
        self.assertEqual(result["lowest_friction_gradient_candidate_ids"], ["B"])

    def test_pareto_dominance(self) -> None:
        items = [
            candidate("A", 100.0, 20.0),
            candidate("B", 90.0, 25.0),
            candidate("C", 120.0, 40.0),
        ]
        result = classify_cooling_channel_candidates(items, comparison())
        self.assertEqual(result["pareto_candidate_ids"], ["A", "C"])
        self.assertEqual(items[1]["comparison"]["pareto_status"], "DOMINATED")

    def test_tolerance_creates_ties(self) -> None:
        spec = comparison()
        spec["relative_tolerance"] = 0.0
        spec["h_l_absolute_tolerance_W_m2K"] = 0.1
        spec["friction_gradient_absolute_tolerance_Pa_m"] = 0.1
        items = [candidate("A", 100.0, 20.0), candidate("B", 100.05, 20.05)]
        result = classify_cooling_channel_candidates(items, spec)
        self.assertEqual(result["highest_h_l_candidate_ids"], ["A", "B"])
        self.assertEqual(
            result["lowest_friction_gradient_candidate_ids"], ["A", "B"]
        )
        self.assertEqual(result["pareto_candidate_ids"], ["A", "B"])

    def test_zero_valid_candidates(self) -> None:
        items = [
            candidate(
                "A",
                100.0,
                20.0,
                decision="EXCLUDE_GEOMETRIC_CONSTRAINT",
                geometry="OUTSIDE",
                applicability="NOT_RUN",
            )
        ]
        result = classify_cooling_channel_candidates(items, comparison())
        self.assertEqual(result["status"], "NO_VALID_CANDIDATES")
        self.assertEqual(result["pareto_candidate_ids"], [])
        self.assertIn("EXCLUDED_GEOMETRY", items[0]["recommendation_tags"])

    def test_single_valid_candidate_is_not_called_an_optimum(self) -> None:
        items = [candidate("A", 100.0, 20.0)]
        result = classify_cooling_channel_candidates(items, comparison())
        self.assertEqual(result["status"], "SINGLE_VALID_CANDIDATE_NO_TRADEOFF")
        self.assertIn("no meaningful tradeoff", result["reason"])
        self.assertFalse(result["final_design_selection"])

    def test_full_path_failure_excludes_candidate_even_if_reference_passes(self) -> None:
        items = [candidate("A", 100.0, 20.0, exit_status="OUTSIDE")]
        result = classify_cooling_channel_candidates(items, comparison())
        self.assertEqual(result["eligible_candidate_ids"], [])
        self.assertIn("UNVERIFIED_INPUT", items[0]["recommendation_tags"])

    def test_missing_reference_station_stops_recommendation(self) -> None:
        items = [candidate("A", 100.0, 20.0)]
        spec = comparison("STATION_ID")
        spec["reference_station_id"] = "MISSING"
        result = classify_cooling_channel_candidates(items, spec)
        self.assertEqual(result["status"], "NO_VALID_CANDIDATES")
        self.assertIn("UNVERIFIED_INPUT", items[0]["recommendation_tags"])

    def test_invalid_baseline_reference_is_input_unknown(self) -> None:
        items = [candidate("A", 100.0, 20.0)]
        baseline = {
            "baseline_id": "BAD",
            "source": "test",
            "geometry_policy": "TEST",
            "settings": {},
            "geometric_applicability_status": "PASS",
            "candidate_id": "UNKNOWN",
            "design_approved": False,
            "limitations": [],
        }
        result = classify_cooling_channel_candidates(items, comparison(), baseline)
        self.assertEqual(result["status"], "INPUT_U")

    def test_summary_csv_contains_recommendation_fields(self) -> None:
        result = solve_cooling_channel_candidates_file(
            ROOT / "examples/cooling_channel_recommendation_synthetic_multi.json",
            ROOT / "schemas/SPACE_Y_cooling_channel_sweep.schema.json",
        )
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            path = Path(directory) / "summary.csv"
            write_candidate_summary_csv(path, result)
            with path.open(encoding="utf-8") as stream:
                rows = list(csv.DictReader(stream))
        self.assertEqual(len(rows), 12)
        self.assertIn("pareto_status", rows[0])
        by_id = {row["candidate_id"]: row for row in rows}
        self.assertIn(
            "HIGHEST_HL_AT_REFERENCE", by_id["CH-0004"]["recommendation_tags"]
        )

    def test_saved_multi_candidate_artifacts_are_reproducible(self) -> None:
        result = solve_cooling_channel_candidates_file(
            ROOT / "examples/cooling_channel_recommendation_synthetic_multi.json",
            ROOT / "schemas/SPACE_Y_cooling_channel_sweep.schema.json",
        )
        saved = json.loads(
            (
                ROOT
                / "artifacts/cooling_channel_sweep/synthetic_recommendation_result.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(json.loads(json.dumps(result)), saved)


if __name__ == "__main__":
    unittest.main()
