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


if __name__ == "__main__":
    unittest.main()
