from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import jsonschema

from space_y.design_space import explore_design_space, explore_design_space_file


ROOT = Path(__file__).resolve().parents[1]


def fixture() -> dict:
    return json.loads(
        (ROOT / "examples/design_space_synthetic_grid.json").read_text(encoding="utf-8")
    )


def candidate(result: dict, candidate_id: str) -> dict:
    return next(item for item in result["candidates"] if item["candidate_id"] == candidate_id)


class DesignSpaceTests(unittest.TestCase):
    def test_schema_and_file_runner_hashes(self) -> None:
        request = fixture()
        schema = json.loads(
            (ROOT / "schemas/SPACE_Y_design_space.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(request)
        result = explore_design_space_file(
            ROOT / "examples/design_space_synthetic_grid.json",
            ROOT / "schemas/SPACE_Y_design_space.schema.json",
        )
        self.assertEqual(result["status"], "PASS")
        self.assertRegex(result["request_sha256"], r"^[0-9a-f]{64}$")
        self.assertRegex(result["schema_sha256"], r"^[0-9a-f]{64}$")

    def test_actual_readiness_template_is_valid_and_blocked(self) -> None:
        request = json.loads(
            (ROOT / "config/SPACE_Y_design_space_readiness.json").read_text(encoding="utf-8")
        )
        schema = json.loads(
            (ROOT / "schemas/SPACE_Y_design_space.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(request)
        result = explore_design_space(request)
        self.assertEqual(result["status"], "BLOCKED_BY_INPUT")
        self.assertEqual(result["candidate_count"], 0)
        self.assertEqual(result["relative_shortlist_candidate_ids"], [])

    def test_grid_generation_is_deterministic(self) -> None:
        first = explore_design_space(fixture())
        second = explore_design_space(copy.deepcopy(fixture()))
        self.assertEqual(first, second)
        self.assertEqual(first["candidate_count"], 4)
        self.assertEqual(
            [item["parameters"] for item in first["candidates"]],
            [item["parameters"] for item in second["candidates"]],
        )

    def test_unapproved_range_generates_no_candidates(self) -> None:
        request = fixture()
        request["design_variables"][0]["range_approval_status"] = "PROVISIONAL"
        result = explore_design_space(request)
        self.assertEqual(result["status"], "BLOCKED_BY_INPUT")
        self.assertEqual(result["candidate_count"], 0)
        self.assertTrue(any("range is not approved" in item for item in result["generation_blockers"]))

    def test_unavailable_range_source_blocks_generation(self) -> None:
        request = fixture()
        request["sources"][0]["status"] = "INPUT_U"
        result = explore_design_space(request)
        self.assertEqual(result["status"], "BLOCKED_BY_INPUT")
        self.assertEqual(result["candidate_count"], 0)
        self.assertTrue(any("source is unavailable" in item for item in result["generation_blockers"]))

    def test_missing_comparison_basis_blocks_generation(self) -> None:
        request = fixture()
        request["comparison_basis"]["ambient_condition"] = None
        result = explore_design_space(request)
        self.assertEqual(result["status"], "BLOCKED_BY_INPUT")
        self.assertIn("comparison basis missing ambient_condition", result["generation_blockers"])

    def test_different_result_boundary_is_not_compared(self) -> None:
        request = fixture()
        request["result_records"][0]["comparison_basis_id"] = "OTHER-BASIS"
        result = explore_design_space(request)
        first = candidate(result, "DSE-0001")
        self.assertEqual(
            first["linked_results"][0]["comparability"], "NOT_COMPARABLE_BOUNDARY"
        )
        self.assertEqual(first["metrics"], {})
        self.assertEqual(first["overall_outcome"], "UNKNOWN")

    def test_unit_mismatch_never_becomes_pass(self) -> None:
        request = fixture()
        metric = request["result_records"][0]["metrics"][0]
        metric["unit"] = "different_unit"
        result = explore_design_space(request)
        assessment = candidate(result, "DSE-0001")["domain_assessments"]["performance"]
        self.assertEqual(assessment["status"], "UNKNOWN")
        self.assertIn("unit mismatch", assessment["constraint_assessments"][0]["reason"])

    def test_missing_metric_is_unknown_and_domain_is_retained(self) -> None:
        request = fixture()
        request["result_records"][0]["metrics"] = [
            metric for metric in request["result_records"][0]["metrics"] if metric["id"] != "M-COMB"
        ]
        result = explore_design_space(request)
        first = candidate(result, "DSE-0001")
        self.assertEqual(first["domain_assessments"]["combustion"]["status"], "UNKNOWN")
        self.assertEqual(set(first["domain_assessments"]), {
            "performance", "supply", "cooling", "structural", "combustion", "operations"
        })
        self.assertEqual(first["overall_outcome"], "UNKNOWN")

    def test_hard_constraint_failure_cannot_be_offset(self) -> None:
        result = explore_design_space(fixture())
        failed = candidate(result, "DSE-0004")
        self.assertEqual(failed["overall_outcome"], "EXCLUDED")
        self.assertEqual(failed["domain_assessments"]["supply"]["status"], "VIOLATED")
        self.assertGreater(failed["metrics"]["M-PERF"]["value"], 100.0)

    def test_synthetic_result_cannot_enter_actual_exploration(self) -> None:
        request = fixture()
        request["purpose"] = "PDR_DESIGN_SPACE_EXPLORATION"
        request["sources"][0]["type"] = "APPROVED_PDR_SOURCE"
        request["comparison_basis"]["approval_status"] = "APPROVED_FOR_EXPLORATION"
        for item in request["fixed_conditions"]:
            item["approval_status"] = "APPROVED_FOR_EXPLORATION"
        for item in request["design_variables"]:
            item["range_approval_status"] = "APPROVED_FOR_EXPLORATION"
        for item in request["constraints"]:
            item["approval_status"] = "APPROVED_FOR_EXPLORATION"
        result = explore_design_space(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("synthetic result cannot enter actual exploration", result["reason"])

    def test_synthetic_candidates_are_never_promoted(self) -> None:
        result = explore_design_space(fixture())
        self.assertFalse(result["safe_design_claim"])
        self.assertFalse(result["validated_engine_prediction"])
        self.assertEqual(result["physical_validation_v01_v12"], "NOT_RUN")
        self.assertTrue(all(not item["pdr_candidate"] for item in result["candidates"]))
        self.assertTrue(all(not item["validated_design"] for item in result["candidates"]))

    def test_pareto_scope_is_relative_only(self) -> None:
        result = explore_design_space(fixture())
        self.assertEqual(result["pareto_analysis"]["status"], "RELATIVE_ONLY")
        self.assertIn("not a global optimum", result["pareto_analysis"]["scope_warning"])
        self.assertNotIn("DSE-0004", result["pareto_analysis"]["candidate_ids"])

    def test_unknown_candidate_is_not_shortlisted(self) -> None:
        request = fixture()
        request["result_records"][0]["metrics"][0]["status"] = "NOT_RUN"
        request["result_records"][0]["metrics"][0]["value"] = None
        request["result_records"][0]["metrics"][0]["unit"] = None
        result = explore_design_space(request)
        self.assertEqual(candidate(result, "DSE-0001")["overall_outcome"], "UNKNOWN")
        self.assertNotIn("DSE-0001", result["relative_shortlist_candidate_ids"])

    def test_parameter_units_must_match_variable_contract(self) -> None:
        request = fixture()
        request["result_records"][0]["candidate_parameters"]["SYN-X"]["unit"] = "bad"
        result = explore_design_space(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("parameter unit mismatch", result["reason"])

    def test_grid_limit_prevents_accidental_large_sweep(self) -> None:
        request = fixture()
        request["max_candidates"] = 3
        result = explore_design_space(request)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("exceeding max_candidates", result["reason"])


if __name__ == "__main__":
    unittest.main()
