from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any


@dataclass(frozen=True)
class Applicability:
    status: str
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class CorrelationDomain:
    source: str
    re_min: float
    re_max: float
    pr_min: float
    pr_max: float
    geometry: str
    fully_developed_required: bool


def total_gas_heat_rate(q_conv_W: float, q_rad_net_W: float) -> float:
    return q_conv_W + q_rad_net_W


def wall_energy_residual_W(
    q_g_total_W: float,
    q_coolant_W: float,
    q_loss_W: float,
    q_axial_net_W: float,
    dU_wall_dt_W: float,
) -> float:
    return q_g_total_W - q_coolant_W - q_loss_W + q_axial_net_W - dU_wall_dt_W


def coolant_outlet_enthalpy_J_kg(
    h_in_J_kg: float, q_coolant_W: float, mass_flow_kg_s: float
) -> float:
    if mass_flow_kg_s <= 0:
        raise ValueError("mass_flow_kg_s must be positive")
    return h_in_J_kg + q_coolant_W / mass_flow_kg_s


def hydraulic_diameter_m(flow_area_m2: float, wetted_perimeter_m: float) -> float:
    if flow_area_m2 <= 0 or wetted_perimeter_m <= 0:
        raise ValueError("area and wetted perimeter must be positive")
    return 4.0 * flow_area_m2 / wetted_perimeter_m


def reynolds_number(
    mass_flow_kg_s: float,
    flow_area_m2: float,
    hydraulic_diameter_m_value: float,
    viscosity_Pa_s: float,
) -> float:
    if min(mass_flow_kg_s, flow_area_m2, hydraulic_diameter_m_value, viscosity_Pa_s) <= 0:
        raise ValueError("Reynolds-number inputs must be positive")
    mass_flux = mass_flow_kg_s / flow_area_m2
    return mass_flux * hydraulic_diameter_m_value / viscosity_Pa_s


def prandtl_number(cp_J_kgK: float, viscosity_Pa_s: float, conductivity_W_mK: float) -> float:
    if min(cp_J_kgK, viscosity_Pa_s, conductivity_W_mK) <= 0:
        raise ValueError("Prandtl-number inputs must be positive")
    return cp_J_kgK * viscosity_Pa_s / conductivity_W_mK


def rectangular_duct_aspect_ratio(width_m: float, height_m: float) -> float:
    """Return Shah--London rectangular-duct aspect ratio in ``(0, 1]``."""

    width = float(width_m)
    height = float(height_m)
    if not math.isfinite(width) or not math.isfinite(height) or min(width, height) <= 0:
        raise ValueError("rectangular duct width and height must be finite and positive")
    return min(width, height) / max(width, height)


def rectangular_laminar_nusselt(
    aspect_ratio: float,
) -> tuple[float, float]:
    """Return fully developed rectangular-duct ``(Nu_T, Nu_H1)``.

    The polynomials are the Shah & London (1978) correlations requested for
    constant wall temperature (``T``) and the distinct ``H1`` thermal boundary
    condition.  The caller must not equate H1 with an arbitrary statement of
    uniform local wall heat flux.
    """

    alpha = float(aspect_ratio)
    if not math.isfinite(alpha) or not 0.0 < alpha <= 1.0:
        raise ValueError("rectangular duct aspect ratio must be in (0, 1]")
    nu_t = 7.541 * (
        1.0
        - 2.610 * alpha
        + 4.970 * alpha**2
        - 5.119 * alpha**3
        + 2.702 * alpha**4
        - 0.548 * alpha**5
    )
    nu_h1 = 8.235 * (
        1.0
        - 2.0421 * alpha
        + 3.0853 * alpha**2
        - 2.4765 * alpha**3
        + 1.0578 * alpha**4
        - 0.1861 * alpha**5
    )
    return nu_t, nu_h1


def rectangular_laminar_darcy_friction_factor(
    reynolds: float, aspect_ratio: float
) -> float:
    """Return the fully developed rectangular-duct Darcy friction factor.

    The Shah--London polynomial gives ``f_D Re``.  This function intentionally
    returns the Darcy--Weisbach factor, not the Fanning factor.
    """

    re_value = float(reynolds)
    alpha = float(aspect_ratio)
    if not math.isfinite(re_value) or re_value <= 0:
        raise ValueError("Reynolds number must be finite and positive")
    if not math.isfinite(alpha) or not 0.0 < alpha <= 1.0:
        raise ValueError("rectangular duct aspect ratio must be in (0, 1]")
    poiseuille = 96.0 * (
        1.0
        - 1.3553 * alpha
        + 1.9467 * alpha**2
        - 1.7012 * alpha**3
        + 0.9564 * alpha**4
        - 0.2537 * alpha**5
    )
    return poiseuille / re_value


def linear_transition_nusselt(
    reynolds: float,
    laminar_endpoint_nusselt: float,
    turbulent_endpoint_nusselt: float,
    *,
    reynolds_low: float = 2300.0,
    reynolds_high: float = 3000.0,
) -> tuple[float, float]:
    """Linearly connect two explicitly supplied Nusselt endpoints.

    This is a numerical interpolation primitive, not a literature correlation.
    SPACE Y Model B uses it only after evaluating the rectangular Shah--London
    endpoint at ``reynolds_low`` and the existing Gnielinski--Colebrook path at
    ``reynolds_high``.  The returned tuple is ``(Nusselt, gamma)``.
    """

    re_value = float(reynolds)
    re_low = float(reynolds_low)
    re_high = float(reynolds_high)
    nu_low = float(laminar_endpoint_nusselt)
    nu_high = float(turbulent_endpoint_nusselt)
    if not all(
        math.isfinite(value)
        for value in (re_value, re_low, re_high, nu_low, nu_high)
    ):
        raise ValueError("transition interpolation inputs must be finite")
    if re_high <= re_low:
        raise ValueError("transition Reynolds endpoints must be increasing")
    if not re_low <= re_value <= re_high:
        raise ValueError("Reynolds number lies outside interpolation endpoints")
    if min(nu_low, nu_high) <= 0:
        raise ValueError("transition Nusselt endpoints must be positive")
    gamma = (re_value - re_low) / (re_high - re_low)
    return (1.0 - gamma) * nu_low + gamma * nu_high, gamma


def colebrook_darcy_friction_factor(
    reynolds: float, relative_roughness: float, *, tolerance: float = 1e-12, max_iterations: int = 100
) -> float:
    """Solve the Darcy-form Colebrook equation for turbulent-flow checking.

    No transition or laminar fallback is applied. The caller must establish that
    this closure is applicable to the current segment.
    """
    if reynolds <= 0 or relative_roughness < 0:
        raise ValueError("reynolds must be positive and relative roughness non-negative")
    if tolerance <= 0 or max_iterations <= 0:
        raise ValueError("solver controls must be positive")
    factor = 0.02
    for _ in range(max_iterations):
        argument = relative_roughness / 3.7 + 2.51 / (reynolds * math.sqrt(factor))
        updated = 1.0 / (-2.0 * math.log10(argument)) ** 2
        if abs(updated - factor) <= tolerance * max(1.0, abs(updated)):
            return updated
        factor = updated
    raise RuntimeError("Colebrook iteration did not converge")


def colebrook_residual(factor: float, reynolds: float, relative_roughness: float) -> float:
    return 1.0 / math.sqrt(factor) + 2.0 * math.log10(
        relative_roughness / 3.7 + 2.51 / (reynolds * math.sqrt(factor))
    )


def darcy_weisbach_pressure_drop_Pa(
    darcy_factor: float,
    length_m: float,
    hydraulic_diameter_m_value: float,
    density_kg_m3: float,
    velocity_m_s: float,
    local_loss_coefficient: float = 0.0,
) -> float:
    if min(darcy_factor, length_m, hydraulic_diameter_m_value, density_kg_m3) <= 0:
        raise ValueError("Darcy-Weisbach inputs must be positive")
    if velocity_m_s < 0 or local_loss_coefficient < 0:
        raise ValueError("velocity and local-loss coefficient must be non-negative")
    return (
        darcy_factor * length_m / hydraulic_diameter_m_value + local_loss_coefficient
    ) * density_kg_m3 * velocity_m_s**2 / 2.0


def gnielinski_common_form(
    reynolds: float,
    prandtl: float,
    darcy_factor: float,
    *,
    domain: CorrelationDomain | None,
    geometry: str,
    fully_developed: bool | None,
) -> tuple[float | None, Applicability]:
    """Evaluate the v2.1 document's displayed Gnielinski form conditionally.

    The implementation intentionally supplies no built-in applicability limits.
    I18 must provide a cited domain before a numerical value is returned.
    """
    if domain is None or fully_developed is None:
        return None, Applicability("INPUT_U", ("I18 domain/development evidence missing",))
    reasons = []
    if not domain.re_min <= reynolds <= domain.re_max:
        reasons.append("Re outside supplied domain")
    if not domain.pr_min <= prandtl <= domain.pr_max:
        reasons.append("Pr outside supplied domain")
    if geometry != domain.geometry:
        reasons.append("geometry differs from supplied domain")
    if domain.fully_developed_required and not fully_developed:
        reasons.append("fully developed condition not met")
    if reasons:
        return None, Applicability("OUTSIDE", tuple(reasons))
    numerator = (darcy_factor / 8.0) * (reynolds - 1000.0) * prandtl
    denominator = 1.0 + 12.7 * math.sqrt(darcy_factor / 8.0) * (
        prandtl ** (2.0 / 3.0) - 1.0
    )
    return numerator / denominator, Applicability("APPLICABLE_TO_SUPPLIED_DOMAIN", ())


def run_basic_checks() -> dict[str, Any]:
    """Deterministic, non-design arithmetic checks for implemented equations."""
    checks: list[dict[str, Any]] = []

    q_total = total_gas_heat_rate(100.0, 20.0)
    checks.append({"id": "P01", "name": "gas heat sum", "pass": q_total == 120.0, "value": q_total})

    residual = wall_energy_residual_W(120.0, 100.0, 5.0, -5.0, 10.0)
    checks.append(
        {"id": "P02", "name": "wall energy identity", "pass": abs(residual) < 1e-12, "value": residual}
    )

    h_out = coolant_outlet_enthalpy_J_kg(200_000.0, 1_000.0, 2.0)
    checks.append(
        {"id": "P03", "name": "coolant enthalpy identity", "pass": h_out == 200_500.0, "value": h_out}
    )

    factor = colebrook_darcy_friction_factor(100_000.0, 1.0e-4)
    cb_residual = colebrook_residual(factor, 100_000.0, 1.0e-4)
    checks.append(
        {
            "id": "P04",
            "name": "Colebrook numerical residual",
            "pass": abs(cb_residual) < 1e-9,
            "value": factor,
            "residual": cb_residual,
        }
    )

    dp = darcy_weisbach_pressure_drop_Pa(0.02, 1.0, 0.01, 1_000.0, 2.0, 1.0)
    checks.append(
        {"id": "P05", "name": "Darcy-Weisbach arithmetic", "pass": dp == 6_000.0, "value": dp}
    )

    nu, applicability = gnielinski_common_form(
        100_000.0,
        1.2,
        factor,
        domain=None,
        geometry="circular_tube",
        fully_developed=True,
    )
    checks.append(
        {
            "id": "P06",
            "name": "Gnielinski missing-domain stop rule",
            "pass": nu is None and applicability.status == "INPUT_U",
            "value": nu,
            "applicability": asdict(applicability),
        }
    )
    return {
        "fixture_classification": "NON_DESIGN_ARITHMETIC_CHECK",
        "checks": checks,
        "status": "PASS" if all(check["pass"] for check in checks) else "FAIL",
        "physical_validation_v01_v12": "NOT_RUN",
        "validated_engine_prediction": False,
    }
