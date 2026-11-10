# SPACE Y Model H V1 헬리컬 냉각채널 구현 및 합성 검산

## 1. 범위와 상태

Model H V1은 기존 Model B의 Nusselt 수 계산을 변경하지 않고, 일정 헬릭스
각도를 갖는 직사각형 채널을 station별 1D 등가 직선 유로로 치환한다.

- 구현 상태: 코드 및 합성 산술 검산 완료
- 실험 검증: 미수행
- 실제 IPA 물성 검증: 미수행
- 실제 SPACE Y 엔진 예측: 아님
- V01–V12: `NOT_RUN`
- 안전 승인: 아님

Dean vortex, 곡률에 따른 천이 Reynolds 수 변화, 3D 이차유동 및 경험적
헬릭스 열전달 증진계수는 포함하지 않았다.

## 2. 기존 코드 분석 및 재사용 범위

| 역할 | 현재 코드 | Model H 처리 |
|---|---|---|
| Model B 국소 `h_l` | `cooling_model_b.py` | `evaluate_model_b_hl()` 직접 호출 |
| Model B 형상 후보 | `cooling_model_b.py` | floor/ceil 채널 수와 중복 제거 재사용 |
| 기존 공식 냉각 모듈 | `src/space_y/cooling_hc.py`, `coolant.py` | 변경하지 않음 |
| 후보 파이프라인 | `src/space_y/cooling_channel_*.py` | 변경하지 않음 |
| station/노즐 통합 | `liquid_engine_design_V1(3).py` | 변경하지 않음 |
| IPA 물성 | 외부 Property Adapter/통합 코드 | Model H는 station 물성만 소비 |
| 냉각 루프 | 외부 통합 코드 | 실제 헬리컬 구간 길이 인터페이스만 명시 |

기존 Model B의 코드와 수치 결과를 변경하지 않았다. Model H의 `β=0°`
회귀검증은 현재 작업공간의 Model B 구현을 기준으로 수행했다. Model B의
상관식 계수나 적용범위를 Model H가 독립적으로 수정하지 않는다.

## 3. 문헌과 구현의 관계

방법론적 배경은 Lv et al. (2023)의 station별 등가 직선 유로 접근이다.

> Lv, J., Du, G., Jin, P., & Li, R. (2023), “Heat Transfer Analysis and
> Structural Optimization for Spiral Channel Regenerative Cooling Thrust
> Chamber,” *International Journal of Aerospace Engineering*, Article 8628107.
> DOI: <https://doi.org/10.1155/2023/8628107>

공식 공개 원문: <https://onlinelibrary.wiley.com/doi/10.1155/2023/8628107>

논문의 spiral-channel 전용 Nu 식이나 등가 유속 보정식은 복제하지 않았다.
Model H는 형상으로 계산한 실제 유동 수직 단면적을 사용하므로 유속에
`1/cosβ`를 다시 곱하지 않는다.

## 4. Model H V1 계산식

헬릭스 각도 `β`는 국소 자오선 방향과 실제 채널 진행 방향 사이의 각도다.

```text
w_H = 2 π r_m cos(β) / N - b
A_perp,H = N w_H h
D_h,H = 2 w_H h / (w_H + h)
v_H = mdot_l / (rho_l A_perp,H)
ds_H = ds_B / cos(β)
Re_H = rho_l v_H D_h,H / mu_l
Pr_H = mu_l cp_l / k_l
h_l,H = Nu_H k_l / D_h,H
```

`ds_B`는 축방향 `dx`가 아니라 기존 Model B의 채널 중심선 자오선 길이
`hypot(dx, dr_m)`이다. 냉각유체 입구는 기존 규칙대로 마지막 station인
노즐 출구이며 그 위치에서 `s_H=0`이다.

### Model B 상관식 호출

- `Re_H < 2300`: 기존 Lee–Garimella H1 열적 입구영역 또는 선택된 기존
  완전 발달 모델
- `2300 <= Re_H < 3000`: 기존 Model B 선형 보간
- `Re_H >= 3000`: 기존 Gnielinski–Colebrook

열적 입구영역에서는 `z*_H=s_H/(Re_H Pr_H D_h,H)`를 Model B에 전달한다.
천이 마찰계수 및 압력손실은 기존 정책대로 계산하지 않는다.

## 5. 형상 후보

Model H 기본 템플릿은 다음 조합이다.

- 기준 폭: `0.8, 0.9, 1.2, 1.5, 2.0, 2.7, 3.0 mm`
- 리브: `0.9, 1.0, 1.2, 1.5 mm`
- 높이: `1.2, 1.5, 2.0, 3.0, 4.0, 5.0 mm`
- 템플릿: `7 × 4 × 6 = 168`
- 각도: `0°, 20°, 30°, 35°`
- floor/ceil 분기 전 템플릿–각도 조합: `672`

채널 수는 Model B와 동일하게 목 기준 `w0+b`로부터 floor/ceil 후보를
만든다. 동일한 base 후보의 `N, b, h`는 모든 각도에서 유지한다.

## 6. 공개 Python 인터페이스

```python
from model_h_v1 import evaluate_model_h_candidates

result = evaluate_model_h_candidates(
    x_m=x_station,
    mean_channel_radius_m=r_channel_center,
    throat_station_index=i_throat,
    station_ids=station_ids,
    mdot_total_kg_s=mdot_coolant,
    density_kg_m3=rho_by_station,
    viscosity_Pa_s=mu_by_station,
    cp_J_kgK=cp_by_station,
    conductivity_W_mK=k_by_station,
    roughness_m=roughness,
    thermal_boundary_condition="SHAH_LONDON_H1",
    fully_developed=True,
    colebrook_re_range=(3000.0, re_max_with_source),
    colebrook_relative_roughness_range=(rr_min, rr_max),
    channel_width_limits_m=(width_min, width_max),
    minimum_rib_thickness_m=rib_min,
    allowed_channel_heights_m=allowed_heights,
    helix_angles_deg=(0.0, 20.0, 30.0, 35.0),
    laminar_model="THERMAL_ENTRY",
    fluid_identity="2-propanol; CAS 67-63-0",
    property_data_classification="EXTERNAL_PROPERTY_ADAPTER",
    property_validity_status=property_status_by_station,
)
```

실제 엔진 계산에서는 물성값뿐 아니라 물성의 범위 판정 결과도 station별로
공급해야 한다. 미확인 실제 IPA 물성은 `INPUT_U`, 물성 범위 이탈은
`OUTSIDE`로 상위 통합 코드가 중단해야 한다.

## 7. 합성 검산 결과

실행 명령:

```powershell
python tools\verify_model_h_v1.py
```

검산 입력은 `SYNTHETIC_TEST_FLUID_NOT_IPA`이며 실제 엔진 형상·IPA 물성이
아니다. 기하 폭 제한도 계산 경로 확인을 위한 넓은 합성 범위다.

### 후보 수

| 항목 | 결과 |
|---|---:|
| 템플릿 | 168 |
| 템플릿–각도 초기 조합 | 672 |
| floor/ceil base 후보 | 336 |
| 4개 각도 확장 후보 | 1,344 |
| 합성 1D 기하 유효 후보 | 1,344 |

### `β=0°` Model B 회귀검증

336개 후보, 1,344개 station을 비교했다.

| 비교량 | 최대 절대차 |
|---|---:|
| 채널 폭 | 0 |
| 총 유동면적 | 0 |
| 수력직경 | 0 |
| 누적 유로거리 | 0 |
| Reynolds 수 | 0 |
| Prandtl 수 | 0 |
| Nusselt 수 | 0 |
| `h_l` | 0 |

회귀 상태는 `PASS`다. 이는 코드 경로의 동일성 검증이며 실험 검증이 아니다.

### 상관식 적용성

총 5,376개 angle–candidate–station 중 5,363개가 계산됐고 13개는
`OUTSIDE`로 중단됐다.

- `β=30°`: Lee–Garimella 장변/단변 범위 `[1,10]` 이탈 1개 station
- `β=35°`: 동일 사유 12개 station
- `INPUT_U`: 0
- `NUM_U`: 0

기하학적으로 양수인 폭이라고 해서 열전달 상관식까지 적용 가능한 것은
아님을 상태로 분리했다.

### 대표 후보의 각도 비교

대표 합성 후보: base `GCH-0139`, `N=58`, `b=1.5 mm`, `h=3.0 mm`.

| β | 유로길이 배율 | Re 최소–최대 | `h_l` 최소–최대 [W/m²K] |
|---:|---:|---:|---:|
| 0° | 1.0000 | 616.5–1489.8 | 304.7–1079.3 |
| 20° | 1.0642 | 647.7–1550.0 | 289.2–1178.4 |
| 30° | 1.1547 | 690.4–1630.4 | 269.8–1336.5 |
| 35° | 1.2208 | 720.6–1686.1 | 266.5–1472.8 |

각도 증가에 따른 `h_l` 단조 증가 조건은 부과하지 않았다. 표에서도 위치별
최솟값은 감소하고 최댓값은 증가하므로, 단일 지표로 헬리컬 형상을 안전하거나
우수하다고 판정할 수 없다.

## 8. 냉각 루프 연결 시 주의점

`h_l`에는 별도 `1/cosβ` 배율을 적용하지 않는다. 냉각 루프가 구간 열량을
계산할 때 실제 헬리컬 segment 길이 `ds_H`로 젖은 면적을 구성해야 한다.
`h_l`과 면적에 같은 헬릭스 배율을 중복 적용하면 안 된다.

본 모듈은 실제 젖은 면적이나 벽 열전도 해를 계산하지 않는다. 통합 담당자는
다음 인터페이스를 명시적으로 연결해야 한다.

- 입력: `segment_length_H_m`, 채널별 젖은 둘레, 실제 열적 경계
- 출력 소비: station별 `h_l_W_m2K`
- 금지: `h_l × 1/cosβ`와 `area × 1/cosβ`의 중복 보정

## 9. 남은 검증

- 실제 2-propanol 물성의 온도·압력·단상 범위 연결
- 실제 노즐 station 좌표와 채널 중심 반경
- 실제 가공 최소 폭·리브·높이와 3D 리브 간섭
- 헬리컬 채널 압력손실과 공급 압력 예산
- Dean vortex 및 곡률 천이 영향
- 벽/핀 효율과 실제 헬리컬 젖은 면적 통합
- CFD 또는 시험 데이터와의 물리 검증

현재 결과는 설계 승인, 제작 승인 또는 안전성 입증이 아니다.
