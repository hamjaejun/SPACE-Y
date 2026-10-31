from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class SteadyWallResult:
    gas_resistance_K_W: float
    wall_resistance_K_W: float
    coolant_resistance_K_W: float
    q_conv_W: float
    q_rad_net_W: float
    q_total_W: float
    gas_side_wall_temperature_K: float
    coolant_side_wall_temperature_K: float
    q_conduction_W: float
    q_coolant_W: float
    gas_balance_residual_W: float
    wall_balance_residual_W: float
    coolant_balance_residual_W: float


def _positive(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def solve_steady_three_resistance_wall(
    *,
    h_g_W_m2K: float,
    gas_area_m2: float,
    adiabatic_wall_temperature_K: float,
    q_rad_net_W: float,
    wall_resistance_K_W: float,
    h_c_W_m2K: float,
    coolant_area_m2: float,
    coolant_bulk_temperature_K: float,
) -> SteadyWallResult:
    """Exact steady 1D gas-film/wall/coolant-film resistance solution.

    Positive heat rate is gas-to-coolant. ``q_rad_net_W`` is an explicit net
    gas-to-wall rate, positive toward the wall; this function never defaults it.
    """
    h_g = _positive(h_g_W_m2K, "gas heat-transfer coefficient")
    gas_area = _positive(gas_area_m2, "gas area")
    t_aw = _positive(adiabatic_wall_temperature_K, "adiabatic wall temperature")
    r_wall = _positive(wall_resistance_K_W, "wall resistance")
    h_c = _positive(h_c_W_m2K, "coolant heat-transfer coefficient")
    coolant_area = _positive(coolant_area_m2, "coolant area")
    t_bulk = _positive(coolant_bulk_temperature_K, "coolant bulk temperature")
    q_rad = float(q_rad_net_W)
    if not math.isfinite(q_rad):
        raise ValueError("net radiation must be finite")

    r_gas = 1.0 / (h_g * gas_area)
    r_coolant = 1.0 / (h_c * coolant_area)
    q_total = (t_aw - t_bulk + q_rad * r_gas) / (r_gas + r_wall + r_coolant)
    q_conv = q_total - q_rad
    t_wall_gas = t_aw - q_conv * r_gas
    t_wall_coolant = t_bulk + q_total * r_coolant
    q_conduction = (t_wall_gas - t_wall_coolant) / r_wall
    q_coolant = h_c * coolant_area * (t_wall_coolant - t_bulk)
    return SteadyWallResult(
        gas_resistance_K_W=r_gas,
        wall_resistance_K_W=r_wall,
        coolant_resistance_K_W=r_coolant,
        q_conv_W=q_conv,
        q_rad_net_W=q_rad,
        q_total_W=q_total,
        gas_side_wall_temperature_K=t_wall_gas,
        coolant_side_wall_temperature_K=t_wall_coolant,
        q_conduction_W=q_conduction,
        q_coolant_W=q_coolant,
        gas_balance_residual_W=q_conv + q_rad - q_total,
        wall_balance_residual_W=q_total - q_conduction,
        coolant_balance_residual_W=q_conduction - q_coolant,
    )
