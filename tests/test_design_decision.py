from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import jsonschema

from space_y.design_decision import solve_design_decision, solve_design_decision_file


ROOT = Path(__file__).resolve().parents[1]


def fixture() -> dict:
    return json.loads(
        (ROOT / "examples/pdr_decision_synthetic_candidates.json").read_text(
            encoding="utf-8"
        )
    )


def by_id(result: dict, candidate_id: str) -> dict:
    return next(item for item in result["candidates"] if item["candidate_id"] == candidate_id)


class DesignDecisionTests(unittest.TestCase):
    def test_request_and_generated_handoff_validate(self) -> None:
        request = fixture()
        request_schema = json.loads(
            (ROOT / "schemas/SPACE_Y_PDR_decision.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(request_schema).validate(request)
        result = solve_design_decision(request)
        handoff_schema = json.loads(
            (ROOT / "schemas/SPACE_Y_CAD_handoff.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(handoff_schema).validate(result["cad_handoff"])

    def test_synthetic_candidate_branches_are_conservative(self) -> None:
        result = solve_design_decision(fixture())
        self.assertEqual(by_id(result, "SYN-A")["overall_outcome"], "CONDITIONAL_REVIEW")
        self.assertEqual(by_id(result, "SYN-B")["overall_outcome"], "EXCLUDED")
        self.assertEqual(by_id(result, "SYN-C")["overall_outcome"], "UNKNOWN")
        self.assertFalse(result["safe_design_claim"])
        self.assertFalse(result["validated_engine_prediction"])

    def test_failed_hard_constraint_cannot_be_offset(self) -> None:
        request = fixture()
        candidate = request["candidates"][1]
        candidate["decision_notes"]["advantages"].extend(["advantage"] * 20)
        result = solve_design_decision(request)
        failed = by_id(result, "SYN-B")
        self.assertEqual(failed["safety_assessments"]["cooling"]["status"], "VIOLATED")
        self.assertEqual(failed["overall_outcome"], "EXCLUDED")

    def test_missing_mandatory_input_propagates_unknown(self) -> None:
        result = solve_design_decision(fixture())
        candidate = by_id(result, "SYN-C")
        self.assertEqual(candidate["safety_assessments"]["supply"]["status"], "UNKNOWN")
        self.assertEqual(candidate["overall_outcome"], "UNKNOWN")

    def test_unvalidated_pass_is_not_promoted(self) -> None:
        request = fixture()
        criterion = request["candidates"][0]["safety_assessments"]["structural"]["criteria"][0]
        criterion["validation_level"] = "NOT_VALIDATED"
        result = solve_design_decision(request)
        candidate = by_id(result, "SYN-A")
        self.assertEqual(candidate["safety_assessments"]["structural"]["status"], "CONDITIONAL_REVIEW")
        self.assertFalse(candidate["safety_assessments"]["structural"]["criteria"][0]["evidence_accepted"])

    def test_unconfirmed_assumption_blocks_acceptance(self) -> None:
        request = fixture()
        request["assumptions"][0]["status"] = "UNCONFIRMED"
        result = solve_design_decision(request)
        self.assertEqual(by_id(result, "SYN-A")["performance"]["status"], "UNKNOWN")
        self.assertEqual(by_id(result, "SYN-A")["overall_outcome"], "UNKNOWN")

    def test_model_and_source_references_are_validated(self) -> None:
        request = fixture()
        request["models"][0]["source_ids"] = ["MISSING-SOURCE"]
        result = solve_design_decision(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("unknown ID", result["reason"])

    def test_traceability_and_reproducibility(self) -> None:
        request = fixture()
        first = solve_design_decision(copy.deepcopy(request))
        second = solve_design_decision(copy.deepcopy(request))
        self.assertEqual(first, second)
        criterion = by_id(first, "SYN-A")["performance"]["criteria"][0]
        self.assertEqual(criterion["traceability"]["source_ids"], ["SRC-SYN-REQ"])
        self.assertEqual(criterion["traceability"]["model_ids"], ["MODEL-SYN-PERF"])
        self.assertEqual(first["evidence_catalog"]["sources"][0]["id"], "SRC-SYN-REQ")

    def test_file_runner_records_input_and_schema_hashes(self) -> None:
        result = solve_design_decision_file(
            ROOT / "examples/pdr_decision_synthetic_candidates.json",
            ROOT / "schemas/SPACE_Y_PDR_decision.schema.json",
        )
        self.assertRegex(result["request_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["schema_sha256"], r"^[0-9a-f]{64}$")

    def test_cad_handoff_never_promotes_synthetic_candidate(self) -> None:
        request = fixture()
        request["cad_handoff_request"]["provisional_baseline_candidate_id"] = "SYN-A"
        result = solve_design_decision(request)
        handoff = result["cad_handoff"]
        self.assertIsNone(handoff["provisional_baseline_candidate_id"])
        self.assertEqual(handoff["provisional_parameters"], [])
        self.assertFalse(handoff["manufacture_release"])
        self.assertFalse(handoff["test_safety_approval"])

    def test_weighted_safety_tradeoff_is_rejected(self) -> None:
        request = fixture()
        request["status_policy"]["allow_weighted_safety_tradeoff"] = True
        result = solve_design_decision(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("weighted safety tradeoff", result["reason"])

    def test_duplicate_criterion_id_is_rejected(self) -> None:
        request = fixture()
        request["candidates"][0]["safety_assessments"]["cooling"]["criteria"][0][
            "id"
        ] = "A-PERF-1"
        result = solve_design_decision(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("duplicate criterion", result["reason"])


if __name__ == "__main__":
    unittest.main()
