from __future__ import annotations

import math
import unittest

from space_y.coolant import (
    CoolantStop,
    build_friction_domain,
    build_heat_transfer_domain,
    build_rectangular_channel_cross_section,
    evaluate_rectangular_cooling_hl,
    evaluate_rectangular_transition_heat_transfer_model_b,
)
from space_y.cooling_transition_sensitivity import (
    SENSITIVITY_MODEL_3000,
    SENSITIVITY_MODEL_4000,
    evaluate_linear_transition_endpoint_sensitivity,
)


GEOMETRY = "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER"


class TransitionSensitivityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.heat_domain = build_heat_transfer_domain(
            {
                "source": "SYNTHETIC_SENSITIVITY_TEST_DOMAIN",
                "re_range": [3000.0, 5_000_000.0],
                "pr_range": [0.5, 100.0],
                "geometry": GEOMETRY,
                "fully_developed_required": True,
            }
        )
        cls.friction_domain = build_friction_domain(
            {
                "source": "SYNTHETIC_SENSITIVITY_TEST_DOMAIN",
                "re_range": [3000.0, 5_000_000.0],
                "relative_roughness_range": [0.0, 0.05],
                "geometry": GEOMETRY,
            }
        )

    def _sensitivity(
        self,
        reynolds: float,
        upper: float,
        *,
        width: float = 0.01,
        height: float = 0.01,
        boundary: str = "CONSTANT_WALL_TEMPERATURE",
        fully_developed: bool | None = True,
    ):
        section = build_rectangular_channel_cross_section(
            {"width_m": width, "height_m": height, "channel_count": 1}
        )
        return evaluate_linear_transition_endpoint_sensitivity(
            reynolds=reynolds,
            prandtl=13.333333333333334,
            width_m=width,
            height_m=height,
            hydraulic_diameter_m=section.hydraulic_diameter_m,
            conductivity_W_mK=0.15,
            equivalent_sand_roughness_m=1.0e-6,
            closure_geometry=GEOMETRY,
            fully_developed=fully_developed,
            thermal_boundary_condition=boundary,
            heat_transfer_domain=self.heat_domain,
            friction_domain=self.friction_domain,
            upper_endpoint_reynolds=upper,
        )

    def _production(self, reynolds: float, *, boundary: str = "CONSTANT_WALL_TEMPERATURE"):
        section = build_rectangular_channel_cross_section(
            {"width_m": 0.01, "height_m": 0.01, "channel_count": 1}
        )
        viscosity = 0.001
        mass_flow = reynolds * section.total_flow_area_m2 * viscosity / section.hydraulic_diameter_m
        return evaluate_rectangular_cooling_hl(
            coolant_mass_flow_kg_s=mass_flow,
            width_m=0.01,
            height_m=0.01,
            channel_count=1,
            viscosity_Pa_s=viscosity,
            cp_J_kgK=2000.0,
            conductivity_W_mK=0.15,
            equivalent_sand_roughness_m=1.0e-6,
            closure_geometry=GEOMETRY,
            fully_developed=True,
            thermal_boundary_condition="SYNTHETIC_UNMAPPED_BOUNDARY",
            heat_transfer_domain=self.heat_domain,
            heat_transfer_thermal_boundary_condition="SYNTHETIC_UNMAPPED_BOUNDARY",
            friction_domain=self.friction_domain,
            density_kg_m3=800.0,
            laminar_thermal_boundary_condition=boundary,
        )

    def test_re_3000_sensitivity_reproduces_production_model_b(self) -> None:
        for boundary in ("CONSTANT_WALL_TEMPERATURE", "SHAH_LONDON_H1"):
            for reynolds in (2300.0, 2500.0, 2999.999, 3000.0):
                with self.subTest(boundary=boundary, reynolds=reynolds):
                    comparison = self._sensitivity(reynolds, 3000.0, boundary=boundary)
                    production = self._production(reynolds, boundary=boundary)
                    self.assertAlmostEqual(comparison.Nusselt, production.Nusselt, places=12)
                    self.assertAlmostEqual(comparison.h_l_W_m2K, production.h_l_W_m2K, places=12)
                    self.assertEqual(comparison.correlation_id, SENSITIVITY_MODEL_3000)

    def test_re_4000_path_connects_endpoints_for_both_boundaries(self) -> None:
        for boundary in ("CONSTANT_WALL_TEMPERATURE", "SHAH_LONDON_H1"):
            low = self._sensitivity(2300.0, 4000.0, boundary=boundary)
            high = self._sensitivity(4000.0, 4000.0, boundary=boundary)
            self.assertEqual(low.interpolation_fraction, 0.0)
            self.assertEqual(high.interpolation_fraction, 1.0)
            self.assertAlmostEqual(low.Nusselt, low.laminar_endpoint_Nusselt, places=12)
            self.assertAlmostEqual(high.Nusselt, high.turbulent_endpoint_Nusselt, places=12)
            self.assertEqual(high.correlation_id, SENSITIVITY_MODEL_4000)

    def test_results_are_positive_across_aspect_ratios(self) -> None:
        for width, height in ((0.01, 0.01), (0.02, 0.01), (0.04, 0.01)):
            for boundary in ("CONSTANT_WALL_TEMPERATURE", "SHAH_LONDON_H1"):
                for upper in (3000.0, 4000.0):
                    result = self._sensitivity(
                        2650.0, upper, width=width, height=height, boundary=boundary
                    )
                    self.assertTrue(math.isfinite(result.Nusselt))
                    self.assertTrue(math.isfinite(result.h_l_W_m2K))
                    self.assertGreater(result.Nusselt, 0.0)
                    self.assertGreater(result.h_l_W_m2K, 0.0)

    def test_transition_friction_remains_unsupported(self) -> None:
        result = self._sensitivity(3000.0, 4000.0)
        self.assertIsNone(result.darcy_factor)
        self.assertIsNone(result.friction_pressure_gradient_Pa_m)
        self.assertFalse(result.experimental_validation)
        self.assertEqual(result.friction_support_status, "OUTSIDE_TRANSITION_FRICTION_UNSUPPORTED")

    def test_sensitivity_path_does_not_change_production_dispatch(self) -> None:
        production = self._production(3500.0)
        comparison = self._sensitivity(3500.0, 4000.0)
        self.assertEqual(
            production.heat_transfer_applicability["status"],
            "APPLICABLE_TO_SUPPLIED_DOMAIN",
        )
        self.assertIsNotNone(production.darcy_factor)
        self.assertIsNone(comparison.darcy_factor)
        self.assertNotAlmostEqual(production.Nusselt, comparison.Nusselt, places=6)

    def test_invalid_ranges_and_assumptions_stop(self) -> None:
        cases = (
            lambda: self._sensitivity(2500.0, 3500.0),
            lambda: self._sensitivity(2200.0, 3000.0),
            lambda: self._sensitivity(4100.0, 4000.0),
            lambda: self._sensitivity(2500.0, 3000.0, fully_developed=None),
            lambda: self._sensitivity(2500.0, 3000.0, fully_developed=False),
            lambda: self._sensitivity(2500.0, 3000.0, boundary="UNSPECIFIED"),
        )
        for operation in cases:
            with self.subTest(operation=operation):
                with self.assertRaises(CoolantStop):
                    operation()


if __name__ == "__main__":
    unittest.main()
