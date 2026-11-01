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


def actual_pdr_fixture() -> dict:
    request = fixture()
    request["purpose"] = "PDR_CANDIDATE_ASSESSMENT"
    request["status_policy"]["validation_policy_id"] = "PDR_APPROVED_EVIDENCE_V1"
    request["sources"][0]["validation_level"] = "SOURCE_REVIEWED"
    request["sources"][0]["type"] = "APPROVED_PDR_SOURCE"
    for model in request["models"]:
        model["validation_level"] = "SOURCE_REVIEWED"
    for candidate in request["candidates"]:
        criteria = list(candidate["performance_criteria"])
        criteria.extend(
            criterion
            for domain in candidate["safety_assessments"].values()
            for criterion in domain["criteria"]
        )
        for criterion in criteria:
            criterion["assessment_method"] = "EXTERNAL_REVIEW"
            criterion["comparison_operator"] = None
            criterion["external_review_status"] = (
                "APPROVED" if criterion["status"] in {"PASS", "FAIL"} else "PENDING"
            )
            criterion["validation_level"] = "SOURCE_REVIEWED"
    return request


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

    def test_numeric_pass_that_contradicts_values_is_rejected(self) -> None:
        request = fixture()
        criterion = request["candidates"][0]["performance_criteria"][0]
        criterion.update(
            {
                "assessment_method": "NUMERIC_COMPARISON",
                "comparison_operator": "GE",
                "external_review_status": "NOT_APPLICABLE",
                "actual_value": 4.0,
                "requirement": 5.0,
                "unit": "N",
                "actual_unit": "N",
                "requirement_unit": "N",
                "status": "PASS",
            }
        )
        result = solve_design_decision(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("contradicts computed FAIL", result["reason"])

    def test_numeric_decision_requires_unit_and_valid_operator(self) -> None:
        for unit, actual_unit, requirement_unit, operator, expected in (
            (None, "N", "N", "GE", "requires explicit units"),
            ("N", "N", "kN", "GE", "units do not match"),
            ("N", "N", "N", "BETTER", "operator is invalid"),
        ):
            with self.subTest(unit=unit, operator=operator):
                request = fixture()
                criterion = request["candidates"][0]["performance_criteria"][0]
                criterion.update(
                    {
                        "assessment_method": "NUMERIC_COMPARISON",
                        "comparison_operator": operator,
                        "external_review_status": "NOT_APPLICABLE",
                        "actual_value": 6.0,
                        "requirement": 5.0,
                        "unit": unit,
                        "actual_unit": actual_unit,
                        "requirement_unit": requirement_unit,
                        "status": "PASS",
                    }
                )
                result = solve_design_decision(request)
                self.assertEqual(result["status"], "INPUT_U")
                self.assertIn(expected, result["reason"])

    def test_valid_numeric_comparison_records_computed_status(self) -> None:
        request = fixture()
        criterion = request["candidates"][0]["performance_criteria"][0]
        criterion.update(
            {
                "assessment_method": "NUMERIC_COMPARISON",
                "comparison_operator": "GE",
                "external_review_status": "NOT_APPLICABLE",
                "actual_value": 6.0,
                "requirement": 5.0,
                "unit": "N",
                "actual_unit": "N",
                "requirement_unit": "N",
                "status": "PASS",
            }
        )
        result = solve_design_decision(request)
        prepared = by_id(result, "SYN-A")["performance"]["criteria"][0]
        self.assertEqual(prepared["computed_status"], "PASS")
        self.assertEqual(
            prepared["validation_components"]["result_validation_level"],
            "SYNTHETIC_ARITHMETIC",
        )

    def test_computed_or_reviewed_fail_requires_source(self) -> None:
        request = fixture()
        criterion = request["candidates"][1]["safety_assessments"]["cooling"][
            "criteria"
        ][0]
        criterion["source_ids"] = []
        result = solve_design_decision(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("non-empty list", result["reason"])

    def test_external_pass_requires_approved_review(self) -> None:
        request = fixture()
        criterion = request["candidates"][0]["performance_criteria"][0]
        criterion.update(
            {
                "assessment_method": "EXTERNAL_REVIEW",
                "comparison_operator": None,
                "external_review_status": "PENDING",
                "status": "PASS",
            }
        )
        result = solve_design_decision(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("requires APPROVED review", result["reason"])

    def test_actual_pdr_cannot_use_synthetic_validation_policy(self) -> None:
        request = fixture()
        request["purpose"] = "PDR_CANDIDATE_ASSESSMENT"
        request["sources"][0]["type"] = "APPROVED_PDR_SOURCE"
        result = solve_design_decision(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("not approved", result["reason"])

    def test_actual_pdr_rejects_synthetic_evidence_source(self) -> None:
        request = fixture()
        request["purpose"] = "PDR_CANDIDATE_ASSESSMENT"
        request["status_policy"]["validation_policy_id"] = "PDR_APPROVED_EVIDENCE_V1"
        result = solve_design_decision(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("cannot use synthetic evidence", result["reason"])

    def test_domain_without_mandatory_criterion_is_unknown(self) -> None:
        request = fixture()
        domain = request["candidates"][0]["safety_assessments"]["cooling"]
        for criterion in domain["criteria"]:
            criterion["mandatory"] = False
        result = solve_design_decision(request)
        cooling = by_id(result, "SYN-A")["safety_assessments"]["cooling"]
        self.assertEqual(cooling["status"], "UNKNOWN")
        self.assertTrue(cooling["missing_mandatory_criteria"])
        self.assertEqual(by_id(result, "SYN-A")["overall_outcome"], "UNKNOWN")

    def test_global_missing_inputs_propagate_for_actual_pdr(self) -> None:
        request = actual_pdr_fixture()
        result = solve_design_decision(request)
        candidate = by_id(result, "SYN-A")
        self.assertEqual(candidate["performance"]["status"], "UNKNOWN")
        self.assertIn(
            request["global_missing_inputs"][0],
            candidate["performance"]["missing_required_inputs"],
        )

    def test_equal_decision_class_has_no_id_based_priority(self) -> None:
        request = fixture()
        peer = copy.deepcopy(request["candidates"][0])
        peer["id"] = "SYN-Z"
        peer["label"] = "Peer candidate with same synthetic evidence"
        peer["parameters"][0]["value"] = "Z"
        request["candidates"].append(peer)
        result = solve_design_decision(request)
        self.assertIsNone(result["priority_candidate_id"])
        self.assertEqual(result["co_priority_candidate_ids"], ["SYN-A", "SYN-Z"])
        self.assertIn("no approved objective tie-break", result["priority_rationale"])


if __name__ == "__main__":
    unittest.main()
