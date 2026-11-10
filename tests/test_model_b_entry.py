from __future__ import annotations

import math
import unittest

import cooling_model_b as model


class ModelBEntryTests(unittest.TestCase):
    def evaluate_at_re(
        self,
        reynolds: float,
        *,
        laminar_model: str = model.LAMINAR_MODEL_THERMAL_ENTRY,
        s_m: float | None = 0.005,
        first_segment_length_m: float | None = None,
        boundary: str = "SHAH_LONDON_H1",
    ) -> dict:
        width = 0.001
        height = 0.002
        viscosity = 0.001
        section = model.rectangular_channel_geometry(width, height, 1)
        mass_flow = (
            reynolds
            * float(section["total_flow_area_m2"])
            * viscosity
            / float(section["hydraulic_diameter_m"])
        )
        return model.evaluate_model_b_hl(
            mdot_total_kg_s=mass_flow,
            channel_count=1,
            width_m=width,
            height_m=height,
            viscosity_Pa_s=viscosity,
            cp_J_kgK=2000.0,
            conductivity_W_mK=0.2,
            roughness_m=1.0e-6,
            thermal_boundary_condition=boundary,
            fully_developed=True,
            colebrook_re_range=(3000.0, 5_000_000.0),
            colebrook_relative_roughness_range=(0.0, 0.05),
            laminar_model=laminar_model,
            s_m=s_m,
            first_segment_length_m=first_segment_length_m,
        )

    def test_coolant_path_distance_runs_from_exit_to_chamber(self) -> None:
        path = model.coolant_path_distances(
            [0.0, 0.03, 0.08], [0.03, 0.025, 0.04]
        )
        ds0 = math.hypot(0.03, -0.005)
        ds1 = math.hypot(0.05, 0.015)
        self.assertAlmostEqual(path["s_m"][0], ds0 + ds1)
        self.assertAlmostEqual(path["s_m"][1], ds1)
        self.assertEqual(path["s_m"][2], 0.0)
        self.assertAlmostEqual(path["entry_segment_length_m"][2], ds1)

    def test_paper_equation_reference_values(self) -> None:
        local = model.lee_garimella_h1_thermal_entry_nusselt(
            reynolds=1000.0,
            prandtl=10.0,
            hydraulic_diameter_m=0.001,
            aspect_ratio_min_over_max=0.5,
            s_m=0.001,
        )
        average = model.lee_garimella_h1_thermal_entry_nusselt(
            reynolds=1000.0,
            prandtl=10.0,
            hydraulic_diameter_m=0.001,
            aspect_ratio_min_over_max=0.5,
            s_m=0.0,
            first_segment_length_m=0.001,
        )
        self.assertAlmostEqual(local["Nusselt"], 29.17989340127464)
        self.assertAlmostEqual(average["Nusselt"], 34.671609039359495)
        self.assertEqual(average["evaluation"], "FIRST_FINITE_SEGMENT_AVERAGE_EQ_13")

    def test_entry_nusselt_approaches_fully_developed_value(self) -> None:
        result = model.lee_garimella_h1_thermal_entry_nusselt(
            reynolds=1000.0,
            prandtl=10.0,
            hydraulic_diameter_m=0.001,
            aspect_ratio_min_over_max=0.5,
            s_m=1.0,
        )
        self.assertEqual(
            result["evaluation"], "FULLY_DEVELOPED_BEYOND_EQ_11_ENTRY_LENGTH"
        )
        self.assertAlmostEqual(result["Nusselt"], result["Nusselt_fully_developed_H1"])

    def test_first_segment_average_remains_finite_under_refinement(self) -> None:
        values = []
        for station_count in (100, 200, 400):
            length = 0.1 / (station_count - 1)
            result = self.evaluate_at_re(
                1000.0, s_m=0.0, first_segment_length_m=length
            )
            values.append(result["Nusselt"])
        self.assertTrue(all(math.isfinite(value) and value > 0.0 for value in values))
        self.assertTrue(values[0] < values[1] < values[2] < 100.0)
        self.assertLess(values[2] - values[1], values[1] - values[0])

    def test_transition_boundaries_are_continuous(self) -> None:
        reynolds_values = (2278.0, 2299.9, 2300.0, 2300.1, 2500.0, 2999.9, 3000.0)
        rows = {value: self.evaluate_at_re(value) for value in reynolds_values}
        self.assertEqual(rows[2300.0]["flow_regime"], "TRANSITION_MODEL_B")
        self.assertEqual(rows[3000.0]["flow_regime"], "TURBULENT")
        self.assertAlmostEqual(
            rows[2300.0]["Nusselt"], rows[2300.0]["Nusselt_thermal_entry"]
        )
        self.assertLess(
            abs(rows[2300.1]["Nusselt"] - rows[2300.0]["Nusselt"]), 0.1
        )
        self.assertLess(
            abs(rows[2999.9]["Nusselt"] - rows[3000.0]["Nusselt"]), 0.1
        )

    def test_fully_developed_option_preserves_legacy_result(self) -> None:
        legacy = self.evaluate_at_re(
            2500.0,
            laminar_model=model.LAMINAR_MODEL_FULLY_DEVELOPED,
            s_m=None,
        )
        explicit = self.evaluate_at_re(
            2500.0,
            laminar_model=model.LAMINAR_MODEL_FULLY_DEVELOPED,
            s_m=0.005,
        )
        self.assertEqual(legacy["Nusselt"], explicit["Nusselt"])
        self.assertEqual(legacy["h_l_W_m2K"], explicit["h_l_W_m2K"])

    def test_entry_model_does_not_extrapolate_boundary_or_aspect_ratio(self) -> None:
        with self.assertRaises(model.CoolingModelBError) as boundary_error:
            self.evaluate_at_re(1000.0, boundary="CONSTANT_WALL_TEMPERATURE")
        self.assertEqual(boundary_error.exception.status, "OUTSIDE")
        with self.assertRaises(model.CoolingModelBError) as aspect_error:
            model.lee_garimella_h1_thermal_entry_nusselt(
                reynolds=1000.0,
                prandtl=10.0,
                hydraulic_diameter_m=0.001,
                aspect_ratio_min_over_max=0.05,
                s_m=0.001,
            )
        self.assertEqual(aspect_error.exception.status, "OUTSIDE")

    def test_gas_side_radius_adapter_uses_candidate_centerline(self) -> None:
        common = dict(
            x_m=[0.0, 0.1],
            throat_station_index=0,
            channel_width_limits_m=(0.0001, 0.02),
            minimum_rib_thickness_m=0.0005,
            allowed_channel_heights_m=[0.002],
            width_candidates_mm=[1.2],
            rib_candidates_mm=[1.0],
            height_candidates_mm=[2.0],
        )
        from_gas = model.generate_channel_candidates(
            **common,
            gas_side_radius_m=[0.02, 0.03],
            wall_thickness_m=0.001,
        )
        explicit = model.generate_channel_candidates(
            **common,
            mean_channel_radius_m=[0.022, 0.032],
        )
        self.assertEqual(from_gas["unique_candidate_count"], explicit["unique_candidate_count"])
        for left, right in zip(from_gas["candidates"], explicit["candidates"]):
            self.assertEqual(left["channel_count"], right["channel_count"])
            for left_station, right_station in zip(left["stations"], right["stations"]):
                self.assertAlmostEqual(
                    left_station["channel_width_m"], right_station["channel_width_m"]
                )


if __name__ == "__main__":
    unittest.main()
