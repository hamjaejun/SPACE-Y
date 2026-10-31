from __future__ import annotations

import copy
from pathlib import Path
import unittest

from space_y.contract import load_json
from space_y.m0_coupled import solve_coupled_single_segment
from space_y.m0_marching import (
    solve_coupled_multisegment,
    solve_coupled_multisegment_file,
)


ROOT = Path(__file__).resolve().parents[1]


class MultisegmentMarchingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request = load_json(ROOT / "examples/m0_multisegment_synthetic_counterflow.json")
        cls.single_request = load_json(ROOT / "examples/m0_coupled_synthetic_single_segment.json")
        cls.cea = load_json(ROOT / "artifacts/phase1/cea_rp1311_example13_rocket_run.json")

    def _records(self, request: dict) -> dict[str, dict]:
        return {segment["segment_id"]: self.cea for segment in request["segments"]}

    def _one_segment_request(self) -> dict:
        single = self.single_request
        segment_id = single["segment_id"]
        return {
            "purpose": single["purpose"],
            "run_id": "M0-MARCH-SYNTHETIC-ONE-SEGMENT",
            "case_id": single["case_id"],
            "geometry_version": single["geometry_version"],
            "spatial_basis": single["spatial_basis"],
            "model_scope": "STEADY_SINGLE_PATH_MULTISEGMENT_GAS_WALL_COOLANT",
            "flow_boundary_mode": single["flow_boundary_mode"],
            "formula_version": single["formula_version"],
            "formula_source": single["formula_source"],
            "gas_axial_coordinate_reference": "synthetic single coordinate",
            "gas_axial_order_segment_ids": [segment_id],
            "coolant_flow_direction": "INCREASING_GAS_AXIAL_COORDINATE",
            "coolant_flow_order_segment_ids": [segment_id],
            "coolant_inlet_segment_id": segment_id,
            "coolant_outlet_segment_id": segment_id,
            "coolant_boundary": copy.deepcopy(single["coolant_boundary"]),
            "coolant_property_model": copy.deepcopy(single["coolant_property_model"]),
            "single_phase_bulk_domain": copy.deepcopy(single["single_phase_bulk_domain"]),
            "coolant_heat_transfer_closure": copy.deepcopy(single["coolant_heat_transfer_closure"]),
            "coolant_friction_closure": copy.deepcopy(single["coolant_friction_closure"]),
            "marching_numerics": {
                "total_energy_residual_tolerance_W": 1e-7,
                "enthalpy_continuity_tolerance_J_kg": 1e-9,
                "pressure_continuity_tolerance_Pa": 1e-9,
                "mass_residual_tolerance_kg_s": 1e-12,
            },
            "segments": [
                {
                    "segment_id": segment_id,
                    "gas_axial_coordinate_m": 0.0,
                    "gas_axial_coordinate_source": "synthetic single coordinate",
                    "cea_station_mapping": {
                        "method": "EXPLICIT_NO_INTERPOLATION",
                        "source": "same explicit synthetic station as single-segment fixture",
                        "cea_run_artifact": single["cea_run_artifact"],
                        "chamber_station_index": single["chamber_station_index"],
                        "cea_station_index": single["cea_station_index"],
                        "transport_basis": single["transport_basis"],
                    },
                    "geometry": copy.deepcopy(single["geometry"]),
                    "radiation": copy.deepcopy(single["radiation"]),
                    "gas_wall_numerics": copy.deepcopy(single["gas_wall_numerics"]),
                    "coupled_numerics": copy.deepcopy(single["coupled_numerics"]),
                }
            ],
        }

    def test_one_segment_marching_matches_single_segment_solver(self) -> None:
        request = self._one_segment_request()
        marching = solve_coupled_multisegment(request, self._records(request))
        direct = solve_coupled_single_segment(self.single_request, self.cea)
        self.assertEqual(marching["numerical_status"], "PASS")
        for qoi in ("Y01", "Y02", "Y03", "Y04", "Y05"):
            self.assertAlmostEqual(
                marching["qoi"][qoi]["value"], direct["qoi"][qoi]["value"], places=8
            )

    def test_counterflow_uses_explicit_coolant_order(self) -> None:
        result = solve_coupled_multisegment(self.request, self._records(self.request))
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertEqual(
            [item["segment_id"] for item in result["segments"]],
            ["SYN-X2", "SYN-X1", "SYN-X0"],
        )
        coordinates = [item["gas_axial_coordinate_m"] for item in result["segments"]]
        self.assertTrue(all(right < left for left, right in zip(coordinates, coordinates[1:])))
        self.assertEqual(result["physical_validation_v01_v12"], "NOT_RUN")
        self.assertFalse(result["validated_engine_prediction"])

    def test_increasing_coordinate_flow_uses_gas_axial_order(self) -> None:
        request = copy.deepcopy(self.request)
        request["coolant_flow_direction"] = "INCREASING_GAS_AXIAL_COORDINATE"
        request["coolant_flow_order_segment_ids"] = copy.deepcopy(
            request["gas_axial_order_segment_ids"]
        )
        request["coolant_inlet_segment_id"] = "SYN-X0"
        request["coolant_outlet_segment_id"] = "SYN-X2"
        result = solve_coupled_multisegment(request, self._records(request))
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertEqual(
            [item["segment_id"] for item in result["segments"]],
            ["SYN-X0", "SYN-X1", "SYN-X2"],
        )
        coordinates = [item["gas_axial_coordinate_m"] for item in result["segments"]]
        self.assertTrue(all(right > left for left, right in zip(coordinates, coordinates[1:])))

    def test_whole_path_energy_mass_and_state_continuity(self) -> None:
        result = solve_coupled_multisegment(self.request, self._records(self.request))
        checks = result["checks"]
        self.assertLess(
            abs(checks["total_energy_residual_W"]),
            self.request["marching_numerics"]["total_energy_residual_tolerance_W"],
        )
        self.assertEqual(checks["mass_residual_max_kg_s"], 0.0)
        self.assertEqual(checks["enthalpy_continuity_residual_max_J_kg"], 0.0)
        self.assertEqual(checks["pressure_continuity_residual_max_Pa"], 0.0)
        self.assertAlmostEqual(
            checks["total_heat_into_coolant_W"],
            checks["boundary_coolant_enthalpy_rise_W"],
            places=7,
        )

    def test_declared_direction_must_match_coolant_order(self) -> None:
        request = copy.deepcopy(self.request)
        request["coolant_flow_direction"] = "INCREASING_GAS_AXIAL_COORDINATE"
        result = solve_coupled_multisegment(request, self._records(request))
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("inconsistent", result["reason"])

    def test_missing_explicit_cea_mapping_source_is_input_u(self) -> None:
        request = copy.deepcopy(self.request)
        del request["segments"][2]["cea_station_mapping"]["source"]
        result = solve_coupled_multisegment(request, self._records(request))
        self.assertEqual(result["status"], "INPUT_U")
        self.assertIn("CEA station mapping", result["reason"])

    def test_outside_cea_station_blocks_downstream_segments(self) -> None:
        request = copy.deepcopy(self.request)
        first = next(item for item in request["segments"] if item["segment_id"] == "SYN-X2")
        first["cea_station_mapping"]["cea_station_index"] = 2
        result = solve_coupled_multisegment(request, self._records(request))
        self.assertEqual(result["status"], "OUTSIDE")
        self.assertEqual(result["stopped_at_segment"], "SYN-X2")
        self.assertEqual(result["segments"][0]["execution_status"], "EXECUTED")
        self.assertTrue(
            all(
                item["execution_status"] == "BLOCKED_BY_UPSTREAM"
                for item in result["segments"][1:]
            )
        )
        self.assertTrue(all(item["status"] == "OUTSIDE" for item in result["segments"]))

    def test_segment_input_u_blocks_downstream_segments(self) -> None:
        request = copy.deepcopy(self.request)
        middle = next(item for item in request["segments"] if item["segment_id"] == "SYN-X1")
        del middle["radiation"]["q_rad_net_W"]
        result = solve_coupled_multisegment(request, self._records(request))
        self.assertEqual(result["status"], "INPUT_U")
        self.assertEqual(result["stopped_at_segment"], "SYN-X1")
        self.assertEqual(result["segments"][2]["execution_status"], "BLOCKED_BY_UPSTREAM")
        self.assertEqual(result["segments"][2]["status"], "INPUT_U")

    def test_segment_nonconvergence_propagates_num_u(self) -> None:
        request = copy.deepcopy(self.request)
        middle = next(item for item in request["segments"] if item["segment_id"] == "SYN-X1")
        middle["coupled_numerics"]["max_iterations"] = 1
        result = solve_coupled_multisegment(request, self._records(request))
        self.assertEqual(result["status"], "NUM_U")
        self.assertEqual(result["stopped_at_segment"], "SYN-X1")
        self.assertEqual(result["segments"][0]["execution_status"], "EXECUTED")
        self.assertEqual(result["segments"][1]["execution_status"], "EXECUTED")
        self.assertEqual(result["segments"][2]["execution_status"], "BLOCKED_BY_UPSTREAM")
        self.assertEqual(result["segments"][2]["status"], "NUM_U")

    def test_file_entrypoint_validates_schema_and_hashes_each_mapping(self) -> None:
        result = solve_coupled_multisegment_file(
            ROOT / "examples/m0_multisegment_synthetic_counterflow.json",
            ROOT / "schemas/SPACE_Y_M0_multisegment.schema.json",
        )
        self.assertEqual(result["numerical_status"], "PASS")
        for item in result["segments"]:
            self.assertRegex(item["solution"]["cea_artifact_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
