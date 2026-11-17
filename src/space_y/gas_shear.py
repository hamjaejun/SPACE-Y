from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

from .liquid_film import CouetteFilmState, couette_film_state


LOCAL_FLAT_PLATE_CORRELATION_ID = "SCHLICHTING_LOCAL_TURBULENT_FLAT_PLATE_1_5_POWER"
LOCAL_FLAT_PLATE_SOURCE = (
    "NASA CR-134814, Theoretical Skin Friction Estimates (1975), Eq. (4), "
    "after Schlichting Eq. 21.12"
)
LOCAL_FLAT_PLATE_SOURCE_URL = "https://ntrs.nasa.gov/citations/19750007533"
INCOMPRESSIBLE_GATE_SOURCE = (
    "NASA TM-85893: density variation is negligible at approximately Mach < 0.3"
)
INCOMPRESSIBLE_GATE_SOURCE_URL = "https://ntrs.nasa.gov/citations/19840015537"

MIN_REYNOLDS_X = 5.0e5
MAX_REYNOLDS_X = 1.0e7
MAX_INCOMPRESSIBLE_MACH = 0.3


class GasShearStop(RuntimeError):
    def __init__(self, status: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class LocalGasFilmShear:
    reynolds_x: float
    local_skin_friction_coefficient: float
    relative_velocity_m_s: float
    gas_shear_stress_Pa: float
    shear_direction: str
    boundary_layer_development_length_m: float
    boundary_layer_origin_source: str
    film_injection_coordinate_source: str
    mach: float
    correlation_id: str
    correlation_source: str
    correlation_domain_status: str
    compressibility_status: str
    pressure_gradient_status: str
    liquid_interface_status: str
    applicability_status: str
    assumptions: tuple[str, ...]
    experimental_validation: bool


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError) as exc:
        raise GasShearStop("INPUT_U", f"{name} must be numeric") from exc
    if not math.isfinite(number):
        raise GasShearStop("INPUT_U", f"{name} must be finite")
    return number


def _positive(value: Any, name: str) -> float:
    number = _finite(value, name)
    if number <= 0.0:
        raise GasShearStop("INPUT_U", f"{name} must be positive")
    return number


def _source(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise GasShearStop("INPUT_U", f"{name} source is required")
    return value.strip()


def local_turbulent_flat_plate_skin_friction(reynolds_x: float) -> float:
    """Return the local smooth-plate coefficient ``0.0592 Re_x**(-1/5)``.

    The source range is enforced rather than extrapolated.  This is a local
    coefficient, defined by ``C_f,x = tau / (0.5 rho U_rel**2)``; it is not the
    length-averaged drag coefficient ``0.074 Re_L**(-1/5)``.
    """

    reynolds = _positive(reynolds_x, "local Reynolds number")
    if not MIN_REYNOLDS_X <= reynolds <= MAX_REYNOLDS_X:
        raise GasShearStop(
            "OUTSIDE",
            "local Reynolds number is outside the cited 5e5 to 1e7 range",
            details={"reynolds_x": reynolds},
        )
    return 0.0592 * reynolds ** (-0.2)


def gas_shear_stress_from_local_coefficient(
    *, local_skin_friction_coefficient: float, density_kg_m3: float, relative_velocity_m_s: float
) -> float:
    coefficient = _positive(local_skin_friction_coefficient, "local skin-friction coefficient")
    density = _positive(density_kg_m3, "gas density")
    relative_velocity = _finite(relative_velocity_m_s, "gas-film relative velocity")
    if relative_velocity <= 0.0:
        raise GasShearStop(
            "OUTSIDE",
            "gas-film relative velocity must entrain the liquid film in the marching direction",
            details={"relative_velocity_m_s": relative_velocity},
        )
    return 0.5 * coefficient * density * relative_velocity**2


def evaluate_local_gas_film_shear(
    *,
    density_kg_m3: float,
    gas_velocity_m_s: float,
    gas_viscosity_Pa_s: float,
    mach: float,
    boundary_layer_development_length_m: float,
    boundary_layer_origin_source: str,
    film_injection_coordinate_source: str,
    film_surface_velocity_m_s: float = 0.0,
    pressure_gradient_mode: str = "UNKNOWN",
) -> LocalGasFilmShear:
    """Evaluate a guarded gas-to-liquid-film shear reference.

    The correlation is an incompressible, zero-pressure-gradient, smooth,
    fully turbulent flat-plate relation.  It is not asserted to be validated
    for a contracting/expanding rocket contour or a deformable liquid surface.
    The boundary-layer origin is an explicit independent input; it is never
    inferred from the liquid-film injection location.
    """

    density = _positive(density_kg_m3, "gas density")
    gas_velocity = _finite(gas_velocity_m_s, "gas velocity")
    interface_velocity = _finite(film_surface_velocity_m_s, "film surface velocity")
    viscosity = _positive(gas_viscosity_Pa_s, "gas viscosity")
    mach_number = _finite(mach, "gas Mach number")
    if mach_number < 0.0:
        raise GasShearStop("INPUT_U", "gas Mach number cannot be negative")
    length = _positive(boundary_layer_development_length_m, "boundary-layer development length")
    origin_source = _source(boundary_layer_origin_source, "boundary-layer origin")
    injection_source = _source(film_injection_coordinate_source, "film injection coordinate")

    gradient_mode = str(pressure_gradient_mode).strip().upper()
    if gradient_mode != "ZERO_PRESSURE_GRADIENT_REFERENCE":
        status = "INPUT_U" if gradient_mode in {"", "UNKNOWN"} else "OUTSIDE"
        raise GasShearStop(
            status,
            "the selected flat-plate shear relation requires a zero-pressure-gradient reference",
            details={"pressure_gradient_mode": gradient_mode},
        )
    if mach_number >= MAX_INCOMPRESSIBLE_MACH:
        raise GasShearStop(
            "OUTSIDE",
            "the uncorrected incompressible skin-friction relation is blocked at Mach >= 0.3",
            details={"mach": mach_number, "compressibility_correction": None},
        )

    relative_velocity = gas_velocity - interface_velocity
    if relative_velocity <= 0.0:
        raise GasShearStop(
            "OUTSIDE",
            "computed shear direction is incompatible with downstream Couette entrainment",
            details={
                "gas_velocity_m_s": gas_velocity,
                "film_surface_velocity_m_s": interface_velocity,
            },
        )
    reynolds = density * relative_velocity * length / viscosity
    coefficient = local_turbulent_flat_plate_skin_friction(reynolds)
    shear = gas_shear_stress_from_local_coefficient(
        local_skin_friction_coefficient=coefficient,
        density_kg_m3=density,
        relative_velocity_m_s=relative_velocity,
    )
    return LocalGasFilmShear(
        reynolds_x=reynolds,
        local_skin_friction_coefficient=coefficient,
        relative_velocity_m_s=relative_velocity,
        gas_shear_stress_Pa=shear,
        shear_direction="GAS_ENTRAINS_FILM_IN_FILM_MARCHING_DIRECTION",
        boundary_layer_development_length_m=length,
        boundary_layer_origin_source=origin_source,
        film_injection_coordinate_source=injection_source,
        mach=mach_number,
        correlation_id=LOCAL_FLAT_PLATE_CORRELATION_ID,
        correlation_source=LOCAL_FLAT_PLATE_SOURCE,
        correlation_domain_status="WITHIN_CITED_REYNOLDS_RANGE",
        compressibility_status="WITHIN_MACH_LT_0P3_REFERENCE_GATE",
        pressure_gradient_status="ZERO_PRESSURE_GRADIENT_REFERENCE_ASSUMED",
        liquid_interface_status="MODEL_U_MOVING_DEFORMABLE_INTERFACE_NOT_VALIDATED",
        applicability_status="MODEL_U",
        assumptions=(
            "smooth flat-plate-equivalent surface",
            "fully turbulent boundary layer from the supplied independent origin",
            "zero streamwise pressure gradient",
            "incompressible uncorrected local skin-friction coefficient",
            "relative velocity uses gas velocity minus liquid-interface velocity",
            "no curvature, blowing, injection-development, or film-wave correction",
        ),
        experimental_validation=False,
    )


def evaluate_couette_from_gas_state(
    *,
    film_mass_flow_kg_s: float,
    radius_m: float,
    coverage_ratio: float,
    film_viscosity_Pa_s: float,
    film_density_kg_m3: float,
    gas_density_kg_m3: float,
    gas_velocity_m_s: float,
    gas_viscosity_Pa_s: float,
    mach: float,
    boundary_layer_development_length_m: float,
    boundary_layer_origin_source: str,
    film_injection_coordinate_source: str,
    film_surface_velocity_m_s: float = 0.0,
    pressure_gradient_mode: str = "UNKNOWN",
) -> dict[str, Any]:
    """Connect the guarded gas-shear reference to the existing Couette model."""

    shear = evaluate_local_gas_film_shear(
        density_kg_m3=gas_density_kg_m3,
        gas_velocity_m_s=gas_velocity_m_s,
        gas_viscosity_Pa_s=gas_viscosity_Pa_s,
        mach=mach,
        boundary_layer_development_length_m=boundary_layer_development_length_m,
        boundary_layer_origin_source=boundary_layer_origin_source,
        film_injection_coordinate_source=film_injection_coordinate_source,
        film_surface_velocity_m_s=film_surface_velocity_m_s,
        pressure_gradient_mode=pressure_gradient_mode,
    )
    couette: CouetteFilmState = couette_film_state(
        film_mass_flow_kg_s=film_mass_flow_kg_s,
        radius_m=radius_m,
        coverage_ratio=coverage_ratio,
        viscosity_Pa_s=film_viscosity_Pa_s,
        density_kg_m3=film_density_kg_m3,
        gas_shear_stress_Pa=shear.gas_shear_stress_Pa,
    )
    return {
        "gas_shear": asdict(shear),
        "couette_film": asdict(couette),
        "status": "MODEL_U",
        "numerical_status": "PASS",
        "validated_engine_prediction": False,
    }


__all__ = [
    "GasShearStop",
    "INCOMPRESSIBLE_GATE_SOURCE",
    "INCOMPRESSIBLE_GATE_SOURCE_URL",
    "LOCAL_FLAT_PLATE_CORRELATION_ID",
    "LOCAL_FLAT_PLATE_SOURCE",
    "LOCAL_FLAT_PLATE_SOURCE_URL",
    "LocalGasFilmShear",
    "MAX_INCOMPRESSIBLE_MACH",
    "MAX_REYNOLDS_X",
    "MIN_REYNOLDS_X",
    "evaluate_couette_from_gas_state",
    "evaluate_local_gas_film_shear",
    "gas_shear_stress_from_local_coefficient",
    "local_turbulent_flat_plate_skin_friction",
]
