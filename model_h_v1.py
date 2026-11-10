"""SPACE Y Model H V1: 1D equivalent helical coolant-channel geometry.

Model H V1 extends the existing :mod:`cooling_model_b` calculation without
changing its Nusselt-number correlations or Reynolds dispatch rules.  A
helical rectangular channel is replaced station by station by an equivalent
straight duct whose cross-section is normal to the actual coolant direction.

Implemented geometry
--------------------

``w_H = 2*pi*r_m*cos(beta)/N - b``

``A_perp,H = N*w_H*h``

``D_h,H = 2*w_H*h/(w_H+h)``

``ds_H = ds_B/cos(beta)``

The resulting width, hydraulic diameter, total flow area, and actual helical
path distance are passed to the unchanged Model B heat-transfer evaluator.
The velocity is computed directly from ``mdot/(rho*A_perp,H)``; no additional
``1/cos(beta)`` velocity multiplier is applied.

The methodological context is Lv et al. (2023), DOI 10.1155/2023/8628107,
which treats small spiral-channel sections as equivalent straight passages.
This implementation intentionally does *not* copy that paper's Nusselt
correlation or equivalent-velocity correction.  It also does not model Dean
vortices, curvature-shifted transition Reynolds numbers, three-dimensional
secondary flow, pressure loss, or manufacturing feasibility.
"""

from __future__ import annotations

from copy import deepcopy
import csv
import json
import math
from pathlib import Path
from typing import Any, Sequence

from cooling_model_b import (
    CoolingModelBError,
    GNIELINSKI_PR_RANGE,
    GNIELINSKI_RE_RANGE,
    LAMINAR_MODEL_FULLY_DEVELOPED,
    LAMINAR_MODEL_THERMAL_ENTRY,
    LAMINAR_MODELS,
    THERMAL_BOUNDARY_CONDITIONS,
    coolant_path_distances,
    evaluate_model_b_hl,
    generate_channel_candidates,
    generate_channel_templates,
    rectangular_channel_geometry,
)


MODEL_H_ID = "SPACE_Y_MODEL_H_V1_EQUIVALENT_HELICAL_GEOMETRY"
INTERFACE_VERSION = "SPACE_Y_MODEL_H_V1_2026-11-11"

MODEL_H_WIDTH_CANDIDATES_MM = (0.8, 0.9, 1.2, 1.5, 2.0, 2.7, 3.0)
MODEL_H_RIB_CANDIDATES_MM = (0.9, 1.0, 1.2, 1.5)
MODEL_H_HEIGHT_CANDIDATES_MM = (1.2, 1.5, 2.0, 3.0, 4.0, 5.0)
MODEL_H_HELIX_ANGLES_DEG = (0.0, 20.0, 30.0, 35.0)


class ModelHError(CoolingModelBError):
    """Model H stop carrying the existing INPUT_U/OUTSIDE/NUM_U status."""


def _finite(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ModelHError("INPUT_U", f"{name} must be numeric") from error
    if not math.isfinite(number):
        raise ModelHError("INPUT_U", f"{name} must be finite")
    return number


def _positive(value: Any, name: str) -> float:
    number = _finite(value, name)
    if number <= 0.0:
        raise ModelHError("INPUT_U", f"{name} must be positive")
    return number


def _positive_integer(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise ModelHError("INPUT_U", f"{name} must be a positive integer")
    return value


def _angle_deg(value: Any) -> float:
    angle = _finite(value, "helix angle")
    if not 0.0 <= angle < 90.0:
        raise ModelHError("INPUT_U", "helix angle must satisfy 0 <= beta < 90 deg")
    return angle


def _numeric_station_values(
    value: float | Sequence[float], station_count: int, name: str
) -> list[float]:
    if isinstance(value, (str, bytes)):
        raise ModelHError("INPUT_U", f"{name} must be numeric or a station array")
    if isinstance(value, Sequence):
        raw = list(value)
        if len(raw) != station_count:
            raise ModelHError("INPUT_U", f"{name} length must equal station count")
    else:
        raw = [value] * station_count
    return [_positive(item, f"{name}[{index}]") for index, item in enumerate(raw)]


def _station_text_values(
    value: str | Sequence[str], station_count: int, name: str
) -> list[str]:
    if isinstance(value, str):
        raw = [value] * station_count
    elif isinstance(value, Sequence):
        raw = list(value)
        if len(raw) != station_count:
            raise ModelHError("INPUT_U", f"{name} length must equal station count")
    else:
        raise ModelHError("INPUT_U", f"{name} must be text or a station array")
    values: list[str] = []
    for index, item in enumerate(raw):
        if not isinstance(item, str) or not item.strip():
            raise ModelHError("INPUT_U", f"{name}[{index}] must be non-empty text")
        values.append(item.strip())
    return values


def generate_model_h_templates(
    *,
    width_candidates_mm: Sequence[float] = MODEL_H_WIDTH_CANDIDATES_MM,
    rib_candidates_mm: Sequence[float] = MODEL_H_RIB_CANDIDATES_MM,
    height_candidates_mm: Sequence[float] = MODEL_H_HEIGHT_CANDIDATES_MM,
) -> list[dict[str, Any]]:
    """Generate the prescribed 7 x 4 x 6 = 168 reference templates."""

    templates = generate_channel_templates(
        width_candidates_mm=width_candidates_mm,
        rib_candidates_mm=rib_candidates_mm,
        height_candidates_mm=height_candidates_mm,
    )
    for index, template in enumerate(templates, start=1):
        template["template_id"] = f"HCT-{index:04d}"
        template["geometry_policy"] = "MODEL_H_CONSTANT_HELIX_ANGLE"
        template["classification"] = (
            "MODEL_H_REFERENCE_GRID_NOT_APPROVED_DESIGN_RANGE"
        )
    return templates


def helical_station_geometry(
    *,
    mean_channel_radius_m: float,
    channel_count: int,
    rib_thickness_m: float,
    channel_height_m: float,
    helix_angle_deg: float,
) -> dict[str, Any]:
    """Return the channel cross-section normal to the helical flow direction."""

    radius = _positive(mean_channel_radius_m, "mean channel radius")
    count = _positive_integer(channel_count, "channel count")
    rib = _positive(rib_thickness_m, "rib thickness")
    height = _positive(channel_height_m, "channel height")
    angle = _angle_deg(helix_angle_deg)
    cosine = math.cos(math.radians(angle))
    circumferential_pitch = 2.0 * math.pi * radius / count
    normal_pitch = circumferential_pitch * cosine
    width = normal_pitch - rib
    if width <= 0.0:
        raise ModelHError(
            "OUTSIDE",
            "helical channel width is non-positive",
            details={
                "helix_angle_deg": angle,
                "normal_pitch_m": normal_pitch,
                "rib_thickness_m": rib,
            },
        )
    rectangular = rectangular_channel_geometry(width, height, count)
    return {
        "helix_angle_deg": angle,
        "helix_cosine": cosine,
        "mean_channel_radius_m": radius,
        "channel_count": count,
        "rib_thickness_m": rib,
        "channel_height_m": height,
        "circumferential_pitch_m": circumferential_pitch,
        "normal_pitch_m": normal_pitch,
        "channel_width_m": width,
        "channel_flow_area_m2": rectangular["channel_flow_area_m2"],
        "total_flow_area_m2": rectangular["total_flow_area_m2"],
        "hydraulic_diameter_m": rectangular["hydraulic_diameter_m"],
        "aspect_ratio_min_over_max": rectangular["aspect_ratio_min_over_max"],
        "aspect_ratio_major_over_minor": 1.0
        / float(rectangular["aspect_ratio_min_over_max"]),
        "velocity_cosine_multiplier_applied": False,
        "dean_vortex_correction_applied": False,
    }


def helical_coolant_path_distances(
    x_m: Sequence[float],
    mean_channel_radius_m: Sequence[float],
    helix_angle_deg: float,
) -> dict[str, Any]:
    """Scale the Model B meridional centerline path by ``1/cos(beta)``.

    The station order and coolant inlet convention are inherited unchanged:
    stations run from chamber to nozzle exit, and the last station is the
    coolant inlet with ``s=0``.
    """

    angle = _angle_deg(helix_angle_deg)
    cosine = math.cos(math.radians(angle))
    base = coolant_path_distances(x_m, mean_channel_radius_m)
    factor = 1.0 / cosine

    def scaled(items: Sequence[float | None]) -> list[float | None]:
        return [None if item is None else float(item) * factor for item in items]

    return {
        "helix_angle_deg": angle,
        "helix_cosine": cosine,
        "path_length_factor": factor,
        "s_B_m": list(base["s_m"]),
        "s_H_m": scaled(base["s_m"]),
        "segment_length_B_m": list(base["segment_length_to_next_station_m"]),
        "segment_length_H_m": scaled(base["segment_length_to_next_station_m"]),
        "entry_segment_length_B_m": list(base["entry_segment_length_m"]),
        "entry_segment_length_H_m": scaled(base["entry_segment_length_m"]),
        "coolant_flow_direction": "LAST_STATION_TO_FIRST_STATION",
        "path_definition": "MODEL_B_MERIDIONAL_CENTERLINE_DIVIDED_BY_COS_BETA",
    }


def evaluate_model_h_hl(
    *,
    mdot_total_kg_s: float,
    density_kg_m3: float,
    mean_channel_radius_m: float,
    channel_count: int,
    rib_thickness_m: float,
    channel_height_m: float,
    helix_angle_deg: float,
    viscosity_Pa_s: float,
    cp_J_kgK: float,
    conductivity_W_mK: float,
    roughness_m: float,
    thermal_boundary_condition: str,
    fully_developed: bool | None,
    colebrook_re_range: tuple[float, float] | None,
    colebrook_relative_roughness_range: tuple[float, float] | None,
    gnielinski_re_range: tuple[float, float] = GNIELINSKI_RE_RANGE,
    gnielinski_pr_range: tuple[float, float] = GNIELINSKI_PR_RANGE,
    laminar_model: str = LAMINAR_MODEL_FULLY_DEVELOPED,
    s_H_m: float | None = None,
    first_segment_length_H_m: float | None = None,
) -> dict[str, Any]:
    """Evaluate one helical station with the unchanged Model B closure.

    The returned dictionary retains ``h_l_W_m2K`` and the other Model B result
    fields so an existing cooling loop can consume the same coefficient key.
    Model H geometry and provenance fields are added without multiplying
    velocity or ``h_l`` by a separate helix factor.
    """

    geometry = helical_station_geometry(
        mean_channel_radius_m=mean_channel_radius_m,
        channel_count=channel_count,
        rib_thickness_m=rib_thickness_m,
        channel_height_m=channel_height_m,
        helix_angle_deg=helix_angle_deg,
    )
    density = _positive(density_kg_m3, "density")
    mass_flow = _positive(mdot_total_kg_s, "total coolant mass flow")
    local = evaluate_model_b_hl(
        mdot_total_kg_s=mass_flow,
        channel_count=channel_count,
        width_m=float(geometry["channel_width_m"]),
        height_m=channel_height_m,
        viscosity_Pa_s=viscosity_Pa_s,
        cp_J_kgK=cp_J_kgK,
        conductivity_W_mK=conductivity_W_mK,
        roughness_m=roughness_m,
        thermal_boundary_condition=thermal_boundary_condition,
        fully_developed=fully_developed,
        colebrook_re_range=colebrook_re_range,
        colebrook_relative_roughness_range=colebrook_relative_roughness_range,
        gnielinski_re_range=gnielinski_re_range,
        gnielinski_pr_range=gnielinski_pr_range,
        laminar_model=laminar_model,
        s_m=s_H_m,
        first_segment_length_m=first_segment_length_H_m,
    )
    return {
        **local,
        "model_id": MODEL_H_ID,
        "model_b_closure_id": local["model_id"],
        **geometry,
        "velocity_m_s": mass_flow
        / (density * float(geometry["total_flow_area_m2"])),
        "s_H_m": s_H_m,
        "first_segment_length_H_m": first_segment_length_H_m,
        "dean_vortex_correction_applied": False,
        "helical_heat_transfer_enhancement_factor": None,
        "velocity_cosine_multiplier_applied": False,
        "experimental_validation": False,
        "validated_engine_prediction": False,
        "safety_approval": False,
    }


def generate_model_h_candidates(
    *,
    x_m: Sequence[float],
    mean_channel_radius_m: Sequence[float],
    throat_station_index: int,
    channel_width_limits_m: tuple[float, float],
    minimum_rib_thickness_m: float,
    allowed_channel_heights_m: Sequence[float],
    station_ids: Sequence[str] | None = None,
    helix_angles_deg: Sequence[float] = MODEL_H_HELIX_ANGLES_DEG,
    width_candidates_mm: Sequence[float] = MODEL_H_WIDTH_CANDIDATES_MM,
    rib_candidates_mm: Sequence[float] = MODEL_H_RIB_CANDIDATES_MM,
    height_candidates_mm: Sequence[float] = MODEL_H_HEIGHT_CANDIDATES_MM,
) -> dict[str, Any]:
    """Generate Model B floor/ceil candidates and apply each helix angle.

    The channel count, rib thickness, and height of each base candidate remain
    unchanged across all requested angles.  Candidate ranking and automatic
    design selection are not performed.
    """

    angles = [_angle_deg(item) for item in helix_angles_deg]
    if not angles or len(set(angles)) != len(angles):
        raise ModelHError("INPUT_U", "helix angles must be non-empty and unique")
    width_min = _positive(channel_width_limits_m[0], "minimum channel width")
    width_max = _positive(channel_width_limits_m[1], "maximum channel width")
    if width_max <= width_min:
        raise ModelHError("INPUT_U", "channel width limits must be increasing")

    base = generate_channel_candidates(
        x_m=x_m,
        mean_channel_radius_m=mean_channel_radius_m,
        throat_station_index=throat_station_index,
        channel_width_limits_m=channel_width_limits_m,
        minimum_rib_thickness_m=minimum_rib_thickness_m,
        allowed_channel_heights_m=allowed_channel_heights_m,
        station_ids=station_ids,
        width_candidates_mm=width_candidates_mm,
        rib_candidates_mm=rib_candidates_mm,
        height_candidates_mm=height_candidates_mm,
    )
    templates = generate_model_h_templates(
        width_candidates_mm=width_candidates_mm,
        rib_candidates_mm=rib_candidates_mm,
        height_candidates_mm=height_candidates_mm,
    )
    template_id_map = {
        old["template_id"]: new["template_id"]
        for old, new in zip(base["templates"], templates)
    }

    candidates: list[dict[str, Any]] = []
    for base_candidate in base["candidates"]:
        for angle in angles:
            station_records: list[dict[str, Any]] = []
            reasons = list(base_candidate["geometry_exclusion_reasons"])
            for station in base_candidate["stations"]:
                try:
                    geometry = helical_station_geometry(
                        mean_channel_radius_m=station["mean_channel_radius_m"],
                        channel_count=base_candidate["channel_count"],
                        rib_thickness_m=base_candidate["rib_thickness_m"],
                        channel_height_m=base_candidate["channel_height_m"],
                        helix_angle_deg=angle,
                    )
                except ModelHError as error:
                    station_reasons = [str(error)]
                    geometry = {
                        "helix_angle_deg": angle,
                        "channel_width_m": None,
                        "total_flow_area_m2": None,
                        "hydraulic_diameter_m": None,
                        "aspect_ratio_min_over_max": None,
                        "aspect_ratio_major_over_minor": None,
                    }
                else:
                    width = float(geometry["channel_width_m"])
                    station_reasons = []
                    if width < width_min:
                        station_reasons.append(
                            "helical channel width is below supplied minimum"
                        )
                    if width > width_max:
                        station_reasons.append(
                            "helical channel width is above supplied maximum"
                        )
                if station_reasons:
                    reasons.extend(
                        f"{station['station_id']}: {reason}"
                        for reason in station_reasons
                    )
                station_records.append(
                    {
                        "station_id": station["station_id"],
                        "station_index": station["station_index"],
                        "x_m": station["x_m"],
                        "mean_channel_radius_m": station["mean_channel_radius_m"],
                        "is_throat": station["is_throat"],
                        **geometry,
                        "geometric_validity_status": (
                            "PASS" if not station_reasons else "OUTSIDE"
                        ),
                        "geometric_reasons": station_reasons,
                        "three_dimensional_manufacturability_status": "MODEL_U",
                    }
                )

            candidate_index = len(candidates) + 1
            valid = not reasons
            origins = deepcopy(base_candidate["template_origins"])
            for origin in origins:
                origin["template_id"] = template_id_map.get(
                    origin["template_id"], origin["template_id"]
                )
            candidates.append(
                {
                    "candidate_id": f"HCH-{candidate_index:05d}",
                    "base_model_b_candidate_id": base_candidate["candidate_id"],
                    "helix_angle_deg": angle,
                    "valid": valid,
                    "status": "PASS" if valid else "OUTSIDE",
                    "channel_count": base_candidate["channel_count"],
                    "rib_thickness_m": base_candidate["rib_thickness_m"],
                    "channel_height_m": base_candidate["channel_height_m"],
                    "geometry_policy": "MODEL_H_CONSTANT_HELIX_ANGLE",
                    "template_origins": origins,
                    "geometry_exclusion_reasons": reasons,
                    "stations": station_records,
                    "constant_channel_count_over_path": True,
                    "constant_helix_angle_over_path": True,
                    "three_dimensional_manufacturability_verified": False,
                    "design_approved": False,
                }
            )

    return {
        "model_id": MODEL_H_ID,
        "interface_version": INTERFACE_VERSION,
        "templates": templates,
        "candidates": candidates,
        "template_count": len(templates),
        "helix_angle_count": len(angles),
        "initial_template_angle_combination_count": len(templates) * len(angles),
        "base_raw_candidate_count": base["raw_candidate_count"],
        "base_unique_candidate_count": base["unique_candidate_count"],
        "angle_expanded_candidate_count": len(candidates),
        "valid_candidate_count": sum(item["valid"] for item in candidates),
        "invalid_candidate_count": sum(not item["valid"] for item in candidates),
        "selection_performed": False,
        "design_approved": False,
    }


def evaluate_model_h_candidates(
    *,
    x_m: Sequence[float],
    mean_channel_radius_m: Sequence[float],
    throat_station_index: int,
    mdot_total_kg_s: float,
    density_kg_m3: float | Sequence[float],
    viscosity_Pa_s: float | Sequence[float],
    cp_J_kgK: float | Sequence[float],
    conductivity_W_mK: float | Sequence[float],
    roughness_m: float,
    thermal_boundary_condition: str,
    fully_developed: bool | None,
    colebrook_re_range: tuple[float, float] | None,
    colebrook_relative_roughness_range: tuple[float, float] | None,
    channel_width_limits_m: tuple[float, float],
    minimum_rib_thickness_m: float,
    allowed_channel_heights_m: Sequence[float],
    station_ids: Sequence[str] | None = None,
    gnielinski_re_range: tuple[float, float] = GNIELINSKI_RE_RANGE,
    gnielinski_pr_range: tuple[float, float] = GNIELINSKI_PR_RANGE,
    laminar_model: str = LAMINAR_MODEL_FULLY_DEVELOPED,
    helix_angles_deg: Sequence[float] = MODEL_H_HELIX_ANGLES_DEG,
    width_candidates_mm: Sequence[float] = MODEL_H_WIDTH_CANDIDATES_MM,
    rib_candidates_mm: Sequence[float] = MODEL_H_RIB_CANDIDATES_MM,
    height_candidates_mm: Sequence[float] = MODEL_H_HEIGHT_CANDIDATES_MM,
    fluid_identity: str = "UNSPECIFIED_FLUID",
    property_data_classification: str = "INPUT_U",
    property_validity_status: str | Sequence[str] = "INPUT_U",
) -> dict[str, Any]:
    """Evaluate all Model H candidates using the unchanged Model B closures."""

    station_count = len(x_m)
    if station_count <= 0 or len(mean_channel_radius_m) != station_count:
        raise ModelHError("INPUT_U", "station geometry arrays must have equal length")
    if thermal_boundary_condition not in THERMAL_BOUNDARY_CONDITIONS:
        raise ModelHError("INPUT_U", "unsupported thermal boundary condition")
    if laminar_model not in LAMINAR_MODELS:
        raise ModelHError("INPUT_U", "unsupported laminar model")
    if fully_developed is None:
        raise ModelHError("INPUT_U", "hydrodynamic development assumption is unconfirmed")
    if fully_developed is not True:
        raise ModelHError(
            "OUTSIDE", "Model H V1 inherits Model B's fully developed velocity assumption"
        )
    mass_flow = _positive(mdot_total_kg_s, "total coolant mass flow")
    roughness = _finite(roughness_m, "equivalent sand roughness")
    if roughness < 0.0:
        raise ModelHError("INPUT_U", "equivalent sand roughness must be non-negative")
    densities = _numeric_station_values(density_kg_m3, station_count, "density_kg_m3")
    viscosities = _numeric_station_values(
        viscosity_Pa_s, station_count, "viscosity_Pa_s"
    )
    heat_capacities = _numeric_station_values(cp_J_kgK, station_count, "cp_J_kgK")
    conductivities = _numeric_station_values(
        conductivity_W_mK, station_count, "conductivity_W_mK"
    )
    property_statuses = _station_text_values(
        property_validity_status, station_count, "property_validity_status"
    )
    if not isinstance(fluid_identity, str) or not fluid_identity.strip():
        raise ModelHError("INPUT_U", "fluid identity must be explicit")
    if not isinstance(property_data_classification, str) or not property_data_classification.strip():
        raise ModelHError("INPUT_U", "property data classification must be explicit")

    geometry = generate_model_h_candidates(
        x_m=x_m,
        mean_channel_radius_m=mean_channel_radius_m,
        throat_station_index=throat_station_index,
        channel_width_limits_m=channel_width_limits_m,
        minimum_rib_thickness_m=minimum_rib_thickness_m,
        allowed_channel_heights_m=allowed_channel_heights_m,
        station_ids=station_ids,
        helix_angles_deg=helix_angles_deg,
        width_candidates_mm=width_candidates_mm,
        rib_candidates_mm=rib_candidates_mm,
        height_candidates_mm=height_candidates_mm,
    )
    paths = {
        float(angle): helical_coolant_path_distances(
            x_m, mean_channel_radius_m, float(angle)
        )
        for angle in helix_angles_deg
    }

    successful_station_count = 0
    failed_station_count = 0
    regime_counts = {"LAMINAR": 0, "TRANSITION_MODEL_B": 0, "TURBULENT": 0}
    evaluated: list[dict[str, Any]] = []
    for candidate in geometry["candidates"]:
        angle = float(candidate["helix_angle_deg"])
        path = paths[angle]
        station_results: list[dict[str, Any]] = []
        h_values: list[float] = []
        re_values: list[float] = []
        if not candidate["valid"]:
            for station in candidate["stations"]:
                station_results.append(
                    {
                        **station,
                        "status": "OUTSIDE",
                        "reason": "candidate violates supplied 1D geometric constraints",
                        "Reynolds": None,
                        "Prandtl": None,
                        "Nusselt": None,
                        "h_l_W_m2K": None,
                        "velocity_m_s": None,
                        "s_H_m": path["s_H_m"][station["station_index"]],
                    }
                )
            evaluated.append(
                {
                    **candidate,
                    "stations": station_results,
                    "summary": {
                        "successful_station_count": 0,
                        "failed_station_count": 0,
                        "minimum_Reynolds": None,
                        "maximum_Reynolds": None,
                        "minimum_h_l_W_m2K": None,
                        "maximum_h_l_W_m2K": None,
                    },
                }
            )
            continue

        for station in candidate["stations"]:
            index = int(station["station_index"])
            property_status = property_statuses[index]
            if property_status in {"INPUT_U", "OUTSIDE", "NUM_U"}:
                failed_station_count += 1
                station_results.append(
                    {
                        **station,
                        "status": property_status,
                        "reason": (
                            "property adapter did not authorize numerical property use"
                        ),
                        "Reynolds": None,
                        "Prandtl": None,
                        "Nusselt": None,
                        "h_l_W_m2K": None,
                        "velocity_m_s": None,
                        "s_B_m": path["s_B_m"][index],
                        "s_H_m": path["s_H_m"][index],
                        "property_validity_status": property_status,
                    }
                )
                continue
            area = float(station["total_flow_area_m2"])
            velocity = mass_flow / (densities[index] * area)
            try:
                local = evaluate_model_h_hl(
                    mdot_total_kg_s=mass_flow,
                    density_kg_m3=densities[index],
                    mean_channel_radius_m=station["mean_channel_radius_m"],
                    channel_count=int(candidate["channel_count"]),
                    rib_thickness_m=float(candidate["rib_thickness_m"]),
                    channel_height_m=float(candidate["channel_height_m"]),
                    helix_angle_deg=angle,
                    viscosity_Pa_s=viscosities[index],
                    cp_J_kgK=heat_capacities[index],
                    conductivity_W_mK=conductivities[index],
                    roughness_m=roughness,
                    thermal_boundary_condition=thermal_boundary_condition,
                    fully_developed=fully_developed,
                    colebrook_re_range=colebrook_re_range,
                    colebrook_relative_roughness_range=(
                        colebrook_relative_roughness_range
                    ),
                    gnielinski_re_range=gnielinski_re_range,
                    gnielinski_pr_range=gnielinski_pr_range,
                    laminar_model=laminar_model,
                    s_H_m=(
                        None
                        if laminar_model != LAMINAR_MODEL_THERMAL_ENTRY
                        else path["s_H_m"][index]
                    ),
                    first_segment_length_H_m=(
                        None
                        if laminar_model != LAMINAR_MODEL_THERMAL_ENTRY
                        else path["entry_segment_length_H_m"][index]
                    ),
                )
            except CoolingModelBError as error:
                failed_station_count += 1
                station_results.append(
                    {
                        **station,
                        "status": error.status,
                        "reason": str(error),
                        "details": error.details,
                        "Reynolds": error.details.get("Reynolds"),
                        "Prandtl": error.details.get("Prandtl"),
                        "Nusselt": None,
                        "h_l_W_m2K": None,
                        "velocity_m_s": velocity,
                        "s_B_m": path["s_B_m"][index],
                        "s_H_m": path["s_H_m"][index],
                        "property_validity_status": property_status,
                    }
                )
                continue

            successful_station_count += 1
            regime_counts[local["flow_regime"]] += 1
            h_values.append(float(local["h_l_W_m2K"]))
            re_values.append(float(local["Reynolds"]))
            station_results.append(
                {
                    **station,
                    **local,
                    "model_h_id": MODEL_H_ID,
                    "s_B_m": path["s_B_m"][index],
                    "s_H_m": path["s_H_m"][index],
                    "segment_length_B_m": path["segment_length_B_m"][index],
                    "segment_length_H_m": path["segment_length_H_m"][index],
                    "path_length_factor": path["path_length_factor"],
                    "property_validity_status": property_status,
                    "reason": "calculation completed with unchanged Model B closure",
                }
            )

        station_failures = sum(item["status"] != "MODEL_U" for item in station_results)
        evaluated.append(
            {
                **candidate,
                "status": "MODEL_U" if station_failures == 0 else "OUTSIDE",
                "stations": station_results,
                "summary": {
                    "successful_station_count": len(station_results) - station_failures,
                    "failed_station_count": station_failures,
                    "minimum_Reynolds": min(re_values) if re_values else None,
                    "maximum_Reynolds": max(re_values) if re_values else None,
                    "minimum_h_l_W_m2K": min(h_values) if h_values else None,
                    "maximum_h_l_W_m2K": max(h_values) if h_values else None,
                },
            }
        )

    valid_station_target = geometry["valid_candidate_count"] * station_count
    overall_status = (
        "MODEL_U"
        if valid_station_target > 0 and successful_station_count == valid_station_target
        else "OUTSIDE"
    )
    return {
        "interface_version": INTERFACE_VERSION,
        "model_id": MODEL_H_ID,
        "status": overall_status,
        "input_classification": property_data_classification,
        "fluid_identity": fluid_identity,
        "property_validity_status_by_station": property_statuses,
        "template_count": geometry["template_count"],
        "helix_angle_count": geometry["helix_angle_count"],
        "initial_template_angle_combination_count": geometry[
            "initial_template_angle_combination_count"
        ],
        "base_raw_candidate_count": geometry["base_raw_candidate_count"],
        "base_unique_candidate_count": geometry["base_unique_candidate_count"],
        "angle_expanded_candidate_count": geometry["angle_expanded_candidate_count"],
        "valid_candidate_count": geometry["valid_candidate_count"],
        "invalid_candidate_count": geometry["invalid_candidate_count"],
        "station_count_per_candidate": station_count,
        "valid_station_target_count": valid_station_target,
        "successful_station_count": successful_station_count,
        "failed_station_count": failed_station_count,
        "flow_regime_counts": regime_counts,
        "mass_flow_total_kg_s": mass_flow,
        "equal_parallel_channel_flow_assumed": True,
        "laminar_model": laminar_model,
        "thermal_boundary_condition": thermal_boundary_condition,
        "templates": geometry["templates"],
        "candidates": evaluated,
        "selection_performed": False,
        "final_design_selection": False,
        "experimental_validation": False,
        "validated_engine_prediction": False,
        "physical_validation_v01_v12": "NOT_RUN",
        "safety_approval": False,
        "heat_transfer_area_interface": {
            "h_l_definition": "LOCAL_COEFFICIENT_ON_ACTUAL_CHANNEL_WETTED_SURFACE",
            "helical_area_multiplier_applied_to_h_l": False,
            "required_downstream_action": (
                "integrator must use actual helical segment length for coolant wetted area; "
                "do not multiply h_l by 1/cos(beta)"
            ),
        },
        "limitations": [
            "Model B correlations are applied to an equivalent straight rectangular duct.",
            "Dean vortices and other curvature-induced secondary-flow enhancement are not modelled.",
            "Curvature-dependent laminar-transition-turbulent Reynolds boundaries are not modelled.",
            "The Model B Re=2300-3000 interpolation is retained for numerical continuity only.",
            "One-dimensional geometry does not prove three-dimensional machinability or rib clearance.",
            "Pressure loss, manifold maldistribution, phase change, wall conduction, and gas-side heat transfer are outside this module.",
            "Actual IPA property validity must be established by the external Property Adapter.",
        ],
        "provenance": {
            "method_context": (
                "Lv et al. (2023), Heat Transfer Analysis and Structural Optimization "
                "for Spiral Channel Regenerative Cooling Thrust Chamber"
            ),
            "doi": "10.1155/2023/8628107",
            "nusselt_correlations": "unchanged cooling_model_b implementation",
        },
    }


def write_model_h_station_csv(path: str | Path, result: dict[str, Any]) -> None:
    """Write one row per angle/candidate/station for plotting and review."""

    columns = [
        "candidate_id",
        "base_model_b_candidate_id",
        "helix_angle_deg",
        "station_id",
        "station_index",
        "x_m",
        "mean_channel_radius_m",
        "channel_count",
        "rib_thickness_m",
        "channel_height_m",
        "channel_width_m",
        "total_flow_area_m2",
        "hydraulic_diameter_m",
        "s_B_m",
        "s_H_m",
        "velocity_m_s",
        "Reynolds",
        "Prandtl",
        "Nusselt",
        "h_l_W_m2K",
        "flow_regime",
        "correlation_id",
        "status",
        "property_validity_status",
    ]
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for candidate in result.get("candidates", []):
            for station in candidate.get("stations", []):
                writer.writerow(
                    {
                        **{key: station.get(key) for key in columns},
                        "candidate_id": candidate.get("candidate_id"),
                        "base_model_b_candidate_id": candidate.get(
                            "base_model_b_candidate_id"
                        ),
                        "helix_angle_deg": candidate.get("helix_angle_deg"),
                        "channel_count": candidate.get("channel_count"),
                        "rib_thickness_m": candidate.get("rib_thickness_m"),
                        "channel_height_m": candidate.get("channel_height_m"),
                    }
                )


def write_model_h_result_json(path: str | Path, result: dict[str, Any]) -> None:
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


__all__ = [
    "INTERFACE_VERSION",
    "MODEL_H_HELIX_ANGLES_DEG",
    "MODEL_H_HEIGHT_CANDIDATES_MM",
    "MODEL_H_ID",
    "MODEL_H_RIB_CANDIDATES_MM",
    "MODEL_H_WIDTH_CANDIDATES_MM",
    "ModelHError",
    "evaluate_model_h_candidates",
    "evaluate_model_h_hl",
    "generate_model_h_candidates",
    "generate_model_h_templates",
    "helical_coolant_path_distances",
    "helical_station_geometry",
    "write_model_h_result_json",
    "write_model_h_station_csv",
]
