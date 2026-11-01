from __future__ import annotations

import math
from typing import Any


PRELIMINARY_STATEMENT = (
    "Preliminary candidate recommendation, not final design optimization."
)


def _finite_nonnegative(value: Any, name: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as error:
        raise ValueError(f"{name} must be numeric") from error
    if not math.isfinite(number) or number < 0:
        raise ValueError(f"{name} must be finite and non-negative")
    return number


def _close(a: float, b: float, relative: float, absolute: float) -> bool:
    return abs(a - b) <= max(absolute, relative * max(abs(a), abs(b)))


def _dominates(
    first: dict[str, Any],
    second: dict[str, Any],
    relative: float,
    h_absolute: float,
    gradient_absolute: float,
) -> bool:
    first_h = first["h_l_at_reference_W_m2K"]
    second_h = second["h_l_at_reference_W_m2K"]
    first_gradient = first["friction_pressure_gradient_at_reference_Pa_m"]
    second_gradient = second["friction_pressure_gradient_at_reference_Pa_m"]
    h_equal = _close(first_h, second_h, relative, h_absolute)
    gradient_equal = _close(
        first_gradient, second_gradient, relative, gradient_absolute
    )
    not_worse_h = first_h > second_h or h_equal
    not_worse_gradient = first_gradient < second_gradient or gradient_equal
    strictly_better = (
        first_h > second_h and not h_equal
    ) or (
        first_gradient < second_gradient and not gradient_equal
    )
    return not_worse_h and not_worse_gradient and strictly_better


def _base_tags(candidate: dict[str, Any]) -> tuple[list[str], list[str]]:
    decision = candidate.get("decision")
    if decision == "EXCLUDE_GEOMETRIC_CONSTRAINT":
        return ["EXCLUDED_GEOMETRY"], [
            "candidate violates at least one supplied geometry constraint"
        ]
    if decision == "EXCLUDE_CORRELATION_DOMAIN":
        return ["EXCLUDED_CORRELATION"], [
            "candidate leaves at least one supplied correlation domain"
        ]
    if decision in {"HOLD_INPUT", "LOCAL_ARITHMETIC_ONLY"}:
        return ["UNVERIFIED_INPUT"], [
            "candidate geometry or required model input remains unverified"
        ]
    if decision == "HOLD_NUMERICAL":
        return ["UNVERIFIED_INPUT"], [
            "candidate contains an unresolved numerical calculation"
        ]
    return [], []


def _resolve_reference_station(
    candidates: list[dict[str, Any]], comparison: dict[str, Any]
) -> tuple[str | None, str | None]:
    reference_type = comparison.get("reference_station_type")
    if reference_type == "STATION_ID":
        station_id = comparison.get("reference_station_id")
        if not isinstance(station_id, str) or not station_id.strip():
            return None, "STATION_ID comparison requires reference_station_id"
        return station_id.strip(), None
    if reference_type != "THROAT":
        return None, "reference_station_type must be THROAT or STATION_ID"

    throat_ids = {
        station.get("station_id")
        for candidate in candidates
        for station in candidate.get("stations", [])
        if station.get("is_throat") is True
    }
    throat_ids.discard(None)
    if len(throat_ids) != 1:
        return None, "THROAT comparison requires exactly one common throat station"
    return next(iter(throat_ids)), None


def _normalise_baseline(
    baseline: Any, candidate_ids: set[str]
) -> tuple[dict[str, Any] | None, str | None]:
    if baseline is None:
        return None, None
    if not isinstance(baseline, dict):
        return None, "baseline_reference must be an object"
    required_text = (
        "baseline_id",
        "source",
        "geometry_policy",
        "geometric_applicability_status",
    )
    for name in required_text:
        if not isinstance(baseline.get(name), str) or not baseline[name].strip():
            return None, f"baseline_reference.{name} is required"
    if baseline.get("design_approved") is not False:
        return None, "baseline_reference.design_approved must be false"
    settings = baseline.get("settings")
    if not isinstance(settings, dict):
        return None, "baseline_reference.settings must be an object"
    limitations = baseline.get("limitations", [])
    if not isinstance(limitations, list) or not all(
        isinstance(item, str) and item.strip() for item in limitations
    ):
        return None, "baseline_reference.limitations must be strings"
    candidate_id = baseline.get("candidate_id")
    if candidate_id is not None and candidate_id not in candidate_ids:
        return None, "baseline_reference.candidate_id does not exist in this sweep"
    return {
        "baseline_id": baseline["baseline_id"].strip(),
        "source": baseline["source"].strip(),
        "geometry_policy": baseline["geometry_policy"].strip(),
        "settings": settings,
        "geometric_applicability_status": baseline[
            "geometric_applicability_status"
        ].strip(),
        "candidate_id": candidate_id,
        "design_approved": False,
        "automatically_selected": False,
        "limitations": list(limitations),
    }, None


def classify_cooling_channel_candidates(
    candidates: list[dict[str, Any]],
    comparison: dict[str, Any] | None,
    baseline_reference: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Annotate candidates using one common station and a two-objective Pareto test."""

    for candidate in candidates:
        tags, reasons = _base_tags(candidate)
        candidate["recommendation_tags"] = tags
        candidate["exclusion_reasons"] = reasons
        candidate["comparison"] = {
            "reference_station_id": None,
            "h_l_at_reference_W_m2K": None,
            "friction_pressure_gradient_at_reference_Pa_m": None,
            "pareto_status": "NOT_EVALUATED",
            "model_applicability": candidate.get("summary", {}).get(
                "correlation_applicability_status", "NOT_RUN"
            ),
        }

    candidate_ids = {
        candidate.get("candidate_id")
        for candidate in candidates
        if isinstance(candidate.get("candidate_id"), str)
    }
    baseline, baseline_error = _normalise_baseline(
        baseline_reference, candidate_ids
    )
    if baseline is not None and baseline.get("candidate_id") is not None:
        for candidate in candidates:
            if candidate.get("candidate_id") == baseline["candidate_id"]:
                candidate["recommendation_tags"].append("BASELINE_REFERENCE")

    if baseline_error is not None:
        return {
            "status": "INPUT_U",
            "reason": baseline_error,
            "baseline_reference": None,
            "statement": PRELIMINARY_STATEMENT,
            "final_design_selection": False,
        }
    if comparison is None:
        return {
            "status": "NOT_RUN",
            "reason": "comparison input was not supplied",
            "baseline_reference": baseline,
            "reference_station_id": None,
            "reference_station_type": None,
            "eligible_candidate_ids": [],
            "highest_h_l_candidate_ids": [],
            "lowest_friction_gradient_candidate_ids": [],
            "pareto_candidate_ids": [],
            "statement": PRELIMINARY_STATEMENT,
            "final_design_selection": False,
        }
    if not isinstance(comparison, dict):
        return {
            "status": "INPUT_U",
            "reason": "comparison must be an object",
            "baseline_reference": baseline,
            "statement": PRELIMINARY_STATEMENT,
            "final_design_selection": False,
        }
    try:
        relative = _finite_nonnegative(
            comparison.get("relative_tolerance"), "relative_tolerance"
        )
        h_absolute = _finite_nonnegative(
            comparison.get("h_l_absolute_tolerance_W_m2K"),
            "h_l_absolute_tolerance_W_m2K",
        )
        gradient_absolute = _finite_nonnegative(
            comparison.get("friction_gradient_absolute_tolerance_Pa_m"),
            "friction_gradient_absolute_tolerance_Pa_m",
        )
    except ValueError as error:
        return {
            "status": "INPUT_U",
            "reason": str(error),
            "baseline_reference": baseline,
            "statement": PRELIMINARY_STATEMENT,
            "final_design_selection": False,
        }

    reference_id, reference_error = _resolve_reference_station(
        candidates, comparison
    )
    if reference_error is not None:
        return {
            "status": "INPUT_U",
            "reason": reference_error,
            "baseline_reference": baseline,
            "reference_station_id": None,
            "reference_station_type": comparison.get("reference_station_type"),
            "eligible_candidate_ids": [],
            "highest_h_l_candidate_ids": [],
            "lowest_friction_gradient_candidate_ids": [],
            "pareto_candidate_ids": [],
            "statement": PRELIMINARY_STATEMENT,
            "final_design_selection": False,
        }

    eligible: list[dict[str, Any]] = []
    for candidate in candidates:
        candidate["comparison"]["reference_station_id"] = reference_id
        if candidate.get("geometric_validity_status") != "PASS":
            continue
        if candidate.get("summary", {}).get(
            "correlation_applicability_status"
        ) != "APPLICABLE_TO_SUPPLIED_DOMAINS":
            if not candidate["recommendation_tags"]:
                candidate["recommendation_tags"].append("UNVERIFIED_INPUT")
                candidate["exclusion_reasons"].append(
                    "full-path model applicability is not established"
                )
            continue
        stations = candidate.get("stations", [])
        if not stations or any(
            (station.get("calculation") or {}).get("status") != "MODEL_U"
            for station in stations
        ):
            candidate["recommendation_tags"].append("UNVERIFIED_INPUT")
            candidate["exclusion_reasons"].append(
                "every path station must have a completed applicable calculation"
            )
            continue
        reference_station = next(
            (
                station
                for station in stations
                if station.get("station_id") == reference_id
            ),
            None,
        )
        if reference_station is None:
            candidate["recommendation_tags"].append("UNVERIFIED_INPUT")
            candidate["exclusion_reasons"].append(
                "reference station is missing from candidate"
            )
            continue
        values = (reference_station.get("calculation") or {}).get("results") or {}
        h_l = values.get("h_l_W_m2K")
        gradient = values.get("friction_pressure_gradient_Pa_m")
        if not all(
            isinstance(value, (int, float))
            and not isinstance(value, bool)
            and math.isfinite(float(value))
            and float(value) > 0
            for value in (h_l, gradient)
        ):
            candidate["recommendation_tags"].append("UNVERIFIED_INPUT")
            candidate["exclusion_reasons"].append(
                "reference station metrics are missing or non-positive"
            )
            continue
        candidate["comparison"].update(
            {
                "h_l_at_reference_W_m2K": float(h_l),
                "friction_pressure_gradient_at_reference_Pa_m": float(gradient),
            }
        )
        eligible.append(candidate)

    if eligible:
        maximum_h = max(
            item["comparison"]["h_l_at_reference_W_m2K"] for item in eligible
        )
        minimum_gradient = min(
            item["comparison"][
                "friction_pressure_gradient_at_reference_Pa_m"
            ]
            for item in eligible
        )
        for candidate in eligible:
            values = candidate["comparison"]
            if _close(
                values["h_l_at_reference_W_m2K"],
                maximum_h,
                relative,
                h_absolute,
            ):
                candidate["recommendation_tags"].append(
                    "HIGHEST_HL_AT_REFERENCE"
                )
            if _close(
                values["friction_pressure_gradient_at_reference_Pa_m"],
                minimum_gradient,
                relative,
                gradient_absolute,
            ):
                candidate["recommendation_tags"].append(
                    "LOWEST_FRICTION_GRADIENT_AT_REFERENCE"
                )

        for candidate in eligible:
            dominated = any(
                _dominates(
                    other["comparison"],
                    candidate["comparison"],
                    relative,
                    h_absolute,
                    gradient_absolute,
                )
                for other in eligible
                if other is not candidate
            )
            if dominated:
                candidate["comparison"]["pareto_status"] = "DOMINATED"
            else:
                candidate["comparison"]["pareto_status"] = "NON_DOMINATED"
                candidate["recommendation_tags"].append("PARETO_CANDIDATE")

    eligible_ids = [item["candidate_id"] for item in eligible]
    highest_ids = [
        item["candidate_id"]
        for item in eligible
        if "HIGHEST_HL_AT_REFERENCE" in item["recommendation_tags"]
    ]
    lowest_ids = [
        item["candidate_id"]
        for item in eligible
        if "LOWEST_FRICTION_GRADIENT_AT_REFERENCE"
        in item["recommendation_tags"]
    ]
    pareto_ids = [
        item["candidate_id"]
        for item in eligible
        if "PARETO_CANDIDATE" in item["recommendation_tags"]
    ]
    if not eligible:
        status = "NO_VALID_CANDIDATES"
        reason = "no candidate passed geometry and full-path applicability checks"
    elif len(eligible) == 1:
        status = "SINGLE_VALID_CANDIDATE_NO_TRADEOFF"
        reason = "one valid candidate is reported, but no meaningful tradeoff comparison exists"
    else:
        status = "PRELIMINARY_RELATIVE_RECOMMENDATION"
        reason = "relative classification completed at one common reference station"

    return {
        "status": status,
        "reason": reason,
        "baseline_reference": baseline,
        "reference_station_id": reference_id,
        "reference_station_type": comparison.get("reference_station_type"),
        "comparison_method": (
            "MAXIMIZE_H_L_AND_MINIMIZE_LOCAL_DARCY_FRICTION_GRADIENT_"
            "AT_ONE_COMMON_STATION"
        ),
        "comparison_basis": "same supplied coolant flow and property conditions",
        "relative_tolerance": relative,
        "h_l_absolute_tolerance_W_m2K": h_absolute,
        "friction_gradient_absolute_tolerance_Pa_m": gradient_absolute,
        "eligible_candidate_ids": eligible_ids,
        "highest_h_l_candidate_ids": highest_ids,
        "lowest_friction_gradient_candidate_ids": lowest_ids,
        "pareto_candidate_ids": pareto_ids,
        "statement": PRELIMINARY_STATEMENT,
        "final_design_selection": False,
    }
