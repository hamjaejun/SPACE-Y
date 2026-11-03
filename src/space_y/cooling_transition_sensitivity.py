from __future__ import annotations

from dataclasses import dataclass
import math

from .coolant import CoolantStop, FrictionDomain
from .physics import (
    CorrelationDomain,
    colebrook_darcy_friction_factor,
    gnielinski_common_form,
    linear_transition_nusselt,
    rectangular_duct_aspect_ratio,
    rectangular_laminar_nusselt,
)


SENSITIVITY_MODEL_3000 = "SPACE_Y_SENSITIVITY_LINEAR_2300_3000"
SENSITIVITY_MODEL_4000 = "SPACE_Y_SENSITIVITY_LINEAR_2300_4000"
_SUPPORTED_UPPER_ENDPOINTS = {3000.0, 4000.0}


@dataclass(frozen=True)
class TransitionEndpointSensitivity:
    """Nusselt-only comparison result; never a production friction closure."""

    Reynolds: float
    Prandtl: float
    aspect_ratio: float
    upper_endpoint_Reynolds: float
    interpolation_fraction: float
    laminar_endpoint_Nusselt: float
    turbulent_endpoint_Nusselt: float
    Nusselt: float
    h_l_W_m2K: float
    thermal_boundary_condition: str
    correlation_id: str
    model_classification: str
    experimental_validation: bool
    darcy_factor: None
    friction_pressure_gradient_Pa_m: None
    friction_support_status: str


def evaluate_linear_transition_endpoint_sensitivity(
    *,
    reynolds: float,
    prandtl: float,
    width_m: float,
    height_m: float,
    hydraulic_diameter_m: float,
    conductivity_W_mK: float,
    equivalent_sand_roughness_m: float,
    closure_geometry: str,
    fully_developed: bool | None,
    thermal_boundary_condition: str,
    heat_transfer_domain: CorrelationDomain,
    friction_domain: FrictionDomain,
    upper_endpoint_reynolds: float,
) -> TransitionEndpointSensitivity:
    """Compare a 2300-to-3000/4000 linear Nusselt connection.

    The 3000 option reproduces the numerical construction used by production
    Model B.  The 4000 option is deliberately isolated as a sensitivity-only
    SPACE Y assumption.  Neither option supplies transition friction or an
    experimental-validation claim.
    """

    values = (
        reynolds,
        prandtl,
        width_m,
        height_m,
        hydraulic_diameter_m,
        conductivity_W_mK,
        equivalent_sand_roughness_m,
        upper_endpoint_reynolds,
    )
    if not all(math.isfinite(float(value)) for value in values):
        raise CoolantStop("INPUT_U", "sensitivity inputs must be finite")
    re_value = float(reynolds)
    pr_value = float(prandtl)
    diameter = float(hydraulic_diameter_m)
    conductivity = float(conductivity_W_mK)
    roughness = float(equivalent_sand_roughness_m)
    upper = float(upper_endpoint_reynolds)
    if min(re_value, pr_value, width_m, height_m, diameter, conductivity) <= 0:
        raise CoolantStop("INPUT_U", "sensitivity inputs must be positive")
    if roughness < 0:
        raise CoolantStop("INPUT_U", "roughness must be non-negative")
    if upper not in _SUPPORTED_UPPER_ENDPOINTS:
        raise CoolantStop(
            "INPUT_U", "upper endpoint must be the explicit 3000 or 4000 sensitivity option"
        )
    if not 2300.0 <= re_value <= upper:
        raise CoolantStop("OUTSIDE", "Reynolds number lies outside sensitivity interval")
    if fully_developed is None:
        raise CoolantStop("INPUT_U", "fully developed condition is unconfirmed")
    if fully_developed is not True:
        raise CoolantStop("OUTSIDE", "sensitivity model requires fully developed flow")
    if closure_geometry != heat_transfer_domain.geometry:
        raise CoolantStop("OUTSIDE", "geometry differs from heat-transfer domain")
    if closure_geometry != friction_domain.geometry:
        raise CoolantStop("OUTSIDE", "geometry differs from friction domain")
    if not heat_transfer_domain.re_min <= upper <= heat_transfer_domain.re_max:
        raise CoolantStop("OUTSIDE", "upper endpoint lies outside heat-transfer domain")
    if not heat_transfer_domain.pr_min <= pr_value <= heat_transfer_domain.pr_max:
        raise CoolantStop("OUTSIDE", "Prandtl number lies outside heat-transfer domain")
    if not friction_domain.re_min <= upper <= friction_domain.re_max:
        raise CoolantStop("OUTSIDE", "upper endpoint lies outside friction domain")

    relative_roughness = roughness / diameter
    if not (
        friction_domain.relative_roughness_min
        <= relative_roughness
        <= friction_domain.relative_roughness_max
    ):
        raise CoolantStop("OUTSIDE", "relative roughness lies outside friction domain")

    aspect_ratio = rectangular_duct_aspect_ratio(width_m, height_m)
    nu_t, nu_h1 = rectangular_laminar_nusselt(aspect_ratio)
    if thermal_boundary_condition == "CONSTANT_WALL_TEMPERATURE":
        laminar_endpoint = nu_t
    elif thermal_boundary_condition == "SHAH_LONDON_H1":
        laminar_endpoint = nu_h1
    else:
        raise CoolantStop(
            "INPUT_U",
            "thermal boundary condition must be CONSTANT_WALL_TEMPERATURE or SHAH_LONDON_H1",
        )

    endpoint_factor = colebrook_darcy_friction_factor(upper, relative_roughness)
    turbulent_endpoint, applicability = gnielinski_common_form(
        upper,
        pr_value,
        endpoint_factor,
        domain=heat_transfer_domain,
        geometry=closure_geometry,
        fully_developed=True,
    )
    if turbulent_endpoint is None:
        raise CoolantStop(
            applicability.status,
            "Gnielinski endpoint unavailable: " + "; ".join(applicability.reasons),
        )
    nu, gamma = linear_transition_nusselt(
        re_value,
        laminar_endpoint,
        turbulent_endpoint,
        reynolds_low=2300.0,
        reynolds_high=upper,
    )
    h_l = nu * conductivity / diameter
    if not all(math.isfinite(value) and value > 0 for value in (nu, h_l)):
        raise CoolantStop("NUM_U", "sensitivity result is not finite and positive")

    model_id = SENSITIVITY_MODEL_3000 if upper == 3000.0 else SENSITIVITY_MODEL_4000
    return TransitionEndpointSensitivity(
        Reynolds=re_value,
        Prandtl=pr_value,
        aspect_ratio=aspect_ratio,
        upper_endpoint_Reynolds=upper,
        interpolation_fraction=gamma,
        laminar_endpoint_Nusselt=laminar_endpoint,
        turbulent_endpoint_Nusselt=turbulent_endpoint,
        Nusselt=nu,
        h_l_W_m2K=h_l,
        thermal_boundary_condition=thermal_boundary_condition,
        correlation_id=model_id,
        model_classification="SENSITIVITY_ONLY_SPACE_Y_INTERNAL_EXTENSION",
        experimental_validation=False,
        darcy_factor=None,
        friction_pressure_gradient_Pa_m=None,
        friction_support_status="OUTSIDE_TRANSITION_FRICTION_UNSUPPORTED",
    )
