from __future__ import annotations

from dataclasses import asdict, dataclass, replace
import math
from typing import Any

from .cooling_applicability import (
    CoolingApplicabilityPolicy,
    assess_local_reynolds_prandtl,
)
from .m0 import FluidState
from .physics import (
    CorrelationDomain,
    colebrook_darcy_friction_factor,
    darcy_weisbach_pressure_drop_Pa,
    gnielinski_common_form,
    linear_transition_nusselt,
    prandtl_number,
    rectangular_duct_aspect_ratio,
    rectangular_laminar_darcy_friction_factor,
    rectangular_laminar_nusselt,
    reynolds_number,
)


class CoolantStop(RuntimeError):
    def __init__(
        self, status: str, message: str, *, details: dict[str, Any] | None = None
    ):
        super().__init__(message)
        self.status = status
        self.details = details


TRANSITION_HEAT_TRANSFER_MODEL_A = "MODEL_A_NULL_2300_3000"
TRANSITION_HEAT_TRANSFER_MODEL_B = "MODEL_B_LINEAR_2300_3000"
_TRANSITION_HEAT_TRANSFER_MODELS = {
    TRANSITION_HEAT_TRANSFER_MODEL_A,
    TRANSITION_HEAT_TRANSFER_MODEL_B,
}


def _snap_reynolds_dispatch_boundary(reynolds: float) -> float:
    """Remove floating-point noise at the two explicit dispatch boundaries."""

    for boundary in (2300.0, 3000.0):
        if math.isclose(reynolds, boundary, rel_tol=1.0e-12, abs_tol=1.0e-9):
            return boundary
    return reynolds


@dataclass(frozen=True)
class SinglePhaseBulkDomain:
    classification: str
    source: str
    temperature_min_K: float
    temperature_max_K: float
    pressure_min_Pa_abs: float
    pressure_max_Pa_abs: float


@dataclass(frozen=True)
class FrictionDomain:
    source: str
    re_min: float
    re_max: float
    relative_roughness_min: float
    relative_roughness_max: float
    geometry: str


@dataclass(frozen=True)
class CoolantGeometry:
    length_m: float
    flow_area_m2: float
    hydraulic_diameter_m: float
    coolant_wetted_area_m2: float
    equivalent_sand_roughness_m: float
    local_loss_coefficient: float
    closure_geometry: str
    fully_developed: bool | None
    thermal_boundary_condition: str


@dataclass(frozen=True)
class CoolantLocalClosure:
    Reynolds: float
    Prandtl: float
    darcy_factor: float
    Nusselt: float
    h_c_W_m2K: float
    velocity_m_s: float
    pressure_drop_Pa: float
    relative_roughness: float
    heat_transfer_applicability: dict[str, Any]
    friction_applicability: dict[str, Any]
    smooth_darcy_factor: float | None
    roughness_friction_ratio: float | None
    roughness_assessment: dict[str, Any]
    applicability_assessment: dict[str, Any]
    channel_aspect_ratio_h_over_w: float | None


@dataclass(frozen=True)
class CoolantHeatTransferClosure:
    """Local coolant-side heat-transfer result without a pressure-drop model.

    ``h_l_W_m2K`` is the liquid/coolant-side coefficient called
    ``h_c_W_m2K`` by the existing coupled M0 interface.
    """

    Reynolds: float
    Prandtl: float
    darcy_factor: float | None
    Nusselt: float
    h_l_W_m2K: float
    velocity_m_s: float | None
    friction_pressure_gradient_Pa_m: float | None
    relative_roughness: float
    heat_transfer_applicability: dict[str, Any]
    friction_applicability: dict[str, Any]
    smooth_darcy_factor: float | None
    roughness_friction_ratio: float | None
    roughness_assessment: dict[str, Any]
    applicability_assessment: dict[str, Any]
    channel_aspect_ratio_h_over_w: float | None


@dataclass(frozen=True)
class RectangularLaminarHeatTransfer:
    """Shah--London fully developed rectangular-duct reference closure."""

    Reynolds: float
    Prandtl: float
    aspect_ratio: float
    darcy_factor: float
    Nusselt_constant_wall_temperature: float
    Nusselt_H1: float
    h_l_constant_wall_temperature_W_m2K: float
    h_l_H1_W_m2K: float
    selected_Nusselt: float | None
    selected_h_l_W_m2K: float | None
    selected_thermal_boundary_condition: str | None
    velocity_m_s: float | None
    friction_pressure_gradient_Pa_m: float | None
    applicability_status: str
    correlation_ids: tuple[str, ...]
    assumptions: tuple[str, ...]


@dataclass(frozen=True)
class RectangularChannelCrossSection:
    width_m: float
    height_m: float
    channel_count: int
    channel_flow_area_m2: float
    total_flow_area_m2: float
    channel_wetted_perimeter_m: float
    hydraulic_diameter_m: float


@dataclass(frozen=True)
class RectangularChannelGeometry:
    """Derived geometry for ``N`` identical, closed rectangular channels.

    ``total_internal_wetted_area_m2`` is a hydraulic bookkeeping area.  It is
    deliberately kept separate from ``nominal_hot_wall_base_area_m2`` and
    must not be substituted for the effective heated area in the wall model.
    """

    width_m: float
    height_m: float
    channel_count: int
    length_m: float
    channel_flow_area_m2: float
    total_flow_area_m2: float
    channel_wetted_perimeter_m: float
    hydraulic_diameter_m: float
    total_internal_wetted_area_m2: float
    nominal_hot_wall_base_area_m2: float
    rib_thickness_m: float | None
    mean_radius_m: float | None
    circumferential_pitch_m: float | None
    layout_check_status: str


@dataclass(frozen=True)
class CoolantFlowAllocation:
    coolant_mass_flow_kg_s: float
    channel_count: int
    channel_mass_flow_kg_s: float
    fuel_mass_flow_kg_s: float | None
    regenerative_fraction_beta: float | None
    upstream_diversion_mass_flow_kg_s: float | None
    downstream_film_mass_flow_kg_s: float | None
    post_regen_to_injector_mass_flow_kg_s: float | None
    resolution_basis: str
    distribution_status: str
    branch_accounting_status: str
    consistency_residuals_kg_s: dict[str, float]


def _positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise CoolantStop("INPUT_U", f"{name} must be finite and positive")
    return number


def _optional_nonnegative(value: Any, name: str) -> float | None:
    if value is None:
        return None
    number = float(value)
    if not math.isfinite(number) or number < 0:
        raise CoolantStop("INPUT_U", f"{name} must be finite and non-negative")
    return number


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool):
        raise CoolantStop("INPUT_U", f"{name} must be a positive integer")
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise CoolantStop("INPUT_U", f"{name} must be a positive integer") from error
    if number <= 0 or float(value) != number:
        raise CoolantStop("INPUT_U", f"{name} must be a positive integer")
    return number


def _required_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise CoolantStop("INPUT_U", f"{name} must be an explicit boolean")
    return value


def _required_text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise CoolantStop("INPUT_U", f"{name} must be explicit")
    return value.strip()


def build_rectangular_channel_cross_section(
    spec: dict[str, Any],
) -> RectangularChannelCrossSection:
    """Build only the geometry required by a local rectangular-channel h_l model."""

    width = _positive(spec.get("width_m"), "channel width")
    height = _positive(spec.get("height_m"), "channel height")
    count = _positive_integer(spec.get("channel_count"), "channel count")
    channel_area = width * height
    perimeter = 2.0 * (width + height)
    return RectangularChannelCrossSection(
        width_m=width,
        height_m=height,
        channel_count=count,
        channel_flow_area_m2=channel_area,
        total_flow_area_m2=count * channel_area,
        channel_wetted_perimeter_m=perimeter,
        hydraulic_diameter_m=4.0 * channel_area / perimeter,
    )


def build_rectangular_channel_geometry(spec: dict[str, Any]) -> RectangularChannelGeometry:
    """Build the closed-duct area and hydraulic diameter without design defaults."""

    cross_section = build_rectangular_channel_cross_section(spec)
    width = cross_section.width_m
    height = cross_section.height_m
    count = cross_section.channel_count
    length = _positive(spec.get("length_m"), "channel length")
    rib = _optional_nonnegative(spec.get("rib_thickness_m"), "rib thickness")
    mean_radius = (
        None
        if spec.get("mean_radius_m") is None
        else _positive(spec.get("mean_radius_m"), "mean channel radius")
    )
    if (rib is None) != (mean_radius is None):
        raise CoolantStop(
            "INPUT_U",
            "rib thickness and mean radius must be supplied together for a layout check",
        )

    pitch = None
    layout_status = "INPUT_U"
    if rib is not None and mean_radius is not None:
        pitch = 2.0 * math.pi * mean_radius / count
        if width + rib > pitch:
            raise CoolantStop(
                "OUTSIDE",
                "channel width plus rib thickness exceeds the circumferential pitch",
            )
        layout_status = "GEOMETRIC_IDENTITY_CHECKED"

    return RectangularChannelGeometry(
        width_m=width,
        height_m=height,
        channel_count=count,
        length_m=length,
        channel_flow_area_m2=cross_section.channel_flow_area_m2,
        total_flow_area_m2=cross_section.total_flow_area_m2,
        channel_wetted_perimeter_m=cross_section.channel_wetted_perimeter_m,
        hydraulic_diameter_m=cross_section.hydraulic_diameter_m,
        total_internal_wetted_area_m2=(
            count * cross_section.channel_wetted_perimeter_m * length
        ),
        nominal_hot_wall_base_area_m2=count * width * length,
        rib_thickness_m=rib,
        mean_radius_m=mean_radius,
        circumferential_pitch_m=pitch,
        layout_check_status=layout_status,
    )


def resolve_coolant_flow(
    spec: dict[str, Any],
    *,
    channel_count: int,
    relative_tolerance: float = 1.0e-9,
) -> CoolantFlowAllocation:
    """Resolve the regenerative-path flow without inventing a split fraction.

    An upstream diversion changes the mass that enters the cooling channels.
    A downstream film split occurs after the regenerative path and therefore
    never reduces the mass flow used for the channel closure.
    """

    count = _positive_integer(channel_count, "channel count")
    if relative_tolerance <= 0 or not math.isfinite(relative_tolerance):
        raise CoolantStop("INPUT_U", "flow consistency tolerance must be positive")

    direct = (
        None
        if spec.get("coolant_mass_flow_kg_s") is None
        else _positive(spec.get("coolant_mass_flow_kg_s"), "coolant mass flow")
    )
    fuel = (
        None
        if spec.get("fuel_mass_flow_kg_s") is None
        else _positive(spec.get("fuel_mass_flow_kg_s"), "fuel mass flow")
    )
    beta = spec.get("regenerative_fraction_beta")
    if beta is not None:
        beta = float(beta)
        if not math.isfinite(beta) or not 0.0 < beta <= 1.0:
            raise CoolantStop("INPUT_U", "regenerative fraction beta must be in (0, 1]")
        if fuel is None:
            raise CoolantStop("INPUT_U", "fuel mass flow is required when beta is supplied")
    upstream = _optional_nonnegative(
        spec.get("upstream_diversion_mass_flow_kg_s"), "upstream diversion mass flow"
    )
    downstream = _optional_nonnegative(
        spec.get("downstream_film_mass_flow_kg_s"), "downstream film mass flow"
    )

    candidates: list[tuple[str, float]] = []
    if direct is not None:
        candidates.append(("DIRECT_COOLANT_MASS_FLOW", direct))
    if beta is not None and fuel is not None:
        candidates.append(("BETA_TIMES_FUEL_MASS_FLOW", beta * fuel))
    if upstream is not None:
        if fuel is None:
            raise CoolantStop(
                "INPUT_U", "fuel mass flow is required with an upstream diversion"
            )
        if upstream >= fuel:
            raise CoolantStop("INPUT_U", "upstream diversion must be less than fuel flow")
        candidates.append(("FUEL_MINUS_UPSTREAM_DIVERSION", fuel - upstream))
    if not candidates:
        raise CoolantStop(
            "INPUT_U", "direct coolant flow or a fuel-flow/beta definition is required"
        )

    coolant = candidates[0][1]
    residuals = {name: value - coolant for name, value in candidates}
    scale = max(abs(coolant), *(abs(value) for _, value in candidates), 1.0e-30)
    if any(abs(value) > relative_tolerance * scale for value in residuals.values()):
        raise CoolantStop(
            "INPUT_U", "direct, beta-based, and branch-based coolant flows are inconsistent"
        )

    post_regen = None
    if downstream is not None:
        if downstream > coolant:
            raise CoolantStop("INPUT_U", "downstream film split exceeds regenerative flow")
        post_regen = coolant - downstream

    # Missing branch values are not silently interpreted as zero.  A complete
    # audit therefore requires explicit upstream and downstream values,
    # including explicit zeros when no branch exists.
    branch_complete = fuel is not None and upstream is not None and downstream is not None

    return CoolantFlowAllocation(
        coolant_mass_flow_kg_s=coolant,
        channel_count=count,
        channel_mass_flow_kg_s=coolant / count,
        fuel_mass_flow_kg_s=fuel,
        regenerative_fraction_beta=beta,
        upstream_diversion_mass_flow_kg_s=upstream,
        downstream_film_mass_flow_kg_s=downstream,
        post_regen_to_injector_mass_flow_kg_s=post_regen,
        resolution_basis=" + ".join(name for name, _ in candidates),
        distribution_status="ASSUMED_EQUAL_DISTRIBUTION",
        branch_accounting_status="CHECKED" if branch_complete else "INPUT_U",
        consistency_residuals_kg_s=residuals,
    )


def build_single_phase_domain(spec: dict[str, Any]) -> SinglePhaseBulkDomain:
    source = spec.get("source")
    classification = spec.get("classification")
    if not isinstance(source, str) or not source.strip():
        raise CoolantStop("INPUT_U", "single-phase domain source is required")
    if not isinstance(classification, str) or not classification.strip():
        raise CoolantStop("INPUT_U", "single-phase classification is required")
    t_min, t_max = map(float, spec["temperature_range_K"])
    p_min, p_max = map(float, spec["pressure_range_Pa_abs"])
    if min(t_min, p_min) <= 0 or t_max <= t_min or p_max <= p_min:
        raise CoolantStop("INPUT_U", "invalid single-phase bulk-state domain")
    return SinglePhaseBulkDomain(classification, source, t_min, t_max, p_min, p_max)


def check_single_phase_bulk_state(
    domain: SinglePhaseBulkDomain, temperature_K: float, pressure_Pa_abs: float
) -> None:
    if not domain.temperature_min_K <= temperature_K <= domain.temperature_max_K:
        raise CoolantStop("OUTSIDE", "coolant bulk temperature is outside supplied single-phase domain")
    if not domain.pressure_min_Pa_abs <= pressure_Pa_abs <= domain.pressure_max_Pa_abs:
        raise CoolantStop("OUTSIDE", "coolant pressure is outside supplied single-phase domain")


def build_heat_transfer_domain(spec: dict[str, Any]) -> CorrelationDomain:
    source = spec.get("source")
    if not isinstance(source, str) or not source.strip():
        raise CoolantStop("INPUT_U", "coolant heat-transfer closure source is required")
    re_min, re_max = map(float, spec["re_range"])
    pr_min, pr_max = map(float, spec["pr_range"])
    geometry = spec.get("geometry")
    if re_min <= 0 or re_max <= re_min or pr_min <= 0 or pr_max <= pr_min:
        raise CoolantStop("INPUT_U", "invalid coolant heat-transfer closure domain")
    geometry = _required_text(geometry, "coolant heat-transfer closure geometry")
    return CorrelationDomain(
        source=source,
        re_min=re_min,
        re_max=re_max,
        pr_min=pr_min,
        pr_max=pr_max,
        geometry=geometry,
        fully_developed_required=_required_bool(
            spec.get("fully_developed_required"),
            "heat-transfer fully-developed requirement",
        ),
    )


def build_friction_domain(spec: dict[str, Any]) -> FrictionDomain:
    source = spec.get("source")
    if not isinstance(source, str) or not source.strip():
        raise CoolantStop("INPUT_U", "friction closure source is required")
    re_min, re_max = map(float, spec["re_range"])
    rr_min, rr_max = map(float, spec["relative_roughness_range"])
    if re_min <= 0 or re_max <= re_min or rr_min < 0 or rr_max < rr_min:
        raise CoolantStop("INPUT_U", "invalid friction closure domain")
    geometry = _required_text(spec.get("geometry"), "friction closure geometry")
    return FrictionDomain(source, re_min, re_max, rr_min, rr_max, geometry)


def evaluate_roughness_friction_ratio(
    *,
    reynolds: float,
    relative_roughness: float,
    friction_domain: FrictionDomain,
    warning_threshold: float | None,
) -> dict[str, Any]:
    """Compare Darcy factors at the same Re without clipping either result."""

    re_value = float(reynolds)
    roughness_value = float(relative_roughness)
    if (
        not math.isfinite(re_value)
        or re_value <= 0
        or not math.isfinite(roughness_value)
        or roughness_value < 0
    ):
        return {
            "status": "INPUT_U",
            "reason": "Reynolds number and relative roughness must be valid",
            "smooth_darcy_factor": None,
            "roughness_friction_ratio": None,
            "roughness_heat_transfer_warning_status": "NOT_EVALUATED",
            "arbitrary_friction_cap_applied": False,
            "arbitrary_heat_transfer_cap_applied": False,
        }
    if not friction_domain.re_min <= re_value <= friction_domain.re_max:
        return {
            "status": "OUTSIDE",
            "reason": "Re outside supplied Colebrook domain",
            "smooth_darcy_factor": None,
            "roughness_friction_ratio": None,
            "roughness_heat_transfer_warning_status": "NOT_EVALUATED",
            "arbitrary_friction_cap_applied": False,
            "arbitrary_heat_transfer_cap_applied": False,
        }
    if not (
        friction_domain.relative_roughness_min <= 0.0
        <= friction_domain.relative_roughness_max
        and friction_domain.relative_roughness_min
        <= roughness_value
        <= friction_domain.relative_roughness_max
    ):
        return {
            "status": "OUTSIDE",
            "reason": "rough or smooth reference lies outside supplied Colebrook domain",
            "smooth_darcy_factor": None,
            "roughness_friction_ratio": None,
            "roughness_heat_transfer_warning_status": "NOT_EVALUATED",
            "arbitrary_friction_cap_applied": False,
            "arbitrary_heat_transfer_cap_applied": False,
        }
    if warning_threshold is not None:
        threshold = float(warning_threshold)
        if not math.isfinite(threshold) or threshold <= 0:
            return {
                "status": "INPUT_U",
                "reason": "roughness warning threshold must be finite and positive",
                "smooth_darcy_factor": None,
                "roughness_friction_ratio": None,
                "roughness_heat_transfer_warning_status": "NOT_EVALUATED",
                "arbitrary_friction_cap_applied": False,
                "arbitrary_heat_transfer_cap_applied": False,
            }
    else:
        threshold = None
    try:
        rough_factor = colebrook_darcy_friction_factor(re_value, roughness_value)
        smooth_factor = colebrook_darcy_friction_factor(re_value, 0.0)
    except RuntimeError as error:
        return {
            "status": "NUM_U",
            "reason": str(error),
            "smooth_darcy_factor": None,
            "roughness_friction_ratio": None,
            "roughness_heat_transfer_warning_status": "NOT_EVALUATED",
            "arbitrary_friction_cap_applied": False,
            "arbitrary_heat_transfer_cap_applied": False,
        }
    if smooth_factor <= 0 or not math.isfinite(smooth_factor):
        return {
            "status": "NUM_U",
            "reason": "smooth Darcy factor is non-positive or non-finite",
            "smooth_darcy_factor": smooth_factor,
            "roughness_friction_ratio": None,
            "roughness_heat_transfer_warning_status": "NOT_EVALUATED",
            "arbitrary_friction_cap_applied": False,
            "arbitrary_heat_transfer_cap_applied": False,
        }
    ratio = rough_factor / smooth_factor
    if threshold is None:
        warning_status = "NOT_EVALUATED_NO_THRESHOLD"
    elif ratio >= threshold:
        warning_status = "ROUGHNESS_HEAT_TRANSFER_OVERPREDICTION_WARNING"
    else:
        warning_status = "BELOW_ROUGHNESS_WARNING_THRESHOLD"
    return {
        "status": "MODEL_U",
        "reason": "rough and smooth Darcy factors evaluated at identical Reynolds number",
        "darcy_factor_definition": "DARCY_WEISBACH_NOT_FANNING",
        "reynolds": re_value,
        "relative_roughness": roughness_value,
        "rough_darcy_factor": rough_factor,
        "smooth_darcy_factor": smooth_factor,
        "roughness_friction_ratio": ratio,
        "warning_threshold": threshold,
        "roughness_heat_transfer_warning_status": warning_status,
        "arbitrary_friction_cap_applied": False,
        "arbitrary_heat_transfer_cap_applied": False,
        "friction_domain_source": friction_domain.source,
    }


def evaluate_rectangular_laminar_heat_transfer(
    *,
    mass_flow_kg_s: float,
    flow_area_m2: float,
    hydraulic_diameter_m: float,
    width_m: float,
    height_m: float,
    viscosity_Pa_s: float,
    cp_J_kgK: float,
    conductivity_W_mK: float,
    fully_developed: bool | None,
    laminar_thermal_boundary_condition: str | None,
    density_kg_m3: float | None = None,
) -> RectangularLaminarHeatTransfer:
    """Evaluate Shah--London fully developed rectangular-duct references.

    Both thermal boundary-condition results are always retained.  A single
    representative value is selected only when the caller explicitly supplies
    ``CONSTANT_WALL_TEMPERATURE`` or ``SHAH_LONDON_H1``.  The H1 identifier is
    deliberately distinct from generic "uniform heat flux" wording.
    """

    mass_flow = _positive(mass_flow_kg_s, "mass flow")
    area = _positive(flow_area_m2, "flow area")
    hydraulic_diameter = _positive(hydraulic_diameter_m, "hydraulic diameter")
    viscosity = _positive(viscosity_Pa_s, "viscosity")
    conductivity = _positive(conductivity_W_mK, "thermal conductivity")
    aspect_ratio = rectangular_duct_aspect_ratio(width_m, height_m)
    re = reynolds_number(mass_flow, area, hydraulic_diameter, viscosity)
    if re >= 2300.0:
        raise CoolantStop(
            "OUTSIDE",
            "rectangular laminar model requires 0 < Re < 2300",
        )
    pr = prandtl_number(cp_J_kgK, viscosity, conductivity)
    nu_t, nu_h1 = rectangular_laminar_nusselt(aspect_ratio)
    factor = rectangular_laminar_darcy_friction_factor(re, aspect_ratio)
    h_t = nu_t * conductivity / hydraulic_diameter
    h_h1 = nu_h1 * conductivity / hydraulic_diameter

    if fully_developed is not None and not isinstance(fully_developed, bool):
        raise CoolantStop(
            "INPUT_U", "fully developed condition must be true, false, or unknown"
        )
    boundary = (
        None
        if laminar_thermal_boundary_condition is None
        else _required_text(
            laminar_thermal_boundary_condition,
            "laminar thermal boundary condition",
        )
    )
    selected_nu = None
    selected_h = None
    if boundary == "CONSTANT_WALL_TEMPERATURE":
        selected_nu = nu_t
        selected_h = h_t
    elif boundary == "SHAH_LONDON_H1":
        selected_nu = nu_h1
        selected_h = h_h1
    elif boundary is not None:
        raise CoolantStop(
            "INPUT_U",
            "laminar thermal boundary must be CONSTANT_WALL_TEMPERATURE or SHAH_LONDON_H1",
        )

    if fully_developed is False:
        status = "OUTSIDE_NOT_FULLY_DEVELOPED"
        selected_nu = None
        selected_h = None
    elif fully_developed is None:
        status = "REFERENCE_ONLY_FULLY_DEVELOPED_UNCONFIRMED"
    elif boundary is None:
        status = "INPUT_U_THERMAL_BOUNDARY_UNSELECTED"
    else:
        status = "MODEL_U_FULLY_DEVELOPED_ASSUMPTION"

    velocity = None
    pressure_gradient = None
    if density_kg_m3 is not None:
        density = _positive(density_kg_m3, "density")
        velocity = mass_flow / (density * area)
        pressure_gradient = (
            factor / hydraulic_diameter * density * velocity**2 / 2.0
        )

    return RectangularLaminarHeatTransfer(
        Reynolds=re,
        Prandtl=pr,
        aspect_ratio=aspect_ratio,
        darcy_factor=factor,
        Nusselt_constant_wall_temperature=nu_t,
        Nusselt_H1=nu_h1,
        h_l_constant_wall_temperature_W_m2K=h_t,
        h_l_H1_W_m2K=h_h1,
        selected_Nusselt=selected_nu,
        selected_h_l_W_m2K=selected_h,
        selected_thermal_boundary_condition=boundary,
        velocity_m_s=velocity,
        friction_pressure_gradient_Pa_m=pressure_gradient,
        applicability_status=status,
        correlation_ids=(
            "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_T",
            "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_H1",
            "SHAH_LONDON_1978_RECTANGULAR_DUCT_DARCY_FRICTION",
        ),
        assumptions=(
            "LAMINAR_0_LT_RE_LT_2300",
            "STRAIGHT_RECTANGULAR_DUCT",
            "FULLY_DEVELOPED_REFERENCE",
            "CONSTANT_PROPERTIES_AT_STATION",
            "ENTRANCE_EFFECTS_NOT_MODELLED",
        ),
    )


def _assess_laminar_against_policy(
    reynolds: float,
    prandtl: float,
    policy: CoolingApplicabilityPolicy | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "flow_regime": "LAMINAR",
        "selected_correlation_id": "SHAH_LONDON_1978_RECTANGULAR_DUCT",
        "correlation_applicability_status": (
            "WITHIN_RECTANGULAR_LAMINAR_MODEL_DOMAIN"
        ),
        "gnielinski_re_in_domain": False,
        "gnielinski_pr_in_domain": None,
        "transition_region_warning": False,
        "laminar_re_range": [0.0, 2300.0],
    }
    if policy is None:
        result.update(
            {
                "policy_id": None,
                "re_design_minimum": None,
                "re_design_margin": None,
                "re_design_recommendation_status": "NOT_EVALUATED",
                "correlation_source": "Shah & London (1978)",
                "design_recommendation_source": None,
            }
        )
        return result
    result.update(
        {
            "policy_id": policy.policy_id,
            "gnielinski_re_range": [
                policy.gnielinski_re_min,
                policy.gnielinski_re_max,
            ],
            "gnielinski_pr_range": [
                policy.gnielinski_pr_min,
                policy.gnielinski_pr_max,
            ],
            "re_design_minimum": policy.recommended_re_min,
            "re_design_margin": reynolds - policy.recommended_re_min,
            "re_design_recommendation_status": (
                "BELOW_PRELIMINARY_RE_RECOMMENDATION"
            ),
            "correlation_source": "Shah & London (1978)",
            "design_recommendation_source": policy.design_recommendation_source,
            "Prandtl": prandtl,
        }
    )
    return result


def _assess_transition_model_b_against_policy(
    reynolds: float,
    prandtl: float,
    policy: CoolingApplicabilityPolicy | None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "flow_regime": "TRANSITION_MODEL_B",
        "selected_correlation_id": "SPACE_Y_MODEL_B_LINEAR_2300_3000",
        "correlation_applicability_status": (
            "MODEL_U_SPACE_Y_FULLY_DEVELOPED_LINEAR_INTERPOLATION"
        ),
        "transition_region_warning": True,
        "transition_re_range": [2300.0, 3000.0],
        "experimental_validation_status": "NOT_RUN",
        "Prandtl": prandtl,
    }
    if policy is None:
        result.update(
            {
                "policy_id": None,
                "re_design_minimum": None,
                "re_design_margin": None,
                "re_design_recommendation_status": "NOT_EVALUATED",
                "correlation_source": (
                    "SPACE Y internal Model B using cited endpoint correlations"
                ),
                "design_recommendation_source": None,
            }
        )
        return result
    result.update(
        {
            "policy_id": policy.policy_id,
            "gnielinski_re_range": [
                policy.gnielinski_re_min,
                policy.gnielinski_re_max,
            ],
            "gnielinski_pr_range": [
                policy.gnielinski_pr_min,
                policy.gnielinski_pr_max,
            ],
            "re_design_minimum": policy.recommended_re_min,
            "re_design_margin": reynolds - policy.recommended_re_min,
            "re_design_recommendation_status": (
                "BELOW_PRELIMINARY_RE_RECOMMENDATION"
            ),
            "correlation_source": (
                "SPACE Y internal Model B using cited endpoint correlations"
            ),
            "design_recommendation_source": policy.design_recommendation_source,
        }
    )
    return result


def evaluate_rectangular_transition_heat_transfer_model_b(
    *,
    mass_flow_kg_s: float,
    flow_area_m2: float,
    hydraulic_diameter_m: float,
    width_m: float,
    height_m: float,
    viscosity_Pa_s: float,
    cp_J_kgK: float,
    conductivity_W_mK: float,
    equivalent_sand_roughness_m: float,
    closure_geometry: str,
    fully_developed: bool | None,
    thermal_boundary_condition: str,
    heat_transfer_domain: CorrelationDomain,
    heat_transfer_thermal_boundary_condition: str,
    friction_domain: FrictionDomain,
    density_kg_m3: float | None = None,
    applicability_policy: CoolingApplicabilityPolicy | None = None,
    laminar_thermal_boundary_condition: str | None = None,
) -> CoolantHeatTransferClosure:
    """Evaluate SPACE Y Model B in ``2300 <= Re < 3000``.

    Model B is an internal, fully developed, stationwise linear interpolation.
    It connects a Shah--London rectangular-duct laminar endpoint to the existing
    Gnielinski--Colebrook endpoint evaluated at ``Re=3000``.  It is not the VDI
    interpolation formula and has no experimental-validation claim.  Friction
    in the transition interval remains unsupported and is returned as null.
    """

    mass_flow = _positive(mass_flow_kg_s, "mass flow")
    area = _positive(flow_area_m2, "flow area")
    hydraulic_diameter = _positive(hydraulic_diameter_m, "hydraulic diameter")
    viscosity = _positive(viscosity_Pa_s, "viscosity")
    conductivity = _positive(conductivity_W_mK, "thermal conductivity")
    closure_geometry = _required_text(closure_geometry, "closure geometry")
    thermal_boundary_condition = _required_text(
        thermal_boundary_condition, "thermal boundary condition"
    )
    heat_transfer_thermal_boundary_condition = _required_text(
        heat_transfer_thermal_boundary_condition,
        "heat-transfer correlation thermal boundary condition",
    )
    if thermal_boundary_condition != heat_transfer_thermal_boundary_condition:
        raise CoolantStop(
            "OUTSIDE",
            "thermal boundary condition differs from supplied Gnielinski domain",
        )
    if closure_geometry != heat_transfer_domain.geometry:
        raise CoolantStop(
            "OUTSIDE", "geometry differs from supplied heat-transfer domain"
        )
    if closure_geometry != friction_domain.geometry:
        raise CoolantStop("OUTSIDE", "geometry differs from supplied friction domain")
    if fully_developed is None:
        raise CoolantStop(
            "INPUT_U",
            "fully developed condition is unconfirmed for transition Model B",
        )
    if not isinstance(fully_developed, bool):
        raise CoolantStop(
            "INPUT_U", "fully developed condition must be true, false, or unknown"
        )
    if not fully_developed:
        raise CoolantStop(
            "OUTSIDE",
            "transition Model B is defined only for the fully developed assumption",
        )

    re = reynolds_number(mass_flow, area, hydraulic_diameter, viscosity)
    re = _snap_reynolds_dispatch_boundary(re)
    if not 2300.0 <= re <= 3000.0:
        raise CoolantStop(
            "OUTSIDE", "transition Model B endpoint evaluator requires 2300 <= Re <= 3000"
        )
    pr = prandtl_number(cp_J_kgK, viscosity, conductivity)
    aspect_ratio = rectangular_duct_aspect_ratio(width_m, height_m)
    nu_t, nu_h1 = rectangular_laminar_nusselt(aspect_ratio)
    boundary = (
        None
        if laminar_thermal_boundary_condition is None
        else _required_text(
            laminar_thermal_boundary_condition,
            "laminar thermal boundary condition",
        )
    )
    if boundary == "CONSTANT_WALL_TEMPERATURE":
        laminar_endpoint = nu_t
        laminar_correlation_id = "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_T"
    elif boundary == "SHAH_LONDON_H1":
        laminar_endpoint = nu_h1
        laminar_correlation_id = "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_H1"
    else:
        raise CoolantStop(
            "INPUT_U",
            "transition Model B requires CONSTANT_WALL_TEMPERATURE or SHAH_LONDON_H1",
            details={
                "Nusselt_constant_wall_temperature": nu_t,
                "Nusselt_H1": nu_h1,
                "selected_thermal_boundary_condition": boundary,
            },
        )

    roughness = float(equivalent_sand_roughness_m)
    if not math.isfinite(roughness) or roughness < 0:
        raise CoolantStop("INPUT_U", "roughness must be finite and non-negative")
    relative_roughness = roughness / hydraulic_diameter
    if not (
        friction_domain.relative_roughness_min
        <= relative_roughness
        <= friction_domain.relative_roughness_max
    ):
        raise CoolantStop(
            "OUTSIDE", "relative roughness outside supplied Colebrook domain"
        )
    if not friction_domain.re_min <= 3000.0 <= friction_domain.re_max:
        raise CoolantStop(
            "OUTSIDE", "Re=3000 endpoint lies outside supplied Colebrook domain"
        )
    endpoint_factor = colebrook_darcy_friction_factor(3000.0, relative_roughness)
    turbulent_endpoint, endpoint_applicability = gnielinski_common_form(
        3000.0,
        pr,
        endpoint_factor,
        domain=heat_transfer_domain,
        geometry=closure_geometry,
        fully_developed=True,
    )
    if turbulent_endpoint is None:
        raise CoolantStop(
            endpoint_applicability.status,
            "transition Model B turbulent endpoint unavailable: "
            + "; ".join(endpoint_applicability.reasons),
        )
    if not math.isfinite(turbulent_endpoint) or turbulent_endpoint <= 0:
        raise CoolantStop(
            "OUTSIDE", "transition Model B turbulent endpoint is not positive"
        )
    nu, gamma = linear_transition_nusselt(
        re,
        laminar_endpoint,
        turbulent_endpoint,
    )
    h_l = nu * conductivity / hydraulic_diameter
    velocity = None
    if density_kg_m3 is not None:
        density = _positive(density_kg_m3, "density")
        velocity = mass_flow / (density * area)

    return CoolantHeatTransferClosure(
        Reynolds=re,
        Prandtl=pr,
        darcy_factor=None,
        Nusselt=nu,
        h_l_W_m2K=h_l,
        velocity_m_s=velocity,
        friction_pressure_gradient_Pa_m=None,
        relative_roughness=relative_roughness,
        heat_transfer_applicability={
            "status": "MODEL_U_SPACE_Y_FULLY_DEVELOPED_LINEAR_INTERPOLATION",
            "source": (
                "SPACE Y internal Model B; endpoint correlations from Shah & "
                "London (1978), Gnielinski (1976), and Colebrook (1939)"
            ),
            "correlation_id": "SPACE_Y_MODEL_B_LINEAR_2300_3000",
            "model_classification": "SPACE_Y_INTERNAL_EXTENSION_ASSUMPTION",
            "not_equivalent_to": "VDI_HEAT_ATLAS_TRANSITION_FORMULA",
            "thermal_boundary_condition": boundary,
            "fully_developed": True,
            "stationwise_quasi_local_application": True,
            "entrance_effects_modelled": False,
            "experimental_validation_status": "NOT_RUN",
            "gamma": gamma,
            "reynolds_endpoints": [2300.0, 3000.0],
            "laminar_endpoint_Nusselt": laminar_endpoint,
            "laminar_endpoint_correlation_id": laminar_correlation_id,
            "turbulent_endpoint_Nusselt": turbulent_endpoint,
            "turbulent_endpoint_darcy_factor_at_re_3000": endpoint_factor,
            "turbulent_endpoint_correlation_id": (
                "GNIELINSKI_COLEBROOK_AT_RE_3000"
            ),
            "Nusselt_constant_wall_temperature": nu_t,
            "Nusselt_H1": nu_h1,
        },
        friction_applicability={
            "status": "OUTSIDE_TRANSITION_FRICTION_UNSUPPORTED",
            "source": None,
            "correlation_id": None,
            "darcy_factor_definition": "DARCY_WEISBACH_NOT_FANNING",
            "reason": (
                "Model B interpolates Nusselt only; no transition friction "
                "correlation is selected"
            ),
            "colebrook_endpoint_use": (
                "RE_3000_NUSSELT_ENDPOINT_ONLY_NOT_LOCAL_FRICTION"
            ),
        },
        smooth_darcy_factor=None,
        roughness_friction_ratio=None,
        roughness_assessment={
            "status": "NOT_EVALUATED_TRANSITION_FRICTION_UNSUPPORTED",
            "reason": "no local transition friction factor was calculated",
            "arbitrary_friction_cap_applied": False,
            "arbitrary_heat_transfer_cap_applied": False,
        },
        applicability_assessment=_assess_transition_model_b_against_policy(
            re,
            pr,
            applicability_policy,
        ),
        channel_aspect_ratio_h_over_w=float(height_m) / float(width_m),
    )


def evaluate_local_coolant_heat_transfer(
    *,
    mass_flow_kg_s: float,
    flow_area_m2: float,
    hydraulic_diameter_m: float,
    viscosity_Pa_s: float,
    cp_J_kgK: float,
    conductivity_W_mK: float,
    equivalent_sand_roughness_m: float,
    closure_geometry: str,
    fully_developed: bool,
    thermal_boundary_condition: str,
    heat_transfer_domain: CorrelationDomain,
    heat_transfer_thermal_boundary_condition: str,
    friction_domain: FrictionDomain,
    density_kg_m3: float | None = None,
    applicability_policy: CoolingApplicabilityPolicy | None = None,
) -> CoolantHeatTransferClosure:
    """Evaluate local h_l without requiring p, T, h, length, or local losses."""

    mass_flow = _positive(mass_flow_kg_s, "mass flow")
    area = _positive(flow_area_m2, "flow area")
    hydraulic_diameter = _positive(hydraulic_diameter_m, "hydraulic diameter")
    fully_developed = _required_bool(fully_developed, "fully developed condition")
    closure_geometry = _required_text(closure_geometry, "closure geometry")
    thermal_boundary_condition = _required_text(
        thermal_boundary_condition, "thermal boundary condition"
    )
    heat_transfer_thermal_boundary_condition = _required_text(
        heat_transfer_thermal_boundary_condition,
        "heat-transfer correlation thermal boundary condition",
    )
    if thermal_boundary_condition != heat_transfer_thermal_boundary_condition:
        raise CoolantStop(
            "OUTSIDE", "thermal boundary condition differs from supplied Gnielinski domain"
        )
    if closure_geometry != friction_domain.geometry:
        raise CoolantStop("OUTSIDE", "geometry differs from supplied friction domain")
    roughness = float(equivalent_sand_roughness_m)
    if not math.isfinite(roughness) or roughness < 0:
        raise CoolantStop("INPUT_U", "roughness must be finite and non-negative")

    re = reynolds_number(mass_flow, area, hydraulic_diameter, viscosity_Pa_s)
    re = _snap_reynolds_dispatch_boundary(re)
    pr = prandtl_number(cp_J_kgK, viscosity_Pa_s, conductivity_W_mK)
    relative_roughness = roughness / hydraulic_diameter
    friction_reasons = []
    if not friction_domain.re_min <= re <= friction_domain.re_max:
        friction_reasons.append("Re outside supplied Colebrook domain")
    if not (
        friction_domain.relative_roughness_min
        <= relative_roughness
        <= friction_domain.relative_roughness_max
    ):
        friction_reasons.append("relative roughness outside supplied Colebrook domain")
    if friction_reasons:
        raise CoolantStop("OUTSIDE", "; ".join(friction_reasons))

    factor = colebrook_darcy_friction_factor(re, relative_roughness)
    warning_threshold = (
        applicability_policy.roughness_friction_ratio_warning_threshold
        if applicability_policy is not None
        and applicability_policy.compare_rough_to_smooth_friction
        else None
    )
    roughness_assessment = evaluate_roughness_friction_ratio(
        reynolds=re,
        relative_roughness=relative_roughness,
        friction_domain=friction_domain,
        warning_threshold=warning_threshold,
    )
    applicability_assessment = (
        assess_local_reynolds_prandtl(re, pr, applicability_policy)
        if applicability_policy is not None
        else {
            "correlation_applicability_status": "NOT_EVALUATED",
            "re_design_recommendation_status": "NOT_EVALUATED",
            "transition_region_warning": None,
        }
    )
    nu, heat_applicability = gnielinski_common_form(
        re,
        pr,
        factor,
        domain=heat_transfer_domain,
        geometry=closure_geometry,
        fully_developed=fully_developed,
    )
    if nu is None:
        raise CoolantStop(heat_applicability.status, "; ".join(heat_applicability.reasons))
    if not math.isfinite(nu) or nu <= 0:
        raise CoolantStop("OUTSIDE", "Gnielinski displayed form returned non-positive Nusselt number")

    velocity = None
    friction_pressure_gradient = None
    if density_kg_m3 is not None:
        density = _positive(density_kg_m3, "density")
        velocity = mass_flow / (density * area)
        friction_pressure_gradient = (
            factor / hydraulic_diameter * density * velocity**2 / 2.0
        )
    return CoolantHeatTransferClosure(
        Reynolds=re,
        Prandtl=pr,
        darcy_factor=factor,
        Nusselt=nu,
        h_l_W_m2K=nu * conductivity_W_mK / hydraulic_diameter,
        velocity_m_s=velocity,
        friction_pressure_gradient_Pa_m=friction_pressure_gradient,
        relative_roughness=relative_roughness,
        heat_transfer_applicability=asdict(heat_applicability),
        friction_applicability={
            "status": "APPLICABLE_TO_SUPPLIED_DOMAIN",
            "source": friction_domain.source,
            "re_range": [friction_domain.re_min, friction_domain.re_max],
            "relative_roughness_range": [
                friction_domain.relative_roughness_min,
                friction_domain.relative_roughness_max,
            ],
        },
        smooth_darcy_factor=roughness_assessment.get("smooth_darcy_factor"),
        roughness_friction_ratio=roughness_assessment.get(
            "roughness_friction_ratio"
        ),
        roughness_assessment=roughness_assessment,
        applicability_assessment=applicability_assessment,
        channel_aspect_ratio_h_over_w=None,
    )


def evaluate_rectangular_cooling_hl(
    *,
    coolant_mass_flow_kg_s: float,
    width_m: float,
    height_m: float,
    channel_count: int,
    viscosity_Pa_s: float,
    cp_J_kgK: float,
    conductivity_W_mK: float,
    equivalent_sand_roughness_m: float,
    closure_geometry: str,
    fully_developed: bool | None,
    thermal_boundary_condition: str,
    heat_transfer_domain: CorrelationDomain,
    heat_transfer_thermal_boundary_condition: str,
    friction_domain: FrictionDomain,
    density_kg_m3: float | None = None,
    applicability_policy: CoolingApplicabilityPolicy | None = None,
    laminar_thermal_boundary_condition: str | None = None,
    transition_heat_transfer_model: str = TRANSITION_HEAT_TRANSFER_MODEL_B,
) -> CoolantHeatTransferClosure:
    """Independent rectangular-channel h_l entry point with regime dispatch.

    ``Re < 2300`` uses the Shah--London rectangular laminar reference and
    ``Re >= 3000`` delegates to the pre-existing Colebrook--Gnielinski
    implementation.  The transition interval selects either preserved Model A
    (null) or the default SPACE Y Model B Nusselt-only interpolation.
    """

    cross_section = build_rectangular_channel_cross_section(
        {"width_m": width_m, "height_m": height_m, "channel_count": channel_count}
    )
    re = reynolds_number(
        coolant_mass_flow_kg_s,
        cross_section.total_flow_area_m2,
        cross_section.hydraulic_diameter_m,
        viscosity_Pa_s,
    )
    re = _snap_reynolds_dispatch_boundary(re)
    transition_model = _required_text(
        transition_heat_transfer_model, "transition heat-transfer model"
    )
    if transition_model not in _TRANSITION_HEAT_TRANSFER_MODELS:
        raise CoolantStop(
            "INPUT_U",
            "transition heat-transfer model must be MODEL_A_NULL_2300_3000 "
            "or MODEL_B_LINEAR_2300_3000",
        )
    boundary = laminar_thermal_boundary_condition
    if boundary is None and thermal_boundary_condition in {
        "CONSTANT_WALL_TEMPERATURE",
        "SHAH_LONDON_H1",
    }:
        boundary = thermal_boundary_condition
    if 2300.0 <= re < 3000.0:
        if transition_model == TRANSITION_HEAT_TRANSFER_MODEL_A:
            raise CoolantStop(
                "OUTSIDE",
                "2300 <= Re < 3000 is unresolved in preserved Model A",
                details={
                    "Reynolds": re,
                    "flow_regime": "TRANSITION_UNRESOLVED",
                    "transition_heat_transfer_model": transition_model,
                    "selected_correlation_id": None,
                    "transition_region_warning": True,
                    "Nusselt": None,
                    "h_l_W_m2K": None,
                },
            )
        return evaluate_rectangular_transition_heat_transfer_model_b(
            mass_flow_kg_s=coolant_mass_flow_kg_s,
            flow_area_m2=cross_section.total_flow_area_m2,
            hydraulic_diameter_m=cross_section.hydraulic_diameter_m,
            width_m=cross_section.width_m,
            height_m=cross_section.height_m,
            viscosity_Pa_s=viscosity_Pa_s,
            cp_J_kgK=cp_J_kgK,
            conductivity_W_mK=conductivity_W_mK,
            equivalent_sand_roughness_m=equivalent_sand_roughness_m,
            closure_geometry=closure_geometry,
            fully_developed=fully_developed,
            thermal_boundary_condition=thermal_boundary_condition,
            heat_transfer_domain=heat_transfer_domain,
            heat_transfer_thermal_boundary_condition=(
                heat_transfer_thermal_boundary_condition
            ),
            friction_domain=friction_domain,
            density_kg_m3=density_kg_m3,
            applicability_policy=applicability_policy,
            laminar_thermal_boundary_condition=boundary,
        )
    if re < 2300.0:
        laminar = evaluate_rectangular_laminar_heat_transfer(
            mass_flow_kg_s=coolant_mass_flow_kg_s,
            flow_area_m2=cross_section.total_flow_area_m2,
            hydraulic_diameter_m=cross_section.hydraulic_diameter_m,
            width_m=cross_section.width_m,
            height_m=cross_section.height_m,
            viscosity_Pa_s=viscosity_Pa_s,
            cp_J_kgK=cp_J_kgK,
            conductivity_W_mK=conductivity_W_mK,
            fully_developed=fully_developed,
            laminar_thermal_boundary_condition=boundary,
            density_kg_m3=density_kg_m3,
        )
        diagnostic = asdict(laminar)
        diagnostic.update(
            {
                "flow_regime": "LAMINAR",
                "Nusselt": laminar.selected_Nusselt,
                "h_l_W_m2K": laminar.selected_h_l_W_m2K,
                "selected_correlation_id": (
                    "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_T"
                    if boundary == "CONSTANT_WALL_TEMPERATURE"
                    else (
                        "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_H1"
                        if boundary == "SHAH_LONDON_H1"
                        else None
                    )
                ),
            }
        )
        if fully_developed is False:
            raise CoolantStop(
                "OUTSIDE",
                "Shah-London laminar reference is not approved when fully_developed=false",
                details=diagnostic,
            )
        if fully_developed is None:
            raise CoolantStop(
                "INPUT_U",
                "fully developed condition is unconfirmed; only reference values are available",
                details=diagnostic,
            )
        if laminar.selected_Nusselt is None or laminar.selected_h_l_W_m2K is None:
            raise CoolantStop(
                "INPUT_U",
                "laminar thermal boundary is unconfirmed; Nu_T and Nu_H1 are retained without a representative h_l",
                details=diagnostic,
            )
        roughness = float(equivalent_sand_roughness_m)
        if not math.isfinite(roughness) or roughness < 0:
            raise CoolantStop(
                "INPUT_U", "roughness must be finite and non-negative"
            )
        relative_roughness = roughness / cross_section.hydraulic_diameter_m
        selected_id = diagnostic["selected_correlation_id"]
        return CoolantHeatTransferClosure(
            Reynolds=laminar.Reynolds,
            Prandtl=laminar.Prandtl,
            darcy_factor=laminar.darcy_factor,
            Nusselt=laminar.selected_Nusselt,
            h_l_W_m2K=laminar.selected_h_l_W_m2K,
            velocity_m_s=laminar.velocity_m_s,
            friction_pressure_gradient_Pa_m=(
                laminar.friction_pressure_gradient_Pa_m
            ),
            relative_roughness=relative_roughness,
            heat_transfer_applicability={
                "status": laminar.applicability_status,
                "source": "Shah & London (1978), Laminar Flow Forced Convection in Ducts",
                "correlation_id": selected_id,
                "thermal_boundary_condition": boundary,
                "Nusselt_constant_wall_temperature": (
                    laminar.Nusselt_constant_wall_temperature
                ),
                "Nusselt_H1": laminar.Nusselt_H1,
                "h_l_constant_wall_temperature_W_m2K": (
                    laminar.h_l_constant_wall_temperature_W_m2K
                ),
                "h_l_H1_W_m2K": laminar.h_l_H1_W_m2K,
                "fully_developed": True,
                "entrance_effects_modelled": False,
                "assumptions": list(laminar.assumptions),
            },
            friction_applicability={
                "status": "MODEL_U_FULLY_DEVELOPED_ASSUMPTION",
                "source": "Shah & London (1978)",
                "correlation_id": (
                    "SHAH_LONDON_1978_RECTANGULAR_DUCT_DARCY_FRICTION"
                ),
                "darcy_factor_definition": "DARCY_WEISBACH_NOT_FANNING",
                "roughness_effect_included": False,
            },
            smooth_darcy_factor=None,
            roughness_friction_ratio=None,
            roughness_assessment={
                "status": "NOT_APPLICABLE_LAMINAR_BRANCH",
                "reason": (
                    "laminar rectangular-duct friction polynomial does not use Colebrook roughness"
                ),
                "roughness_heat_transfer_warning_status": (
                    "NOT_APPLICABLE_LAMINAR_BRANCH"
                ),
                "arbitrary_friction_cap_applied": False,
                "arbitrary_heat_transfer_cap_applied": False,
            },
            applicability_assessment=_assess_laminar_against_policy(
                laminar.Reynolds,
                laminar.Prandtl,
                applicability_policy,
            ),
            channel_aspect_ratio_h_over_w=(
                cross_section.height_m / cross_section.width_m
            ),
        )
    result = evaluate_local_coolant_heat_transfer(
        mass_flow_kg_s=coolant_mass_flow_kg_s,
        flow_area_m2=cross_section.total_flow_area_m2,
        hydraulic_diameter_m=cross_section.hydraulic_diameter_m,
        viscosity_Pa_s=viscosity_Pa_s,
        cp_J_kgK=cp_J_kgK,
        conductivity_W_mK=conductivity_W_mK,
        equivalent_sand_roughness_m=equivalent_sand_roughness_m,
        closure_geometry=closure_geometry,
        fully_developed=fully_developed,
        thermal_boundary_condition=thermal_boundary_condition,
        heat_transfer_domain=heat_transfer_domain,
        heat_transfer_thermal_boundary_condition=(
            heat_transfer_thermal_boundary_condition
        ),
        friction_domain=friction_domain,
        density_kg_m3=density_kg_m3,
        applicability_policy=applicability_policy,
    )
    return replace(
        result,
        channel_aspect_ratio_h_over_w=(
            cross_section.height_m / cross_section.width_m
        ),
    )


def evaluate_local_coolant_closures(
    *,
    state: FluidState,
    mass_flow_kg_s: float,
    geometry: CoolantGeometry,
    heat_transfer_domain: CorrelationDomain,
    heat_transfer_thermal_boundary_condition: str,
    friction_domain: FrictionDomain,
    applicability_policy: CoolingApplicabilityPolicy | None = None,
    rectangular_width_m: float | None = None,
    rectangular_height_m: float | None = None,
    rectangular_channel_count: int | None = None,
    laminar_thermal_boundary_condition: str | None = None,
    transition_heat_transfer_model: str = TRANSITION_HEAT_TRANSFER_MODEL_B,
) -> CoolantLocalClosure:
    if geometry.local_loss_coefficient < 0:
        raise CoolantStop("INPUT_U", "local-loss coefficient must be non-negative")
    rectangular_inputs = (
        rectangular_width_m,
        rectangular_height_m,
        rectangular_channel_count,
    )
    if any(value is not None for value in rectangular_inputs) and not all(
        value is not None for value in rectangular_inputs
    ):
        raise CoolantStop(
            "INPUT_U",
            "rectangular width, height, and channel count must be supplied together",
        )
    if all(value is not None for value in rectangular_inputs):
        heat_transfer = evaluate_rectangular_cooling_hl(
            coolant_mass_flow_kg_s=mass_flow_kg_s,
            width_m=float(rectangular_width_m),
            height_m=float(rectangular_height_m),
            channel_count=int(rectangular_channel_count),
            viscosity_Pa_s=state.viscosity_Pa_s,
            cp_J_kgK=state.cp_J_kgK,
            conductivity_W_mK=state.conductivity_W_mK,
            equivalent_sand_roughness_m=geometry.equivalent_sand_roughness_m,
            closure_geometry=geometry.closure_geometry,
            fully_developed=geometry.fully_developed,
            thermal_boundary_condition=geometry.thermal_boundary_condition,
            heat_transfer_domain=heat_transfer_domain,
            heat_transfer_thermal_boundary_condition=(
                heat_transfer_thermal_boundary_condition
            ),
            friction_domain=friction_domain,
            density_kg_m3=state.density_kg_m3,
            applicability_policy=applicability_policy,
            laminar_thermal_boundary_condition=(
                laminar_thermal_boundary_condition
            ),
            transition_heat_transfer_model=transition_heat_transfer_model,
        )
    else:
        heat_transfer = evaluate_local_coolant_heat_transfer(
            mass_flow_kg_s=mass_flow_kg_s,
            flow_area_m2=geometry.flow_area_m2,
            hydraulic_diameter_m=geometry.hydraulic_diameter_m,
            viscosity_Pa_s=state.viscosity_Pa_s,
            cp_J_kgK=state.cp_J_kgK,
            conductivity_W_mK=state.conductivity_W_mK,
            equivalent_sand_roughness_m=geometry.equivalent_sand_roughness_m,
            closure_geometry=geometry.closure_geometry,
            fully_developed=geometry.fully_developed,
            thermal_boundary_condition=geometry.thermal_boundary_condition,
            heat_transfer_domain=heat_transfer_domain,
            heat_transfer_thermal_boundary_condition=(
                heat_transfer_thermal_boundary_condition
            ),
            friction_domain=friction_domain,
            density_kg_m3=state.density_kg_m3,
            applicability_policy=applicability_policy,
        )
    if heat_transfer.velocity_m_s is None:  # density was supplied above
        raise RuntimeError("coolant velocity was not evaluated")
    if heat_transfer.darcy_factor is None:
        raise CoolantStop(
            "OUTSIDE",
            "coolant heat-transfer Model B returned h_l, but segment pressure "
            "drop remains unavailable because transition friction is unsupported",
            details={
                "heat_transfer_result": asdict(heat_transfer),
                "pressure_drop_Pa": None,
                "friction_status": (
                    heat_transfer.friction_applicability.get("status")
                ),
            },
        )
    pressure_drop = darcy_weisbach_pressure_drop_Pa(
        heat_transfer.darcy_factor,
        _positive(geometry.length_m, "segment length"),
        geometry.hydraulic_diameter_m,
        state.density_kg_m3,
        heat_transfer.velocity_m_s,
        geometry.local_loss_coefficient,
    )
    return CoolantLocalClosure(
        Reynolds=heat_transfer.Reynolds,
        Prandtl=heat_transfer.Prandtl,
        darcy_factor=heat_transfer.darcy_factor,
        Nusselt=heat_transfer.Nusselt,
        h_c_W_m2K=heat_transfer.h_l_W_m2K,
        velocity_m_s=heat_transfer.velocity_m_s,
        pressure_drop_Pa=pressure_drop,
        relative_roughness=heat_transfer.relative_roughness,
        heat_transfer_applicability=heat_transfer.heat_transfer_applicability,
        friction_applicability=heat_transfer.friction_applicability,
        smooth_darcy_factor=heat_transfer.smooth_darcy_factor,
        roughness_friction_ratio=heat_transfer.roughness_friction_ratio,
        roughness_assessment=heat_transfer.roughness_assessment,
        applicability_assessment=heat_transfer.applicability_assessment,
        channel_aspect_ratio_h_over_w=(
            heat_transfer.channel_aspect_ratio_h_over_w
        ),
    )
