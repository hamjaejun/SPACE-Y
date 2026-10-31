from __future__ import annotations

import json
from pathlib import Path
import unittest

import jsonschema

from space_y.property_adapter import (
    CAPABILITY_PHASE_PT,
    CAPABILITY_PH,
    CAPABILITY_PT,
    CAPABILITY_SATURATION_T,
    PropertyAdapterStop,
    SyntheticTabulatedPropertyAdapter,
    readiness_adapter_from_record,
    require_exact_ipa_identity,
)


ROOT = Path(__file__).resolve().parents[1]


def synthetic_spec() -> dict:
    return {
        "purpose": "SYNTHETIC_VERIFICATION_ONLY",
        "model": "TABULATED_T_PRESSURE_BOUNDED",
        "source": "independent arithmetic fixture; not IPA",
        "version": "TEST-1",
        "enthalpy_reference": "TEST_ZERO_AT_250_K",
        "pressure_range_Pa_abs": [100_000.0, 500_000.0],
        "rows": [
            {
                "temperature_K": 250.0,
                "enthalpy_J_kg": 0.0,
                "density_kg_m3": 800.0,
                "viscosity_Pa_s": 0.0020,
                "cp_J_kgK": 2000.0,
                "conductivity_W_mK": 0.140,
            },
            {
                "temperature_K": 300.0,
                "enthalpy_J_kg": 100_000.0,
                "density_kg_m3": 750.0,
                "viscosity_Pa_s": 0.0010,
                "cp_J_kgK": 2200.0,
                "conductivity_W_mK": 0.120,
            },
            {
                "temperature_K": 350.0,
                "enthalpy_J_kg": 220_000.0,
                "density_kg_m3": 700.0,
                "viscosity_Pa_s": 0.0006,
                "cp_J_kgK": 2400.0,
                "conductivity_W_mK": 0.100,
            },
        ],
    }


class PropertyAdapterTests(unittest.TestCase):
    def test_ipa_source_manifest_validates_against_schema(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_IPA_property_sources.json").read_text(encoding="utf-8")
        )
        schema = json.loads(
            (ROOT / "schemas/SPACE_Y_IPA_property_sources.schema.json").read_text(
                encoding="utf-8"
            )
        )
        jsonschema.Draft202012Validator(schema).validate(record)

    def test_exact_identity_rejects_one_propanol_and_generic_name(self) -> None:
        for name, cas in (("1-propanol", "71-23-8"), ("propanol", "67-63-0")):
            with self.subTest(name=name), self.assertRaises(PropertyAdapterStop) as caught:
                require_exact_ipa_identity(name, cas)
            self.assertEqual(caught.exception.status, "INPUT_U")
            self.assertEqual(caught.exception.capability, "fluid_identity")
        require_exact_ipa_identity("2-propanol", "67-63-0")

    def test_synthetic_provider_is_explicitly_not_ipa(self) -> None:
        adapter = SyntheticTabulatedPropertyAdapter(synthetic_spec())
        self.assertEqual(adapter.provenance.fluid_name, "SYNTHETIC_FLUID_NOT_IPA")
        self.assertIn("NOT_ENGINE_PREDICTION", adapter.provenance.use_scope)
        self.assertIsNone(adapter.provenance.cas_number)

    def test_pt_ph_round_trip_and_si_units(self) -> None:
        adapter = SyntheticTabulatedPropertyAdapter(synthetic_spec())
        state_pt = adapter.state_pt(300_000.0, 275.0)
        state_ph = adapter.state_ph(300_000.0, state_pt.enthalpy_J_kg)
        self.assertAlmostEqual(state_pt.enthalpy_J_kg, 50_000.0)
        self.assertAlmostEqual(state_ph.temperature_K, state_pt.temperature_K)
        self.assertAlmostEqual(state_ph.density_kg_m3, 775.0)
        self.assertEqual(state_ph.enthalpy_reference, "TEST_ZERO_AT_250_K")
        self.assertTrue(adapter.provenance.unit_system.startswith("SI:"))

    def test_transport_and_caloric_properties_are_individually_queryable(self) -> None:
        adapter = SyntheticTabulatedPropertyAdapter(synthetic_spec())
        self.assertAlmostEqual(adapter.density_pt(300_000.0, 275.0), 775.0)
        self.assertAlmostEqual(adapter.viscosity_pt(300_000.0, 275.0), 0.0015)
        self.assertAlmostEqual(adapter.cp_pt(300_000.0, 275.0), 2100.0)
        self.assertAlmostEqual(adapter.conductivity_pt(300_000.0, 275.0), 0.130)

    def test_no_property_extrapolation(self) -> None:
        adapter = SyntheticTabulatedPropertyAdapter(synthetic_spec())
        for call in (
            lambda: adapter.state_pt(99_999.0, 275.0),
            lambda: adapter.state_pt(300_000.0, 351.0),
            lambda: adapter.state_ph(300_000.0, 220_001.0),
        ):
            with self.assertRaises(PropertyAdapterStop) as caught:
                call()
            self.assertEqual(caught.exception.status, "OUTSIDE")

    def test_saturation_is_independent_and_unavailable(self) -> None:
        adapter = SyntheticTabulatedPropertyAdapter(synthetic_spec())
        report = adapter.capability_report()
        self.assertEqual(report[CAPABILITY_PT].status, "AVAILABLE")
        self.assertEqual(report[CAPABILITY_PH].status, "AVAILABLE")
        self.assertEqual(report[CAPABILITY_SATURATION_T].status, "INPUT_U")
        with self.assertRaises(PropertyAdapterStop) as caught:
            adapter.saturation_at_temperature(300.0)
        self.assertEqual(caught.exception.status, "INPUT_U")
        self.assertEqual(caught.exception.capability, CAPABILITY_SATURATION_T)

    def test_phase_declaration_does_not_claim_equilibrium_model(self) -> None:
        adapter = SyntheticTabulatedPropertyAdapter(synthetic_spec())
        phase = adapter.classify_phase_pt(300_000.0, 300.0)
        self.assertTrue(phase.single_phase_confirmed)
        self.assertIn("fixture declaration only", phase.evidence)
        self.assertEqual(
            adapter.capability_report()[CAPABILITY_PHASE_PT].phase_scope,
            "DECLARED_SINGLE_PHASE_TEST_DOMAIN",
        )
        phase_ph = adapter.classify_phase_ph(300_000.0, 100_000.0)
        self.assertTrue(phase_ph.single_phase_confirmed)
        self.assertIn("no equilibrium evidence", phase_ph.evidence)

    def test_invalid_values_are_input_u_not_backend_errors(self) -> None:
        adapter = SyntheticTabulatedPropertyAdapter(synthetic_spec())
        for call in (
            lambda: adapter.state_pt(float("nan"), 300.0),
            lambda: adapter.state_pt(300_000.0, 0.0),
        ):
            with self.assertRaises(PropertyAdapterStop) as caught:
                call()
            self.assertEqual(caught.exception.status, "INPUT_U")

    def test_real_ipa_readiness_record_blocks_execution(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_IPA_property_sources.json").read_text(encoding="utf-8")
        )
        adapter = readiness_adapter_from_record(record)
        self.assertEqual(adapter.provenance.cas_number, "67-63-0")
        self.assertEqual(adapter.capability_report()[CAPABILITY_PT].status, "INPUT_U")
        with self.assertRaises(PropertyAdapterStop) as caught:
            adapter.state_pt(200_000.0, 300.0)
        self.assertEqual(caught.exception.status, "INPUT_U")
        self.assertEqual(caught.exception.capability, CAPABILITY_PT)

    def test_readiness_manifest_preserves_refinement_and_validation_status(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_IPA_property_sources.json").read_text(encoding="utf-8")
        )
        self.assertEqual(record["overall_status"], "INPUT_U")
        self.assertEqual(record["physical_validation_v01_v12"], "NOT_RUN")
        refinement = record["refinement_status_preserved"]
        self.assertEqual(refinement["asymptotic_regime"], "NOT_ESTABLISHED")
        self.assertFalse(refinement["four_n_is_truth"])
        self.assertEqual(refinement["richardson_extrapolation"], "NOT_PERFORMED")
        self.assertEqual(refinement["gci"], "NOT_PERFORMED")

    def test_candidate_records_do_not_claim_m0_readiness(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_IPA_property_sources.json").read_text(encoding="utf-8")
        )
        self.assertTrue(record["candidates"])
        self.assertTrue(all(item["m0_status"] == "INPUT_U" for item in record["candidates"]))
        self.assertTrue(all(item["provider"] for item in record["candidates"]))

    def test_coolprop_probe_does_not_enable_real_backend(self) -> None:
        record = json.loads(
            (
                ROOT
                / "artifacts/property/ipa_coolprop_pcsaft_capability_probe.json"
            ).read_text(encoding="utf-8")
        )
        self.assertEqual(record["fluid_identity"]["cas_number"], "67-63-0")
        self.assertEqual(record["overall_status"], "INPUT_U")
        self.assertFalse(record["m0_backend_implemented"])
        self.assertFalse(record["validated_engine_prediction"])
        self.assertEqual(record["physical_validation_v01_v12"], "NOT_RUN")
        for capability in ("enthalpy_pt", "cp_pt", "viscosity_pt", "conductivity_pt", "state_ph"):
            self.assertEqual(record["capabilities"][capability]["status"], "INPUT_U")


if __name__ == "__main__":
    unittest.main()
