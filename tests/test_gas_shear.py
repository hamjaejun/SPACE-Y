from __future__ import annotations

import math
import unittest

from space_y.gas_shear import (
    GasShearStop,
    evaluate_couette_from_gas_state,
    evaluate_local_gas_film_shear,
    gas_shear_stress_from_local_coefficient,
    local_turbulent_flat_plate_skin_friction,
)


class GasFilmShearTests(unittest.TestCase):
    def test_local_skin_friction_matches_cited_equation(self) -> None:
        reynolds = 1.0e6
        expected = 0.0592 * reynolds ** (-0.2)
        self.assertAlmostEqual(local_turbulent_flat_plate_skin_friction(reynolds), expected)

    def test_local_not_average_coefficient_is_used(self) -> None:
        reynolds = 1.0e6
        local = local_turbulent_flat_plate_skin_friction(reynolds)
        average = 0.074 * reynolds ** (-0.2)
        self.assertNotAlmostEqual(local, average)
        self.assertLess(local, average)

    def test_reynolds_range_is_not_extrapolated(self) -> None:
        for reynolds in (4.99e5, 1.01e7):
            with self.subTest(reynolds=reynolds), self.assertRaises(GasShearStop) as caught:
                local_turbulent_flat_plate_skin_friction(reynolds)
            self.assertEqual(caught.exception.status, "OUTSIDE")

    def test_shear_definition_and_units(self) -> None:
        shear = gas_shear_stress_from_local_coefficient(
            local_skin_friction_coefficient=0.004,
            density_kg_m3=2.0,
            relative_velocity_m_s=100.0,
        )
        self.assertEqual(shear, 40.0)

    def test_evaluation_keeps_boundary_layer_origin_independent(self) -> None:
        result = evaluate_local_gas_film_shear(
            density_kg_m3=1.0,
            gas_velocity_m_s=100.0,
            gas_viscosity_Pa_s=1.0e-5,
            mach=0.2,
            boundary_layer_development_length_m=0.1,
            boundary_layer_origin_source="independent chamber boundary-layer origin",
            film_injection_coordinate_source="wall film injector coordinate",
            pressure_gradient_mode="ZERO_PRESSURE_GRADIENT_REFERENCE",
        )
        self.assertAlmostEqual(result.reynolds_x, 1.0e6)
        self.assertNotEqual(
            result.boundary_layer_origin_source, result.film_injection_coordinate_source
        )
        self.assertEqual(result.applicability_status, "MODEL_U")
        self.assertFalse(result.experimental_validation)

    def test_moving_interface_uses_relative_velocity(self) -> None:
        result = evaluate_local_gas_film_shear(
            density_kg_m3=1.0,
            gas_velocity_m_s=110.0,
            film_surface_velocity_m_s=10.0,
            gas_viscosity_Pa_s=1.0e-5,
            mach=0.2,
            boundary_layer_development_length_m=0.1,
            boundary_layer_origin_source="BL origin",
            film_injection_coordinate_source="film injector",
            pressure_gradient_mode="ZERO_PRESSURE_GRADIENT_REFERENCE",
        )
        self.assertEqual(result.relative_velocity_m_s, 100.0)
        self.assertAlmostEqual(result.reynolds_x, 1.0e6)

    def test_compressible_condition_is_blocked_without_correction(self) -> None:
        with self.assertRaises(GasShearStop) as caught:
            evaluate_local_gas_film_shear(
                density_kg_m3=1.0,
                gas_velocity_m_s=100.0,
                gas_viscosity_Pa_s=1.0e-5,
                mach=0.3,
                boundary_layer_development_length_m=0.1,
                boundary_layer_origin_source="BL origin",
                film_injection_coordinate_source="film injector",
                pressure_gradient_mode="ZERO_PRESSURE_GRADIENT_REFERENCE",
            )
        self.assertEqual(caught.exception.status, "OUTSIDE")

    def test_nonzero_pressure_gradient_is_blocked(self) -> None:
        with self.assertRaises(GasShearStop) as caught:
            evaluate_local_gas_film_shear(
                density_kg_m3=1.0,
                gas_velocity_m_s=100.0,
                gas_viscosity_Pa_s=1.0e-5,
                mach=0.2,
                boundary_layer_development_length_m=0.1,
                boundary_layer_origin_source="BL origin",
                film_injection_coordinate_source="film injector",
                pressure_gradient_mode="KNOWN_NONZERO",
            )
        self.assertEqual(caught.exception.status, "OUTSIDE")

    def test_reverse_shear_direction_is_not_a_normal_result(self) -> None:
        with self.assertRaises(GasShearStop) as caught:
            evaluate_local_gas_film_shear(
                density_kg_m3=1.0,
                gas_velocity_m_s=10.0,
                film_surface_velocity_m_s=20.0,
                gas_viscosity_Pa_s=1.0e-5,
                mach=0.1,
                boundary_layer_development_length_m=1.0,
                boundary_layer_origin_source="BL origin",
                film_injection_coordinate_source="film injector",
                pressure_gradient_mode="ZERO_PRESSURE_GRADIENT_REFERENCE",
            )
        self.assertEqual(caught.exception.status, "OUTSIDE")

    def test_couette_connection_closes_mass_identity(self) -> None:
        result = evaluate_couette_from_gas_state(
            film_mass_flow_kg_s=0.02,
            radius_m=0.05,
            coverage_ratio=0.75,
            film_viscosity_Pa_s=2.0e-3,
            film_density_kg_m3=780.0,
            gas_density_kg_m3=1.0,
            gas_velocity_m_s=100.0,
            gas_viscosity_Pa_s=1.0e-5,
            mach=0.2,
            boundary_layer_development_length_m=0.1,
            boundary_layer_origin_source="BL origin",
            film_injection_coordinate_source="film injector",
            pressure_gradient_mode="ZERO_PRESSURE_GRADIENT_REFERENCE",
        )
        film = result["couette_film"]
        recovered = 780.0 * film["average_velocity_m_s"] * film["thickness_m"]
        self.assertAlmostEqual(recovered, film["line_mass_flow_kg_m_s"])
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertFalse(result["validated_engine_prediction"])

    def test_velocity_sensitivity_follows_u_to_1p8_within_domain(self) -> None:
        first = evaluate_local_gas_film_shear(
            density_kg_m3=1.0,
            gas_velocity_m_s=100.0,
            gas_viscosity_Pa_s=1.0e-5,
            mach=0.2,
            boundary_layer_development_length_m=0.1,
            boundary_layer_origin_source="BL origin",
            film_injection_coordinate_source="film injector",
            pressure_gradient_mode="ZERO_PRESSURE_GRADIENT_REFERENCE",
        )
        second = evaluate_local_gas_film_shear(
            density_kg_m3=1.0,
            gas_velocity_m_s=200.0,
            gas_viscosity_Pa_s=1.0e-5,
            mach=0.2,
            boundary_layer_development_length_m=0.1,
            boundary_layer_origin_source="BL origin",
            film_injection_coordinate_source="film injector",
            pressure_gradient_mode="ZERO_PRESSURE_GRADIENT_REFERENCE",
        )
        self.assertTrue(
            math.isclose(
                second.gas_shear_stress_Pa / first.gas_shear_stress_Pa,
                2.0**1.8,
                rel_tol=1.0e-12,
            )
        )


if __name__ == "__main__":
    unittest.main()
