from __future__ import annotations

import json
from pathlib import Path
import unittest

import jsonschema


ROOT = Path(__file__).resolve().parents[1]


class RealInputReadinessTests(unittest.TestCase):
    def test_readiness_template_validates_and_remains_unready(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_M0_real_input_readiness.json").read_text(encoding="utf-8")
        )
        schema = json.loads(
            (ROOT / "schemas/SPACE_Y_M0_real_input_readiness.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(schema).validate(record)
        self.assertEqual(record["overall_status"], "INPUT_U")
        self.assertEqual(record["property_model"]["status"], "INPUT_U")
        self.assertEqual(record["phase_equilibrium"]["status"], "INPUT_U")
        self.assertEqual(record["nozzle_cea_mapping"]["status"], "INPUT_U")
        self.assertEqual(record["nozzle_cea_mapping"]["segment_station_mappings"], [])
        self.assertEqual(record["physical_validation_v01_v12"], "NOT_RUN")

    def test_no_actual_design_values_are_present_in_readiness_template(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_M0_real_input_readiness.json").read_text(encoding="utf-8")
        )
        self.assertIsNone(record["coolant_identity"]["purity_mass_fraction"])
        self.assertIsNone(record["coolant_identity"]["water_mass_fraction"])
        self.assertIsNone(record["property_model"]["temperature_range_K"])
        self.assertIsNone(record["property_model"]["pressure_range_Pa_abs"])
        self.assertIsNone(record["nozzle_cea_mapping"]["geometry_version"])
        self.assertIsNone(record["nozzle_cea_mapping"]["cea_artifact_sha256"])

    def test_pdr_input_readiness_is_schema_valid_and_unready(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_PDR_input_readiness.json").read_text(
                encoding="utf-8"
            )
        )
        schema = json.loads(
            (ROOT / "schemas/SPACE_Y_PDR_input_readiness.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(schema).validate(record)
        self.assertFalse(record["actual_design_values_present"])
        self.assertEqual(record["physical_validation_v01_v12"], "NOT_RUN")
        self.assertTrue(record["available_analysis_assets"])
        self.assertTrue(
            all(
                asset["actual_candidate_evidence_status"] == "INPUT_U"
                for asset in record["available_analysis_assets"]
            )
        )
        for items in record["categories"].values():
            self.assertTrue(items)
            self.assertTrue(all(item["status"] == "INPUT_U" for item in items))
            self.assertTrue(all(item["current_value"] is None for item in items))
            self.assertTrue(all(item["owner"] is None for item in items))
            self.assertTrue(all(item["due_date"] is None for item in items))

    def test_cad_handoff_template_contains_no_released_baseline(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_CAD_handoff_template.json").read_text(
                encoding="utf-8"
            )
        )
        schema = json.loads(
            (ROOT / "schemas/SPACE_Y_CAD_handoff.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(schema).validate(record)
        self.assertIsNone(record["provisional_baseline_candidate_id"])
        self.assertEqual(record["provisional_parameters"], [])
        self.assertFalse(record["manufacture_release"])
        self.assertFalse(record["test_safety_approval"])

    def test_rpa_audit_keeps_liquid_ipa_adapter_blocked(self) -> None:
        record = json.loads(
            (ROOT / "artifacts/property/rpa_2_3_ipa_property_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["fluid_identity"]["cas_number"], "67-63-0")
        self.assertEqual(
            record["combustion_thermochemistry"]["status"],
            "AVAILABLE_FOR_RPA_COMBUSTION_THERMOCHEMISTRY_ONLY",
        )
        coolant = record["regenerative_coolant_properties"]
        self.assertTrue(
            all(
                value == "INPUT_U"
                for key, value in coolant.items()
                if key != "user_database_present_in_reviewed_installation"
            )
        )
        self.assertFalse(record["property_adapter_connection"]["implemented"])
        self.assertFalse(record["license_and_publication"]["installed_database_files_committed"])
        self.assertFalse(record["validated_engine_prediction"])

    def test_rpa_design_handoff_separates_inputs_outputs_and_external_design(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_RPA_design_handoff_template.json").read_text(
                encoding="utf-8"
            )
        )
        schema = json.loads(
            (ROOT / "schemas/SPACE_Y_RPA_design_handoff.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(schema).validate(record)
        self.assertIsNone(record["source_candidate_id"])
        self.assertFalse(record["team_selected_baseline"])
        self.assertTrue(all(item["value"] is None for item in record["rpa_direct_inputs"]))
        self.assertTrue(
            all(item["status"] == "NOT_RUN" for item in record["rpa_computed_outputs"])
        )
        self.assertTrue(record["separately_decided_design_items"])
        self.assertFalse(record["rpa_run_completed"])
        self.assertFalse(record["geometry_export_verified"])

    def test_rpa_geometry_audit_is_documentation_not_execution_evidence(self) -> None:
        record = json.loads(
            (ROOT / "artifacts/rpa/rpa_2_3_geometry_capability_audit.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(record["product"], "RPA Standard Edition v.2.3.2")
        self.assertEqual(
            record["capability_status"]["dxf_contour_export"],
            "DOCUMENTED_NOT_EXECUTED_FOR_SPACE_Y",
        )
        self.assertFalse(record["local_space_y_geometry_run_completed"])
        self.assertFalse(record["local_dxf_export_verified"])
        self.assertFalse(record["validated_engine_prediction"])


if __name__ == "__main__":
    unittest.main()
