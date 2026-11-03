from __future__ import annotations

import copy
import json
import math
from pathlib import Path
import unittest

from space_y.coolant import (
    TRANSITION_HEAT_TRANSFER_MODEL_A,
    TRANSITION_HEAT_TRANSFER_MODEL_B,
    build_friction_domain,
    build_heat_transfer_domain,
    build_rectangular_channel_cross_section,
    evaluate_rectangular_cooling_hl,
    evaluate_rectangular_transition_heat_transfer_model_b,
)
from space_y.physics import (
    linear_transition_nusselt,
    rectangular_duct_aspect_ratio,
    rectangular_laminar_nusselt,
)
from space_y.cooling_channel_candidates import solve_cooling_channel_candidates


ROOT = Path(__file__).resolve().parents[1]


class TransitionModelBTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.heat_domain = build_heat_transfer_domain(
            {
                "source": "SYNTHETIC_MODEL_B_TEST_DOMAIN",
                "re_range": [3000.0, 5_000_000.0],
                "pr_range": [0.5, 100.0],
                "geometry": "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
                "fully_developed_required": True,
            }
        )
        cls.friction_domain = build_friction_domain(
            {
                "source": "SYNTHETIC_MODEL_B_TEST_DOMAIN",
                "re_range": [3000.0, 5_000_000.0],
                "relative_roughness_range": [0.0, 0.05],
                "geometry": "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
            }
        )

    def _evaluate(
        self,
        reynolds: float,
        *,
        width_m: float = 0.01,
        height_m: float = 0.01,
        boundary: str = "CONSTANT_WALL_TEMPERATURE",
        model: str = TRANSITION_HEAT_TRANSFER_MODEL_B,
    ):
        section = build_rectangular_channel_cross_section(
            {"width_m": width_m, "height_m": height_m, "channel_count": 1}
        )
        viscosity = 0.001
        mass_flow = (
            reynolds
            * section.total_flow_area_m2
            * viscosity
            / section.hydraulic_diameter_m
        )
        return evaluate_rectangular_cooling_hl(
            coolant_mass_flow_kg_s=mass_flow,
            width_m=width_m,
            height_m=height_m,
            channel_count=1,
            viscosity_Pa_s=viscosity,
            cp_J_kgK=2000.0,
            conductivity_W_mK=0.15,
            equivalent_sand_roughness_m=1.0e-6,
            closure_geometry=(
                "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER"
            ),
            fully_developed=True,
            thermal_boundary_condition="SYNTHETIC_UNMAPPED_BOUNDARY",
            heat_transfer_domain=self.heat_domain,
            heat_transfer_thermal_boundary_condition=(
                "SYNTHETIC_UNMAPPED_BOUNDARY"
            ),
            friction_domain=self.friction_domain,
            density_kg_m3=800.0,
            laminar_thermal_boundary_condition=boundary,
            transition_heat_transfer_model=model,
        )

    def _evaluate_model_b_endpoint(
        self,
        reynolds: float,
        *,
        width_m: float = 0.01,
        height_m: float = 0.01,
        boundary: str = "CONSTANT_WALL_TEMPERATURE",
    ):
        section = build_rectangular_channel_cross_section(
            {"width_m": width_m, "height_m": height_m, "channel_count": 1}
        )
        viscosity = 0.001
        mass_flow = (
            reynolds
            * section.total_flow_area_m2
            * viscosity
            / section.hydraulic_diameter_m
        )
        return evaluate_rectangular_transition_heat_transfer_model_b(
            mass_flow_kg_s=mass_flow,
            flow_area_m2=section.total_flow_area_m2,
            hydraulic_diameter_m=section.hydraulic_diameter_m,
            width_m=width_m,
            height_m=height_m,
            viscosity_Pa_s=viscosity,
            cp_J_kgK=2000.0,
            conductivity_W_mK=0.15,
            equivalent_sand_roughness_m=1.0e-6,
            closure_geometry=(
                "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER"
            ),
            fully_developed=True,
            thermal_boundary_condition="SYNTHETIC_UNMAPPED_BOUNDARY",
            heat_transfer_domain=self.heat_domain,
            heat_transfer_thermal_boundary_condition=(
                "SYNTHETIC_UNMAPPED_BOUNDARY"
            ),
            friction_domain=self.friction_domain,
            density_kg_m3=800.0,
            laminar_thermal_boundary_condition=boundary,
        )

    def test_interpolation_primitive_matches_both_endpoints(self) -> None:
        self.assertEqual(linear_transition_nusselt(2300.0, 3.0, 12.0), (3.0, 0.0))
        self.assertEqual(linear_transition_nusselt(3000.0, 3.0, 12.0), (12.0, 1.0))
        value, gamma = linear_transition_nusselt(2650.0, 3.0, 12.0)
        self.assertAlmostEqual(gamma, 0.5)
        self.assertAlmostEqual(value, 7.5)

    def test_re_2300_matches_shah_london_endpoint(self) -> None:
        for boundary, index in (
            ("CONSTANT_WALL_TEMPERATURE", 0),
            ("SHAH_LONDON_H1", 1),
        ):
            with self.subTest(boundary=boundary):
                result = self._evaluate_model_b_endpoint(2300.0, boundary=boundary)
                aspect = rectangular_duct_aspect_ratio(0.01, 0.01)
                expected = rectangular_laminar_nusselt(aspect)[index]
                self.assertAlmostEqual(result.Nusselt, expected, places=12)
                self.assertEqual(result.heat_transfer_applicability["gamma"], 0.0)

    def test_re_3000_matches_existing_gnielinski_endpoint(self) -> None:
        for boundary in ("CONSTANT_WALL_TEMPERATURE", "SHAH_LONDON_H1"):
            with self.subTest(boundary=boundary):
                model_b = self._evaluate_model_b_endpoint(3000.0, boundary=boundary)
                existing = self._evaluate(3000.0, boundary=boundary)
                self.assertAlmostEqual(model_b.Nusselt, existing.Nusselt, places=12)
                self.assertAlmostEqual(model_b.h_l_W_m2K, existing.h_l_W_m2K, places=12)
                self.assertEqual(model_b.heat_transfer_applicability["gamma"], 1.0)

    def test_boundary_neighborhood_is_continuous_and_positive(self) -> None:
        reynolds_values = (
            2299.999,
            2300.0,
            2300.001,
            2500.0,
            2800.0,
            2999.999,
            3000.0,
            3000.001,
        )
        results = [self._evaluate(value) for value in reynolds_values]
        for result in results:
            self.assertTrue(math.isfinite(result.Nusselt))
            self.assertTrue(math.isfinite(result.h_l_W_m2K))
            self.assertGreater(result.Nusselt, 0.0)
            self.assertGreater(result.h_l_W_m2K, 0.0)
        self.assertLess(abs(results[2].Nusselt - results[1].Nusselt), 1.0e-3)
        self.assertLess(abs(results[6].Nusselt - results[5].Nusselt), 1.0e-3)
        self.assertLess(abs(results[7].Nusselt - results[6].Nusselt), 1.0e-3)

    def test_interpolation_is_monotonic_for_multiple_aspect_ratios(self) -> None:
        for width_m, height_m in (
            (0.01, 0.01),
            (0.02, 0.01),
            (0.04, 0.01),
            (0.01, 0.004),
        ):
            for boundary in ("CONSTANT_WALL_TEMPERATURE", "SHAH_LONDON_H1"):
                with self.subTest(
                    width_m=width_m,
                    height_m=height_m,
                    boundary=boundary,
                ):
                    values = [
                        self._evaluate(
                            reynolds,
                            width_m=width_m,
                            height_m=height_m,
                            boundary=boundary,
                        ).Nusselt
                        for reynolds in (2300.0, 2500.0, 2800.0, 2999.999)
                    ]
                    self.assertTrue(
                        all(second > first for first, second in zip(values, values[1:]))
                    )

    def test_model_b_does_not_claim_transition_friction(self) -> None:
        result = self._evaluate(2500.0)
        self.assertIsNone(result.darcy_factor)
        self.assertIsNone(result.friction_pressure_gradient_Pa_m)
        self.assertEqual(
            result.friction_applicability["status"],
            "OUTSIDE_TRANSITION_FRICTION_UNSUPPORTED",
        )
        self.assertEqual(
            result.heat_transfer_applicability["model_classification"],
            "SPACE_Y_INTERNAL_EXTENSION_ASSUMPTION",
        )
        self.assertEqual(
            result.heat_transfer_applicability["experimental_validation_status"],
            "NOT_RUN",
        )

    def test_model_choice_does_not_change_outer_regimes(self) -> None:
        for reynolds in (1000.0, 2299.999, 3000.0, 10000.0):
            with self.subTest(reynolds=reynolds):
                model_a = self._evaluate(
                    reynolds, model=TRANSITION_HEAT_TRANSFER_MODEL_A
                )
                model_b = self._evaluate(
                    reynolds, model=TRANSITION_HEAT_TRANSFER_MODEL_B
                )
                self.assertEqual(model_a, model_b)

    def test_candidate_station_pipeline_uses_default_model_b_for_h_l(self) -> None:
        request = json.loads(
            (ROOT / "examples/cooling_channel_sweep_synthetic_axial.json").read_text(
                encoding="utf-8"
            )
        )
        request = copy.deepcopy(request)
        request["run_id"] = "SYNTHETIC-MODEL-B-STATION-INTEGRATION"
        request["coolant_conditions"]["coolant_mass_flow_kg_s"] = 0.35
        request["model_conditions"]["laminar_thermal_boundary_condition"] = (
            "CONSTANT_WALL_TEMPERATURE"
        )
        request["model_conditions"]["transition_heat_transfer_model"] = (
            TRANSITION_HEAT_TRANSFER_MODEL_B
        )
        result = solve_cooling_channel_candidates(request)
        transition_calculations = [
            station["calculation"]
            for candidate in result["candidates"]
            for station in candidate["stations"]
            if (station.get("calculation", {}).get("results") or {})
            .get("applicability_assessment", {})
            .get("flow_regime")
            == "TRANSITION_MODEL_B"
        ]
        self.assertTrue(transition_calculations)
        for calculation in transition_calculations:
            self.assertEqual(calculation["status"], "MODEL_U")
            values = calculation["results"]
            self.assertGreater(values["Nusselt"], 0.0)
            self.assertGreater(values["h_l_W_m2K"], 0.0)
            self.assertIsNone(values["darcy_factor"])
            self.assertIsNone(values["friction_pressure_gradient_Pa_m"])


if __name__ == "__main__":
    unittest.main()
