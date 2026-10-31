from __future__ import annotations

import copy
from pathlib import Path
import unittest

from space_y.contract import load_json
from space_y.gas import solve_gas_map, solve_gas_map_file


ROOT = Path(__file__).resolve().parents[1]


class GasMappingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.request = load_json(ROOT / "examples/gas_mapping_synthetic_non_design.json")
        cls.cea = load_json(ROOT / "artifacts/phase1/cea_rp1311_example13_rocket_run.json")

    def test_synthetic_mapping_returns_m0_ready_boundaries(self) -> None:
        result = solve_gas_map(self.request, self.cea)
        self.assertEqual(result["status"], "MODEL_U")
        self.assertEqual(len(result["gas_boundaries"]), 3)
        first = result["gas_boundaries"][0]
        # Fixed regression values for the displayed RPA 2012 equations and the
        # tracked CEA artifact/fixture inputs.
        self.assertAlmostEqual(first["sigma"], 1.2135467985483397, places=12)
        self.assertAlmostEqual(first["h_g_W_m2K"], 30231.548859361494, places=8)
        self.assertAlmostEqual(
            first["adiabatic_wall_temperature_K"], 2821.6927044308845, places=8
        )
        self.assertEqual(
            first["gas_boundary"]["radiation_definition"],
            "EXPLICIT_NET_RATE_GAS_TO_WALL_POSITIVE",
        )
        self.assertFalse(result["validated_engine_prediction"])

    def test_cea_transport_si_conversion_provenance(self) -> None:
        transport = self.cea["result"]["transport"]
        self.assertEqual(self.cea["result"]["station_units"]["P"], "bar(a)")
        self.assertAlmostEqual(
            self.cea["result"]["stations"]["P"][0] * 1.0e5,
            20684271.240234375,
            places=6,
        )
        self.assertEqual(transport["original_units"]["conductivity"], "mW/(cm K)")
        for original, si in zip(
            transport["original"]["conductivity_fr_mW_cmK"],
            transport["si"]["conductivity_fr_W_mK"],
        ):
            self.assertAlmostEqual(si, original * 0.1, places=12)
        for original, si in zip(
            transport["original"]["viscosity_millipoise"],
            transport["si"]["viscosity_Pa_s"],
        ):
            self.assertAlmostEqual(si, original * 1.0e-4, places=12)

    def test_missing_transport_is_input_u(self) -> None:
        cea = copy.deepcopy(self.cea)
        cea["result"]["transport"] = None
        result = solve_gas_map(self.request, cea)
        self.assertEqual(result["status"], "INPUT_U")
        self.assertEqual(result["gas_boundaries"], [])

    def test_chamber_station_cannot_be_mapped_as_flow_station(self) -> None:
        request = copy.deepcopy(self.request)
        request["segments"][0]["cea_station_index"] = 0
        result = solve_gas_map(request, self.cea)
        self.assertEqual(result["status"], "INPUT_U")

    def test_station_outside_available_data_is_outside(self) -> None:
        request = copy.deepcopy(self.request)
        request["segments"][0]["cea_station_index"] = 999
        result = solve_gas_map(request, self.cea)
        self.assertEqual(result["status"], "OUTSIDE")

    def test_missing_curvature_source_is_input_u(self) -> None:
        request = copy.deepcopy(self.request)
        del request["geometry"]["bartz_curvature_radius_source"]
        result = solve_gas_map(request, self.cea)
        self.assertEqual(result["status"], "INPUT_U")

    def test_file_entrypoint_validates_schema_and_resolves_artifact(self) -> None:
        result = solve_gas_map_file(
            ROOT / "examples/gas_mapping_synthetic_non_design.json",
            ROOT / "schemas/SPACE_Y_GAS_mapping.schema.json",
        )
        self.assertEqual(result["status"], "MODEL_U")
        self.assertRegex(result["cea_artifact_sha256"], r"^[0-9a-f]{64}$")


if __name__ == "__main__":
    unittest.main()
