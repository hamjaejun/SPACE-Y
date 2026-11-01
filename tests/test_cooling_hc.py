from __future__ import annotations

import copy
import json
from pathlib import Path
import unittest

import jsonschema

from space_y.coolant import (
    CoolantStop,
    build_rectangular_channel_geometry,
    resolve_coolant_flow,
)
from space_y.cooling_hc import solve_cooling_hc, solve_cooling_hc_file


ROOT = Path(__file__).resolve().parents[1]


class CoolingHCTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request = json.loads(
            (ROOT / "examples/cooling_hc_rectangular_synthetic.json").read_text(
                encoding="utf-8"
            )
        )

    def test_rectangular_area_and_hydraulic_diameter(self) -> None:
        geometry = build_rectangular_channel_geometry(self.request["channel_geometry"])
        self.assertAlmostEqual(geometry.channel_flow_area_m2, 2.0e-6)
        self.assertAlmostEqual(geometry.total_flow_area_m2, 4.0e-5)
        self.assertAlmostEqual(geometry.channel_wetted_perimeter_m, 0.006)
        self.assertAlmostEqual(geometry.hydraulic_diameter_m, 4.0e-3 / 3.0)
        self.assertNotEqual(
            geometry.total_internal_wetted_area_m2,
            geometry.nominal_hot_wall_base_area_m2,
        )

    def test_equal_distribution_conserves_mass(self) -> None:
        flow = resolve_coolant_flow(
            self.request["flow_conditions"], channel_count=20
        )
        self.assertAlmostEqual(flow.channel_mass_flow_kg_s * 20, 0.3)
        self.assertEqual(flow.distribution_status, "ASSUMED_EQUAL_DISTRIBUTION")

    def test_beta_one_routes_all_fuel_through_regen(self) -> None:
        flow = resolve_coolant_flow(
            {
                "fuel_mass_flow_kg_s": 0.3,
                "regenerative_fraction_beta": 1.0,
            },
            channel_count=3,
        )
        self.assertAlmostEqual(flow.coolant_mass_flow_kg_s, 0.3)

    def test_upstream_and_downstream_branches_are_distinguished(self) -> None:
        flow = resolve_coolant_flow(
            {
                "coolant_mass_flow_kg_s": 0.4,
                "fuel_mass_flow_kg_s": 0.5,
                "regenerative_fraction_beta": 0.8,
                "upstream_diversion_mass_flow_kg_s": 0.1,
                "downstream_film_mass_flow_kg_s": 0.05,
            },
            channel_count=10,
        )
        self.assertAlmostEqual(flow.coolant_mass_flow_kg_s, 0.4)
        self.assertAlmostEqual(flow.post_regen_to_injector_mass_flow_kg_s, 0.35)
        self.assertEqual(flow.branch_accounting_status, "CHECKED")

    def test_downstream_film_is_not_double_subtracted(self) -> None:
        flow = resolve_coolant_flow(
            {
                "coolant_mass_flow_kg_s": 0.3,
                "downstream_film_mass_flow_kg_s": 0.03,
            },
            channel_count=20,
        )
        self.assertAlmostEqual(flow.channel_mass_flow_kg_s, 0.015)
        self.assertAlmostEqual(flow.post_regen_to_injector_mass_flow_kg_s, 0.27)
        self.assertEqual(flow.branch_accounting_status, "INPUT_U")

    def test_direct_and_beta_flows_must_be_consistent(self) -> None:
        with self.assertRaises(CoolantStop) as caught:
            resolve_coolant_flow(
                {
                    "coolant_mass_flow_kg_s": 0.2,
                    "fuel_mass_flow_kg_s": 0.3,
                    "regenerative_fraction_beta": 1.0,
                },
                channel_count=10,
            )
        self.assertEqual(caught.exception.status, "INPUT_U")

    def test_invalid_dimensions_and_count_are_rejected(self) -> None:
        for key, value in (("width_m", 0.0), ("height_m", -1.0), ("channel_count", 0)):
            spec = copy.deepcopy(self.request["channel_geometry"])
            spec[key] = value
            with self.subTest(key=key), self.assertRaises(CoolantStop):
                build_rectangular_channel_geometry(spec)

    def test_layout_overlap_is_outside(self) -> None:
        spec = copy.deepcopy(self.request["channel_geometry"])
        spec["channel_count"] = 1000
        with self.assertRaises(CoolantStop) as caught:
            build_rectangular_channel_geometry(spec)
        self.assertEqual(caught.exception.status, "OUTSIDE")

    def test_synthetic_fixture_runs_but_is_not_physical_validation(self) -> None:
        result = solve_cooling_hc(self.request)
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertGreater(result["results"]["Reynolds"], 3000.0)
        self.assertGreater(result["results"]["h_c_W_m2K"], 0.0)
        self.assertEqual(result["physical_validation_v01_v12"], "NOT_RUN")
        self.assertFalse(result["validated_engine_prediction"])
        self.assertFalse(result["safety_approval"])

    def test_flow_change_propagates_consistently(self) -> None:
        base = solve_cooling_hc(self.request)
        changed_request = copy.deepcopy(self.request)
        changed_request["flow_conditions"]["coolant_mass_flow_kg_s"] = 0.6
        changed_request["flow_conditions"]["fuel_mass_flow_kg_s"] = 0.6
        changed = solve_cooling_hc(changed_request)
        self.assertEqual(changed["numerical_status"], "PASS")
        self.assertAlmostEqual(
            changed["results"]["Reynolds"], 2.0 * base["results"]["Reynolds"]
        )
        self.assertGreater(
            changed["results"]["pressure_drop_Pa"], base["results"]["pressure_drop_Pa"]
        )

    def test_channel_count_change_propagates_through_total_flow_area(self) -> None:
        base = solve_cooling_hc(self.request)
        changed_request = copy.deepcopy(self.request)
        changed_request["channel_geometry"]["channel_count"] = 40
        changed = solve_cooling_hc(changed_request)
        self.assertEqual(changed["numerical_status"], "PASS")
        self.assertAlmostEqual(
            changed["inputs_consumed"]["channel_geometry"]["total_flow_area_m2"],
            2.0
            * base["inputs_consumed"]["channel_geometry"]["total_flow_area_m2"],
        )
        self.assertAlmostEqual(
            changed["results"]["Reynolds"], 0.5 * base["results"]["Reynolds"]
        )
        self.assertLess(
            changed["results"]["pressure_drop_Pa"], base["results"]["pressure_drop_Pa"]
        )

    def test_correlation_range_exit_is_outside(self) -> None:
        request = copy.deepcopy(self.request)
        request["model_conditions"]["heat_transfer_domain"]["re_range"] = [
            100000.0,
            200000.0,
        ]
        result = solve_cooling_hc(request)
        self.assertEqual(result["status"], "OUTSIDE")
        self.assertIsNone(result["results"])

    def test_phase_domain_exit_is_outside(self) -> None:
        request = copy.deepcopy(self.request)
        request["model_conditions"]["single_phase_domain"]["temperature_range_K"] = [
            200.0,
            250.0,
        ]
        result = solve_cooling_hc(request)
        self.assertEqual(result["status"], "OUTSIDE")

    def test_missing_correlation_thermal_boundary_is_input_u(self) -> None:
        request = copy.deepcopy(self.request)
        del request["model_conditions"]["heat_transfer_thermal_boundary_condition"]
        result = solve_cooling_hc(request)
        self.assertEqual(result["status"], "INPUT_U")

    def test_exact_ipa_identity_is_enforced(self) -> None:
        request = copy.deepcopy(self.request)
        request["fluid_state"]["canonical_name"] = "2-propanol"
        request["fluid_state"]["cas_number"] = "71-23-8"
        result = solve_cooling_hc(request)
        self.assertEqual(result["status"], "INPUT_U")

    def test_file_schema_and_cli_fixture(self) -> None:
        schema = json.loads(
            (ROOT / "schemas/SPACE_Y_cooling_hc.schema.json").read_text(encoding="utf-8")
        )
        jsonschema.Draft202012Validator(schema).validate(self.request)
        result = solve_cooling_hc_file(
            ROOT / "examples/cooling_hc_rectangular_synthetic.json",
            ROOT / "schemas/SPACE_Y_cooling_hc.schema.json",
        )
        self.assertEqual(result["numerical_status"], "PASS")

    def test_reference_presets_are_never_space_y_defaults(self) -> None:
        presets = json.loads(
            (ROOT / "config/SPACE_Y_cooling_channel_reference_presets.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            presets["classification"], "REFERENCE_PRESETS_NOT_SPACE_Y_DEFAULTS"
        )
        self.assertTrue(all(item["not_space_y_default"] for item in presets["presets"]))

    def test_actual_input_readiness_remains_unready(self) -> None:
        readiness = json.loads(
            (ROOT / "config/SPACE_Y_cooling_hc_input_readiness.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(readiness["overall_status"], "INPUT_U")
        self.assertTrue(
            all(item["status"] in {"INPUT_U", "MODEL_U"} for item in readiness["items"])
        )
        self.assertEqual(readiness["physical_validation_v01_v12"], "NOT_RUN")


if __name__ == "__main__":
    unittest.main()
