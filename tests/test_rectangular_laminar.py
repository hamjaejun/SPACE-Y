from __future__ import annotations

import copy
from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from space_y.coolant import (
    CoolantStop,
    TRANSITION_HEAT_TRANSFER_MODEL_A,
    TRANSITION_HEAT_TRANSFER_MODEL_B,
    build_friction_domain,
    build_heat_transfer_domain,
    evaluate_local_coolant_heat_transfer,
    evaluate_rectangular_cooling_hl,
    evaluate_rectangular_laminar_heat_transfer,
)
from space_y.cooling_channel_candidates import (
    solve_cooling_channel_candidates,
    write_candidate_results_csv,
)
from space_y.cooling_hc import solve_cooling_hc
from space_y.physics import (
    rectangular_laminar_darcy_friction_factor,
    rectangular_laminar_nusselt,
)


ROOT = Path(__file__).resolve().parents[1]


class RectangularLaminarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.heat_domain = build_heat_transfer_domain(
            {
                "source": "SYNTHETIC_TURBULENT_REGRESSION_DOMAIN",
                "re_range": [3000.0, 5_000_000.0],
                "pr_range": [0.5, 100.0],
                "geometry": "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
                "fully_developed_required": True,
            }
        )
        cls.friction_domain = build_friction_domain(
            {
                "source": "SYNTHETIC_TURBULENT_REGRESSION_DOMAIN",
                "re_range": [3000.0, 5_000_000.0],
                "relative_roughness_range": [0.0, 0.05],
                "geometry": "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
            }
        )

    def _evaluate_at_re(
        self,
        reynolds: float,
        *,
        fully_developed: bool | None = True,
        laminar_boundary: str | None = "CONSTANT_WALL_TEMPERATURE",
        transition_model: str = TRANSITION_HEAT_TRANSFER_MODEL_B,
    ):
        # For w=h=0.01 m, N=1, mu=0.001 Pa s: Re=100000*m_dot.
        return evaluate_rectangular_cooling_hl(
            coolant_mass_flow_kg_s=reynolds / 100_000.0,
            width_m=0.01,
            height_m=0.01,
            channel_count=1,
            viscosity_Pa_s=0.001,
            cp_J_kgK=2000.0,
            conductivity_W_mK=0.15,
            equivalent_sand_roughness_m=0.0,
            closure_geometry=(
                "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER"
            ),
            fully_developed=fully_developed,
            thermal_boundary_condition="SYNTHETIC_UNMAPPED_BOUNDARY",
            heat_transfer_domain=self.heat_domain,
            heat_transfer_thermal_boundary_condition=(
                "SYNTHETIC_UNMAPPED_BOUNDARY"
            ),
            friction_domain=self.friction_domain,
            density_kg_m3=800.0,
            laminar_thermal_boundary_condition=laminar_boundary,
            transition_heat_transfer_model=transition_model,
        )

    def test_square_channel_reference_values(self) -> None:
        nu_t, nu_h1 = rectangular_laminar_nusselt(1.0)
        factor = rectangular_laminar_darcy_friction_factor(1000.0, 1.0)
        self.assertAlmostEqual(nu_t, 2.978695, places=6)
        self.assertAlmostEqual(nu_h1, 3.610224, places=6)
        self.assertAlmostEqual(factor * 1000.0, 56.9184, places=6)

    def test_parallel_plate_limit(self) -> None:
        alpha = 1.0e-9
        nu_t, nu_h1 = rectangular_laminar_nusselt(alpha)
        factor = rectangular_laminar_darcy_friction_factor(1000.0, alpha)
        self.assertAlmostEqual(nu_t, 7.541, places=7)
        self.assertAlmostEqual(nu_h1, 8.235, places=7)
        self.assertAlmostEqual(factor * 1000.0, 96.0, places=6)

    def test_reynolds_dispatch_boundaries(self) -> None:
        for reynolds in (1000.0, 2299.0):
            with self.subTest(reynolds=reynolds):
                result = self._evaluate_at_re(reynolds)
                self.assertEqual(
                    result.applicability_assessment["flow_regime"], "LAMINAR"
                )
                self.assertEqual(
                    result.heat_transfer_applicability["correlation_id"],
                    "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_T",
                )
        for reynolds in (2300.0, 2500.0, 2999.0):
            with self.subTest(reynolds=reynolds):
                result = self._evaluate_at_re(reynolds)
                self.assertEqual(
                    result.applicability_assessment["flow_regime"],
                    "TRANSITION_MODEL_B",
                )
                self.assertGreater(result.Nusselt, 0.0)
                self.assertGreater(result.h_l_W_m2K, 0.0)
                self.assertIsNone(result.darcy_factor)
                self.assertIsNone(result.friction_pressure_gradient_Pa_m)
        result = self._evaluate_at_re(3000.0)
        self.assertEqual(result.Reynolds, 3000.0)
        self.assertEqual(
            result.heat_transfer_applicability["status"],
            "APPLICABLE_TO_SUPPLIED_DOMAIN",
        )

    def test_model_a_preserves_unresolved_transition_interval(self) -> None:
        for reynolds in (2300.0, 2500.0, 2999.0):
            with self.subTest(reynolds=reynolds):
                with self.assertRaises(CoolantStop) as raised:
                    self._evaluate_at_re(
                        reynolds,
                        transition_model=TRANSITION_HEAT_TRANSFER_MODEL_A,
                    )
                self.assertEqual(raised.exception.status, "OUTSIDE")
                self.assertEqual(
                    raised.exception.details["flow_regime"],
                    "TRANSITION_UNRESOLVED",
                )
                self.assertIsNone(raised.exception.details["h_l_W_m2K"])

    def test_laminar_branch_does_not_call_colebrook(self) -> None:
        with patch(
            "space_y.coolant.colebrook_darcy_friction_factor",
            side_effect=AssertionError("Colebrook must not run in laminar branch"),
        ):
            result = self._evaluate_at_re(1000.0)
        self.assertAlmostEqual(result.darcy_factor * result.Reynolds, 56.9184)

    def test_boundary_unselected_retains_both_references_without_h_l(self) -> None:
        with self.assertRaises(CoolantStop) as raised:
            self._evaluate_at_re(1000.0, laminar_boundary=None)
        self.assertEqual(raised.exception.status, "INPUT_U")
        details = raised.exception.details
        self.assertIsNone(details["selected_h_l_W_m2K"])
        self.assertIsNone(details["h_l_W_m2K"])
        self.assertGreater(details["h_l_constant_wall_temperature_W_m2K"], 0.0)
        self.assertGreater(details["h_l_H1_W_m2K"], 0.0)

    def test_fully_developed_state_controls_approval(self) -> None:
        with self.assertRaises(CoolantStop) as raised:
            self._evaluate_at_re(1000.0, fully_developed=False)
        self.assertEqual(raised.exception.status, "OUTSIDE")
        self.assertEqual(
            raised.exception.details["applicability_status"],
            "OUTSIDE_NOT_FULLY_DEVELOPED",
        )

        reference = evaluate_rectangular_laminar_heat_transfer(
            mass_flow_kg_s=0.01,
            flow_area_m2=1.0e-4,
            hydraulic_diameter_m=0.01,
            width_m=0.01,
            height_m=0.01,
            viscosity_Pa_s=0.001,
            cp_J_kgK=2000.0,
            conductivity_W_mK=0.15,
            density_kg_m3=800.0,
            fully_developed=None,
            laminar_thermal_boundary_condition="SHAH_LONDON_H1",
        )
        self.assertEqual(
            reference.applicability_status,
            "REFERENCE_ONLY_FULLY_DEVELOPED_UNCONFIRMED",
        )
        self.assertGreater(reference.selected_h_l_W_m2K, 0.0)

        with self.assertRaises(CoolantStop) as integrated:
            self._evaluate_at_re(1000.0, fully_developed=None)
        self.assertEqual(integrated.exception.status, "INPUT_U")
        self.assertEqual(
            integrated.exception.details["applicability_status"],
            "REFERENCE_ONLY_FULLY_DEVELOPED_UNCONFIRMED",
        )
        self.assertGreater(
            integrated.exception.details["h_l_H1_W_m2K"], 0.0
        )

    def test_laminar_pressure_gradient_uses_darcy_factor(self) -> None:
        result = self._evaluate_at_re(1000.0)
        expected = (
            result.darcy_factor / 0.01 * 800.0 * result.velocity_m_s**2 / 2.0
        )
        self.assertAlmostEqual(result.friction_pressure_gradient_Pa_m, expected)
        self.assertEqual(
            result.friction_applicability["darcy_factor_definition"],
            "DARCY_WEISBACH_NOT_FANNING",
        )

    def test_re_3000_matches_existing_turbulent_core(self) -> None:
        dispatched = self._evaluate_at_re(3000.0)
        direct = evaluate_local_coolant_heat_transfer(
            mass_flow_kg_s=0.03,
            flow_area_m2=1.0e-4,
            hydraulic_diameter_m=0.01,
            viscosity_Pa_s=0.001,
            cp_J_kgK=2000.0,
            conductivity_W_mK=0.15,
            equivalent_sand_roughness_m=0.0,
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
        )
        self.assertEqual(
            dispatched,
            replace(direct, channel_aspect_ratio_h_over_w=1.0),
        )

    def test_low_flow_candidate_serializes_to_json_and_csv(self) -> None:
        request = json.loads(
            (ROOT / "examples/cooling_channel_sweep_synthetic_axial.json").read_text(
                encoding="utf-8"
            )
        )
        request = copy.deepcopy(request)
        request["run_id"] = "SYNTHETIC-LAMINAR-SERIALIZATION-TEST"
        request["coolant_conditions"]["coolant_mass_flow_kg_s"] = 0.003
        request["model_conditions"]["laminar_thermal_boundary_condition"] = (
            "CONSTANT_WALL_TEMPERATURE"
        )
        request["model_conditions"]["applicability_policy"] = json.loads(
            (ROOT / "config/SPACE_Y_cooling_correlation_policy.json").read_text(
                encoding="utf-8"
            )
        )
        result = solve_cooling_channel_candidates(request)
        self.assertEqual(result["status"], "MODEL_U")
        json.dumps(result)
        successful = [
            station["calculation"]
            for candidate in result["candidates"]
            for station in candidate["stations"]
            if station.get("calculation", {}).get("status") == "MODEL_U"
        ]
        self.assertTrue(successful)
        self.assertTrue(
            all(
                item["results"]["applicability_assessment"]["flow_regime"]
                == "LAMINAR"
                for item in successful
            )
        )
        complete_candidate = next(
            candidate
            for candidate in result["candidates"]
            if candidate["summary"]["valid_calculation_station_count"] == 3
        )
        self.assertEqual(
            complete_candidate["summary"]["applicability_assessment"][
                "correlation_domain_status"
            ],
            "ALL_DISCRETE_STATIONS_WITHIN_SELECTED_CORRELATION_DOMAINS",
        )
        self.assertIn(
            "BELOW_RE_DESIGN_RECOMMENDATION",
            complete_candidate["recommendation_tags"],
        )
        self.assertNotIn("EXCLUDED_CORRELATION", complete_candidate["recommendation_tags"])
        with tempfile.TemporaryDirectory(dir=ROOT) as directory:
            output = Path(directory) / "laminar.csv"
            write_candidate_results_csv(output, result)
            csv_text = output.read_text(encoding="utf-8")
            self.assertIn("WITHIN_RECTANGULAR_LAMINAR_MODEL_DOMAIN", csv_text)
            self.assertIn("MODEL_U", csv_text)

    def test_single_segment_interface_returns_laminar_closure(self) -> None:
        request = json.loads(
            (ROOT / "examples/cooling_hc_rectangular_synthetic.json").read_text(
                encoding="utf-8"
            )
        )
        request["run_id"] = "SYNTHETIC-LAMINAR-SINGLE-SEGMENT-TEST"
        flow = request["flow_conditions"]
        flow["coolant_mass_flow_kg_s"] = 0.03
        flow["fuel_mass_flow_kg_s"] = 0.03
        flow["downstream_film_mass_flow_kg_s"] = 0.003
        request["model_conditions"]["laminar_thermal_boundary_condition"] = (
            "SHAH_LONDON_H1"
        )
        result = solve_cooling_hc(request)
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(
            result["applicability_status"],
            "MODEL_U_FULLY_DEVELOPED_LAMINAR_REFERENCE",
        )
        self.assertAlmostEqual(result["results"]["Reynolds"], 1000.0)
        self.assertEqual(
            result["results"]["heat_transfer_applicability"]["correlation_id"],
            "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_H1",
        )
        self.assertGreater(result["results"]["pressure_drop_Pa"], 0.0)
        self.assertFalse(result["validated_engine_prediction"])


if __name__ == "__main__":
    unittest.main()
