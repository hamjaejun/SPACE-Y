"""Standalone SPACE Y coolant-channel candidate sweep and Model B ``h_l``.

이 파일 하나로 80개 예비 형상 템플릿을 생성하고 목 기준 채널 수 후보를
구성한 뒤, 외부 엔진 모델이 공급한 위치별 형상·물성으로 ``h_l``을 계산한다.
다른 SPACE Y 모듈을 import하지 않으며 Python 표준 라이브러리만 사용한다.

Model selection
---------------
* ``Re < 2300``: Shah--London fully developed rectangular-duct correlation.
* ``2300 <= Re < 3000``: SPACE Y Model B linear Nusselt interpolation.
* ``Re >= 3000``: the existing Gnielinski--Colebrook calculation.

The interpolation is a SPACE Y preliminary modeling assumption.  It is not
presented as the complete Shah--London, VDI, Nellis--Klein, or Gnielinski
transition formulation and has not been experimentally validated for IPA or
the SPACE Y engine.

Reference context: Shah & London (1978) supplies the rectangular laminar
endpoints, while the existing turbulent path uses Gnielinski (1976) and
Colebrook (1939).  Nellis & Klein (2009) is retained only as literature context
for transition-region connection approaches; it is not cited as the source of
the complete SPACE Y rectangular interpolation.

단일 station 연결 예::

    from cooling_model_b import evaluate_model_b_hl

    def calc_h_l(T_l):
        result = evaluate_model_b_hl(
            mdot_total_kg_s=mdot_f,  # total regenerative-coolant flow
            channel_count=n_ch,
            width_m=w_ch,
            height_m=h_ch,
            viscosity_Pa_s=ipa_mu(T_l),
            cp_J_kgK=ipa_cp(T_l),
            conductivity_W_mK=ipa_k(T_l),
            roughness_m=roughness_m,
            thermal_boundary_condition=selected_thermal_bc,
            fully_developed=True,  # preliminary assumption, not validation
            colebrook_re_range=selected_colebrook_re_range,
            colebrook_relative_roughness_range=(rr_min, rr_max),
        )
        return result["h_l_W_m2K"]

``roughness_m`` is equivalent sand roughness.  It must not be confused with an
engine expansion ratio commonly named ``eps``.

전체 후보 순회는 :func:`evaluate_cooling_candidates`를 호출한다. 이 파일은
IPA 물성 생성, 노즐 생성, 벽온도, 유체 온도 적분, 상변화, 전체 열수지,
후보 점수화 또는 최적화를 수행하지 않는다.
"""

from __future__ import annotations

import json
import math
from itertools import product
from typing import Any, Sequence


# ============================================================
# 1. 냉각 채널 형상 후보 설정
# ============================================================

# 단위는 mm이다. 아래 값은 SPACE Y의 80개 예비 템플릿 그리드이며,
# 실제 제작 승인 범위나 최적 형상으로 해석하지 않는다.
WIDTH_CANDIDATES_MM = [0.9, 1.2, 1.5, 2.0, 2.7]
RIB_CANDIDATES_MM = [0.9, 1.0, 1.2, 1.5]
HEIGHT_CANDIDATES_MM = [1.2, 1.5, 2.0, 3.0]

MODEL_ID = "SPACE_Y_MODEL_B_LINEAR_2300_3000"
MODEL_B_ENTRY_ID = "SPACE_Y_MODEL_B_ENTRY_V1_LEE_GARIMELLA_2006_H1"
LAMINAR_MODEL_FULLY_DEVELOPED = "FULLY_DEVELOPED"
LAMINAR_MODEL_THERMAL_ENTRY = "THERMAL_ENTRY"
LAMINAR_MODELS = {
    LAMINAR_MODEL_FULLY_DEVELOPED,
    LAMINAR_MODEL_THERMAL_ENTRY,
}
GNIELINSKI_RE_RANGE = (3000.0, 5_000_000.0)
GNIELINSKI_PR_RANGE = (0.5, 2000.0)
THERMAL_BOUNDARY_CONDITIONS = {
    "CONSTANT_WALL_TEMPERATURE",
    "SHAH_LONDON_H1",
}


# ============================================================
# 2. 외부 입력 정의 및 검증
# ============================================================

class CoolingModelBError(RuntimeError):
    """기존 ``INPUT_U/OUTSIDE/NUM_U`` 상태를 보존하는 계산 중단 예외."""

    def __init__(
        self,
        status: str,
        message: str,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.details = details or {}


def _finite(value: float, name: str) -> float:
    """입력값을 유한 실수로 검증해 반환한다."""

    number = float(value)
    if not math.isfinite(number):
        raise CoolingModelBError("INPUT_U", f"{name} must be finite")
    return number


def _positive(value: float, name: str) -> float:
    """입력값을 양의 유한 실수로 검증해 반환한다."""

    number = _finite(value, name)
    if number <= 0.0:
        raise CoolingModelBError("INPUT_U", f"{name} must be positive")
    return number


def _range_pair(
    value: tuple[float, float] | None,
    name: str,
    *,
    allow_zero_minimum: bool = False,
) -> tuple[float, float]:
    """명시적 ``(최솟값, 최댓값)`` 적용범위를 검증한다."""

    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise CoolingModelBError("INPUT_U", f"{name} must be supplied as (min, max)")
    lower = _finite(value[0], f"{name} minimum")
    upper = _finite(value[1], f"{name} maximum")
    minimum_limit = 0.0 if allow_zero_minimum else math.nextafter(0.0, 1.0)
    if lower < minimum_limit or upper <= lower:
        raise CoolingModelBError("INPUT_U", f"invalid {name}")
    return lower, upper


def _snap_dispatch_boundary(reynolds: float) -> float:
    """부동소수점 오차로 Re=2300/3000 분기가 바뀌지 않게 보정한다."""

    for boundary in (2300.0, 3000.0):
        if math.isclose(reynolds, boundary, rel_tol=1.0e-12, abs_tol=1.0e-9):
            return boundary
    return reynolds


def _positive_integer(value: Any, name: str) -> int:
    """양의 정수 입력을 검증한다. bool은 정수로 허용하지 않는다."""

    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise CoolingModelBError("INPUT_U", f"{name} must be a positive integer")
    return value


def _numeric_station_values(
    value: float | Sequence[float],
    station_count: int,
    name: str,
    *,
    positive: bool,
) -> list[float]:
    """스칼라 또는 station 배열을 길이가 같은 SI 단위 배열로 변환한다.

    스칼라는 외부 모델이 모든 위치에 동일한 값을 명시적으로 공급했다는 뜻이다.
    누락값을 채우거나 물성을 보간하지 않는다.
    """

    if isinstance(value, (str, bytes, bool)):
        raise CoolingModelBError("INPUT_U", f"{name} must be numeric")
    if isinstance(value, Sequence):
        raw = list(value)
        if len(raw) != station_count:
            raise CoolingModelBError(
                "INPUT_U", f"{name} length must equal station count"
            )
    else:
        raw = [value] * station_count
    converter = _positive if positive else _finite
    return [converter(item, f"{name}[{index}]") for index, item in enumerate(raw)]


def _station_ids(value: Sequence[str] | None, station_count: int) -> list[str]:
    """외부 station ID를 검증하거나 비물리적인 순번 ID를 생성한다."""

    if value is None:
        return [f"STATION-{index + 1:04d}" for index in range(station_count)]
    if isinstance(value, (str, bytes)) or len(value) != station_count:
        raise CoolingModelBError(
            "INPUT_U", "station_ids length must equal station count"
        )
    result = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise CoolingModelBError(
                "INPUT_U", f"station_ids[{index}] must be non-empty text"
            )
        result.append(item.strip())
    if len(set(result)) != len(result):
        raise CoolingModelBError("INPUT_U", "station IDs must be unique")
    return result


def _positive_candidate_values_mm(value: Sequence[float], name: str) -> list[float]:
    """후보 그리드의 mm 값을 검증한다."""

    if isinstance(value, (str, bytes)) or not value:
        raise CoolingModelBError("INPUT_U", f"{name} must be a non-empty sequence")
    return [_positive(item, f"{name}[{index}]") for index, item in enumerate(value)]


def coolant_path_distances(
    x_m: Sequence[float], mean_channel_radius_m: Sequence[float]
) -> dict[str, list[float | None]]:
    """노즐 출구(마지막 station)부터 냉각유체 누적 유로거리를 계산한다.

    station 배열은 인젝터/챔버에서 노즐 출구 방향으로 정렬되어 있다고
    가정한다. 인접 거리에는 축방향과 채널 중심 반경 변화만 포함하며,
    입구 매니폴드나 별도 배관 길이는 포함하지 않는다.

    ``s_m[i] = sum(j=i..n-2, hypot(dx_j, dr_j))``이고 출구 station의
    ``s_m``은 0이다. ``entry_segment_length_m``은 출구 station에만 첫
    유한 구간 길이를 기록하여 입구 평균 Nu 계산에 사용한다.
    """

    if isinstance(x_m, (str, bytes)) or isinstance(
        mean_channel_radius_m, (str, bytes)
    ):
        raise CoolingModelBError("INPUT_U", "path coordinates must be sequences")
    x_values = [_finite(item, f"x_m[{i}]") for i, item in enumerate(x_m)]
    r_values = [
        _positive(item, f"mean_channel_radius_m[{i}]")
        for i, item in enumerate(mean_channel_radius_m)
    ]
    if len(x_values) < 2 or len(x_values) != len(r_values):
        raise CoolingModelBError(
            "INPUT_U", "path coordinates require equal lengths of at least two"
        )

    segment_lengths = [
        math.hypot(x_values[i + 1] - x_values[i], r_values[i + 1] - r_values[i])
        for i in range(len(x_values) - 1)
    ]
    if any(length <= 0.0 for length in segment_lengths):
        raise CoolingModelBError(
            "INPUT_U", "adjacent channel centerline stations must be distinct"
        )

    s_values = [0.0] * len(x_values)
    for i in range(len(x_values) - 2, -1, -1):
        s_values[i] = s_values[i + 1] + segment_lengths[i]
    entry_lengths: list[float | None] = [None] * len(x_values)
    entry_lengths[-1] = segment_lengths[-1]
    return {
        "s_m": s_values,
        "segment_length_to_next_station_m": segment_lengths + [None],
        "entry_segment_length_m": entry_lengths,
    }


# ============================================================
# 3. 형상 후보 생성
# ============================================================

def generate_channel_templates(
    *,
    width_candidates_mm: Sequence[float] = WIDTH_CANDIDATES_MM,
    rib_candidates_mm: Sequence[float] = RIB_CANDIDATES_MM,
    height_candidates_mm: Sequence[float] = HEIGHT_CANDIDATES_MM,
) -> list[dict[str, Any]]:
    """80개 기준 형상 템플릿을 결정론적 순서로 생성한다.

    입력은 모두 mm이고 반환 형상값은 m이다. 템플릿은 ``w_ref/b/h``만
    정의하며, 채널 수는 목의 평균 채널 반경이 공급된 뒤 결정한다.
    """

    widths = _positive_candidate_values_mm(width_candidates_mm, "width candidates")
    ribs = _positive_candidate_values_mm(rib_candidates_mm, "rib candidates")
    heights = _positive_candidate_values_mm(height_candidates_mm, "height candidates")
    templates: list[dict[str, Any]] = []
    for index, (width_mm, rib_mm, height_mm) in enumerate(
        product(widths, ribs, heights), start=1
    ):
        templates.append(
            {
                "template_id": f"CHT-{index:04d}",
                "reference_width_m": width_mm * 1.0e-3,
                "rib_thickness_m": rib_mm * 1.0e-3,
                "channel_height_m": height_mm * 1.0e-3,
                "channel_count": None,
                "geometry_policy": "CONSTANT_RIB_WIDTH_AXIAL",
                "classification": "REFERENCE_GRID_NOT_APPROVED_DESIGN_RANGE",
                "design_approved": False,
            }
        )
    return templates


def generate_channel_candidates(
    *,
    x_m: Sequence[float],
    mean_channel_radius_m: Sequence[float] | None = None,
    gas_side_radius_m: Sequence[float] | None = None,
    wall_thickness_m: float | None = None,
    throat_station_index: int,
    channel_width_limits_m: tuple[float, float],
    minimum_rib_thickness_m: float,
    allowed_channel_heights_m: Sequence[float],
    station_ids: Sequence[str] | None = None,
    width_candidates_mm: Sequence[float] = WIDTH_CANDIDATES_MM,
    rib_candidates_mm: Sequence[float] = RIB_CANDIDATES_MM,
    height_candidates_mm: Sequence[float] = HEIGHT_CANDIDATES_MM,
) -> dict[str, Any]:
    """목 기준 floor/ceil 채널 수와 위치별 형상을 생성한다.

    ``x_m``과 ``mean_channel_radius_m``는 동일 길이의 SI 배열이다. 통합
    엔진 코드처럼 가스 측 반경만 있는 경우에는 ``gas_side_radius_m``와
    ``wall_thickness_m``를 함께 공급하며, 각 후보의 중심 반경을
    ``r_gas + t_wall + h/2``로 계산한다. 두 반경 입력 모드를 섞지 않는다.
    각 템플릿에서 ``N_ideal=2*pi*r_m,t/(w_ref+b)``를 계산하고 서로 다른
    floor/ceil 정수를 검토한다. 물리적으로 같은 ``(N,b,h)``는 한 번만
    계산하되 모든 템플릿 출처를 보존한다. 폭 제한, 최소 rib, 허용 높이는
    외부 팀이 공급해야 하며 이 함수가 설계 허용값을 만들지 않는다.
    """

    if isinstance(x_m, (str, bytes)):
        raise CoolingModelBError("INPUT_U", "station coordinates must be sequences")
    x_values = [_finite(item, f"x_m[{index}]") for index, item in enumerate(x_m)]
    explicit_radius_mode = mean_channel_radius_m is not None
    gas_radius_mode = gas_side_radius_m is not None or wall_thickness_m is not None
    if explicit_radius_mode == gas_radius_mode:
        raise CoolingModelBError(
            "INPUT_U",
            "supply either mean_channel_radius_m or gas_side_radius_m with wall_thickness_m",
        )
    if explicit_radius_mode:
        if isinstance(mean_channel_radius_m, (str, bytes)):
            raise CoolingModelBError("INPUT_U", "mean channel radii must be a sequence")
        base_radius_values = [
            _positive(item, f"mean_channel_radius_m[{index}]")
            for index, item in enumerate(mean_channel_radius_m)
        ]
        wall_thickness = None
    else:
        if gas_side_radius_m is None or wall_thickness_m is None:
            raise CoolingModelBError(
                "INPUT_U", "gas-side radius mode requires radius and wall thickness"
            )
        if isinstance(gas_side_radius_m, (str, bytes)):
            raise CoolingModelBError("INPUT_U", "gas-side radii must be a sequence")
        base_radius_values = [
            _positive(item, f"gas_side_radius_m[{index}]")
            for index, item in enumerate(gas_side_radius_m)
        ]
        wall_thickness = _positive(wall_thickness_m, "wall thickness")
    if not x_values or len(x_values) != len(base_radius_values):
        raise CoolingModelBError(
            "INPUT_U", "x_m and radius input must have equal non-zero length"
        )
    if (
        isinstance(throat_station_index, bool)
        or not isinstance(throat_station_index, int)
        or not 0 <= throat_station_index < len(x_values)
    ):
        raise CoolingModelBError("INPUT_U", "throat station index is out of range")
    ids = _station_ids(station_ids, len(x_values))
    width_min, width_max = _range_pair(channel_width_limits_m, "channel width limits")
    minimum_rib = _positive(minimum_rib_thickness_m, "minimum rib thickness")
    if isinstance(allowed_channel_heights_m, (str, bytes)) or not allowed_channel_heights_m:
        raise CoolingModelBError("INPUT_U", "allowed channel heights are required")
    allowed_heights = [
        _positive(item, f"allowed channel heights[{index}]")
        for index, item in enumerate(allowed_channel_heights_m)
    ]

    templates = generate_channel_templates(
        width_candidates_mm=width_candidates_mm,
        rib_candidates_mm=rib_candidates_mm,
        height_candidates_mm=height_candidates_mm,
    )
    records: dict[tuple[int, float, float], dict[str, Any]] = {}
    raw_count = 0

    for template in templates:
        reference_width = float(template["reference_width_m"])
        rib = float(template["rib_thickness_m"])
        height = float(template["channel_height_m"])
        radius_values = (
            base_radius_values
            if wall_thickness is None
            else [radius + wall_thickness + 0.5 * height for radius in base_radius_values]
        )
        throat_radius = radius_values[throat_station_index]
        ideal_count = 2.0 * math.pi * throat_radius / (reference_width + rib)
        counts = sorted(
            {max(1, math.floor(ideal_count)), max(1, math.ceil(ideal_count))}
        )
        for channel_count in counts:
            raw_count += 1
            key = (channel_count, rib, height)
            actual_throat_width = 2.0 * math.pi * throat_radius / channel_count - rib
            origin = {
                "template_id": template["template_id"],
                "reference_width_m": reference_width,
                "ideal_channel_count": ideal_count,
                "actual_throat_width_m": actual_throat_width,
                "actual_minus_reference_width_m": actual_throat_width - reference_width,
            }
            if key in records:
                records[key]["template_origins"].append(origin)
                continue

            stations: list[dict[str, Any]] = []
            exclusion_reasons: list[str] = []
            for index, (station_id, x_value, radius) in enumerate(
                zip(ids, x_values, radius_values)
            ):
                pitch = 2.0 * math.pi * radius / channel_count
                width = pitch - rib
                geometry: dict[str, Any] | None = None
                station_reasons: list[str] = []
                if width <= 0.0:
                    station_reasons.append("channel width is non-positive")
                else:
                    geometry = rectangular_channel_geometry(width, height, channel_count)
                    if width < width_min:
                        station_reasons.append("channel width is below supplied minimum")
                    if width > width_max:
                        station_reasons.append("channel width is above supplied maximum")
                if station_reasons:
                    exclusion_reasons.extend(
                        f"{station_id}: {reason}" for reason in station_reasons
                    )
                stations.append(
                    {
                        "station_id": station_id,
                        "station_index": index,
                        "x_m": x_value,
                        "mean_channel_radius_m": radius,
                        "is_throat": index == throat_station_index,
                        "circumferential_pitch_m": pitch,
                        "channel_width_m": width,
                        "channel_height_m": height,
                        "flow_area_m2": (
                            None if geometry is None else geometry["total_flow_area_m2"]
                        ),
                        "hydraulic_diameter_m": (
                            None if geometry is None else geometry["hydraulic_diameter_m"]
                        ),
                        "geometric_validity_status": (
                            "PASS" if not station_reasons else "OUTSIDE"
                        ),
                        "geometric_reasons": station_reasons,
                    }
                )

            if rib < minimum_rib:
                exclusion_reasons.append("rib thickness is below supplied minimum")
            if not any(
                math.isclose(height, allowed, rel_tol=0.0, abs_tol=1.0e-12)
                for allowed in allowed_heights
            ):
                exclusion_reasons.append("channel height is not in supplied allowed set")
            valid = not exclusion_reasons
            candidate_id = f"GCH-{len(records) + 1:04d}"
            records[key] = {
                "candidate_id": candidate_id,
                "geometry_candidate_id": candidate_id,
                "valid": valid,
                "status": "PASS" if valid else "OUTSIDE",
                "channel_count": channel_count,
                "rib_thickness_m": rib,
                "channel_height_m": height,
                "geometry_policy": "CONSTANT_RIB_WIDTH_AXIAL",
                "template_origins": [origin],
                "geometry_exclusion_reasons": exclusion_reasons,
                "stations": stations,
                "design_approved": False,
            }

    candidates = list(records.values())
    return {
        "templates": templates,
        "candidates": candidates,
        "template_count": len(templates),
        "raw_candidate_count": raw_count,
        "unique_candidate_count": len(candidates),
        "valid_candidate_count": sum(item["valid"] for item in candidates),
        "invalid_candidate_count": sum(not item["valid"] for item in candidates),
        "selection_performed": False,
        "design_approved": False,
    }


# ============================================================
# 4. 기본 유동 및 열전달 계산
# ============================================================

def rectangular_channel_geometry(
    width_m: float,
    height_m: float,
    channel_count: int,
) -> dict[str, float | int]:
    """직사각형 채널의 면적, 총면적, 수력직경과 종횡비를 SI 단위로 반환한다."""

    width = _positive(width_m, "channel width")
    height = _positive(height_m, "channel height")
    if isinstance(channel_count, bool) or not isinstance(channel_count, int):
        raise CoolingModelBError("INPUT_U", "channel count must be an integer")
    if channel_count <= 0:
        raise CoolingModelBError("INPUT_U", "channel count must be positive")
    area = width * height
    hydraulic_diameter = 2.0 * width * height / (width + height)
    return {
        "width_m": width,
        "height_m": height,
        "channel_count": channel_count,
        "channel_flow_area_m2": area,
        "total_flow_area_m2": area * channel_count,
        "hydraulic_diameter_m": hydraulic_diameter,
        "aspect_ratio_min_over_max": min(width, height) / max(width, height),
    }


def rectangular_laminar_nusselt(aspect_ratio: float) -> tuple[float, float]:
    """Shah–London 직사각형 덕트의 ``(Nu_T, Nu_H1)``을 반환한다."""

    alpha = _finite(aspect_ratio, "rectangular aspect ratio")
    if not 0.0 < alpha <= 1.0:
        raise CoolingModelBError("INPUT_U", "aspect ratio must be in (0, 1]")
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


def lee_garimella_h1_thermal_entry_nusselt(
    *,
    reynolds: float,
    prandtl: float,
    hydraulic_diameter_m: float,
    aspect_ratio_min_over_max: float,
    s_m: float,
    first_segment_length_m: float | None = None,
) -> dict[str, Any]:
    """Lee & Garimella (2006)의 H1 열적 입구영역 Nu를 계산한다.

    출처: P.-S. Lee and S.V. Garimella, IJHMT 49 (2006) 3060–3067,
    DOI 10.1016/j.ijheatmasstransfer.2006.02.011.

    * 식 (10): 완전 발달 H1 Nu
    * 식 (11): 무차원 열 입구길이 ``z*_th``
    * 식 (12): 국소 Nu
    * 식 (13): 입구부터 유한 길이까지의 평균 Nu

    논문의 종횡비는 장변/단변 ``1 <= alpha <= 10``이다. 속도장은
    수력학적으로 완전 발달했다고 가정하며 H1(주위 방향 일정 벽온도,
    축방향 일정 열입력) 조건이다. 축방향 전도, 점성 소산, 물성 변화와
    속도·열경계층 동시 발달은 포함하지 않는다.
    """

    re_value = _positive(reynolds, "Reynolds number")
    pr_value = _positive(prandtl, "Prandtl number")
    diameter = _positive(hydraulic_diameter_m, "hydraulic diameter")
    distance = _finite(s_m, "coolant path distance")
    if distance < 0.0:
        raise CoolingModelBError("INPUT_U", "coolant path distance must be non-negative")
    alpha_minor = _finite(aspect_ratio_min_over_max, "rectangular aspect ratio")
    if not 0.0 < alpha_minor <= 1.0:
        raise CoolingModelBError("INPUT_U", "aspect ratio must be in (0, 1]")
    alpha = 1.0 / alpha_minor
    if not 1.0 <= alpha <= 10.0:
        raise CoolingModelBError(
            "OUTSIDE",
            "Lee-Garimella H1 correlation requires major/minor aspect ratio in [1, 10]",
            details={"aspect_ratio_major_over_minor": alpha},
        )

    _, nu_fully_developed = rectangular_laminar_nusselt(alpha_minor)
    z_star_thermal_end = (
        -1.275e-6 * alpha**6
        + 4.709e-5 * alpha**5
        - 6.902e-4 * alpha**4
        + 5.014e-3 * alpha**3
        - 1.769e-2 * alpha**2
        + 1.845e-2 * alpha
        + 5.691e-2
    )
    if z_star_thermal_end <= 0.0 or not math.isfinite(z_star_thermal_end):
        raise CoolingModelBError("NUM_U", "invalid Lee-Garimella entrance length")

    # 출구 첫 station은 s=0의 국소값 대신 cooling_loop가 열량을 적용하는
    # 첫 유한 구간의 입구-구간 평균값(논문 식 13)을 사용한다.
    if first_segment_length_m is not None:
        length = _positive(first_segment_length_m, "first segment length")
        if distance != 0.0:
            raise CoolingModelBError(
                "INPUT_U", "first-segment average is only valid at coolant inlet s=0"
            )
        z_star = length / (re_value * pr_value * diameter)
        if z_star >= z_star_thermal_end:
            raise CoolingModelBError(
                "OUTSIDE",
                "first segment exceeds Lee-Garimella Eq. (13) thermal-entry domain",
                details={
                    "dimensionless_distance": z_star,
                    "dimensionless_thermal_entry_end": z_star_thermal_end,
                },
            )
        c1 = (
            -2.757e-3 * alpha**3
            + 3.274e-2 * alpha**2
            - 7.464e-5 * alpha
            + 4.476
        )
        c2 = 6.391e-1
        c3 = 1.604e-4 * alpha**2 - 2.622e-3 * alpha + 2.568e-2
        c4 = (
            7.301
            - 1.311 * 10.0 ** (1.0 / alpha)
            + 1.519 * 10.0 ** (1.0 / alpha**2)
            - 6.094 / alpha**3
        )
        nusselt = 1.0 / (c1 * z_star**c2 + c3) + c4
        evaluation = "FIRST_FINITE_SEGMENT_AVERAGE_EQ_13"
        correlation_id = "LEE_GARIMELLA_2006_H1_AVERAGE_EQ_13"
    else:
        if distance <= 0.0:
            raise CoolingModelBError(
                "INPUT_U",
                "s=0 requires first_segment_length_m for finite average Nusselt",
            )
        z_star = distance / (re_value * pr_value * diameter)
        if z_star >= z_star_thermal_end:
            nusselt = nu_fully_developed
            evaluation = "FULLY_DEVELOPED_BEYOND_EQ_11_ENTRY_LENGTH"
            correlation_id = "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_H1"
        else:
            c1 = (
                -3.122e-3 * alpha**3
                + 2.435e-2 * alpha**2
                + 2.143e-1 * alpha
                + 7.325
            )
            c2 = 6.412e-1
            c3 = 1.589e-4 * alpha**2 - 2.603e-3 * alpha + 2.444e-2
            c4 = (
                7.148
                - 1.328 * 10.0 ** (1.0 / alpha)
                + 1.515 * 10.0 ** (1.0 / alpha**2)
                - 5.936 / alpha**3
            )
            nusselt = 1.0 / (c1 * z_star**c2 + c3) + c4
            evaluation = "LOCAL_THERMAL_ENTRY_EQ_12"
            correlation_id = "LEE_GARIMELLA_2006_H1_LOCAL_EQ_12"

    if not math.isfinite(nusselt) or nusselt <= 0.0:
        raise CoolingModelBError("NUM_U", "Lee-Garimella returned invalid Nusselt")
    return {
        "Nusselt": nusselt,
        "Nusselt_fully_developed_H1": nu_fully_developed,
        "dimensionless_distance": z_star,
        "dimensionless_thermal_entry_end": z_star_thermal_end,
        "aspect_ratio_major_over_minor": alpha,
        "evaluation": evaluation,
        "correlation_id": correlation_id,
        "source": "Lee and Garimella (2006), Eqs. (10)-(13)",
        "thermal_boundary_condition": "SHAH_LONDON_H1",
        "hydrodynamically_fully_developed": True,
        "thermally_developing": evaluation != "FULLY_DEVELOPED_BEYOND_EQ_11_ENTRY_LENGTH",
        "simultaneous_velocity_thermal_development_modelled": False,
    }


def colebrook_darcy_friction_factor(
    reynolds: float,
    relative_roughness: float,
    *,
    tolerance: float = 1.0e-12,
    max_iterations: int = 100,
) -> float:
    """다른 유동영역 보완 없이 Darcy 형식 Colebrook 식을 반복 계산한다."""

    re_value = _positive(reynolds, "Reynolds number")
    roughness = _finite(relative_roughness, "relative roughness")
    if roughness < 0.0:
        raise CoolingModelBError("INPUT_U", "relative roughness must be non-negative")
    if tolerance <= 0.0 or max_iterations <= 0:
        raise CoolingModelBError("INPUT_U", "Colebrook solver controls must be positive")
    factor = 0.02
    for _ in range(max_iterations):
        argument = roughness / 3.7 + 2.51 / (re_value * math.sqrt(factor))
        updated = 1.0 / (-2.0 * math.log10(argument)) ** 2
        if abs(updated - factor) <= tolerance * max(1.0, abs(updated)):
            return updated
        factor = updated
    raise CoolingModelBError("NUM_U", "Colebrook iteration did not converge")


def gnielinski_nusselt(reynolds: float, prandtl: float, darcy_factor: float) -> float:
    """검증된 무차원 입력으로 기존 Gnielinski 공통형의 Nu를 계산한다."""

    re_value = _positive(reynolds, "Reynolds number")
    pr_value = _positive(prandtl, "Prandtl number")
    factor = _positive(darcy_factor, "Darcy friction factor")
    numerator = (factor / 8.0) * (re_value - 1000.0) * pr_value
    denominator = 1.0 + 12.7 * math.sqrt(factor / 8.0) * (
        pr_value ** (2.0 / 3.0) - 1.0
    )
    if denominator == 0.0:
        raise CoolingModelBError("NUM_U", "Gnielinski denominator is zero")
    nusselt = numerator / denominator
    if not math.isfinite(nusselt) or nusselt <= 0.0:
        raise CoolingModelBError("OUTSIDE", "Gnielinski returned non-positive Nusselt")
    return nusselt


def linear_transition_nusselt(
    reynolds: float,
    laminar_endpoint_nusselt: float,
    turbulent_endpoint_nusselt: float,
) -> tuple[float, float]:
    """``2300<=Re<=3000``에서 SPACE Y Model B의 ``(Nu, gamma)``를 반환한다."""

    re_value = _finite(reynolds, "Reynolds number")
    nu_low = _positive(laminar_endpoint_nusselt, "laminar endpoint Nusselt")
    nu_high = _positive(turbulent_endpoint_nusselt, "turbulent endpoint Nusselt")
    if not 2300.0 <= re_value <= 3000.0:
        raise CoolingModelBError("OUTSIDE", "Model B interpolation requires 2300 <= Re <= 3000")
    gamma = (re_value - 2300.0) / 700.0
    return (1.0 - gamma) * nu_low + gamma * nu_high, gamma


def _check_in_range(value: float, bounds: tuple[float, float], name: str) -> None:
    """값이 공급된 적용범위를 벗어나면 ``OUTSIDE``로 중단한다."""

    if not bounds[0] <= value <= bounds[1]:
        raise CoolingModelBError(
            "OUTSIDE",
            f"{name}={value:.12g} is outside [{bounds[0]:.12g}, {bounds[1]:.12g}]",
        )


# ============================================================
# 5. Reynolds 수에 따른 상관식 선택
# ============================================================

def calculate_model_b_nusselt(
    *,
    reynolds: float,
    prandtl: float,
    relative_roughness: float,
    aspect_ratio: float,
    thermal_boundary_condition: str,
    colebrook_re_range: tuple[float, float] | None,
    colebrook_relative_roughness_range: tuple[float, float] | None,
    gnielinski_re_range: tuple[float, float] = GNIELINSKI_RE_RANGE,
    gnielinski_pr_range: tuple[float, float] = GNIELINSKI_PR_RANGE,
    laminar_model: str = LAMINAR_MODEL_FULLY_DEVELOPED,
    hydraulic_diameter_m: float | None = None,
    s_m: float | None = None,
    first_segment_length_m: float | None = None,
) -> dict[str, Any]:
    """Reynolds 수에 따라 Shah–London, Model B, Gnielinski를 선택한다.

    입력은 무차원 ``Re``, ``Pr``, 상대거칠기 ``epsilon/D_h``, 직사각형
    종횡비와 명시적 상관식 적용범위이다. 반환값은 Nu, 유동 영역,
    상관식 ID 및 사용 가능한 Darcy 계수이다. 2300<=Re<3000에서 Nu만
    보간하며 마찰계수와 압력손실은 계산하지 않는다.
    """

    re_value = _snap_dispatch_boundary(_positive(reynolds, "Reynolds number"))
    pr_value = _positive(prandtl, "Prandtl number")
    roughness = _finite(relative_roughness, "relative roughness")
    if roughness < 0.0:
        raise CoolingModelBError("INPUT_U", "relative roughness must be non-negative")
    if thermal_boundary_condition not in THERMAL_BOUNDARY_CONDITIONS:
        raise CoolingModelBError(
            "INPUT_U",
            "thermal boundary must be CONSTANT_WALL_TEMPERATURE or SHAH_LONDON_H1",
        )
    if laminar_model not in LAMINAR_MODELS:
        raise CoolingModelBError(
            "INPUT_U",
            "laminar_model must be FULLY_DEVELOPED or THERMAL_ENTRY",
        )

    nu_t, nu_h1 = rectangular_laminar_nusselt(aspect_ratio)
    fully_developed_laminar = (
        nu_t
        if thermal_boundary_condition == "CONSTANT_WALL_TEMPERATURE"
        else nu_h1
    )
    interpolation_fraction: float | None = None
    endpoint_factor: float | None = None
    endpoint_nusselt: float | None = None
    local_factor: float | None = None
    thermal_entry: dict[str, Any] | None = None

    def selected_laminar_nusselt(entry_reynolds: float) -> float:
        """선택된 층류 모델을 동일 위치 조건에서 평가한다."""

        nonlocal thermal_entry
        if laminar_model == LAMINAR_MODEL_FULLY_DEVELOPED:
            return fully_developed_laminar
        if thermal_boundary_condition != "SHAH_LONDON_H1":
            raise CoolingModelBError(
                "OUTSIDE",
                "THERMAL_ENTRY V1 supports only the Lee-Garimella H1 boundary condition",
            )
        if hydraulic_diameter_m is None or s_m is None:
            raise CoolingModelBError(
                "INPUT_U",
                "THERMAL_ENTRY requires hydraulic_diameter_m and coolant path distance s_m",
            )
        thermal_entry = lee_garimella_h1_thermal_entry_nusselt(
            reynolds=entry_reynolds,
            prandtl=pr_value,
            hydraulic_diameter_m=hydraulic_diameter_m,
            aspect_ratio_min_over_max=aspect_ratio,
            s_m=s_m,
            first_segment_length_m=first_segment_length_m,
        )
        return float(thermal_entry["Nusselt"])

    # ------------------------------------------------------------
    # Reynolds 수에 따라 Nusselt 계산식을 선택
    # ------------------------------------------------------------
    if re_value < 2300.0:
        # [층류] 선택에 따라 Shah–London 완전 발달 또는 H1 열적 입구영역
        flow_regime = "LAMINAR"
        nusselt = selected_laminar_nusselt(re_value)
        correlation_id = (
            str(thermal_entry["correlation_id"])
            if thermal_entry is not None
            else (
                "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_T"
                if thermal_boundary_condition == "CONSTANT_WALL_TEMPERATURE"
                else "SHAH_LONDON_1978_RECTANGULAR_DUCT_NU_H1"
            )
        )
        friction_status = "NOT_CALCULATED_H_L_ONLY_MODULE"
    else:
        # 층류 경로에는 필요하지 않은 난류 상관식 적용범위를 여기서 검증한다.
        heat_re_bounds = _range_pair(gnielinski_re_range, "Gnielinski Re range")
        heat_pr_bounds = _range_pair(gnielinski_pr_range, "Gnielinski Pr range")
        friction_re_bounds = _range_pair(colebrook_re_range, "Colebrook Re range")
        roughness_bounds = _range_pair(
            colebrook_relative_roughness_range,
            "Colebrook relative roughness range",
            allow_zero_minimum=True,
        )
        _check_in_range(pr_value, heat_pr_bounds, "Pr")
        _check_in_range(roughness, roughness_bounds, "relative roughness")

        if re_value < 3000.0:
            # [천이 보간] SPACE Y Model B 선형 보간식. 마찰은 미지원이다.
            _check_in_range(3000.0, heat_re_bounds, "Model B turbulent endpoint Re")
            _check_in_range(3000.0, friction_re_bounds, "Colebrook endpoint Re")
            endpoint_factor = colebrook_darcy_friction_factor(3000.0, roughness)
            endpoint_nusselt = gnielinski_nusselt(
                3000.0, pr_value, endpoint_factor
            )
            # 층류 끝값은 동일 위치·형상·Pr에서 Re=2300으로 다시 평가한다.
            # 입구영역 Nu가 난류 끝값보다 커도 임의 clipping하지 않는다.
            laminar_endpoint = selected_laminar_nusselt(2300.0)
            nusselt, interpolation_fraction = linear_transition_nusselt(
                re_value, laminar_endpoint, endpoint_nusselt
            )
            flow_regime = "TRANSITION_MODEL_B"
            correlation_id = MODEL_ID
            friction_status = "OUTSIDE_TRANSITION_FRICTION_UNSUPPORTED"
        else:
            # [난류] 기존 Gnielinski–Colebrook 경로
            _check_in_range(re_value, heat_re_bounds, "Re")
            _check_in_range(re_value, friction_re_bounds, "Colebrook Re")
            local_factor = colebrook_darcy_friction_factor(re_value, roughness)
            nusselt = gnielinski_nusselt(re_value, pr_value, local_factor)
            flow_regime = "TURBULENT"
            correlation_id = "GNIELINSKI_COLEBROOK_EXISTING_PATH"
            friction_status = "LOCAL_COLEBROOK_FACTOR_AVAILABLE"

    return {
        "Reynolds": re_value,
        "Prandtl": pr_value,
        "Nusselt": nusselt,
        "flow_regime": flow_regime,
        "correlation_id": correlation_id,
        "Nusselt_constant_wall_temperature": nu_t,
        "Nusselt_H1": nu_h1,
        "laminar_model": laminar_model,
        "Nusselt_fully_developed_laminar": fully_developed_laminar,
        "Nusselt_thermal_entry": (
            None if thermal_entry is None else thermal_entry["Nusselt"]
        ),
        "thermal_entry_diagnostics": thermal_entry,
        "interpolation_fraction": interpolation_fraction,
        "turbulent_endpoint_Nusselt_at_Re_3000": endpoint_nusselt,
        "turbulent_endpoint_darcy_factor_at_Re_3000": endpoint_factor,
        "local_darcy_factor": local_factor,
        "friction_status": friction_status,
    }


# ============================================================
# 6. 단일 station의 h_l 계산
# ============================================================

def evaluate_model_b_hl(
    *,
    mdot_total_kg_s: float,
    channel_count: int,
    width_m: float,
    height_m: float,
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
    s_m: float | None = None,
    first_segment_length_m: float | None = None,
) -> dict[str, Any]:
    """한 station의 냉각 측 ``h_l`` [W/(m²·K)]를 계산한다.

    ``mdot_total_kg_s``는 모든 병렬 채널을 지나는 총유량 [kg/s]이며 채널
    수로 균등 분배한다. 폭·높이·거칠기는 m, 점도는 Pa·s, 비열은
    J/(kg·K), 열전도율은 W/(m·K)이다. 두 Colebrook 적용범위는 기존
    SPACE Y 정책에 따라 매 실행에서 근거와 함께 외부 공급되어야 한다.
    """

    geometry = rectangular_channel_geometry(width_m, height_m, channel_count)
    mass_flow = _positive(mdot_total_kg_s, "total coolant mass flow")
    viscosity = _positive(viscosity_Pa_s, "dynamic viscosity")
    cp_value = _positive(cp_J_kgK, "specific heat")
    conductivity = _positive(conductivity_W_mK, "thermal conductivity")
    roughness = _finite(roughness_m, "equivalent sand roughness")
    if roughness < 0.0:
        raise CoolingModelBError("INPUT_U", "equivalent sand roughness must be non-negative")
    if (
        not isinstance(thermal_boundary_condition, str)
        or thermal_boundary_condition not in THERMAL_BOUNDARY_CONDITIONS
    ):
        raise CoolingModelBError(
            "INPUT_U",
            "thermal boundary must be CONSTANT_WALL_TEMPERATURE or SHAH_LONDON_H1",
        )
    if fully_developed is None:
        raise CoolingModelBError("INPUT_U", "fully developed assumption is unconfirmed")
    if not isinstance(fully_developed, bool):
        raise CoolingModelBError("INPUT_U", "fully_developed must be true, false, or null")
    if not fully_developed:
        raise CoolingModelBError("OUTSIDE", "Model B requires the fully developed assumption")

    total_area = float(geometry["total_flow_area_m2"])
    hydraulic_diameter = float(geometry["hydraulic_diameter_m"])
    reynolds = _snap_dispatch_boundary(
        mass_flow * hydraulic_diameter / (total_area * viscosity)
    )
    prandtl = viscosity * cp_value / conductivity
    relative_roughness = roughness / hydraulic_diameter
    model = calculate_model_b_nusselt(
        reynolds=reynolds,
        prandtl=prandtl,
        relative_roughness=relative_roughness,
        aspect_ratio=float(geometry["aspect_ratio_min_over_max"]),
        thermal_boundary_condition=thermal_boundary_condition,
        colebrook_re_range=colebrook_re_range,
        colebrook_relative_roughness_range=colebrook_relative_roughness_range,
        gnielinski_re_range=gnielinski_re_range,
        gnielinski_pr_range=gnielinski_pr_range,
        laminar_model=laminar_model,
        hydraulic_diameter_m=hydraulic_diameter,
        s_m=s_m,
        first_segment_length_m=first_segment_length_m,
    )

    h_l = float(model["Nusselt"]) * conductivity / hydraulic_diameter
    h_l_fully_developed = (
        float(model["Nusselt_fully_developed_laminar"])
        * conductivity
        / hydraulic_diameter
    )
    h_l_thermal_entry = (
        None
        if model["Nusselt_thermal_entry"] is None
        else float(model["Nusselt_thermal_entry"])
        * conductivity
        / hydraulic_diameter
    )
    return {
        "status": "MODEL_U",
        "model_id": (
            MODEL_B_ENTRY_ID
            if laminar_model == LAMINAR_MODEL_THERMAL_ENTRY
            else MODEL_ID
        ),
        "flow_regime": model["flow_regime"],
        "correlation_id": model["correlation_id"],
        "thermal_boundary_condition": thermal_boundary_condition,
        "Reynolds": reynolds,
        "Prandtl": prandtl,
        "Nusselt": model["Nusselt"],
        "h_l_W_m2K": h_l,
        "laminar_model": laminar_model,
        "s_m": s_m,
        "first_segment_length_m": first_segment_length_m,
        "Nusselt_fully_developed_laminar": model[
            "Nusselt_fully_developed_laminar"
        ],
        "Nusselt_thermal_entry": model["Nusselt_thermal_entry"],
        "h_l_fully_developed_reference_W_m2K": h_l_fully_developed,
        "h_l_thermal_entry_reference_W_m2K": h_l_thermal_entry,
        "thermal_entry_diagnostics": model["thermal_entry_diagnostics"],
        "channel_flow_area_m2": geometry["channel_flow_area_m2"],
        "total_flow_area_m2": total_area,
        "hydraulic_diameter_m": hydraulic_diameter,
        "channel_aspect_ratio_min_over_max": geometry[
            "aspect_ratio_min_over_max"
        ],
        "mass_flow_total_kg_s": mass_flow,
        "mass_flow_per_channel_kg_s": mass_flow / channel_count,
        "relative_roughness": relative_roughness,
        "Nusselt_constant_wall_temperature": model[
            "Nusselt_constant_wall_temperature"
        ],
        "Nusselt_H1": model["Nusselt_H1"],
        "interpolation_fraction": model["interpolation_fraction"],
        "turbulent_endpoint_Nusselt_at_Re_3000": model[
            "turbulent_endpoint_Nusselt_at_Re_3000"
        ],
        "turbulent_endpoint_darcy_factor_at_Re_3000": model[
            "turbulent_endpoint_darcy_factor_at_Re_3000"
        ],
        "local_darcy_factor": model["local_darcy_factor"],
        "friction_status": model["friction_status"],
        "pressure_loss_computed": False,
        "fully_developed": True,
        "hydrodynamically_fully_developed": True,
        "thermally_fully_developed": (
            laminar_model == LAMINAR_MODEL_FULLY_DEVELOPED
        ),
        "stationwise_quasi_local_application": True,
        "entrance_effects_modelled": model["thermal_entry_diagnostics"] is not None,
        "simultaneous_velocity_thermal_development_modelled": False,
        "experimental_validation": False,
        "validated_engine_prediction": False,
        "safety_approval": False,
    }


# ============================================================
# 7. 전체 냉각 채널 후보 및 station 순회
# ============================================================

def evaluate_cooling_candidates(
    *,
    x_m: Sequence[float],
    mean_channel_radius_m: Sequence[float],
    throat_station_index: int,
    mdot_total_kg_s: float,
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
    width_candidates_mm: Sequence[float] = WIDTH_CANDIDATES_MM,
    rib_candidates_mm: Sequence[float] = RIB_CANDIDATES_MM,
    height_candidates_mm: Sequence[float] = HEIGHT_CANDIDATES_MM,
) -> dict[str, Any]:
    """전체 형상 후보와 모든 station의 Model B ``h_l``을 계산한다.

    노즐 위치/평균 채널 반경은 m, 총 냉각유량은 kg/s, 점도는 Pa·s,
    비열은 J/(kg·K), 열전도율은 W/(m·K), 거칠기는 m로 받는다.
    물성은 station 길이의 배열 또는 명시적 상수 스칼라로 공급한다.
    유량은 모든 병렬 채널에 균등 분배된다고 가정한다.

    반환값에는 유효/무효 후보를 모두 보존하고, 유효 후보의 각 station에
    Re, Pr, Nu, ``h_l``과 상태를 연결한다. 함수는 최적 형상을 선정하지 않는다.
    """

    if isinstance(x_m, (str, bytes)):
        raise CoolingModelBError("INPUT_U", "x_m must be a sequence")
    station_count = len(x_m)
    if station_count <= 0:
        raise CoolingModelBError("INPUT_U", "at least one station is required")
    if thermal_boundary_condition not in THERMAL_BOUNDARY_CONDITIONS:
        raise CoolingModelBError(
            "INPUT_U",
            "thermal boundary must be CONSTANT_WALL_TEMPERATURE or SHAH_LONDON_H1",
        )
    if laminar_model not in LAMINAR_MODELS:
        raise CoolingModelBError(
            "INPUT_U", "laminar_model must be FULLY_DEVELOPED or THERMAL_ENTRY"
        )
    if fully_developed is None:
        raise CoolingModelBError("INPUT_U", "fully developed assumption is unconfirmed")
    if not isinstance(fully_developed, bool):
        raise CoolingModelBError("INPUT_U", "fully_developed must be boolean")
    if not fully_developed:
        raise CoolingModelBError(
            "OUTSIDE", "Model B requires the fully developed assumption"
        )
    # 후보군의 유동영역을 계산하기 전에 필수 상관식 범위가 실제로
    # 공급됐는지 확인한다. 이 검사는 범위 안이라는 물리 검증을 대신하지 않는다.
    _range_pair(gnielinski_re_range, "Gnielinski Re range")
    _range_pair(gnielinski_pr_range, "Gnielinski Pr range")
    _range_pair(colebrook_re_range, "Colebrook Re range")
    _range_pair(
        colebrook_relative_roughness_range,
        "Colebrook relative roughness range",
        allow_zero_minimum=True,
    )
    viscosity_values = _numeric_station_values(
        viscosity_Pa_s, station_count, "viscosity_Pa_s", positive=True
    )
    cp_values = _numeric_station_values(
        cp_J_kgK, station_count, "cp_J_kgK", positive=True
    )
    conductivity_values = _numeric_station_values(
        conductivity_W_mK, station_count, "conductivity_W_mK", positive=True
    )
    mass_flow = _positive(mdot_total_kg_s, "total coolant mass flow")
    roughness = _finite(roughness_m, "equivalent sand roughness")
    if roughness < 0.0:
        raise CoolingModelBError(
            "INPUT_U", "equivalent sand roughness must be non-negative"
        )

    geometry_result = generate_channel_candidates(
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
    path = (
        coolant_path_distances(x_m, mean_channel_radius_m)
        if laminar_model == LAMINAR_MODEL_THERMAL_ENTRY
        else None
    )

    evaluated_candidates: list[dict[str, Any]] = []
    successful_station_count = 0
    failed_station_count = 0
    regime_counts = {"LAMINAR": 0, "TRANSITION_MODEL_B": 0, "TURBULENT": 0}

    for candidate in geometry_result["candidates"]:
        station_results: list[dict[str, Any]] = []
        h_values: list[float] = []

        if not candidate["valid"]:
            # [기하 조건 미충족] 계산하지 않고 사유와 null 결과를 보존한다.
            for station in candidate["stations"]:
                station_results.append(
                    {
                        **station,
                        "status": "OUTSIDE",
                        "reason": "candidate violates supplied geometric constraints",
                        "Reynolds": None,
                        "Prandtl": None,
                        "Nusselt": None,
                        "h_l_W_m2K": None,
                        "flow_regime": None,
                        "correlation_id": None,
                    }
                )
            evaluated_candidates.append(
                {
                    **candidate,
                    "stations": station_results,
                    "summary": {
                        "successful_station_count": 0,
                        "failed_station_count": 0,
                        "minimum_h_l_W_m2K": None,
                        "maximum_h_l_W_m2K": None,
                        "throat_h_l_W_m2K": None,
                    },
                }
            )
            continue

        # [후보 선택] 유효한 한 개의 N/b/h 형상을 모든 station에서 계산한다.
        for station in candidate["stations"]:
            station_index = int(station["station_index"])
            try:
                # [Model B 호출] 외부에서 받은 해당 위치 물성만 사용한다.
                local = evaluate_model_b_hl(
                    mdot_total_kg_s=mass_flow,
                    channel_count=int(candidate["channel_count"]),
                    width_m=float(station["channel_width_m"]),
                    height_m=float(candidate["channel_height_m"]),
                    viscosity_Pa_s=viscosity_values[station_index],
                    cp_J_kgK=cp_values[station_index],
                    conductivity_W_mK=conductivity_values[station_index],
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
                    s_m=(None if path is None else path["s_m"][station_index]),
                    first_segment_length_m=(
                        None
                        if path is None
                        else path["entry_segment_length_m"][station_index]
                    ),
                )
            except CoolingModelBError as error:
                # [계산 실패] 후보 전체를 정상으로 위장하지 않고 위치별 사유를 남긴다.
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
                        "flow_regime": error.details.get("flow_regime"),
                        "correlation_id": None,
                    }
                )
                continue

            successful_station_count += 1
            regime_counts[local["flow_regime"]] += 1
            h_values.append(float(local["h_l_W_m2K"]))
            station_results.append({**station, **local, "reason": "calculation completed"})

        station_successes = sum(item["status"] == "MODEL_U" for item in station_results)
        station_failures = len(station_results) - station_successes
        throat_result = next(item for item in station_results if item["is_throat"])
        evaluated_candidates.append(
            {
                **candidate,
                "status": "MODEL_U" if station_failures == 0 else "OUTSIDE",
                "stations": station_results,
                "summary": {
                    "successful_station_count": station_successes,
                    "failed_station_count": station_failures,
                    "minimum_h_l_W_m2K": min(h_values) if h_values else None,
                    "maximum_h_l_W_m2K": max(h_values) if h_values else None,
                    "throat_h_l_W_m2K": throat_result.get("h_l_W_m2K"),
                },
            }
        )

    valid_station_target = geometry_result["valid_candidate_count"] * station_count
    overall_status = (
        "MODEL_U"
        if valid_station_target > 0 and successful_station_count == valid_station_target
        else "OUTSIDE"
    )

    # ============================================================
    # 8. 결과 정리 및 반환
    # ============================================================
    return {
        "status": overall_status,
        "model_id": (
            MODEL_B_ENTRY_ID
            if laminar_model == LAMINAR_MODEL_THERMAL_ENTRY
            else MODEL_ID
        ),
        "laminar_model": laminar_model,
        "input_classification": "EXTERNAL_DATA_NOT_VALIDATED_BY_THIS_MODULE",
        "template_count": geometry_result["template_count"],
        "raw_candidate_count": geometry_result["raw_candidate_count"],
        "unique_candidate_count": geometry_result["unique_candidate_count"],
        "valid_candidate_count": geometry_result["valid_candidate_count"],
        "invalid_candidate_count": geometry_result["invalid_candidate_count"],
        "station_count_per_candidate": station_count,
        "valid_station_target_count": valid_station_target,
        "successful_station_count": successful_station_count,
        "failed_station_count": failed_station_count,
        "flow_regime_counts": regime_counts,
        "mass_flow_total_kg_s": mass_flow,
        "equal_parallel_channel_flow_assumed": True,
        "templates": geometry_result["templates"],
        "candidates": evaluated_candidates,
        "selection_performed": False,
        "final_design_selection": False,
        "experimental_validation": False,
        "validated_engine_prediction": False,
        "physical_validation_v01_v12": "NOT_RUN",
        "safety_approval": False,
        "limitations": [
            "Equal flow distribution among parallel channels is assumed, not validated.",
            "Fully developed quasi-local use does not model entrance effects.",
            "Transition Model B is a SPACE Y internal linear interpolation.",
            "Transition friction factor and pressure loss are not calculated.",
            "No property model, phase check, wall solution, heat balance, or optimization is included.",
        ],
    }


# ============================================================
# 9. 실행 예제 및 간단한 검산
# ============================================================

def _synthetic_local_self_check() -> list[dict[str, float | str]]:
    """경계 영역의 소규모 산술 검산을 수행한다. IPA 검증이 아니다."""

    rows: list[dict[str, float | str]] = []
    for reynolds in (1000.0, 2300.0, 2500.0, 3000.0, 10_000.0):
        width = height = 0.01
        viscosity = 0.001
        area = width * height
        diameter = 2.0 * width * height / (width + height)
        mass_flow = reynolds * area * viscosity / diameter
        result = evaluate_model_b_hl(
            mdot_total_kg_s=mass_flow,
            channel_count=1,
            width_m=width,
            height_m=height,
            viscosity_Pa_s=viscosity,
            cp_J_kgK=2000.0,
            conductivity_W_mK=0.15,
            roughness_m=1.0e-6,
            thermal_boundary_condition="CONSTANT_WALL_TEMPERATURE",
            fully_developed=True,
            colebrook_re_range=(3000.0, 5_000_000.0),
            colebrook_relative_roughness_range=(0.0, 0.05),
        )
        rows.append(
            {
                "Re": result["Reynolds"],
                "regime": result["flow_regime"],
                "Nu": result["Nusselt"],
                "h_l_W_m2K": result["h_l_W_m2K"],
            }
        )
    return rows


def _synthetic_candidate_self_check() -> dict[str, Any]:
    """기존 80→160→156 및 468 station 합성 fixture를 재현한다."""

    result = evaluate_cooling_candidates(
        x_m=[-0.1, 0.0, 0.1],
        mean_channel_radius_m=[0.03, 0.02, 0.04],
        throat_station_index=1,
        station_ids=["SYN-INLET", "SYN-THROAT", "SYN-EXIT"],
        mdot_total_kg_s=0.6,
        viscosity_Pa_s=[0.001, 0.001, 0.001],
        cp_J_kgK=[2000.0, 2000.0, 2000.0],
        conductivity_W_mK=[0.15, 0.15, 0.15],
        roughness_m=0.000012,
        thermal_boundary_condition="SHAH_LONDON_H1",
        fully_developed=True,
        colebrook_re_range=(3000.0, 10_000_000.0),
        colebrook_relative_roughness_range=(0.0, 0.1),
        channel_width_limits_m=(0.0005, 0.007),
        minimum_rib_thickness_m=0.0009,
        allowed_channel_heights_m=[0.0012, 0.0015, 0.002, 0.003],
    )
    valid = [item for item in result["candidates"] if item["valid"]]
    representative = None
    if valid:
        station = valid[0]["stations"][0]
        representative = {
            "candidate_id": valid[0]["candidate_id"],
            "station_id": station["station_id"],
            "Reynolds": station["Reynolds"],
            "Nusselt": station["Nusselt"],
            "h_l_W_m2K": station["h_l_W_m2K"],
            "flow_regime": station["flow_regime"],
        }
    return {
        "fixture_classification": "SYNTHETIC_TEST_FLUID_NOT_IPA",
        "template_count": result["template_count"],
        "unique_candidate_count": result["unique_candidate_count"],
        "valid_candidate_count": result["valid_candidate_count"],
        "station_count_per_candidate": result["station_count_per_candidate"],
        "successful_station_count": result["successful_station_count"],
        "representative_result": representative,
        "experimental_validation": False,
        "validated_engine_prediction": False,
    }


if __name__ == "__main__":
    print(
        json.dumps(
            {
                "fixture_classification": "SYNTHETIC_ARITHMETIC_CHECK_NOT_IPA",
                "experimental_validation": False,
                "boundary_rows": _synthetic_local_self_check(),
                "candidate_sweep": _synthetic_candidate_self_check(),
            },
            indent=2,
        )
    )
