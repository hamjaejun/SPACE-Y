from __future__ import annotations

from itertools import product
import math
from typing import Any


def _positive_array(value: Any, name: str) -> list[float]:
    if not isinstance(value, list) or not value:
        raise ValueError(f"{name} must be a non-empty array")
    result = [float(item) for item in value]
    if any(not math.isfinite(item) or item <= 0 for item in result):
        raise ValueError(f"{name} entries must be finite and positive")
    return result


def generate_reference_width_templates(spec: dict[str, Any]) -> list[dict[str, Any]]:
    """Generate w_ref/b/h templates without inventing a throat radius or channel count."""

    if not isinstance(spec, dict):
        raise ValueError("template grid must be an object")
    source = spec.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("template grid source is required")
    widths = _positive_array(
        spec.get("reference_width_candidates_m"), "reference widths"
    )
    ribs = _positive_array(
        spec.get("rib_thickness_candidates_m"), "rib thicknesses"
    )
    heights = _positive_array(
        spec.get("channel_height_candidates_m"), "channel heights"
    )
    templates = []
    for index, (width, rib, height) in enumerate(
        product(widths, ribs, heights), start=1
    ):
        templates.append(
            {
                "template_id": f"CHT-{index:04d}",
                "reference_width_m": width,
                "rib_thickness_m": rib,
                "channel_height_m": height,
                "channel_count": None,
                "geometry_policy": "CONSTANT_RIB_WIDTH_AXIAL",
                "source": source.strip(),
                "classification": "REFERENCE_GRID_NOT_APPROVED_DESIGN_RANGE",
                "design_approved": False,
            }
        )
    return templates


def adjacent_channel_count_candidates(
    template: dict[str, Any], throat_mean_channel_radius_m: float
) -> dict[str, Any]:
    radius = float(throat_mean_channel_radius_m)
    if not math.isfinite(radius) or radius <= 0:
        raise ValueError("throat mean channel radius must be finite and positive")
    width = float(template["reference_width_m"])
    rib = float(template["rib_thickness_m"])
    ideal = 2.0 * math.pi * radius / (width + rib)
    lower = max(1, math.floor(ideal))
    upper = max(1, math.ceil(ideal))
    counts = sorted({lower, upper})
    widths = {
        str(count): 2.0 * math.pi * radius / count - rib
        for count in counts
    }
    return {
        "template_id": template.get("template_id"),
        "throat_mean_channel_radius_m": radius,
        "ideal_channel_count": ideal,
        "adjacent_integer_channel_counts": counts,
        "recalculated_throat_widths_m": widths,
        "geometric_validity_by_channel_count": {
            key: {
                "status": "PASS" if value > 0 else "OUTSIDE",
                "reason": (
                    "positive recalculated throat width"
                    if value > 0
                    else "recalculated throat width is non-positive"
                ),
            }
            for key, value in widths.items()
        },
        "selection_performed": False,
        "design_approved": False,
    }
