from __future__ import annotations

import unittest

from model_h_engine_integration import (
    assess_supplied_ipa_states,
    evaluate_candidate_key_by_angle,
)
from model_h_v1 import generate_model_h_candidates


class FakeIPA:
    T_CRIT = 508.3

    def __init__(self) -> None:
        self.property_calls = 0

    def rho(self, temperature_K: float) -> float:
        self.property_calls += 1
        return 780.0

    def mu(self, temperature_K: float) -> float:
        self.property_calls += 1
        return 1.0e-3

    def cp(self, temperature_K: float) -> float:
        self.property_calls += 1
        return 2400.0

    def k(self, temperature_K: float) -> float:
        self.property_calls += 1
        return 0.14

    def T_sat(self, pressure_Pa: float) -> float:
        return 490.0


class ModelHEngineIntegrationTests(unittest.TestCase):
    def test_property_gate_does_not_extrapolate_transport_correlations(self) -> None:
        ipa = FakeIPA()
        result = assess_supplied_ipa_states(
            temperature_K=[300.0, 409.0, None],
            pressure_Pa_abs=[4.0e6, 4.0e6, 4.0e6],
            ipa=ipa,
        )
        self.assertEqual(result["status_by_station"], ["MODEL_U", "OUTSIDE", "INPUT_U"])
        self.assertEqual(ipa.property_calls, 4)
        self.assertIsNone(result["viscosity_Pa_s"][1])
        self.assertIsNone(result["conductivity_W_mK"][2])
        self.assertFalse(result["pressure_effects_in_transport_properties"])
        self.assertFalse(result["validated_engine_prediction"])

    def test_phase_boundary_stops_property_calculation(self) -> None:
        ipa = FakeIPA()
        result = assess_supplied_ipa_states(
            temperature_K=[491.0],
            pressure_Pa_abs=[4.0e6],
            ipa=ipa,
        )
        self.assertEqual(result["status_by_station"], ["OUTSIDE"])
        self.assertEqual(ipa.property_calls, 0)

    def test_beta_zero_regression_with_engine_radius_mode_is_exact(self) -> None:
        x_m = [0.0, 0.01, 0.02]
        geometry = generate_model_h_candidates(
            x_m=x_m,
            gas_side_radius_m=[0.03, 0.025, 0.04],
            wall_thickness_m=0.001,
            throat_station_index=1,
            channel_width_limits_m=(0.0001, 0.02),
            minimum_rib_thickness_m=0.0005,
            allowed_channel_heights_m=[0.002],
            helix_angles_deg=[0.0, 20.0],
            width_candidates_mm=[1.2],
            rib_candidates_mm=[1.0],
            height_candidates_mm=[2.0],
        )
        candidate = next(item for item in geometry["candidates"] if item["valid"])
        key = (
            candidate["channel_count"],
            candidate["rib_thickness_m"],
            candidate["channel_height_m"],
        )
        properties = {
            "status_by_station": ["MODEL_U"] * 3,
            "density_kg_m3": [780.0] * 3,
            "viscosity_Pa_s": [0.001] * 3,
            "cp_J_kgK": [2400.0] * 3,
            "conductivity_W_mK": [0.14] * 3,
        }
        result = evaluate_candidate_key_by_angle(
            candidates=geometry["candidates"],
            x_m=x_m,
            candidate_key=key,
            mdot_total_kg_s=0.04,
            properties=properties,
            roughness_m=20.0e-6,
            thermal_boundary_condition="SHAH_LONDON_H1",
            fully_developed=True,
            colebrook_re_range=(3000.0, 1.0e7),
            colebrook_relative_roughness_range=(0.0, 0.05),
        )
        self.assertTrue(result["beta_zero_model_b_regression"]["pass"])
        self.assertEqual(len(result["angles"]), 2)
        self.assertFalse(result["recoupled_helical_wall_coolant_march"])


if __name__ == "__main__":
    unittest.main()
