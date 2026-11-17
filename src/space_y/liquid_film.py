from __future__ import annotations

from dataclasses import asdict, dataclass
import math
from typing import Any, Sequence

from .coolant import CoolantStop, resolve_coolant_flow


FILM_FRACTIONS = (0.05, 0.07, 0.09, 0.11, 0.13, 0.15)
COVERAGE_RATIOS = (1.0, 0.75, 0.50)
REGEN_GEOMETRY_MODES = ("AXIAL", "FIXED_HELICAL", "VARIABLE_HELICAL")
FILM_SCENARIOS = ("FILM_OFF", "SPLIT_WITHOUT_FILM_PROTECTION", "FILM_ON")

GIAMBELLI_SOURCE = (
    "Giambelli et al. (2023), Liquid Film Cooling: Open-Literature Review, "
    "Modeling and Efforts Towards Validation, DOI 10.13009/EUCASS2023-648"
)
COUETTE_CORRELATION_ID = "GIAMBELLI_2023_EQ17_COUETTE_REFERENCE"
STECHMAN_CORRELATION_ID = "STECHMAN_1969_AS_TRANSCRIBED_GIAMBELLI_2023_EQ6"
CONDUCTION_REFERENCE_ID = "SPACE_Y_FILM_THICKNESS_CONDUCTION_REFERENCE"


class FilmStop(RuntimeError):
    def __init__(
        self, status: str, message: str, *, details: dict[str, Any] | None = None
    ):
        super().__init__(message)
        self.status = status
        self.details = details or {}


@dataclass(frozen=True)
class FilmFlowSplit:
    total_fuel_mass_flow_kg_s: float
    film_fraction: float
    film_mass_flow_kg_s: float
    regenerative_mass_flow_kg_s: float
    channel_mass_flow_kg_s: float
    channel_count: int
    mass_residual_kg_s: float
    branch_accounting_status: str
    split_location: str


@dataclass(frozen=True)
class CouetteFilmState:
    line_mass_flow_kg_m_s: float
    thickness_m: float
    average_velocity_m_s: float
    surface_velocity_m_s: float
    gas_shear_stress_Pa: float
    correlation_id: str
    assumptions: tuple[str, ...]
    experimental_validation: bool


@dataclass(frozen=True)
class FilmEnergyStep:
    inlet_temperature_K: float
    outlet_temperature_K: float
    gas_to_film_heat_rate_W: float
    film_to_wall_heat_rate_W: float
    sensible_heat_rate_W: float
    energy_residual_W: float
    wetted_perimeter_m: float
    segment_length_m: float


@dataclass(frozen=True)
class FilmInjectionLocation:
    x_head_m: float
    x_film_injection_m: float
    head_coordinate_source: str
    film_injection_coordinate_source: str
    injection_mode: str
    upstream_boundary_mode: str


@dataclass(frozen=True)
class WetDryAreaPartition:
    total_gas_side_area_m2: float
    wet_gas_side_area_m2: float
    dry_gas_side_area_m2: float
    coverage_ratio: float
    area_residual_m2: float


def _finite(value: Any, name: str) -> float:
    number = float(value)
    if not math.isfinite(number):
        raise FilmStop("INPUT_U", f"{name} must be finite")
    return number


def _positive(value: Any, name: str) -> float:
    number = _finite(value, name)
    if number <= 0.0:
        raise FilmStop("INPUT_U", f"{name} must be positive")
    return number


def _fraction(value: Any, name: str, *, allow_zero: bool = False) -> float:
    number = _finite(value, name)
    lower_ok = number >= 0.0 if allow_zero else number > 0.0
    if not lower_ok or number > 1.0:
        bracket = "[0, 1]" if allow_zero else "(0, 1]"
        raise FilmStop("INPUT_U", f"{name} must be in {bracket}")
    return number


def resolve_upstream_film_split(
    *, total_fuel_mass_flow_kg_s: float, film_fraction: float, channel_count: int
) -> FilmFlowSplit:
    """Split film before the regenerative path using the existing flow audit.

    The public ``film_fraction`` is based on total fuel flow.  Internally the
    existing coolant allocation interface receives both the regenerative
    fraction and the explicit upstream diversion, so inconsistent branch
    bookkeeping cannot silently pass.
    """

    total = _positive(total_fuel_mass_flow_kg_s, "total fuel mass flow")
    fraction = _fraction(film_fraction, "film fraction", allow_zero=True)
    if fraction >= 1.0:
        raise FilmStop("INPUT_U", "film fraction must leave positive regenerative flow")
    if isinstance(channel_count, bool) or int(channel_count) != channel_count or channel_count <= 0:
        raise FilmStop("INPUT_U", "channel count must be a positive integer")
    count = int(channel_count)
    film = fraction * total
    regenerative = total - film
    try:
        allocation = resolve_coolant_flow(
            {
                "coolant_mass_flow_kg_s": regenerative,
                "fuel_mass_flow_kg_s": total,
                "regenerative_fraction_beta": 1.0 - fraction,
                "upstream_diversion_mass_flow_kg_s": film,
                "downstream_film_mass_flow_kg_s": 0.0,
            },
            channel_count=count,
        )
    except CoolantStop as error:
        raise FilmStop(error.status, str(error), details=error.details) from error
    residual = total - film - allocation.coolant_mass_flow_kg_s
    return FilmFlowSplit(
        total_fuel_mass_flow_kg_s=total,
        film_fraction=fraction,
        film_mass_flow_kg_s=film,
        regenerative_mass_flow_kg_s=allocation.coolant_mass_flow_kg_s,
        channel_mass_flow_kg_s=allocation.channel_mass_flow_kg_s,
        channel_count=count,
        mass_residual_kg_s=residual,
        branch_accounting_status=allocation.branch_accounting_status,
        split_location="UPSTREAM_OF_REGENERATIVE_PATH",
    )


def core_oxidizer_fuel_ratio(
    *, oxidizer_mass_flow_kg_s: float, split: FilmFlowSplit
) -> float:
    oxidizer = _positive(oxidizer_mass_flow_kg_s, "oxidizer mass flow")
    return oxidizer / split.regenerative_mass_flow_kg_s


def build_film_case_matrix(
    geometry_modes: Sequence[str] = REGEN_GEOMETRY_MODES,
    film_fractions: Sequence[float] = FILM_FRACTIONS,
    coverage_ratios: Sequence[float] = COVERAGE_RATIOS,
) -> list[dict[str, Any]]:
    """Build the deterministic 3 x 6 x 3 Film-ON case matrix only."""

    modes = [str(item).strip().upper() for item in geometry_modes]
    if not modes or any(not item for item in modes) or len(set(modes)) != len(modes):
        raise FilmStop("INPUT_U", "geometry modes must be non-empty and unique")
    fractions = [_fraction(item, "film fraction") for item in film_fractions]
    coverages = [_fraction(item, "coverage ratio") for item in coverage_ratios]
    if len(set(fractions)) != len(fractions) or len(set(coverages)) != len(coverages):
        raise FilmStop("INPUT_U", "film fractions and coverage ratios must be unique")
    cases: list[dict[str, Any]] = []
    for mode in modes:
        for fraction in fractions:
            for coverage in coverages:
                cases.append(
                    {
                        "case_id": f"{mode}:F{round(fraction * 100):02d}:C{round(coverage * 100):03d}",
                        "regenerative_geometry_mode": mode,
                        "film_fraction": fraction,
                        "coverage_ratio": coverage,
                        "film_mode": "ON",
                        "calculation_status": "INPUT_U",
                        "validated_engine_prediction": False,
                    }
                )
    return cases


def film_off_baselines(
    geometry_modes: Sequence[str] = REGEN_GEOMETRY_MODES,
) -> list[dict[str, Any]]:
    return [
        {
            "case_id": f"{str(mode).strip().upper()}:FILM_OFF",
            "regenerative_geometry_mode": str(mode).strip().upper(),
            "film_fraction": 0.0,
            "coverage_ratio": None,
            "film_mode": "OFF",
            "calculation_status": "NOT_RUN",
            "validated_engine_prediction": False,
        }
        for mode in geometry_modes
    ]


def build_v1_comparison_matrix(
    geometry_modes: Sequence[str] = REGEN_GEOMETRY_MODES,
    film_fractions: Sequence[float] = FILM_FRACTIONS,
    coverage_ratios: Sequence[float] = COVERAGE_RATIOS,
) -> list[dict[str, Any]]:
    """Build Film OFF, split-only, and Film ON cases without ranking them."""

    off = film_off_baselines(geometry_modes)
    split_only: list[dict[str, Any]] = []
    for mode in geometry_modes:
        normalized = str(mode).strip().upper()
        for fraction in film_fractions:
            value = _fraction(fraction, "film fraction")
            split_only.append(
                {
                    "case_id": f"{normalized}:F{round(value * 100):02d}:SPLIT_NO_PROTECTION",
                    "regenerative_geometry_mode": normalized,
                    "film_fraction": value,
                    "coverage_ratio": None,
                    "film_mode": "SPLIT_WITHOUT_FILM_PROTECTION",
                    "gas_side_boundary": "EXISTING_DRY_WALL",
                    "interpretation": "flow-split isolation case; not a guaranteed worst case",
                    "calculation_status": "INPUT_U",
                    "validated_engine_prediction": False,
                }
            )
    return off + split_only + build_film_case_matrix(
        geometry_modes, film_fractions, coverage_ratios
    )


def resolve_film_injection_location(
    *,
    x_head_m: float,
    head_coordinate_source: str,
    x_film_injection_m: float | None = None,
    film_injection_coordinate_source: str | None = None,
) -> FilmInjectionLocation:
    """Keep the head coordinate distinct from direct wall-film injection.

    V1 defaults the two numerical coordinates to equality, but does not infer
    that outer main-injector fuel automatically attaches to the wall.
    """

    head = _finite(x_head_m, "head coordinate")
    if not isinstance(head_coordinate_source, str) or not head_coordinate_source.strip():
        raise FilmStop("INPUT_U", "head coordinate source is required")
    if x_film_injection_m is None:
        injection = head
        injection_source = "V1_ASSUMPTION_X_FILM_INJECTION_EQUALS_X_HEAD"
    else:
        injection = _finite(x_film_injection_m, "film injection coordinate")
        if not isinstance(film_injection_coordinate_source, str) or not film_injection_coordinate_source.strip():
            raise FilmStop("INPUT_U", "explicit film injection coordinate source is required")
        injection_source = film_injection_coordinate_source.strip()
    return FilmInjectionLocation(
        x_head_m=head,
        x_film_injection_m=injection,
        head_coordinate_source=head_coordinate_source.strip(),
        film_injection_coordinate_source=injection_source,
        injection_mode="DIRECT_LIQUID_IPA_FILM_ON_CHAMBER_WALL",
        upstream_boundary_mode="EXISTING_DRY_WALL_MODEL",
    )


def gas_side_segment_area_m2(
    *, x_left_m: float, radius_left_m: float, x_right_m: float, radius_right_m: float
) -> float:
    """Integrate ``2*pi*r ds`` over one piecewise-linear contour segment.

    The trapezoidal radius integral is exact for a conical-frustum segment and
    explicitly uses meridional distance instead of axial distance.
    """

    x_left = _finite(x_left_m, "left axial coordinate")
    x_right = _finite(x_right_m, "right axial coordinate")
    radius_left = _positive(radius_left_m, "left gas-side radius")
    radius_right = _positive(radius_right_m, "right gas-side radius")
    dx = x_right - x_left
    dr = radius_right - radius_left
    ds = math.hypot(dx, dr)
    if ds <= 0.0:
        raise FilmStop("INPUT_U", "adjacent gas-side contour stations must be distinct")
    return math.pi * (radius_left + radius_right) * ds


def gas_side_segment_areas_m2(
    x_m: Sequence[float], radius_m: Sequence[float]
) -> list[float]:
    if len(x_m) != len(radius_m) or len(x_m) < 2:
        raise FilmStop("INPUT_U", "gas-side contour arrays require equal length >= 2")
    return [
        gas_side_segment_area_m2(
            x_left_m=x_m[index],
            radius_left_m=radius_m[index],
            x_right_m=x_m[index + 1],
            radius_right_m=radius_m[index + 1],
        )
        for index in range(len(x_m) - 1)
    ]


def partition_wet_dry_gas_area(
    *, total_gas_side_area_m2: float, coverage_ratio: float
) -> WetDryAreaPartition:
    area = _positive(total_gas_side_area_m2, "gas-side station area")
    coverage = _fraction(coverage_ratio, "coverage ratio")
    wet = coverage * area
    dry = (1.0 - coverage) * area
    return WetDryAreaPartition(
        total_gas_side_area_m2=area,
        wet_gas_side_area_m2=wet,
        dry_gas_side_area_m2=dry,
        coverage_ratio=coverage,
        area_residual_m2=area - wet - dry,
    )


def audit_heat_transfer_area_bases(
    *,
    gas_side_area_m2: float,
    regenerative_coolant_area_m2: float,
    regenerative_geometry_mode: str,
) -> dict[str, Any]:
    """Record distinct hot-gas and coolant area bases without equating them."""

    gas_area = _positive(gas_side_area_m2, "gas-side area")
    coolant_area = _positive(regenerative_coolant_area_m2, "regenerative coolant area")
    mode = str(regenerative_geometry_mode).strip().upper()
    if mode not in REGEN_GEOMETRY_MODES:
        raise FilmStop("INPUT_U", "unknown regenerative geometry mode")
    return {
        "gas_side_area_m2": gas_area,
        "regenerative_coolant_area_m2": coolant_area,
        "regenerative_geometry_mode": mode,
        "areas_are_distinct_bases": True,
        "equality_required": False,
        "status": "CHECKED",
    }


def covered_wetted_perimeter_m(radius_m: float, coverage_ratio: float) -> float:
    radius = _positive(radius_m, "local gas-side radius")
    coverage = _fraction(coverage_ratio, "coverage ratio")
    return 2.0 * math.pi * radius * coverage


def film_line_mass_flow_kg_m_s(
    *, film_mass_flow_kg_s: float, radius_m: float, coverage_ratio: float
) -> float:
    mass_flow = _positive(film_mass_flow_kg_s, "film mass flow")
    return mass_flow / covered_wetted_perimeter_m(radius_m, coverage_ratio)


def couette_film_state(
    *,
    film_mass_flow_kg_s: float,
    radius_m: float,
    coverage_ratio: float,
    viscosity_Pa_s: float,
    density_kg_m3: float,
    gas_shear_stress_Pa: float,
) -> CouetteFilmState:
    """Evaluate the fully developed linear Couette reference from Eq. 17.

    Gas shear is deliberately an explicit input.  No fully developed pipe or
    flat-plate friction law is silently extended through a contracting or
    expanding rocket contour.
    """

    viscosity = _positive(viscosity_Pa_s, "film dynamic viscosity")
    density = _positive(density_kg_m3, "film density")
    shear = _positive(gas_shear_stress_Pa, "gas-film shear stress")
    line_flow = film_line_mass_flow_kg_m_s(
        film_mass_flow_kg_s=film_mass_flow_kg_s,
        radius_m=radius_m,
        coverage_ratio=coverage_ratio,
    )
    thickness = math.sqrt(2.0 * viscosity * line_flow / (density * shear))
    if not math.isfinite(thickness) or thickness <= 0.0:
        raise FilmStop("NUM_U", "Couette film thickness is non-finite or non-positive")
    surface_velocity = shear * thickness / viscosity
    average_velocity = 0.5 * surface_velocity
    recovered = density * average_velocity * thickness
    if not math.isclose(recovered, line_flow, rel_tol=1.0e-12, abs_tol=1.0e-15):
        raise FilmStop("NUM_U", "Couette mass-flow identity did not close")
    return CouetteFilmState(
        line_mass_flow_kg_m_s=line_flow,
        thickness_m=thickness,
        average_velocity_m_s=average_velocity,
        surface_velocity_m_s=surface_velocity,
        gas_shear_stress_Pa=shear,
        correlation_id=COUETTE_CORRELATION_ID,
        assumptions=(
            "steady one-dimensional film",
            "fully developed linear Couette velocity profile",
            "constant film mass flow; no evaporation or entrainment",
            "gas shear supplied by an independently applicable closure",
            "no pressure-gradient, inertia, curvature, or injection-development term",
        ),
        experimental_validation=False,
    )


def finite_volume_cell_centers_m(segment_lengths_m: Sequence[float]) -> list[float]:
    """Return positive distance from injection to each finite-volume center."""

    lengths = [_positive(value, f"segment length {index}") for index, value in enumerate(segment_lengths_m)]
    centers: list[float] = []
    left = 0.0
    for length in lengths:
        centers.append(left + 0.5 * length)
        left += length
    return centers


def film_wall_conduction_reference_W_m2K(
    *, conductivity_W_mK: float, thickness_m: float
) -> dict[str, Any]:
    conductivity = _positive(conductivity_W_mK, "film thermal conductivity")
    thickness = _positive(thickness_m, "film thickness")
    return {
        "h_film_wall_W_m2K": conductivity / thickness,
        "status": "MODEL_U",
        "correlation_id": CONDUCTION_REFERENCE_ID,
        "basis": "one-dimensional conduction across the computed film thickness",
        "stechman_equivalent": False,
        "experimental_validation": False,
    }


def stechman_film_wall_heat_transfer_W_m2K(**inputs: Any) -> float:
    """Block Eq. 6 until its original variable definitions and unit basis exist.

    Giambelli et al. transcribe the coefficient and variables, but the current
    project evidence does not establish the original Stechman unit system or
    traceable definitions/ranges for zeta, chi and g_s.  Returning a number
    here would therefore be an ungrounded correlation application.
    """

    raise FilmStop(
        "MODEL_U",
        "Stechman Eq. 6 is not enabled: original unit basis, zeta/chi/g_s definitions, "
        "and IPA applicability evidence are not available",
        details={
            "correlation_id": STECHMAN_CORRELATION_ID,
            "source": GIAMBELLI_SOURCE,
            "provided_inputs": sorted(inputs),
        },
    )


def wet_dry_area_average_heat_flux_W_m2(
    *, coverage_ratio: float, wet_heat_flux_W_m2: float, dry_heat_flux_W_m2: float
) -> float:
    coverage = _fraction(coverage_ratio, "coverage ratio")
    wet = _finite(wet_heat_flux_W_m2, "wet heat flux")
    dry = _finite(dry_heat_flux_W_m2, "dry heat flux")
    return coverage * wet + (1.0 - coverage) * dry


def wet_dry_heat_rate_W(
    *,
    total_area_m2: float,
    coverage_ratio: float,
    wet_heat_flux_W_m2: float,
    dry_heat_flux_W_m2: float,
) -> float:
    area = _positive(total_area_m2, "total station area")
    return area * wet_dry_area_average_heat_flux_W_m2(
        coverage_ratio=coverage_ratio,
        wet_heat_flux_W_m2=wet_heat_flux_W_m2,
        dry_heat_flux_W_m2=dry_heat_flux_W_m2,
    )


def reconcile_wet_dry_with_regenerative_heat_W(
    *,
    gas_side_area_m2: float,
    coverage_ratio: float,
    wet_heat_flux_W_m2: float,
    dry_heat_flux_W_m2: float,
    heat_rate_into_regenerative_coolant_W: float,
) -> dict[str, Any]:
    """Audit the station heat rate after wet and dry regions are solved separately."""

    partition = partition_wet_dry_gas_area(
        total_gas_side_area_m2=gas_side_area_m2,
        coverage_ratio=coverage_ratio,
    )
    wet_flux = _finite(wet_heat_flux_W_m2, "wet heat flux")
    dry_flux = _finite(dry_heat_flux_W_m2, "dry heat flux")
    coolant_rate = _finite(
        heat_rate_into_regenerative_coolant_W,
        "heat rate into regenerative coolant",
    )
    wet_rate = partition.wet_gas_side_area_m2 * wet_flux
    dry_rate = partition.dry_gas_side_area_m2 * dry_flux
    total = wet_rate + dry_rate
    return {
        "wet_heat_rate_W": wet_rate,
        "dry_heat_rate_W": dry_rate,
        "total_wall_heat_rate_W": total,
        "heat_rate_into_regenerative_coolant_W": coolant_rate,
        "energy_residual_W": total - coolant_rate,
        "area_partition": asdict(partition),
        "circumferential_wall_conduction_modelled": False,
        "three_dimensional_peak_wall_temperature_validated": False,
    }


def march_film_energy_step(
    *,
    inlet_temperature_K: float,
    film_mass_flow_kg_s: float,
    cp_J_kgK: float,
    radius_m: float,
    coverage_ratio: float,
    segment_length_m: float,
    gas_to_film_heat_flux_W_m2: float,
    film_to_wall_heat_flux_W_m2: float,
) -> FilmEnergyStep:
    inlet = _positive(inlet_temperature_K, "film inlet temperature")
    mass_flow = _positive(film_mass_flow_kg_s, "film mass flow")
    cp = _positive(cp_J_kgK, "film heat capacity")
    length = _positive(segment_length_m, "segment length")
    gas_flux = _finite(gas_to_film_heat_flux_W_m2, "gas-to-film heat flux")
    wall_flux = _finite(film_to_wall_heat_flux_W_m2, "film-to-wall heat flux")
    perimeter = covered_wetted_perimeter_m(radius_m, coverage_ratio)
    gas_rate = perimeter * length * gas_flux
    wall_rate = perimeter * length * wall_flux
    sensible_rate = gas_rate - wall_rate
    outlet = inlet + sensible_rate / (mass_flow * cp)
    if not math.isfinite(outlet) or outlet <= 0.0:
        raise FilmStop("NUM_U", "film energy step produced a non-physical temperature")
    residual = gas_rate - wall_rate - mass_flow * cp * (outlet - inlet)
    return FilmEnergyStep(
        inlet_temperature_K=inlet,
        outlet_temperature_K=outlet,
        gas_to_film_heat_rate_W=gas_rate,
        film_to_wall_heat_rate_W=wall_rate,
        sensible_heat_rate_W=sensible_rate,
        energy_residual_W=residual,
        wetted_perimeter_m=perimeter,
        segment_length_m=length,
    )


def assess_liquid_film_state(
    *,
    temperature_K: float,
    pressure_Pa_abs: float | None,
    property_temperature_range_K: tuple[float, float] | None,
    saturation_temperature_K: float | None,
    film_surface_temperature_K: float | None = None,
) -> dict[str, Any]:
    temperature = _positive(temperature_K, "film temperature")
    if pressure_Pa_abs is None:
        return {
            "status": "INPUT_U",
            "reason": "film pressure is required for a single-phase assessment",
            "single_phase_verified": False,
            "film_bulk_status": "INPUT_U",
            "film_surface_phase_status": "INPUT_U",
        }
    pressure = _positive(pressure_Pa_abs, "film pressure")
    if property_temperature_range_K is None:
        return {
            "status": "INPUT_U",
            "reason": "traceable IPA property temperature range is required",
            "pressure_Pa_abs": pressure,
            "single_phase_verified": False,
            "film_bulk_status": "INPUT_U",
            "film_surface_phase_status": "INPUT_U",
        }
    lower, upper = map(float, property_temperature_range_K)
    if not 0.0 < lower < upper:
        raise FilmStop("INPUT_U", "invalid property temperature range")
    if not lower <= temperature <= upper:
        return {
            "status": "OUTSIDE",
            "reason": "film temperature is outside the supplied property range",
            "pressure_Pa_abs": pressure,
            "single_phase_verified": False,
            "film_bulk_status": "OUTSIDE",
            "film_surface_phase_status": "INPUT_U",
        }
    if saturation_temperature_K is None:
        return {
            "status": "INPUT_U",
            "reason": "saturation temperature at film pressure is required",
            "pressure_Pa_abs": pressure,
            "single_phase_verified": False,
            "film_bulk_status": "INPUT_U",
            "film_surface_phase_status": "INPUT_U",
        }
    saturation = _positive(saturation_temperature_K, "saturation temperature")
    if temperature >= saturation:
        return {
            "status": "OUT_OF_MODEL_SCOPE",
            "reason": "film reached or exceeded saturation while phase change is excluded",
            "pressure_Pa_abs": pressure,
            "saturation_margin_K": saturation - temperature,
            "single_phase_verified": False,
            "film_bulk_status": "OUT_OF_MODEL_SCOPE",
            "film_surface_phase_status": "INPUT_U",
        }
    surface_status = "INPUT_U"
    surface_reason = "film surface temperature is not modelled"
    if film_surface_temperature_K is not None:
        surface = _positive(film_surface_temperature_K, "film surface temperature")
        surface_status = "OUT_OF_MODEL_SCOPE" if surface >= saturation else "MODEL_U"
        surface_reason = (
            "film surface reached or exceeded saturation"
            if surface_status == "OUT_OF_MODEL_SCOPE"
            else "film surface is subcooled at supplied pressure; evaporation remains unvalidated"
        )
    return {
        "status": "MODEL_U",
        "reason": "bulk film is subcooled within supplied bounds; surface phase status is separate",
        "pressure_Pa_abs": pressure,
        "saturation_margin_K": saturation - temperature,
        "single_phase_verified": False,
        "film_bulk_status": "MODEL_U",
        "film_surface_phase_status": surface_status,
        "film_surface_phase_reason": surface_reason,
    }


def assess_regenerative_coolant_phase(
    *,
    bulk_temperature_K: float,
    local_pressure_Pa_abs: float | None,
    coolant_side_wall_temperature_K: float | None,
    saturation_temperature_K: float | None,
    wall_onset_boiling_temperature_K: float | None = None,
) -> dict[str, Any]:
    """Separate bulk subcooling from wall-side boiling applicability.

    This does not create an ONB correlation.  Wall boiling remains INPUT_U
    unless an independently sourced onset temperature is supplied.
    """

    bulk = _positive(bulk_temperature_K, "bulk coolant temperature")
    if local_pressure_Pa_abs is None or saturation_temperature_K is None:
        return {
            "status": "INPUT_U",
            "bulk_phase_status": "INPUT_U",
            "wall_boiling_status": "INPUT_U",
            "reason": "local pressure and saturation temperature are required",
        }
    pressure = _positive(local_pressure_Pa_abs, "local coolant pressure")
    saturation = _positive(saturation_temperature_K, "saturation temperature")
    bulk_margin = saturation - bulk
    bulk_status = "MODEL_U" if bulk_margin > 0.0 else "OUT_OF_MODEL_SCOPE"
    wall_status = "INPUT_U"
    wall_margin = None
    wall_reason = "coolant-side wall temperature or ONB criterion is unavailable"
    if coolant_side_wall_temperature_K is not None and wall_onset_boiling_temperature_K is not None:
        wall = _positive(coolant_side_wall_temperature_K, "coolant-side wall temperature")
        onset = _positive(wall_onset_boiling_temperature_K, "wall onset-boiling temperature")
        wall_margin = onset - wall
        wall_status = "MODEL_U" if wall_margin > 0.0 else "OUT_OF_MODEL_SCOPE"
        wall_reason = (
            "wall remains below supplied ONB threshold"
            if wall_status == "MODEL_U"
            else "wall reached or exceeded supplied ONB threshold"
        )
    overall = "OUT_OF_MODEL_SCOPE" if "OUT_OF_MODEL_SCOPE" in (bulk_status, wall_status) else "MODEL_U"
    if wall_status == "INPUT_U":
        overall = "INPUT_U"
    return {
        "status": overall,
        "pressure_Pa_abs": pressure,
        "bulk_phase_status": bulk_status,
        "bulk_subcooling_margin_K": bulk_margin,
        "wall_boiling_status": wall_status,
        "wall_boiling_margin_K": wall_margin,
        "wall_boiling_reason": wall_reason,
        "single_phase_verified": False,
    }


def heat_transfer_model_sensitivity_interfaces() -> list[dict[str, Any]]:
    """Expose comparison slots without inventing a correction multiplier."""

    return [
        {
            "model_id": STECHMAN_CORRELATION_ID,
            "execution_status": "MODEL_U",
            "reason": "original unit basis and empirical-variable provenance are incomplete",
            "calibration_multiplier": None,
        },
        {
            "model_id": CONDUCTION_REFERENCE_ID,
            "execution_status": "MODEL_U",
            "reason": "reference-only h=k/delta; not experimentally equivalent to Stechman",
            "calibration_multiplier": None,
        },
    ]


def separate_thermal_and_propulsion_outputs(
    *, thermal_performance: dict[str, Any], propulsion_performance: dict[str, Any] | None
) -> dict[str, Any]:
    if not isinstance(thermal_performance, dict):
        raise FilmStop("INPUT_U", "thermal performance must be a mapping")
    if propulsion_performance is not None and not isinstance(propulsion_performance, dict):
        raise FilmStop("INPUT_U", "propulsion performance must be a mapping when supplied")
    return {
        "thermal_performance": dict(thermal_performance),
        "propulsion_performance": (
            {"status": "INPUT_U", "reason": "separate thermo-chemical/performance calculation required"}
            if propulsion_performance is None
            else dict(propulsion_performance)
        ),
        "automatic_optimum_selection_performed": False,
    }


def film_v1_readiness_record() -> dict[str, Any]:
    """Describe implemented arithmetic separately from blocked engine prediction."""

    return {
        "interface_version": "SPACE_Y_LIQUID_FILM_V1_M0_M1",
        "status": "INPUT_U",
        "implemented": [
            "upstream film/regenerative mass split using existing coolant allocation audit",
            "core O/F update arithmetic",
            "deterministic 54-case Film-ON matrix and three Film-OFF baselines",
            "fully developed Couette film thickness from explicit gas shear",
            "finite-volume cell-center distance without an s=0 singularity",
            "film-thickness conduction reference h=k/delta",
            "wet/dry area bookkeeping",
            "single finite-volume sensible-energy balance",
            "single-phase/property/saturation applicability gate",
            "distinct x_head and x_film_injection interface with dry upstream boundary",
            "curved gas-side segment area integration and wet/dry area partition",
            "separate regenerative bulk-subcooling and wall-boiling status",
            "Film OFF / split-without-protection / Film ON comparison matrix",
            "separate thermal and propulsion performance output namespaces",
        ],
        "blocked": [
            "Stechman Eq. 6 numerical use pending original unit basis and variable definitions",
            "gas-shear closure applicable to contracting/expanding engine stations",
            "real 2-propanol p,T properties and saturation interface",
            "coupled wet-film/wall/regenerative fixed-point solution",
            "case-specific CEA reruns at core O/F",
            "54-case engine execution and spatial-refinement evidence",
        ],
        "film_fractions": list(FILM_FRACTIONS),
        "coverage_ratios": list(COVERAGE_RATIOS),
        "geometry_modes": list(REGEN_GEOMETRY_MODES),
        "sources": {
            "couette": GIAMBELLI_SOURCE + ", Eq. 17",
            "stechman_transcription": GIAMBELLI_SOURCE + ", Eq. 6",
        },
        "physical_validation_v01_v12": "NOT_RUN",
        "validated_engine_prediction": False,
    }


def serialize_split(split: FilmFlowSplit) -> dict[str, Any]:
    return asdict(split)


__all__ = [
    "COVERAGE_RATIOS",
    "COUETTE_CORRELATION_ID",
    "FILM_FRACTIONS",
    "FILM_SCENARIOS",
    "FilmEnergyStep",
    "FilmFlowSplit",
    "FilmStop",
    "REGEN_GEOMETRY_MODES",
    "assess_liquid_film_state",
    "assess_regenerative_coolant_phase",
    "audit_heat_transfer_area_bases",
    "build_film_case_matrix",
    "build_v1_comparison_matrix",
    "core_oxidizer_fuel_ratio",
    "couette_film_state",
    "film_line_mass_flow_kg_m_s",
    "film_off_baselines",
    "film_v1_readiness_record",
    "film_wall_conduction_reference_W_m2K",
    "finite_volume_cell_centers_m",
    "gas_side_segment_area_m2",
    "gas_side_segment_areas_m2",
    "heat_transfer_model_sensitivity_interfaces",
    "march_film_energy_step",
    "resolve_upstream_film_split",
    "resolve_film_injection_location",
    "reconcile_wet_dry_with_regenerative_heat_W",
    "separate_thermal_and_propulsion_outputs",
    "stechman_film_wall_heat_transfer_W_m2K",
    "wet_dry_area_average_heat_flux_W_m2",
    "wet_dry_heat_rate_W",
    "partition_wet_dry_gas_area",
]
