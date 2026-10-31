from __future__ import annotations

from dataclasses import dataclass
import math


@dataclass(frozen=True)
class BartzChamberReference:
    temperature_K: float
    pressure_Pa_abs: float
    c_star_m_s: float
    viscosity_Pa_s: float
    cp_J_kgK: float
    Pr: float
    throat_diameter_m: float
    curvature_radius_m: float


@dataclass(frozen=True)
class BartzStation:
    Mach: float
    gamma: float
    area_ratio_A_At: float
    Pr: float


def _require_positive(value: float, name: str) -> float:
    value = float(value)
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return value


def validate_bartz_inputs(reference: BartzChamberReference, station: BartzStation) -> None:
    for name, value in (
        ("chamber temperature", reference.temperature_K),
        ("chamber pressure", reference.pressure_Pa_abs),
        ("c_star", reference.c_star_m_s),
        ("chamber viscosity", reference.viscosity_Pa_s),
        ("chamber cp", reference.cp_J_kgK),
        ("chamber Pr", reference.Pr),
        ("throat diameter", reference.throat_diameter_m),
        ("curvature radius", reference.curvature_radius_m),
        ("station area ratio", station.area_ratio_A_At),
        ("station Pr", station.Pr),
    ):
        _require_positive(value, name)
    if not math.isfinite(station.Mach) or station.Mach < 0:
        raise ValueError("station Mach must be finite and non-negative")
    if not math.isfinite(station.gamma) or station.gamma <= 1:
        raise ValueError("basic Bartz recovery relation requires gamma > 1")


def recovery_temperature_K(reference: BartzChamberReference, station: BartzStation) -> float:
    """RPA 2012 displayed recovery-temperature relation using Pr**0.33."""
    validate_bartz_inputs(reference, station)
    compressibility = 1.0 + 0.5 * (station.gamma - 1.0) * station.Mach**2
    return reference.temperature_K * (
        1.0 + station.Pr**0.33 * 0.5 * (station.gamma - 1.0) * station.Mach**2
    ) / compressibility


def sigma(reference: BartzChamberReference, station: BartzStation, wall_temperature_K: float) -> float:
    """RPA 2012 basic Bartz wall-temperature correction."""
    validate_bartz_inputs(reference, station)
    wall_temperature_K = _require_positive(wall_temperature_K, "gas-side wall temperature")
    compressibility = 1.0 + 0.5 * (station.gamma - 1.0) * station.Mach**2
    return (
        0.5 * (wall_temperature_K / reference.temperature_K) * compressibility + 0.5
    ) ** -0.68 * compressibility**-0.12


def heat_transfer_coefficient_W_m2K(
    reference: BartzChamberReference,
    station: BartzStation,
    wall_temperature_K: float,
) -> float:
    """RPA 2012 basic Bartz coefficient without an empirical calibration multiplier."""
    correction = sigma(reference, station, wall_temperature_K)
    coefficient = (
        0.026
        / reference.throat_diameter_m**0.2
        * reference.viscosity_Pa_s**0.2
        * reference.cp_J_kgK
        / reference.Pr**0.6
        * (reference.pressure_Pa_abs / reference.c_star_m_s) ** 0.8
        * (reference.throat_diameter_m / reference.curvature_radius_m) ** 0.1
        * (1.0 / station.area_ratio_A_At) ** 0.9
        * correction
    )
    return _require_positive(coefficient, "Bartz heat-transfer coefficient")
