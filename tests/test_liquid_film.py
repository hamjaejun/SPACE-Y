from __future__ import annotations

import math
import unittest

from space_y.liquid_film import (
    FilmStop,
    assess_liquid_film_state,
    assess_regenerative_coolant_phase,
    audit_heat_transfer_area_bases,
    build_film_case_matrix,
    build_v1_comparison_matrix,
    core_oxidizer_fuel_ratio,
    couette_film_state,
    film_line_mass_flow_kg_m_s,
    film_off_baselines,
    film_v1_readiness_record,
    film_wall_conduction_reference_W_m2K,
    finite_volume_cell_centers_m,
    gas_side_segment_area_m2,
    gas_side_segment_areas_m2,
    heat_transfer_model_sensitivity_interfaces,
    march_film_energy_step,
    resolve_upstream_film_split,
    resolve_film_injection_location,
    reconcile_wet_dry_with_regenerative_heat_W,
    separate_thermal_and_propulsion_outputs,
    stechman_film_wall_heat_transfer_W_m2K,
    wet_dry_area_average_heat_flux_W_m2,
    wet_dry_heat_rate_W,
    partition_wet_dry_gas_area,
)


class LiquidFilmV1Tests(unittest.TestCase):
    def test_case_matrix_is_54_and_deterministic(self) -> None:
        first = build_film_case_matrix()
        second = build_film_case_matrix()
        self.assertEqual(first, second)
        self.assertEqual(len(first), 54)
        self.assertEqual(len({item["case_id"] for item in first}), 54)
        self.assertTrue(all(item["calculation_status"] == "INPUT_U" for item in first))
        self.assertTrue(all(not item["validated_engine_prediction"] for item in first))
        self.assertEqual(len(film_off_baselines()), 3)

    def test_upstream_split_conserves_mass_and_channel_distribution(self) -> None:
        split = resolve_upstream_film_split(
            total_fuel_mass_flow_kg_s=2.0, film_fraction=0.15, channel_count=10
        )
        self.assertAlmostEqual(split.film_mass_flow_kg_s, 0.3)
        self.assertAlmostEqual(split.regenerative_mass_flow_kg_s, 1.7)
        self.assertAlmostEqual(split.channel_mass_flow_kg_s, 0.17)
        self.assertAlmostEqual(split.mass_residual_kg_s, 0.0)
        self.assertEqual(split.branch_accounting_status, "CHECKED")

    def test_film_off_preserves_full_regenerative_flow(self) -> None:
        split = resolve_upstream_film_split(
            total_fuel_mass_flow_kg_s=2.0, film_fraction=0.0, channel_count=4
        )
        self.assertEqual(split.film_mass_flow_kg_s, 0.0)
        self.assertEqual(split.regenerative_mass_flow_kg_s, 2.0)
        self.assertEqual(split.channel_mass_flow_kg_s, 0.5)

    def test_core_of_uses_only_regenerative_core_fuel(self) -> None:
        split = resolve_upstream_film_split(
            total_fuel_mass_flow_kg_s=2.0, film_fraction=0.10, channel_count=2
        )
        self.assertAlmostEqual(
            core_oxidizer_fuel_ratio(oxidizer_mass_flow_kg_s=3.6, split=split), 2.0
        )

    def test_covered_circumference_does_not_duplicate_film_mass(self) -> None:
        gamma = film_line_mass_flow_kg_m_s(
            film_mass_flow_kg_s=0.2, radius_m=0.05, coverage_ratio=0.5
        )
        recovered = gamma * 2.0 * math.pi * 0.05 * 0.5
        self.assertAlmostEqual(recovered, 0.2)

    def test_couette_state_closes_mass_identity(self) -> None:
        state = couette_film_state(
            film_mass_flow_kg_s=0.02,
            radius_m=0.05,
            coverage_ratio=0.75,
            viscosity_Pa_s=2.0e-3,
            density_kg_m3=780.0,
            gas_shear_stress_Pa=120.0,
        )
        self.assertGreater(state.thickness_m, 0.0)
        self.assertAlmostEqual(state.surface_velocity_m_s, 2.0 * state.average_velocity_m_s)
        self.assertAlmostEqual(
            780.0 * state.average_velocity_m_s * state.thickness_m,
            state.line_mass_flow_kg_m_s,
        )
        self.assertFalse(state.experimental_validation)

    def test_couette_rejects_missing_or_nonpositive_shear(self) -> None:
        with self.assertRaises(FilmStop) as caught:
            couette_film_state(
                film_mass_flow_kg_s=0.02,
                radius_m=0.05,
                coverage_ratio=1.0,
                viscosity_Pa_s=2.0e-3,
                density_kg_m3=780.0,
                gas_shear_stress_Pa=0.0,
            )
        self.assertEqual(caught.exception.status, "INPUT_U")

    def test_finite_volume_centers_never_evaluate_at_injection_singularity(self) -> None:
        centers = finite_volume_cell_centers_m([0.01, 0.02, 0.03])
        self.assertEqual(centers, [0.005, 0.02, 0.045])
        refined = finite_volume_cell_centers_m([0.005] * 12)
        self.assertGreater(refined[0], 0.0)
        self.assertAlmostEqual(refined[-1], 0.0575)

    def test_conduction_reference_is_not_mislabeled_as_stechman(self) -> None:
        result = film_wall_conduction_reference_W_m2K(
            conductivity_W_mK=0.12, thickness_m=2.0e-4
        )
        self.assertAlmostEqual(result["h_film_wall_W_m2K"], 600.0)
        self.assertEqual(result["status"], "MODEL_U")
        self.assertFalse(result["stechman_equivalent"])

    def test_stechman_is_blocked_without_original_unit_basis(self) -> None:
        with self.assertRaises(FilmStop) as caught:
            stechman_film_wall_heat_transfer_W_m2K(cp=2500.0, zeta=0.8)
        self.assertEqual(caught.exception.status, "MODEL_U")
        self.assertIn("unit basis", str(caught.exception))

    def test_wet_dry_area_weighting_is_exact(self) -> None:
        average = wet_dry_area_average_heat_flux_W_m2(
            coverage_ratio=0.75,
            wet_heat_flux_W_m2=100.0,
            dry_heat_flux_W_m2=500.0,
        )
        self.assertEqual(average, 200.0)
        self.assertEqual(
            wet_dry_heat_rate_W(
                total_area_m2=2.0,
                coverage_ratio=0.75,
                wet_heat_flux_W_m2=100.0,
                dry_heat_flux_W_m2=500.0,
            ),
            400.0,
        )

    def test_film_energy_step_conserves_energy(self) -> None:
        step = march_film_energy_step(
            inlet_temperature_K=300.0,
            film_mass_flow_kg_s=0.1,
            cp_J_kgK=2500.0,
            radius_m=0.05,
            coverage_ratio=0.5,
            segment_length_m=0.02,
            gas_to_film_heat_flux_W_m2=1.0e6,
            film_to_wall_heat_flux_W_m2=0.4e6,
        )
        self.assertAlmostEqual(step.energy_residual_W, 0.0, places=9)
        self.assertGreater(step.outlet_temperature_K, step.inlet_temperature_K)

    def test_phase_gate_distinguishes_missing_outside_and_saturation(self) -> None:
        self.assertEqual(
            assess_liquid_film_state(
                temperature_K=300.0,
                pressure_Pa_abs=None,
                property_temperature_range_K=(280.0, 400.0),
                saturation_temperature_K=350.0,
            )["status"],
            "INPUT_U",
        )
        self.assertEqual(
            assess_liquid_film_state(
                temperature_K=410.0,
                pressure_Pa_abs=2.0e6,
                property_temperature_range_K=(280.0, 400.0),
                saturation_temperature_K=450.0,
            )["status"],
            "OUTSIDE",
        )
        self.assertEqual(
            assess_liquid_film_state(
                temperature_K=360.0,
                pressure_Pa_abs=2.0e6,
                property_temperature_range_K=(280.0, 400.0),
                saturation_temperature_K=350.0,
            )["status"],
            "OUT_OF_MODEL_SCOPE",
        )

    def test_readiness_never_claims_engine_validation(self) -> None:
        record = film_v1_readiness_record()
        self.assertEqual(record["status"], "INPUT_U")
        self.assertEqual(record["physical_validation_v01_v12"], "NOT_RUN")
        self.assertFalse(record["validated_engine_prediction"])
        self.assertIn("54-case engine execution and spatial-refinement evidence", record["blocked"])

    def test_head_and_direct_wall_film_injection_are_distinct_fields(self) -> None:
        location = resolve_film_injection_location(
            x_head_m=0.0, head_coordinate_source="engine geometry head station"
        )
        self.assertEqual(location.x_head_m, location.x_film_injection_m)
        self.assertEqual(location.injection_mode, "DIRECT_LIQUID_IPA_FILM_ON_CHAMBER_WALL")
        self.assertEqual(location.upstream_boundary_mode, "EXISTING_DRY_WALL_MODEL")
        self.assertNotEqual(
            location.head_coordinate_source, location.film_injection_coordinate_source
        )

    def test_explicit_film_injection_coordinate_requires_source(self) -> None:
        with self.assertRaises(FilmStop) as caught:
            resolve_film_injection_location(
                x_head_m=0.0,
                head_coordinate_source="head",
                x_film_injection_m=0.01,
            )
        self.assertEqual(caught.exception.status, "INPUT_U")

    def test_curved_gas_side_area_uses_meridional_distance(self) -> None:
        area = gas_side_segment_area_m2(
            x_left_m=0.0,
            radius_left_m=0.04,
            x_right_m=0.03,
            radius_right_m=0.08,
        )
        expected = math.pi * (0.04 + 0.08) * math.hypot(0.03, 0.04)
        axial_only = math.pi * (0.04 + 0.08) * 0.03
        self.assertAlmostEqual(area, expected)
        self.assertGreater(area, axial_only)
        self.assertEqual(
            gas_side_segment_areas_m2([0.0, 0.03], [0.04, 0.08]), [area]
        )

    def test_wet_dry_area_partition_conserves_area_for_all_coverages(self) -> None:
        for coverage in (1.0, 0.75, 0.5):
            with self.subTest(coverage=coverage):
                partition = partition_wet_dry_gas_area(
                    total_gas_side_area_m2=2.0, coverage_ratio=coverage
                )
                self.assertAlmostEqual(partition.area_residual_m2, 0.0)
                self.assertAlmostEqual(
                    partition.wet_gas_side_area_m2 + partition.dry_gas_side_area_m2,
                    2.0,
                )
        full = partition_wet_dry_gas_area(
            total_gas_side_area_m2=2.0, coverage_ratio=1.0
        )
        self.assertEqual(full.dry_gas_side_area_m2, 0.0)

    def test_gas_and_regenerative_area_bases_remain_distinct(self) -> None:
        result = audit_heat_transfer_area_bases(
            gas_side_area_m2=1.0,
            regenerative_coolant_area_m2=1.4,
            regenerative_geometry_mode="VARIABLE_HELICAL",
        )
        self.assertTrue(result["areas_are_distinct_bases"])
        self.assertFalse(result["equality_required"])

    def test_wet_dry_heat_rate_reconciles_with_regenerative_heat(self) -> None:
        audit = reconcile_wet_dry_with_regenerative_heat_W(
            gas_side_area_m2=2.0,
            coverage_ratio=0.75,
            wet_heat_flux_W_m2=100.0,
            dry_heat_flux_W_m2=500.0,
            heat_rate_into_regenerative_coolant_W=400.0,
        )
        self.assertAlmostEqual(audit["energy_residual_W"], 0.0)
        self.assertFalse(audit["three_dimensional_peak_wall_temperature_validated"])

    def test_regenerative_phase_separates_bulk_and_wall_boiling(self) -> None:
        unknown_wall = assess_regenerative_coolant_phase(
            bulk_temperature_K=330.0,
            local_pressure_Pa_abs=2.0e6,
            coolant_side_wall_temperature_K=390.0,
            saturation_temperature_K=400.0,
        )
        self.assertEqual(unknown_wall["bulk_phase_status"], "MODEL_U")
        self.assertEqual(unknown_wall["wall_boiling_status"], "INPUT_U")
        self.assertEqual(unknown_wall["status"], "INPUT_U")
        failed_wall = assess_regenerative_coolant_phase(
            bulk_temperature_K=330.0,
            local_pressure_Pa_abs=2.0e6,
            coolant_side_wall_temperature_K=390.0,
            saturation_temperature_K=400.0,
            wall_onset_boiling_temperature_K=380.0,
        )
        self.assertEqual(failed_wall["bulk_phase_status"], "MODEL_U")
        self.assertEqual(failed_wall["wall_boiling_status"], "OUT_OF_MODEL_SCOPE")

    def test_film_surface_phase_is_unknown_without_surface_temperature(self) -> None:
        result = assess_liquid_film_state(
            temperature_K=330.0,
            pressure_Pa_abs=2.0e6,
            property_temperature_range_K=(280.0, 400.0),
            saturation_temperature_K=400.0,
        )
        self.assertEqual(result["film_bulk_status"], "MODEL_U")
        self.assertEqual(result["film_surface_phase_status"], "INPUT_U")

    def test_comparison_matrix_separates_all_three_scenarios(self) -> None:
        cases = build_v1_comparison_matrix()
        self.assertEqual(len(cases), 3 + 18 + 54)
        modes = {item["film_mode"] for item in cases}
        self.assertEqual(
            modes, {"OFF", "SPLIT_WITHOUT_FILM_PROTECTION", "ON"}
        )
        split = [
            item for item in cases
            if item["film_mode"] == "SPLIT_WITHOUT_FILM_PROTECTION"
        ]
        self.assertTrue(all(item["gas_side_boundary"] == "EXISTING_DRY_WALL" for item in split))

    def test_sensitivity_interfaces_have_no_calibration_multiplier(self) -> None:
        models = heat_transfer_model_sensitivity_interfaces()
        self.assertEqual(len(models), 2)
        self.assertTrue(all(item["calibration_multiplier"] is None for item in models))

    def test_thermal_and_propulsion_outputs_are_not_ranked_together(self) -> None:
        result = separate_thermal_and_propulsion_outputs(
            thermal_performance={"maximum_wall_temperature_K": 500.0},
            propulsion_performance=None,
        )
        self.assertEqual(result["propulsion_performance"]["status"], "INPUT_U")
        self.assertFalse(result["automatic_optimum_selection_performed"])


if __name__ == "__main__":
    unittest.main()
