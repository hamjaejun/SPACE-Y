from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any

from .m0 import FluidState
from .physics import (
    CorrelationDomain,
    colebrook_darcy_friction_factor,
    darcy_weisbach_pressure_drop_Pa,
    gnielinski_common_form,
    prandtl_number,
    reynolds_number,
)


class CoolantStop(RuntimeError):
    def __init__(self, status: str, message: str):
        super().__init__(message)
        self.status = status


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
    fully_developed: bool
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


def _positive(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number) or number <= 0:
        raise CoolantStop("INPUT_U", f"{name} must be finite and positive")
    return number


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
    if not isinstance(geometry, str) or not geometry.strip():
        raise CoolantStop("INPUT_U", "coolant heat-transfer closure geometry is required")
    return CorrelationDomain(
        source=source,
        re_min=re_min,
        re_max=re_max,
        pr_min=pr_min,
        pr_max=pr_max,
        geometry=geometry,
        fully_developed_required=bool(spec["fully_developed_required"]),
    )


def build_friction_domain(spec: dict[str, Any]) -> FrictionDomain:
    source = spec.get("source")
    if not isinstance(source, str) or not source.strip():
        raise CoolantStop("INPUT_U", "friction closure source is required")
    re_min, re_max = map(float, spec["re_range"])
    rr_min, rr_max = map(float, spec["relative_roughness_range"])
    if re_min <= 0 or re_max <= re_min or rr_min < 0 or rr_max < rr_min:
        raise CoolantStop("INPUT_U", "invalid friction closure domain")
    return FrictionDomain(source, re_min, re_max, rr_min, rr_max, str(spec["geometry"]))


def evaluate_local_coolant_closures(
    *,
    state: FluidState,
    mass_flow_kg_s: float,
    geometry: CoolantGeometry,
    heat_transfer_domain: CorrelationDomain,
    heat_transfer_thermal_boundary_condition: str,
    friction_domain: FrictionDomain,
) -> CoolantLocalClosure:
    mass_flow = _positive(mass_flow_kg_s, "mass flow")
    if geometry.thermal_boundary_condition != heat_transfer_thermal_boundary_condition:
        raise CoolantStop("OUTSIDE", "thermal boundary condition differs from supplied Gnielinski domain")
    if geometry.closure_geometry != friction_domain.geometry:
        raise CoolantStop("OUTSIDE", "geometry differs from supplied friction domain")
    if geometry.equivalent_sand_roughness_m < 0 or geometry.local_loss_coefficient < 0:
        raise CoolantStop("INPUT_U", "roughness and local-loss coefficient must be non-negative")

    re = reynolds_number(
        mass_flow,
        _positive(geometry.flow_area_m2, "flow area"),
        _positive(geometry.hydraulic_diameter_m, "hydraulic diameter"),
        state.viscosity_Pa_s,
    )
    pr = prandtl_number(state.cp_J_kgK, state.viscosity_Pa_s, state.conductivity_W_mK)
    relative_roughness = geometry.equivalent_sand_roughness_m / geometry.hydraulic_diameter_m
    friction_reasons = []
    if not friction_domain.re_min <= re <= friction_domain.re_max:
        friction_reasons.append("Re outside supplied Colebrook domain")
    if not friction_domain.relative_roughness_min <= relative_roughness <= friction_domain.relative_roughness_max:
        friction_reasons.append("relative roughness outside supplied Colebrook domain")
    if friction_reasons:
        raise CoolantStop("OUTSIDE", "; ".join(friction_reasons))

    factor = colebrook_darcy_friction_factor(re, relative_roughness)
    nu, heat_applicability = gnielinski_common_form(
        re,
        pr,
        factor,
        domain=heat_transfer_domain,
        geometry=geometry.closure_geometry,
        fully_developed=geometry.fully_developed,
    )
    if nu is None:
        raise CoolantStop(heat_applicability.status, "; ".join(heat_applicability.reasons))
    if not math.isfinite(nu) or nu <= 0:
        raise CoolantStop("OUTSIDE", "Gnielinski displayed form returned non-positive Nusselt number")
    h_c = nu * state.conductivity_W_mK / geometry.hydraulic_diameter_m
    velocity = mass_flow / (state.density_kg_m3 * geometry.flow_area_m2)
    pressure_drop = darcy_weisbach_pressure_drop_Pa(
        factor,
        _positive(geometry.length_m, "segment length"),
        geometry.hydraulic_diameter_m,
        state.density_kg_m3,
        velocity,
        geometry.local_loss_coefficient,
    )
    return CoolantLocalClosure(
        Reynolds=re,
        Prandtl=pr,
        darcy_factor=factor,
        Nusselt=nu,
        h_c_W_m2K=h_c,
        velocity_m_s=velocity,
        pressure_drop_Pa=pressure_drop,
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
    )
