from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


@dataclass(frozen=True)
class CoolingApplicabilityPolicy:
    policy_id: str
    classification: str
    gnielinski_source: str
    gnielinski_re_min: float
    gnielinski_re_max: float
    gnielinski_pr_min: float
    gnielinski_pr_max: float
    recommended_re_min: float
    transition_warning_re_threshold: float
    design_recommendation_source: str
    evaluate_minimum_re_over_all_stations: bool
    compare_rough_to_smooth_friction: bool
    roughness_friction_ratio_warning_threshold: float
    roughness_warning_source: str
    apply_arbitrary_friction_cap: bool
    apply_arbitrary_heat_transfer_cap: bool


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} is required")
    return value.strip()


def _positive(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(number) or number <= 0:
        raise ValueError(f"{name} must be finite and positive")
    return number


def _explicit_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{name} must be an explicit boolean")
    return value


def build_cooling_applicability_policy(
    spec: dict[str, Any],
) -> CoolingApplicabilityPolicy:
    if not isinstance(spec, dict):
        raise ValueError("cooling applicability policy must be an object")
    heat = spec.get("heat_transfer_correlation")
    design = spec.get("design_recommendation")
    roughness = spec.get("roughness_assessment")
    if not all(isinstance(item, dict) for item in (heat, design, roughness)):
        raise ValueError("cooling applicability policy sections are required")
    re_min, re_max = map(float, heat.get("re_range", []))
    pr_min, pr_max = map(float, heat.get("pr_range", []))
    if (
        not all(math.isfinite(item) for item in (re_min, re_max, pr_min, pr_max))
        or re_min <= 0
        or re_max <= re_min
        or pr_min <= 0
        or pr_max <= pr_min
    ):
        raise ValueError("invalid Gnielinski policy range")
    recommended = _positive(
        design.get("recommended_re_min"), "recommended_re_min"
    )
    transition = _positive(
        design.get("transition_warning_re_threshold"),
        "transition_warning_re_threshold",
    )
    if recommended < re_min or transition < recommended:
        raise ValueError(
            "Re policy must satisfy correlation minimum <= recommended minimum <= transition threshold"
        )
    apply_cap = _explicit_bool(
        roughness.get("apply_arbitrary_friction_cap"),
        "apply_arbitrary_friction_cap",
    )
    apply_heat_cap = _explicit_bool(
        roughness.get("apply_arbitrary_heat_transfer_cap", False),
        "apply_arbitrary_heat_transfer_cap",
    )
    if apply_cap or apply_heat_cap:
        raise ValueError("arbitrary roughness friction or heat-transfer caps are prohibited")
    return CoolingApplicabilityPolicy(
        policy_id=_text(spec.get("policy_id"), "policy_id"),
        classification=_text(spec.get("classification"), "classification"),
        gnielinski_source=_text(heat.get("source"), "Gnielinski source"),
        gnielinski_re_min=re_min,
        gnielinski_re_max=re_max,
        gnielinski_pr_min=pr_min,
        gnielinski_pr_max=pr_max,
        recommended_re_min=recommended,
        transition_warning_re_threshold=transition,
        design_recommendation_source=_text(
            design.get("source"), "design recommendation source"
        ),
        evaluate_minimum_re_over_all_stations=_explicit_bool(
            design.get("evaluate_minimum_re_over_all_stations"),
            "evaluate_minimum_re_over_all_stations",
        ),
        compare_rough_to_smooth_friction=_explicit_bool(
            roughness.get("compare_rough_to_smooth_friction"),
            "compare_rough_to_smooth_friction",
        ),
        roughness_friction_ratio_warning_threshold=_positive(
            roughness.get("roughness_friction_ratio_warning_threshold"),
            "roughness_friction_ratio_warning_threshold",
        ),
        roughness_warning_source=_text(
            roughness.get("source"), "roughness warning source"
        ),
        apply_arbitrary_friction_cap=False,
        apply_arbitrary_heat_transfer_cap=False,
    )


def assess_local_reynolds_prandtl(
    reynolds: float,
    prandtl: float,
    policy: CoolingApplicabilityPolicy,
) -> dict[str, Any]:
    re_value = float(reynolds)
    pr_value = float(prandtl)
    if not math.isfinite(re_value) or re_value <= 0:
        raise ValueError("Reynolds number must be finite and positive")
    if not math.isfinite(pr_value) or pr_value <= 0:
        raise ValueError("Prandtl number must be finite and positive")
    re_in_domain = policy.gnielinski_re_min <= re_value <= policy.gnielinski_re_max
    pr_in_domain = policy.gnielinski_pr_min <= pr_value <= policy.gnielinski_pr_max
    correlation_status = (
        "WITHIN_GNIELINSKI_POLICY_DOMAIN"
        if re_in_domain and pr_in_domain
        else "OUTSIDE_GNIELINSKI_POLICY_DOMAIN"
    )
    if not re_in_domain:
        design_status = "NOT_APPLICABLE_CORRELATION_RE_OUTSIDE"
    elif re_value < policy.recommended_re_min:
        design_status = "BELOW_PRELIMINARY_RE_RECOMMENDATION"
    elif re_value < policy.transition_warning_re_threshold:
        design_status = "MEETS_RE_MIN_WITH_TRANSITION_WARNING"
    else:
        design_status = "MEETS_PRELIMINARY_RE_RECOMMENDATION"
    transition_warning = (
        policy.gnielinski_re_min
        <= re_value
        < policy.transition_warning_re_threshold
    )
    return {
        "policy_id": policy.policy_id,
        "correlation_applicability_status": correlation_status,
        "gnielinski_re_in_domain": re_in_domain,
        "gnielinski_pr_in_domain": pr_in_domain,
        "gnielinski_re_range": [
            policy.gnielinski_re_min,
            policy.gnielinski_re_max,
        ],
        "gnielinski_pr_range": [
            policy.gnielinski_pr_min,
            policy.gnielinski_pr_max,
        ],
        "re_design_minimum": policy.recommended_re_min,
        "re_design_margin": re_value - policy.recommended_re_min,
        "re_design_recommendation_status": design_status,
        "transition_warning_re_threshold": (
            policy.transition_warning_re_threshold
        ),
        "transition_region_warning": transition_warning,
        "correlation_source": policy.gnielinski_source,
        "design_recommendation_source": policy.design_recommendation_source,
    }


def not_evaluated_path_assessment(reason: str) -> dict[str, Any]:
    return {
        "status": "NOT_EVALUATED",
        "reason": reason,
        "minimum_reynolds": None,
        "minimum_reynolds_station_id": None,
        "minimum_reynolds_x_m": None,
        "re_design_minimum": None,
        "re_design_margin": None,
        "re_design_recommendation_status": "NOT_EVALUATED",
        "transition_warning_station_ids": [],
        "maximum_roughness_friction_ratio": None,
        "maximum_roughness_ratio_station_id": None,
        "roughness_warning_status": "NOT_EVALUATED",
        "correlation_domain_status": "NOT_EVALUATED",
        "continuous_path_claim": False,
    }


def assess_candidate_station_path(
    stations: list[dict[str, Any]],
    policy: CoolingApplicabilityPolicy | None,
) -> dict[str, Any]:
    if policy is None:
        return not_evaluated_path_assessment(
            "cooling applicability policy was not supplied"
        )
    if not policy.evaluate_minimum_re_over_all_stations:
        return not_evaluated_path_assessment(
            "policy does not authorize an all-station minimum-Re assessment"
        )
    records: list[dict[str, Any]] = []
    for station in stations:
        calculation = station.get("calculation") or {}
        values = calculation.get("results") or {}
        reynolds = values.get("Reynolds")
        prandtl = values.get("Prandtl")
        if (
            calculation.get("status") != "MODEL_U"
            or not isinstance(reynolds, (int, float))
            or isinstance(reynolds, bool)
            or not isinstance(prandtl, (int, float))
            or isinstance(prandtl, bool)
        ):
            result = not_evaluated_path_assessment(
                "at least one discrete station lacks an applicable completed calculation"
            )
            result["status"] = "INPUT_U"
            return result
        local = values.get("applicability_assessment")
        if not isinstance(local, dict):
            local = assess_local_reynolds_prandtl(reynolds, prandtl, policy)
        roughness = values.get("roughness_assessment")
        records.append(
            {
                "station_id": station.get("station_id"),
                "x_m": station.get("x_m"),
                "Reynolds": float(reynolds),
                "local": local,
                "roughness": roughness if isinstance(roughness, dict) else {},
            }
        )
    if not records:
        return not_evaluated_path_assessment("candidate has no stations")

    minimum = min(records, key=lambda item: item["Reynolds"])
    transition_ids = [
        item["station_id"]
        for item in records
        if item["local"].get("transition_region_warning") is True
    ]
    correlation_status = (
        "ALL_DISCRETE_STATIONS_WITHIN_GNIELINSKI_POLICY_DOMAIN"
        if all(
            item["local"].get("correlation_applicability_status")
            == "WITHIN_GNIELINSKI_POLICY_DOMAIN"
            for item in records
        )
        else "AT_LEAST_ONE_DISCRETE_STATION_OUTSIDE_GNIELINSKI_POLICY_DOMAIN"
    )
    minimum_local = minimum["local"]
    ratios = [
        (
            item["roughness"].get("roughness_friction_ratio"),
            item["station_id"],
        )
        for item in records
        if isinstance(
            item["roughness"].get("roughness_friction_ratio"),
            (int, float),
        )
    ]
    if policy.compare_rough_to_smooth_friction and len(ratios) != len(records):
        roughness_status = "NOT_EVALUATED_INCOMPLETE_PATH"
        max_ratio = None
        max_ratio_station = None
    elif ratios:
        max_ratio, max_ratio_station = max(ratios, key=lambda item: item[0])
        roughness_status = (
            "ROUGHNESS_HEAT_TRANSFER_OVERPREDICTION_WARNING"
            if max_ratio
            >= policy.roughness_friction_ratio_warning_threshold
            else "BELOW_ROUGHNESS_WARNING_THRESHOLD"
        )
    else:
        roughness_status = "NOT_EVALUATED"
        max_ratio = None
        max_ratio_station = None
    return {
        "status": "MODEL_U",
        "reason": (
            "all supplied discrete stations assessed; unsampled continuous positions are not proven"
        ),
        "minimum_reynolds": minimum["Reynolds"],
        "minimum_reynolds_station_id": minimum["station_id"],
        "minimum_reynolds_x_m": minimum["x_m"],
        "re_design_minimum": policy.recommended_re_min,
        "re_design_margin": minimum["Reynolds"] - policy.recommended_re_min,
        "re_design_recommendation_status": minimum_local[
            "re_design_recommendation_status"
        ],
        "transition_warning_station_ids": transition_ids,
        "maximum_roughness_friction_ratio": max_ratio,
        "maximum_roughness_ratio_station_id": max_ratio_station,
        "roughness_warning_status": roughness_status,
        "correlation_domain_status": correlation_status,
        "continuous_path_claim": False,
        "policy_id": policy.policy_id,
    }
