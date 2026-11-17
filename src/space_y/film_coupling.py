from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Callable, Mapping, Sequence


FilmMarcher = Callable[..., Mapping[str, Any]]
RegenerativeMarcher = Callable[..., Mapping[str, Any]]


class FilmCouplingStop(RuntimeError):
    def __init__(self, status: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class CouplingNumerics:
    max_iterations: int
    relaxation: float
    wall_temperature_tolerance_K: float
    total_energy_residual_tolerance_W: float
    film_energy_residual_tolerance_W: float


@dataclass(frozen=True)
class ExternalFilmStationState:
    station_id: str
    liquid_film_mass_flow_kg_s: float
    liquid_film_temperature_K: float
    liquid_film_depleted: bool
    liquid_couette_active: bool
    evaporated_mass_flow_kg_s: float | None
    gas_film_boundary: Mapping[str, Any] | None
    film_to_wall_heat_flux_W_m2: float | None
    applicability_status: str


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise FilmCouplingStop("INPUT_U", f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise FilmCouplingStop("INPUT_U", f"{name} must be finite")
    return number


def _positive(value: Any, name: str) -> float:
    number = _finite(value, name)
    if number <= 0.0:
        raise FilmCouplingStop("INPUT_U", f"{name} must be positive")
    return number


def _station_ids(values: Sequence[str]) -> tuple[str, ...]:
    identifiers = tuple(str(value).strip() for value in values)
    if not identifiers or any(not value for value in identifiers):
        raise FilmCouplingStop("INPUT_U", "non-empty station IDs are required")
    if len(set(identifiers)) != len(identifiers):
        raise FilmCouplingStop("INPUT_U", "station IDs must be unique")
    return identifiers


def _station_map(
    values: Mapping[str, Any], station_ids: Sequence[str], name: str, *, positive: bool = False
) -> dict[str, float]:
    if set(values) != set(station_ids):
        raise FilmCouplingStop("INPUT_U", f"{name} must contain every station exactly once")
    converter = _positive if positive else _finite
    return {station_id: converter(values[station_id], f"{name} at {station_id}") for station_id in station_ids}


def validate_external_film_station_state(state: ExternalFilmStationState) -> dict[str, Any]:
    """Validate, but do not calculate, future evaporation/depletion inputs."""

    mass_flow = _finite(state.liquid_film_mass_flow_kg_s, "liquid-film mass flow")
    temperature = _positive(state.liquid_film_temperature_K, "liquid-film temperature")
    if mass_flow < 0.0:
        raise FilmCouplingStop("INPUT_U", "liquid-film mass flow cannot be negative")
    if state.liquid_film_depleted and state.liquid_couette_active:
        raise FilmCouplingStop(
            "OUTSIDE",
            "the liquid Couette model cannot remain active after film depletion",
            details={"station_id": state.station_id},
        )
    if state.liquid_film_depleted and mass_flow > 1.0e-12:
        raise FilmCouplingStop(
            "NUM_U",
            "a depleted film station has a non-zero liquid-film mass flow",
            details={"station_id": state.station_id, "mass_flow_kg_s": mass_flow},
        )
    if state.evaporated_mass_flow_kg_s is not None:
        evaporated = _finite(state.evaporated_mass_flow_kg_s, "evaporated mass flow")
        if evaporated < 0.0:
            raise FilmCouplingStop("INPUT_U", "evaporated mass flow cannot be negative")
    return {
        **asdict(state),
        "liquid_film_temperature_K": temperature,
        "interface_only": True,
        "evaporation_model_implemented_here": False,
    }


def _numerics(values: CouplingNumerics | Mapping[str, Any]) -> CouplingNumerics:
    if isinstance(values, CouplingNumerics):
        result = values
    else:
        result = CouplingNumerics(
            max_iterations=int(values["max_iterations"]),
            relaxation=float(values["relaxation"]),
            wall_temperature_tolerance_K=float(values["wall_temperature_tolerance_K"]),
            total_energy_residual_tolerance_W=float(values["total_energy_residual_tolerance_W"]),
            film_energy_residual_tolerance_W=float(values["film_energy_residual_tolerance_W"]),
        )
    if result.max_iterations <= 0:
        raise FilmCouplingStop("INPUT_U", "maximum iterations must be positive")
    if not 0.0 < result.relaxation <= 1.0:
        raise FilmCouplingStop("INPUT_U", "relaxation must be in (0, 1]")
    _positive(result.wall_temperature_tolerance_K, "wall-temperature tolerance")
    _positive(result.total_energy_residual_tolerance_W, "total-energy tolerance")
    _positive(result.film_energy_residual_tolerance_W, "film-energy tolerance")
    return result


def _failure(
    *, status: str, reason: str, film_order: Sequence[str], coolant_order: Sequence[str], history: list[dict[str, Any]]
) -> dict[str, Any]:
    return {
        "status": status,
        "numerical_status": status if status == "NUM_U" else "NOT_RUN",
        "reason": reason,
        "converged": False,
        "film_flow_order_station_ids": list(film_order),
        "coolant_flow_order_station_ids": list(coolant_order),
        "iteration_history": history,
        "physical_validation_v01_v12": "NOT_RUN",
        "validated_engine_prediction": False,
    }


def solve_counterflow_film_regenerative_coupling(
    *,
    station_ids_in_gas_axial_order: Sequence[str],
    fixed_h_g_W_m2K_by_station: Mapping[str, float],
    initial_wall_temperature_K_by_station: Mapping[str, float],
    film_marcher: FilmMarcher,
    regenerative_marcher: RegenerativeMarcher,
    numerics: CouplingNumerics | Mapping[str, Any],
    film_mode: str = "ON",
) -> dict[str, Any]:
    """Couple opposing film and regenerative marches through callbacks.

    Film order is the supplied gas-axial order (injector to nozzle exit).
    Regenerative order is its exact reverse.  The callbacks are the integration
    boundary: this module does not replace the existing regenerative solver or
    invent a film heat-flux closure.  ``h_g`` is copied into each call and is
    never updated by this fixed-``h_g`` M2 iteration.
    """

    film_order = _station_ids(station_ids_in_gas_axial_order)
    coolant_order = tuple(reversed(film_order))
    fixed_h_g = _station_map(
        fixed_h_g_W_m2K_by_station, film_order, "fixed gas-side heat-transfer coefficient", positive=True
    )
    fixed_h_g_fingerprint = tuple((station_id, fixed_h_g[station_id]) for station_id in film_order)
    wall = _station_map(
        initial_wall_temperature_K_by_station, film_order, "initial wall temperature", positive=True
    )
    controls = _numerics(numerics)
    mode = str(film_mode).strip().upper()
    if mode not in {"OFF", "SPLIT_ONLY", "ON"}:
        raise FilmCouplingStop("INPUT_U", "film_mode must be OFF, SPLIT_ONLY, or ON")
    if not callable(film_marcher) or not callable(regenerative_marcher):
        raise FilmCouplingStop("INPUT_U", "film and regenerative marchers must be callable")

    history: list[dict[str, Any]] = []
    last_film: Mapping[str, Any] | None = None
    last_regenerative: Mapping[str, Any] | None = None
    for iteration in range(1, controls.max_iterations + 1):
        last_film = film_marcher(
            station_ids_in_film_flow_order=film_order,
            wall_temperature_K_by_station=dict(wall),
            fixed_h_g_W_m2K_by_station=dict(fixed_h_g),
            film_mode=mode,
            iteration=iteration,
        )
        if not isinstance(last_film, Mapping):
            return _failure(
                status="NUM_U",
                reason="film marcher did not return a mapping",
                film_order=film_order,
                coolant_order=coolant_order,
                history=history,
            )
        if last_film.get("numerical_status") != "PASS":
            return _failure(
                status=str(last_film.get("status", "MODEL_U")),
                reason=f"film marcher stopped: {last_film.get('reason', 'unspecified')}",
                film_order=film_order,
                coolant_order=coolant_order,
                history=history,
            )
        film_to_wall_flux = _station_map(
            last_film["film_to_wall_heat_flux_W_m2_by_station"],
            film_order,
            "film-to-wall heat flux",
        )
        dry_to_wall_flux = _station_map(
            last_film["dry_gas_to_wall_heat_flux_W_m2_by_station"],
            film_order,
            "dry gas-to-wall heat flux",
        )
        combined_wall_flux = {
            station_id: film_to_wall_flux[station_id] + dry_to_wall_flux[station_id]
            for station_id in film_order
        }
        last_regenerative = regenerative_marcher(
            station_ids_in_coolant_flow_order=coolant_order,
            wall_heat_flux_W_m2_by_station=dict(combined_wall_flux),
            previous_wall_temperature_K_by_station=dict(wall),
            iteration=iteration,
        )
        if not isinstance(last_regenerative, Mapping):
            return _failure(
                status="NUM_U",
                reason="regenerative marcher did not return a mapping",
                film_order=film_order,
                coolant_order=coolant_order,
                history=history,
            )
        if last_regenerative.get("numerical_status") != "PASS":
            return _failure(
                status=str(last_regenerative.get("status", "NUM_U")),
                reason=f"regenerative marcher stopped: {last_regenerative.get('reason', 'unspecified')}",
                film_order=film_order,
                coolant_order=coolant_order,
                history=history,
            )
        new_wall = _station_map(
            last_regenerative["wall_temperature_K_by_station"],
            film_order,
            "regenerative wall temperature",
            positive=True,
        )

        wall_residual = max(abs(new_wall[key] - wall[key]) for key in film_order)
        gas_to_film_rate = _finite(
            last_film["gas_to_film_heat_rate_W"], "gas-to-film heat rate"
        )
        film_to_wall_rate = _finite(
            last_film["film_to_wall_heat_rate_W"], "film-to-wall heat rate"
        )
        film_sensible_rate = _finite(
            last_film["film_sensible_heat_rate_W"], "film sensible heat rate"
        )
        reported_film_energy_residual = _finite(
            last_film["film_energy_residual_W"], "reported film energy residual"
        )
        film_energy_residual = gas_to_film_rate - film_to_wall_rate - film_sensible_rate
        reported_film_residual_discrepancy = (
            reported_film_energy_residual - film_energy_residual
        )
        wall_input_rate = film_to_wall_rate + _finite(
            last_film["dry_gas_to_wall_heat_rate_W"], "dry gas-to-wall heat rate"
        )
        coolant_rate = _finite(
            last_regenerative["heat_rate_into_coolant_W"], "heat rate into coolant"
        )
        coupling_energy_residual = wall_input_rate - coolant_rate
        energy_scale = max(abs(wall_input_rate), abs(coolant_rate), 1.0)
        normalized_energy_residual = coupling_energy_residual / energy_scale
        history.append(
            {
                "iteration": iteration,
                "wall_temperature_residual_max_K": wall_residual,
                "film_energy_residual_W": film_energy_residual,
                "reported_film_energy_residual_W": reported_film_energy_residual,
                "reported_film_residual_discrepancy_W": reported_film_residual_discrepancy,
                "coupling_energy_residual_W": coupling_energy_residual,
                "normalized_coupling_energy_residual": normalized_energy_residual,
            }
        )
        if (
            wall_residual <= controls.wall_temperature_tolerance_K
            and abs(film_energy_residual) <= controls.film_energy_residual_tolerance_W
            and abs(reported_film_residual_discrepancy)
            <= controls.film_energy_residual_tolerance_W
            and abs(coupling_energy_residual) <= controls.total_energy_residual_tolerance_W
        ):
            if tuple((station_id, fixed_h_g[station_id]) for station_id in film_order) != fixed_h_g_fingerprint:
                return _failure(
                    status="NUM_U",
                    reason="fixed h_g input changed during coupling",
                    film_order=film_order,
                    coolant_order=coolant_order,
                    history=history,
                )
            return {
                "status": "MODEL_U",
                "numerical_status": "PASS",
                "converged": True,
                "iterations": iteration,
                "film_mode": mode,
                "film_flow_order_station_ids": list(film_order),
                "coolant_flow_order_station_ids": list(coolant_order),
                "fixed_h_g_W_m2K_by_station": dict(fixed_h_g),
                "wall_temperature_K_by_station": new_wall,
                "film_solution": dict(last_film),
                "regenerative_solution": dict(last_regenerative),
                "checks": history[-1],
                "iteration_history": history,
                "assumptions": [
                    "fixed supplied h_g and gas thermal boundary during coupling",
                    "film marches injector to nozzle exit",
                    "regenerative coolant marches nozzle exit to injector",
                    "no axial wall conduction or radiation added by this M2 layer",
                    "no evaporation, depletion, gas-film mixing, or h_g reduction model",
                ],
                "physical_validation_v01_v12": "NOT_RUN",
                "validated_engine_prediction": False,
            }
        wall = {
            station_id: wall[station_id]
            + controls.relaxation * (new_wall[station_id] - wall[station_id])
            for station_id in film_order
        }
        if any(not math.isfinite(value) or value <= 0.0 for value in wall.values()):
            return _failure(
                status="NUM_U",
                reason="under-relaxed wall temperature became non-physical",
                film_order=film_order,
                coolant_order=coolant_order,
                history=history,
            )

    return _failure(
        status="NUM_U",
        reason="film/regenerative fixed-point iteration did not converge",
        film_order=film_order,
        coolant_order=coolant_order,
        history=history,
    )


__all__ = [
    "CouplingNumerics",
    "ExternalFilmStationState",
    "FilmCouplingStop",
    "FilmMarcher",
    "RegenerativeMarcher",
    "solve_counterflow_film_regenerative_coupling",
    "validate_external_film_station_state",
]
