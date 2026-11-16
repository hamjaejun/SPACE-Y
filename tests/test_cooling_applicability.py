from __future__ import annotations

import json
import math
from pathlib import Path
import unittest

import jsonschema

from space_y.coolant import (
    build_friction_domain,
    build_heat_transfer_domain,
    evaluate_local_coolant_heat_transfer,
    evaluate_roughness_friction_ratio,
)
from space_y.cooling_applicability import (
    assess_candidate_station_path,
    assess_local_reynolds_prandtl,
    build_cooling_applicability_policy,
)
from space_y.cooling_channel_templates import (
    adjacent_channel_count_candidates,
    generate_reference_width_templates,
)
from space_y.cooling_channel_candidates import solve_cooling_channel_candidates
from space_y.cooling_hc import solve_cooling_hc
from space_y.physics import colebrook_darcy_friction_factor, gnielinski_common_form


ROOT = Path(__file__).resolve().parents[1]


def policy():
    record = json.loads(
        (ROOT / "config/SPACE_Y_cooling_correlation_policy.json").read_text(
            encoding="utf-8"
        )
    )
    return build_cooling_applicability_policy(record)


def heat_domain():
    return build_heat_transfer_domain(
        {
            "source": "TEST_GNIELINSKI_DOMAIN",
            "re_range": [3000.0, 5000000.0],
            "pr_range": [0.5, 2000.0],
            "geometry": "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
            "fully_developed_required": True,
        }
    )


def friction_domain(re_range=(3000.0, 10000000.0), rr_range=(0.0, 0.1)):
    return build_friction_domain(
        {
            "source": "TEST_COLEBROOK_DOMAIN_SEPARATE_FROM_GNIELINSKI",
            "re_range": list(re_range),
            "relative_roughness_range": list(rr_range),
            "geometry": "RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
        }
    )


def station(station_id: str, x_m: float, reynolds: float, prandtl: float = 10.0):
    local = assess_local_reynolds_prandtl(reynolds, prandtl, policy())
    return {
        "station_id": station_id,
        "x_m": x_m,
        "calculation": {
            "status": "MODEL_U",
            "results": {
                "Reynolds": reynolds,
                "Prandtl": prandtl,
                "applicability_assessment": local,
                "roughness_assessment": {
                    "roughness_friction_ratio": 1.2,
                    "roughness_heat_transfer_warning_status": (
                        "BELOW_ROUGHNESS_WARNING_THRESHOLD"
                    ),
                },
            },
        },
    }


class CoolingApplicabilityTests(unittest.TestCase):
    def test_policy_separates_correlation_and_team_recommendation(self) -> None:
        value = policy()
        self.assertEqual(value.gnielinski_re_min, 3000.0)
        self.assertEqual(value.gnielinski_re_max, 5000000.0)
        self.assertEqual(value.gnielinski_pr_min, 0.5)
        self.assertEqual(value.gnielinski_pr_max, 2000.0)
        self.assertEqual(value.recommended_re_min, 5000.0)
        self.assertEqual(value.transition_warning_re_threshold, 10000.0)
        self.assertFalse(value.apply_arbitrary_friction_cap)
        self.assertFalse(value.apply_arbitrary_heat_transfer_cap)

    def test_policy_and_template_configs_validate_against_schemas(self) -> None:
        pairs = (
            (
                "config/SPACE_Y_cooling_correlation_policy.json",
                "schemas/SPACE_Y_cooling_correlation_policy.schema.json",
            ),
            (
                "config/SPACE_Y_cooling_channel_template_grid.json",
                "schemas/SPACE_Y_cooling_channel_template_grid.schema.json",
            ),
        )
        for record_path, schema_path in pairs:
            with self.subTest(record=record_path):
                record = json.loads((ROOT / record_path).read_text(encoding="utf-8"))
                schema = json.loads((ROOT / schema_path).read_text(encoding="utf-8"))
                jsonschema.Draft202012Validator(schema).validate(record)

    def test_single_segment_output_exposes_policy_and_roughness_evidence(self) -> None:
        request = json.loads(
            (ROOT / "examples/cooling_hc_rectangular_synthetic.json").read_text(
                encoding="utf-8"
            )
        )
        multi = json.loads(
            (
                ROOT / "examples/cooling_channel_recommendation_synthetic_multi.json"
            ).read_text(encoding="utf-8")
        )
        request["model_conditions"]["applicability_policy"] = multi[
            "model_conditions"
        ]["applicability_policy"]
        result = solve_cooling_hc(request)
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(
            result["results"]["applicability_assessment"][
                "re_design_recommendation_status"
            ],
            "MEETS_PRELIMINARY_RE_RECOMMENDATION",
        )
        self.assertAlmostEqual(result["results"]["channel_aspect_ratio_h_over_w"], 2.0)
        self.assertGreaterEqual(result["results"]["roughness_friction_ratio"], 1.0)
        self.assertFalse(result["validated_engine_prediction"])

    def test_candidate_path_policy_is_integrated_without_design_approval(self) -> None:
        request = json.loads(
            (
                ROOT / "examples/cooling_channel_recommendation_synthetic_multi.json"
            ).read_text(encoding="utf-8")
        )
        result = solve_cooling_channel_candidates(request)
        retained = [
            item
            for item in result["candidates"]
            if item["comparison"]["h_l_at_reference_W_m2K"] is not None
        ]
        self.assertTrue(retained)
        self.assertTrue(
            all(
                item["summary"]["applicability_assessment"][
                    "minimum_reynolds_station_id"
                ]
                == "SYN-EXIT"
                for item in retained
            )
        )
        self.assertTrue(
            all("TRANSITION_REGION_WARNING" in item["recommendation_tags"] for item in retained)
        )
        self.assertEqual(
            result["candidate_recommendation"]["design_recommended_candidate_ids"],
            [],
        )
        self.assertFalse(result["validated_engine_prediction"])

    def test_gnielinski_re_lower_boundary_is_inclusive(self) -> None:
        result = assess_local_reynolds_prandtl(3000.0, 1.0, policy())
        self.assertTrue(result["gnielinski_re_in_domain"])
        self.assertEqual(
            result["re_design_recommendation_status"],
            "BELOW_PRELIMINARY_RE_RECOMMENDATION",
        )

    def test_gnielinski_re_upper_boundary_is_inclusive(self) -> None:
        result = assess_local_reynolds_prandtl(5000000.0, 1.0, policy())
        self.assertTrue(result["gnielinski_re_in_domain"])

    def test_gnielinski_re_outside_boundaries(self) -> None:
        for reynolds in (2999.9, 5000000.1):
            with self.subTest(reynolds=reynolds):
                result = assess_local_reynolds_prandtl(reynolds, 1.0, policy())
                self.assertFalse(result["gnielinski_re_in_domain"])
                self.assertEqual(
                    result["correlation_applicability_status"],
                    "OUTSIDE_GNIELINSKI_POLICY_DOMAIN",
                )

    def test_gnielinski_pr_boundaries_are_inclusive(self) -> None:
        for prandtl in (0.5, 2000.0):
            with self.subTest(prandtl=prandtl):
                result = assess_local_reynolds_prandtl(10000.0, prandtl, policy())
                self.assertTrue(result["gnielinski_pr_in_domain"])

    def test_gnielinski_pr_outside_boundaries(self) -> None:
        for prandtl in (0.4999, 2000.1):
            with self.subTest(prandtl=prandtl):
                result = assess_local_reynolds_prandtl(10000.0, prandtl, policy())
                self.assertFalse(result["gnielinski_pr_in_domain"])

    def test_displayed_gnielinski_form_respects_boundary_domain(self) -> None:
        factor = colebrook_darcy_friction_factor(3000.0, 0.0)
        nu, applicability = gnielinski_common_form(
            3000.0,
            0.5,
            factor,
            domain=heat_domain(),
            geometry="RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
            fully_developed=True,
        )
        self.assertIsNotNone(nu)
        self.assertEqual(applicability.status, "APPLICABLE_TO_SUPPLIED_DOMAIN")

    def test_re_3000_to_5000_is_below_design_recommendation(self) -> None:
        result = assess_local_reynolds_prandtl(4000.0, 1.0, policy())
        self.assertEqual(
            result["re_design_recommendation_status"],
            "BELOW_PRELIMINARY_RE_RECOMMENDATION",
        )
        self.assertTrue(result["transition_region_warning"])

    def test_re_5000_to_10000_has_transition_warning(self) -> None:
        result = assess_local_reynolds_prandtl(5000.0, 1.0, policy())
        self.assertEqual(
            result["re_design_recommendation_status"],
            "MEETS_RE_MIN_WITH_TRANSITION_WARNING",
        )
        self.assertTrue(result["transition_region_warning"])

    def test_re_10000_meets_recommendation_without_transition_warning(self) -> None:
        result = assess_local_reynolds_prandtl(10000.0, 1.0, policy())
        self.assertEqual(
            result["re_design_recommendation_status"],
            "MEETS_PRELIMINARY_RE_RECOMMENDATION",
        )
        self.assertFalse(result["transition_region_warning"])

    def test_path_minimum_re_can_occur_away_from_inlet(self) -> None:
        result = assess_candidate_station_path(
            [
                station("INLET", 0.0, 7000.0),
                station("MID", 0.5, 4000.0),
                station("EXIT", 1.0, 8000.0),
            ],
            policy(),
        )
        self.assertEqual(result["minimum_reynolds"], 4000.0)
        self.assertEqual(result["minimum_reynolds_station_id"], "MID")
        self.assertEqual(result["minimum_reynolds_x_m"], 0.5)
        self.assertEqual(result["re_design_margin"], -1000.0)
        self.assertFalse(result["continuous_path_claim"])

    def test_incomplete_station_holds_path_assessment(self) -> None:
        items = [station("A", 0.0, 6000.0), station("B", 1.0, 7000.0)]
        items[1]["calculation"]["status"] = "OUTSIDE"
        items[1]["calculation"]["results"] = None
        result = assess_candidate_station_path(items, policy())
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIsNone(result["minimum_reynolds"])

    def test_smooth_channel_ratio_is_one(self) -> None:
        result = evaluate_roughness_friction_ratio(
            reynolds=100000.0,
            relative_roughness=0.0,
            friction_domain=friction_domain(),
            warning_threshold=4.0,
        )
        self.assertAlmostEqual(result["roughness_friction_ratio"], 1.0)
        self.assertEqual(
            result["roughness_heat_transfer_warning_status"],
            "BELOW_ROUGHNESS_WARNING_THRESHOLD",
        )

    def test_rough_factor_uses_same_re_and_is_higher(self) -> None:
        result = evaluate_roughness_friction_ratio(
            reynolds=100000.0,
            relative_roughness=0.01,
            friction_domain=friction_domain(),
            warning_threshold=4.0,
        )
        self.assertEqual(result["reynolds"], 100000.0)
        self.assertGreater(result["rough_darcy_factor"], result["smooth_darcy_factor"])
        self.assertLess(result["roughness_friction_ratio"], 4.0)

    def test_ratio_at_or_above_four_raises_warning_only(self) -> None:
        result = evaluate_roughness_friction_ratio(
            reynolds=100000.0,
            relative_roughness=0.08,
            friction_domain=friction_domain(),
            warning_threshold=4.0,
        )
        self.assertGreaterEqual(result["roughness_friction_ratio"], 4.0)
        self.assertEqual(
            result["roughness_heat_transfer_warning_status"],
            "ROUGHNESS_HEAT_TRANSFER_OVERPREDICTION_WARNING",
        )
        self.assertFalse(result["arbitrary_friction_cap_applied"])
        self.assertFalse(result["arbitrary_heat_transfer_cap_applied"])

    def test_ratio_is_not_created_outside_friction_domain(self) -> None:
        result = evaluate_roughness_friction_ratio(
            reynolds=2999.0,
            relative_roughness=0.01,
            friction_domain=friction_domain(),
            warning_threshold=4.0,
        )
        self.assertEqual(result["status"], "OUTSIDE")
        self.assertIsNone(result["roughness_friction_ratio"])

    def test_high_roughness_does_not_cap_friction_or_h_l(self) -> None:
        result = evaluate_local_coolant_heat_transfer(
            mass_flow_kg_s=1.0,
            flow_area_m2=1.0e-4,
            hydraulic_diameter_m=0.01,
            viscosity_Pa_s=0.001,
            cp_J_kgK=1000.0,
            conductivity_W_mK=0.1,
            equivalent_sand_roughness_m=0.0008,
            closure_geometry="RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
            fully_developed=True,
            thermal_boundary_condition="TEST_BOUNDARY",
            heat_transfer_domain=heat_domain(),
            heat_transfer_thermal_boundary_condition="TEST_BOUNDARY",
            friction_domain=friction_domain(),
            density_kg_m3=1000.0,
            applicability_policy=policy(),
        )
        expected_factor = colebrook_darcy_friction_factor(
            result.Reynolds, result.relative_roughness
        )
        expected_nu, _ = gnielinski_common_form(
            result.Reynolds,
            result.Prandtl,
            expected_factor,
            domain=heat_domain(),
            geometry="RECTANGULAR_CLOSED_DUCT_USING_HYDRAULIC_DIAMETER",
            fully_developed=True,
        )
        self.assertEqual(result.darcy_factor, expected_factor)
        self.assertAlmostEqual(result.Nusselt, expected_nu)
        self.assertEqual(
            result.roughness_assessment[
                "roughness_heat_transfer_warning_status"
            ],
            "ROUGHNESS_HEAT_TRANSFER_OVERPREDICTION_WARNING",
        )

    def test_roughness_warning_path_maximum_station(self) -> None:
        items = [station("A", 0.0, 10000.0), station("B", 1.0, 11000.0)]
        items[0]["calculation"]["results"]["roughness_assessment"][
            "roughness_friction_ratio"
        ] = 1.5
        items[1]["calculation"]["results"]["roughness_assessment"].update(
            {
                "roughness_friction_ratio": 4.2,
                "roughness_heat_transfer_warning_status": (
                    "ROUGHNESS_HEAT_TRANSFER_OVERPREDICTION_WARNING"
                ),
            }
        )
        result = assess_candidate_station_path(items, policy())
        self.assertEqual(result["maximum_roughness_friction_ratio"], 4.2)
        self.assertEqual(result["maximum_roughness_ratio_station_id"], "B")
        self.assertEqual(
            result["roughness_warning_status"],
            "ROUGHNESS_HEAT_TRANSFER_OVERPREDICTION_WARNING",
        )

    def test_reference_width_grid_has_80_non_design_templates(self) -> None:
        spec = json.loads(
            (ROOT / "config/SPACE_Y_cooling_channel_template_grid.json").read_text(
                encoding="utf-8"
            )
        )
        templates = generate_reference_width_templates(spec)
        self.assertEqual(len(templates), 80)
        self.assertTrue(all(item["channel_count"] is None for item in templates))
        self.assertTrue(all(not item["design_approved"] for item in templates))

    def test_channel_count_requires_radius_and_returns_adjacent_integers(self) -> None:
        template = {
            "template_id": "T",
            "reference_width_m": 0.0012,
            "rib_thickness_m": 0.001,
        }
        result = adjacent_channel_count_candidates(template, 0.02)
        ideal = 2.0 * math.pi * 0.02 / 0.0022
        self.assertAlmostEqual(result["ideal_channel_count"], ideal)
        self.assertEqual(
            result["adjacent_integer_channel_counts"],
            sorted({math.floor(ideal), math.ceil(ideal)}),
        )
        self.assertTrue(
            all(
                item["status"] == "PASS"
                for item in result[
                    "geometric_validity_by_channel_count"
                ].values()
            )
        )
        self.assertFalse(result["selection_performed"])

    def test_adjacent_channel_count_reports_nonpositive_width_as_outside(self) -> None:
        template = {
            "template_id": "T",
            "reference_width_m": 0.0012,
            "rib_thickness_m": 0.001,
        }
        result = adjacent_channel_count_candidates(template, 0.0001)
        self.assertEqual(
            result["geometric_validity_by_channel_count"]["1"]["status"],
            "OUTSIDE",
        )
        self.assertFalse(result["selection_performed"])

    def test_policy_rejects_arbitrary_cap(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_cooling_correlation_policy.json").read_text(
                encoding="utf-8"
            )
        )
        record["roughness_assessment"]["apply_arbitrary_friction_cap"] = True
        with self.assertRaisesRegex(ValueError, "caps are prohibited"):
            build_cooling_applicability_policy(record)

    def test_policy_rejects_arbitrary_heat_transfer_cap(self) -> None:
        record = json.loads(
            (ROOT / "config/SPACE_Y_cooling_correlation_policy.json").read_text(
                encoding="utf-8"
            )
        )
        record["roughness_assessment"]["apply_arbitrary_heat_transfer_cap"] = True
        with self.assertRaisesRegex(ValueError, "caps are prohibited"):
            build_cooling_applicability_policy(record)


if __name__ == "__main__":
    unittest.main()
