from __future__ import annotations

import csv
import math
from pathlib import Path
import tempfile
import unittest

from cooling_model_b import (
    LAMINAR_MODEL_FULLY_DEVELOPED,
    LAMINAR_MODEL_THERMAL_ENTRY,
    evaluate_cooling_candidates,
    evaluate_model_b_hl,
    generate_channel_candidates,
)
from model_h_v1 import (
    MODEL_H_HELIX_ANGLES_DEG,
    ModelHError,
    evaluate_model_h_candidates,
    evaluate_model_h_hl,
    generate_model_h_candidates,
    generate_model_h_templates,
    helical_coolant_path_distances,
    helical_station_geometry,
    write_model_h_station_csv,
)


class ModelHV1Tests(unittest.TestCase):
    def setUp(self) -> None:
        self.x_m = [0.0, 0.012, 0.026]
        self.r_m = [0.034, 0.026, 0.044]
        self.station_ids = ["SYN-CHAMBER", "SYN-THROAT", "SYN-EXIT"]
        self.geometry = {
            "x_m": self.x_m,
            "mean_channel_radius_m": self.r_m,
            "throat_station_index": 1,
            "channel_width_limits_m": (0.0001, 0.050),
            "minimum_rib_thickness_m": 0.0005,
            "allowed_channel_heights_m": [0.002],
            "station_ids": self.station_ids,
            "width_candidates_mm": [1.2],
            "rib_candidates_mm": [1.0],
            "height_candidates_mm": [2.0],
        }
        self.evaluation = {
            **self.geometry,
            "mdot_total_kg_s": 0.04,
            "density_kg_m3": [800.0, 790.0, 780.0],
            "viscosity_Pa_s": [0.0012, 0.0011, 0.0010],
            "cp_J_kgK": [2300.0, 2320.0, 2350.0],
            "conductivity_W_mK": [0.15, 0.148, 0.145],
            "roughness_m": 2.0e-6,
            "thermal_boundary_condition": "SHAH_LONDON_H1",
            "fully_developed": True,
            "colebrook_re_range": (3000.0, 10_000_000.0),
            "colebrook_relative_roughness_range": (0.0, 0.1),
            "fluid_identity": "SYNTHETIC_TEST_FLUID_NOT_IPA",
            "property_data_classification": "SYNTHETIC_NON_DESIGN",
            "property_validity_status": "SYNTHETIC_DOMAIN",
        }

    def test_prescribed_template_and_initial_angle_counts(self) -> None:
        templates = generate_model_h_templates()
        self.assertEqual(len(templates), 168)
        generated = generate_model_h_candidates(
            x_m=self.x_m,
            mean_channel_radius_m=self.r_m,
            throat_station_index=1,
            channel_width_limits_m=(0.0001, 0.050),
            minimum_rib_thickness_m=0.0005,
            allowed_channel_heights_m=[0.0012, 0.0015, 0.002, 0.003, 0.004, 0.005],
            station_ids=self.station_ids,
        )
        self.assertEqual(generated["template_count"], 168)
        self.assertEqual(generated["helix_angle_count"], 4)
        self.assertEqual(generated["initial_template_angle_combination_count"], 672)

    def test_beta_zero_geometry_matches_model_b_exactly(self) -> None:
        base = generate_channel_candidates(**self.geometry)
        helical = generate_model_h_candidates(
            **self.geometry, helix_angles_deg=[0.0]
        )
        base_by_key = {
            (item["channel_count"], item["rib_thickness_m"], item["channel_height_m"]): item
            for item in base["candidates"]
        }
        self.assertEqual(len(base_by_key), len(helical["candidates"]))
        for candidate in helical["candidates"]:
            key = (
                candidate["channel_count"],
                candidate["rib_thickness_m"],
                candidate["channel_height_m"],
            )
            reference = base_by_key[key]
            self.assertEqual(candidate["valid"], reference["valid"])
            for actual, expected in zip(candidate["stations"], reference["stations"]):
                self.assertEqual(actual["channel_width_m"], expected["channel_width_m"])
                self.assertEqual(actual["total_flow_area_m2"], expected["flow_area_m2"])
                self.assertEqual(
                    actual["hydraulic_diameter_m"], expected["hydraulic_diameter_m"]
                )

    def test_beta_zero_path_matches_model_b_values(self) -> None:
        path = helical_coolant_path_distances(self.x_m, self.r_m, 0.0)
        self.assertEqual(path["s_H_m"], path["s_B_m"])
        self.assertEqual(path["segment_length_H_m"], path["segment_length_B_m"])
        self.assertEqual(path["path_length_factor"], 1.0)

    def test_single_station_beta_zero_interface_matches_model_b(self) -> None:
        model_b = evaluate_model_b_hl(
            mdot_total_kg_s=0.04,
            channel_count=60,
            width_m=2.0 * math.pi * 0.03 / 60 - 0.001,
            height_m=0.002,
            viscosity_Pa_s=0.0012,
            cp_J_kgK=2300.0,
            conductivity_W_mK=0.15,
            roughness_m=2.0e-6,
            thermal_boundary_condition="SHAH_LONDON_H1",
            fully_developed=True,
            colebrook_re_range=(3000.0, 10_000_000.0),
            colebrook_relative_roughness_range=(0.0, 0.1),
            laminar_model=LAMINAR_MODEL_FULLY_DEVELOPED,
        )
        model_h = evaluate_model_h_hl(
            mdot_total_kg_s=0.04,
            density_kg_m3=800.0,
            mean_channel_radius_m=0.03,
            channel_count=60,
            rib_thickness_m=0.001,
            channel_height_m=0.002,
            helix_angle_deg=0.0,
            viscosity_Pa_s=0.0012,
            cp_J_kgK=2300.0,
            conductivity_W_mK=0.15,
            roughness_m=2.0e-6,
            thermal_boundary_condition="SHAH_LONDON_H1",
            fully_developed=True,
            colebrook_re_range=(3000.0, 10_000_000.0),
            colebrook_relative_roughness_range=(0.0, 0.1),
            laminar_model=LAMINAR_MODEL_FULLY_DEVELOPED,
        )
        for key in ("Reynolds", "Prandtl", "Nusselt", "h_l_W_m2K"):
            self.assertEqual(model_h[key], model_b[key])
        self.assertEqual(model_h["model_b_closure_id"], model_b["model_id"])
        self.assertGreater(model_h["velocity_m_s"], 0.0)

    def test_helical_geometry_uses_normal_cross_section_once(self) -> None:
        geometry = helical_station_geometry(
            mean_channel_radius_m=0.03,
            channel_count=60,
            rib_thickness_m=0.001,
            channel_height_m=0.002,
            helix_angle_deg=30.0,
        )
        expected_width = 2.0 * math.pi * 0.03 * math.cos(math.radians(30.0)) / 60 - 0.001
        self.assertAlmostEqual(geometry["channel_width_m"], expected_width, places=15)
        self.assertAlmostEqual(
            geometry["total_flow_area_m2"], 60 * expected_width * 0.002, places=15
        )
        self.assertFalse(geometry["velocity_cosine_multiplier_applied"])

    def test_helical_path_scales_meridional_path_by_secant(self) -> None:
        path = helical_coolant_path_distances(self.x_m, self.r_m, 35.0)
        factor = 1.0 / math.cos(math.radians(35.0))
        self.assertAlmostEqual(path["path_length_factor"], factor, places=15)
        for straight, helical in zip(path["s_B_m"], path["s_H_m"]):
            self.assertAlmostEqual(helical, straight * factor, places=15)

    def test_nonpositive_helical_width_is_outside(self) -> None:
        with self.assertRaises(ModelHError) as caught:
            helical_station_geometry(
                mean_channel_radius_m=0.01,
                channel_count=200,
                rib_thickness_m=0.001,
                channel_height_m=0.001,
                helix_angle_deg=35.0,
            )
        self.assertEqual(caught.exception.status, "OUTSIDE")

    def test_beta_zero_full_model_matches_model_b(self) -> None:
        model_b = evaluate_cooling_candidates(
            **{key: value for key, value in self.evaluation.items() if key not in {
                "density_kg_m3",
                "fluid_identity",
                "property_data_classification",
                "property_validity_status",
            }},
            laminar_model=LAMINAR_MODEL_FULLY_DEVELOPED,
        )
        model_h = evaluate_model_h_candidates(
            **self.evaluation,
            helix_angles_deg=[0.0],
            laminar_model=LAMINAR_MODEL_FULLY_DEVELOPED,
        )
        base_by_id = {
            item["candidate_id"]: item for item in model_b["candidates"]
        }
        for candidate in model_h["candidates"]:
            reference = base_by_id[candidate["base_model_b_candidate_id"]]
            for actual, expected in zip(candidate["stations"], reference["stations"]):
                self.assertEqual(actual["status"], expected["status"])
                if actual["status"] == "MODEL_U":
                    for key in ("Reynolds", "Prandtl", "Nusselt", "h_l_W_m2K"):
                        self.assertEqual(actual[key], expected[key])

    def test_beta_zero_thermal_entry_model_matches_model_b(self) -> None:
        model_b = evaluate_cooling_candidates(
            **{key: value for key, value in self.evaluation.items() if key not in {
                "density_kg_m3",
                "fluid_identity",
                "property_data_classification",
                "property_validity_status",
            }},
            laminar_model=LAMINAR_MODEL_THERMAL_ENTRY,
        )
        model_h = evaluate_model_h_candidates(
            **self.evaluation,
            helix_angles_deg=[0.0],
            laminar_model=LAMINAR_MODEL_THERMAL_ENTRY,
        )
        base_by_id = {
            item["candidate_id"]: item for item in model_b["candidates"]
        }
        for candidate in model_h["candidates"]:
            reference = base_by_id[candidate["base_model_b_candidate_id"]]
            for actual, expected in zip(candidate["stations"], reference["stations"]):
                self.assertEqual(actual["status"], expected["status"])
                if actual["status"] == "MODEL_U":
                    self.assertEqual(actual["Nusselt"], expected["Nusselt"])
                    self.assertEqual(actual["h_l_W_m2K"], expected["h_l_W_m2K"])

    def test_all_angles_are_evaluated_without_dean_correction(self) -> None:
        result = evaluate_model_h_candidates(
            **self.evaluation,
            helix_angles_deg=MODEL_H_HELIX_ANGLES_DEG,
            laminar_model=LAMINAR_MODEL_FULLY_DEVELOPED,
        )
        base_ids = {}
        for candidate in result["candidates"]:
            base_ids.setdefault(candidate["base_model_b_candidate_id"], []).append(candidate)
        comparable = next(
            group
            for group in base_ids.values()
            if len(group) == 4 and all(item["valid"] for item in group)
        )
        self.assertEqual(
            {item["helix_angle_deg"] for item in comparable}, set(MODEL_H_HELIX_ANGLES_DEG)
        )
        for candidate in comparable:
            for station in candidate["stations"]:
                self.assertEqual(station["status"], "MODEL_U")
                self.assertGreater(station["h_l_W_m2K"], 0.0)
                self.assertFalse(station["dean_vortex_correction_applied"])
                self.assertIsNone(station["helical_heat_transfer_enhancement_factor"])

    def test_velocity_mass_conservation_uses_total_normal_area(self) -> None:
        result = evaluate_model_h_candidates(
            **self.evaluation,
            helix_angles_deg=[30.0],
            laminar_model=LAMINAR_MODEL_FULLY_DEVELOPED,
        )
        candidate = next(item for item in result["candidates"] if item["valid"])
        for index, station in enumerate(candidate["stations"]):
            expected = self.evaluation["mdot_total_kg_s"] / (
                self.evaluation["density_kg_m3"][index]
                * station["total_flow_area_m2"]
            )
            self.assertAlmostEqual(station["velocity_m_s"], expected, places=14)

    def test_station_csv_contains_plotting_fields(self) -> None:
        result = evaluate_model_h_candidates(
            **self.evaluation,
            helix_angles_deg=[0.0, 20.0],
            laminar_model=LAMINAR_MODEL_FULLY_DEVELOPED,
        )
        with tempfile.TemporaryDirectory(dir=Path.cwd()) as directory:
            path = Path(directory) / "model_h.csv"
            write_model_h_station_csv(path, result)
            with path.open(encoding="utf-8-sig", newline="") as handle:
                rows = list(csv.DictReader(handle))
        self.assertTrue(rows)
        for name in (
            "helix_angle_deg",
            "x_m",
            "channel_width_m",
            "hydraulic_diameter_m",
            "s_H_m",
            "Reynolds",
            "h_l_W_m2K",
        ):
            self.assertIn(name, rows[0])
        self.assertTrue(rows[0]["candidate_id"].startswith("HCH-"))
        self.assertTrue(rows[0]["base_model_b_candidate_id"].startswith("GCH-"))

    def test_property_adapter_status_stops_station_calculation(self) -> None:
        property_status = ["SYNTHETIC_DOMAIN", "OUTSIDE", "INPUT_U"]
        result = evaluate_model_h_candidates(
            **{
                **self.evaluation,
                "property_validity_status": property_status,
            },
            helix_angles_deg=[0.0],
            laminar_model=LAMINAR_MODEL_FULLY_DEVELOPED,
        )
        candidate = next(item for item in result["candidates"] if item["valid"])
        self.assertEqual(candidate["stations"][0]["status"], "MODEL_U")
        self.assertEqual(candidate["stations"][1]["status"], "OUTSIDE")
        self.assertEqual(candidate["stations"][2]["status"], "INPUT_U")
        self.assertIsNone(candidate["stations"][1]["h_l_W_m2K"])
        self.assertIsNone(candidate["stations"][2]["Reynolds"])

    def test_result_keeps_model_and_validation_limits_explicit(self) -> None:
        result = evaluate_model_h_candidates(
            **self.evaluation,
            helix_angles_deg=[0.0],
            laminar_model=LAMINAR_MODEL_FULLY_DEVELOPED,
        )
        self.assertFalse(result["experimental_validation"])
        self.assertFalse(result["validated_engine_prediction"])
        self.assertEqual(result["physical_validation_v01_v12"], "NOT_RUN")
        self.assertFalse(
            result["heat_transfer_area_interface"]["helical_area_multiplier_applied_to_h_l"]
        )
        self.assertTrue(any("Dean" in item for item in result["limitations"]))


if __name__ == "__main__":
    unittest.main()
