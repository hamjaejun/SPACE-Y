from __future__ import annotations

import unittest

from space_y.film_coupling import (
    ExternalFilmStationState,
    FilmCouplingStop,
    solve_counterflow_film_regenerative_coupling,
    validate_external_film_station_state,
)


STATIONS = ("injector", "throat", "exit")
FIXED_HG = {"injector": 1000.0, "throat": 2000.0, "exit": 800.0}
INITIAL_WALL = {station: 300.0 for station in STATIONS}
NUMERICS = {
    "max_iterations": 20,
    "relaxation": 1.0,
    "wall_temperature_tolerance_K": 1.0e-8,
    "total_energy_residual_tolerance_W": 1.0e-10,
    "film_energy_residual_tolerance_W": 1.0e-10,
}


def synthetic_film_marcher(**kwargs):
    order = tuple(kwargs["station_ids_in_film_flow_order"])
    wall = kwargs["wall_temperature_K_by_station"]
    mode = kwargs["film_mode"]
    total_flux = {station: 100.0 + 0.1 * (wall[station] - 300.0) for station in order}
    if mode == "ON":
        film_flux = {station: 0.6 * total_flux[station] for station in order}
        dry_flux = {station: 0.4 * total_flux[station] for station in order}
    else:
        film_flux = {station: 0.0 for station in order}
        dry_flux = dict(total_flux)
    film_rate = sum(film_flux.values())
    dry_rate = sum(dry_flux.values())
    return {
        "status": "MODEL_U",
        "numerical_status": "PASS",
        "film_to_wall_heat_flux_W_m2_by_station": film_flux,
        "dry_gas_to_wall_heat_flux_W_m2_by_station": dry_flux,
        "gas_to_film_heat_rate_W": film_rate + 30.0,
        "film_to_wall_heat_rate_W": film_rate,
        "dry_gas_to_wall_heat_rate_W": dry_rate,
        "film_sensible_heat_rate_W": 30.0,
        "film_energy_residual_W": 0.0,
        "synthetic_test_only": True,
    }


def synthetic_regenerative_marcher(**kwargs):
    order = tuple(kwargs["station_ids_in_coolant_flow_order"])
    flux = kwargs["wall_heat_flux_W_m2_by_station"]
    return {
        "status": "MODEL_U",
        "numerical_status": "PASS",
        "coolant_order_observed": list(order),
        "wall_temperature_K_by_station": {
            station: 300.0 + 0.2 * flux[station] for station in STATIONS
        },
        "heat_rate_into_coolant_W": sum(flux.values()),
        "synthetic_test_only": True,
    }


class FilmRegenerativeCouplingTests(unittest.TestCase):
    def test_counterflow_orders_and_fixed_point_converge(self) -> None:
        result = solve_counterflow_film_regenerative_coupling(
            station_ids_in_gas_axial_order=STATIONS,
            fixed_h_g_W_m2K_by_station=FIXED_HG,
            initial_wall_temperature_K_by_station=INITIAL_WALL,
            film_marcher=synthetic_film_marcher,
            regenerative_marcher=synthetic_regenerative_marcher,
            numerics=NUMERICS,
        )
        self.assertTrue(result["converged"])
        self.assertEqual(result["numerical_status"], "PASS")
        self.assertEqual(result["film_flow_order_station_ids"], list(STATIONS))
        self.assertEqual(result["coolant_flow_order_station_ids"], list(reversed(STATIONS)))
        self.assertEqual(
            result["regenerative_solution"]["coolant_order_observed"], list(reversed(STATIONS))
        )
        self.assertGreater(result["iterations"], 1)

    def test_fixed_hg_is_unchanged(self) -> None:
        supplied = dict(FIXED_HG)

        def attempting_mutation(**kwargs):
            kwargs["fixed_h_g_W_m2K_by_station"]["throat"] = 1.0
            return synthetic_film_marcher(**kwargs)

        result = solve_counterflow_film_regenerative_coupling(
            station_ids_in_gas_axial_order=STATIONS,
            fixed_h_g_W_m2K_by_station=supplied,
            initial_wall_temperature_K_by_station=INITIAL_WALL,
            film_marcher=attempting_mutation,
            regenerative_marcher=synthetic_regenerative_marcher,
            numerics=NUMERICS,
        )
        self.assertEqual(supplied, FIXED_HG)
        self.assertEqual(result["fixed_h_g_W_m2K_by_station"], FIXED_HG)

    def test_film_off_reproduces_dry_wall_path(self) -> None:
        result = solve_counterflow_film_regenerative_coupling(
            station_ids_in_gas_axial_order=STATIONS,
            fixed_h_g_W_m2K_by_station=FIXED_HG,
            initial_wall_temperature_K_by_station=INITIAL_WALL,
            film_marcher=synthetic_film_marcher,
            regenerative_marcher=synthetic_regenerative_marcher,
            numerics=NUMERICS,
            film_mode="OFF",
        )
        self.assertTrue(result["converged"])
        self.assertEqual(result["film_solution"]["film_to_wall_heat_rate_W"], 0.0)
        self.assertGreater(result["film_solution"]["dry_gas_to_wall_heat_rate_W"], 0.0)

    def test_split_only_reproduces_dry_wall_path(self) -> None:
        result = solve_counterflow_film_regenerative_coupling(
            station_ids_in_gas_axial_order=STATIONS,
            fixed_h_g_W_m2K_by_station=FIXED_HG,
            initial_wall_temperature_K_by_station=INITIAL_WALL,
            film_marcher=synthetic_film_marcher,
            regenerative_marcher=synthetic_regenerative_marcher,
            numerics=NUMERICS,
            film_mode="SPLIT_ONLY",
        )
        self.assertTrue(result["converged"])
        self.assertEqual(result["film_solution"]["film_to_wall_heat_rate_W"], 0.0)

    def test_wet_dry_and_total_energy_close(self) -> None:
        result = solve_counterflow_film_regenerative_coupling(
            station_ids_in_gas_axial_order=STATIONS,
            fixed_h_g_W_m2K_by_station=FIXED_HG,
            initial_wall_temperature_K_by_station=INITIAL_WALL,
            film_marcher=synthetic_film_marcher,
            regenerative_marcher=synthetic_regenerative_marcher,
            numerics=NUMERICS,
        )
        self.assertAlmostEqual(result["checks"]["film_energy_residual_W"], 0.0)
        self.assertAlmostEqual(result["checks"]["coupling_energy_residual_W"], 0.0)
        self.assertAlmostEqual(result["checks"]["normalized_coupling_energy_residual"], 0.0)

    def test_nonconvergence_is_num_u_not_pass(self) -> None:
        def drifting_regenerative(**kwargs):
            previous = kwargs["previous_wall_temperature_K_by_station"]
            flux = kwargs["wall_heat_flux_W_m2_by_station"]
            return {
                "status": "MODEL_U",
                "numerical_status": "PASS",
                "wall_temperature_K_by_station": {
                    station: previous[station] + 1.0 for station in STATIONS
                },
                "heat_rate_into_coolant_W": sum(flux.values()),
            }

        controls = dict(NUMERICS, max_iterations=3)
        result = solve_counterflow_film_regenerative_coupling(
            station_ids_in_gas_axial_order=STATIONS,
            fixed_h_g_W_m2K_by_station=FIXED_HG,
            initial_wall_temperature_K_by_station=INITIAL_WALL,
            film_marcher=synthetic_film_marcher,
            regenerative_marcher=drifting_regenerative,
            numerics=controls,
        )
        self.assertFalse(result["converged"])
        self.assertEqual(result["status"], "NUM_U")
        self.assertEqual(result["numerical_status"], "NUM_U")

    def test_energy_mismatch_prevents_false_convergence(self) -> None:
        def wrong_energy_regenerative(**kwargs):
            result = dict(synthetic_regenerative_marcher(**kwargs))
            result["heat_rate_into_coolant_W"] += 5.0
            return result

        controls = dict(NUMERICS, max_iterations=5)
        result = solve_counterflow_film_regenerative_coupling(
            station_ids_in_gas_axial_order=STATIONS,
            fixed_h_g_W_m2K_by_station=FIXED_HG,
            initial_wall_temperature_K_by_station=INITIAL_WALL,
            film_marcher=synthetic_film_marcher,
            regenerative_marcher=wrong_energy_regenerative,
            numerics=controls,
        )
        self.assertEqual(result["status"], "NUM_U")
        self.assertFalse(result["converged"])
        self.assertAlmostEqual(result["iteration_history"][-1]["coupling_energy_residual_W"], -5.0)

    def test_solver_recomputes_film_energy_residual(self) -> None:
        def inconsistent_film(**kwargs):
            result = dict(synthetic_film_marcher(**kwargs))
            result["gas_to_film_heat_rate_W"] += 7.0
            result["film_energy_residual_W"] = 0.0
            return result

        controls = dict(NUMERICS, max_iterations=4)
        result = solve_counterflow_film_regenerative_coupling(
            station_ids_in_gas_axial_order=STATIONS,
            fixed_h_g_W_m2K_by_station=FIXED_HG,
            initial_wall_temperature_K_by_station=INITIAL_WALL,
            film_marcher=inconsistent_film,
            regenerative_marcher=synthetic_regenerative_marcher,
            numerics=controls,
        )
        self.assertEqual(result["status"], "NUM_U")
        self.assertAlmostEqual(result["iteration_history"][-1]["film_energy_residual_W"], 7.0)

    def test_upstream_failure_status_is_propagated(self) -> None:
        def blocked_film(**kwargs):
            return {"status": "INPUT_U", "numerical_status": "NOT_RUN", "reason": "missing closure"}

        result = solve_counterflow_film_regenerative_coupling(
            station_ids_in_gas_axial_order=STATIONS,
            fixed_h_g_W_m2K_by_station=FIXED_HG,
            initial_wall_temperature_K_by_station=INITIAL_WALL,
            film_marcher=blocked_film,
            regenerative_marcher=synthetic_regenerative_marcher,
            numerics=NUMERICS,
        )
        self.assertEqual(result["status"], "INPUT_U")
        self.assertFalse(result["converged"])

    def test_depleted_film_cannot_keep_liquid_couette_active(self) -> None:
        state = ExternalFilmStationState(
            station_id="exit",
            liquid_film_mass_flow_kg_s=0.0,
            liquid_film_temperature_K=350.0,
            liquid_film_depleted=True,
            liquid_couette_active=True,
            evaporated_mass_flow_kg_s=None,
            gas_film_boundary=None,
            film_to_wall_heat_flux_W_m2=None,
            applicability_status="OUTSIDE",
        )
        with self.assertRaises(FilmCouplingStop) as caught:
            validate_external_film_station_state(state)
        self.assertEqual(caught.exception.status, "OUTSIDE")

    def test_future_team_interface_does_not_implement_evaporation(self) -> None:
        state = ExternalFilmStationState(
            station_id="throat",
            liquid_film_mass_flow_kg_s=0.01,
            liquid_film_temperature_K=340.0,
            liquid_film_depleted=False,
            liquid_couette_active=True,
            evaporated_mass_flow_kg_s=None,
            gas_film_boundary=None,
            film_to_wall_heat_flux_W_m2=2.0e5,
            applicability_status="INPUT_U",
        )
        result = validate_external_film_station_state(state)
        self.assertTrue(result["interface_only"])
        self.assertFalse(result["evaporation_model_implemented_here"])

    def test_invalid_station_mapping_is_rejected(self) -> None:
        with self.assertRaises(FilmCouplingStop) as caught:
            solve_counterflow_film_regenerative_coupling(
                station_ids_in_gas_axial_order=STATIONS,
                fixed_h_g_W_m2K_by_station={"injector": 1000.0},
                initial_wall_temperature_K_by_station=INITIAL_WALL,
                film_marcher=synthetic_film_marcher,
                regenerative_marcher=synthetic_regenerative_marcher,
                numerics=NUMERICS,
            )
        self.assertEqual(caught.exception.status, "INPUT_U")


if __name__ == "__main__":
    unittest.main()
