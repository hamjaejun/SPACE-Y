# SPACE Y Liquid Film Cooling V1 — M0/M1 구현 기록

## 상태 요약

- 구현 상태: M0 완료, M1의 유량 분기·Couette 기준 모델·적용성 게이트 완료
- 실제 Film ON 엔진 벽온도 계산: `INPUT_U`
- 실제 IPA 물성/상평형 검증: 미완료
- Stechman Eq. 6 수치 실행: `MODEL_U`
- V01–V12: `NOT_RUN`
- 실제 엔진 예측 검증: `false`

이 기록에서 단위 테스트 통과는 산술·인터페이스 검산을 뜻하며 액막 열전달의 실험 검증을 뜻하지 않는다.

## M0 — 기존 코드와 데이터 흐름

저장소의 기준 계산 계층은 다음과 같다.

1. `src/space_y/gas.py`, `bartz.py`, `gas_wall.py`: 명시적 CEA station 매핑과 Bartz 벽온도 반복
2. `src/space_y/coolant.py`: 냉각 유량 분배, Model B 열전달, 마찰 및 적용성 상태
3. `src/space_y/m0_coupled.py`: 단일 구간 GAS–WALL–COOLANT 반복
4. `src/space_y/m0_marching.py`: 단일 냉각 경로 다구간 상태 전달
5. `model_h_v1.py`, `model_h_engine_integration.py`: 고정 헬릭스 형상과 제공 엔진 입력의 검증 계층
6. `src/space_y/property_adapter.py`: 실제 IPA backend 준비 인터페이스. 현재 실제 엔진 실행 가능 backend는 승인되지 않았다.

기존 가스 측 모델 선정과 Bartz 구현, Model B 천이영역, Axial/Helical 형상 계산은 변경하지 않았다. 새 모듈은 기존 `resolve_coolant_flow()`를 재사용하여 필름이 재생냉각 이전에 분기되는 질량수지를 감사한다.

현재 저장소에는 head 시작 좌표와 가변 헬릭스 전체 엔진 냉각 루프가 하나의 공개 API로 통합되어 있지 않다. 따라서 주입 위치와 3종 형상의 실제 54개 사례를 실행하지 않았다.

## 문헌 확인

확인한 공개 원문:

- F. Giambelli et al., “Liquid Film Cooling: Open-Literature Review, Modeling and Efforts Towards Validation,” EUCASS 2023, DOI `10.13009/EUCASS2023-648`.
- G. P. Sutton and O. Biblarz, *Rocket Propulsion Elements*, 9th ed., Wiley, Chapter 8. 공개된 Wiley 목차와 지시된 §8.2/§8.5 범위를 기준으로 사용했으며, 교재가 직접 제공하지 않는 액막 경험계수는 추가하지 않았다.

확인된 관계:

- Eq. 17: 선형 Couette 액막의 운동량·질량 관계와 `tau = 0.5 C_f P_D`
- Eq. 6: Giambelli가 전재한 Stechman 액막–벽 열전달계수
- Eq. 20–21: McKeon 마찰계수와 Friend–Metzner Stanton 관계. 두 식은 완전 발달 관내 유동에 적합하다고 논문이 명시한다.
- Eq. 26/36: 축방향 에너지·질량·운동량 marching 구조

Eq. 17의 선형 속도분포를 적분하면 다음 산술 기준식을 얻는다.

`Gamma = rho * u_avg * delta = rho * tau * delta^2 / (2 mu)`

따라서:

`delta = sqrt(2 mu Gamma / (rho tau))`

여기서 `Gamma = mdot_film / (2 pi r phi_c)`이다. 구현은 가스 전단응력 `tau`를 외부 입력으로 요구한다. 완전 발달 원형관 상관식을 수축부·확장부에 자동 적용하지 않는다.

Stechman Eq. 6은 계수 `0.0288`과 식 형태를 확인했으나, 현재 증거만으로 원 Stechman 논문의 단위계와 `zeta`, `chi`, `g_s`의 추적 가능한 정의·적용범위를 확정하지 못했다. 따라서 숫자를 반환하지 않고 `MODEL_U`로 중단한다. `k/delta`는 별도의 전도 참고값이며 Stechman 대체식으로 취급하지 않는다.

Sutton & Biblarz §8.5의 1D 열저항 관계는 가스측·벽·냉각재측 면적과 열전달계수가 축방향으로 변하며, 총 열량은 국소 면적에 대한 적분으로 계산해야 함을 강조한다. 이 원칙을 이용해 고온가스 측 곡면 면적과 재생냉각 채널 면적을 서로 다른 면적 기준으로 유지했다. 교재의 비등 영역 논의에 따라 벌크 subcooling과 벽면 비등 판정을 분리했으며, ONB 상관식은 새로 만들지 않았다.

## 구현 파일

### `src/space_y/liquid_film.py`

- `resolve_upstream_film_split()`: 전체 연료에서 필름을 먼저 분기하고 기존 냉각 유량 감사 로직으로 질량보존 확인
- `core_oxidizer_fuel_ratio()`: 분기 후 core 연료유량으로 O/F 계산
- `build_film_case_matrix()`: Axial/Fixed Helical/Variable Helical × F05–F15 × C100/C75/C50의 54개 사례 생성
- `film_off_baselines()`: 형상별 Film OFF 기준선 3개
- `couette_film_state()`: 외부에서 검증된 가스 전단을 입력받는 완전 발달 Couette 기준값
- `finite_volume_cell_centers_m()`: 주입점 `s=0` 대신 첫 유한체적 중심에서 평가할 거리 생성
- `film_wall_conduction_reference_W_m2K()`: `h=k/delta` 참고 모델, `MODEL_U`
- `stechman_film_wall_heat_transfer_W_m2K()`: 원문 단위·변수 증거 미확보 시 명시적 `MODEL_U`
- `wet_dry_area_average_heat_flux_W_m2()`, `wet_dry_heat_rate_W()`: 부분 피복 면적 합산
- `march_film_energy_step()`: `q_gf - q_fw = mdot cp DeltaT` 단일 셀 에너지 보존
- `assess_liquid_film_state()`: 압력·물성범위·포화온도 게이트
- `film_v1_readiness_record()`: 구현과 차단 항목의 기계 판독 기록
- `resolve_film_injection_location()`: `x_head`와 직접 벽면 액막 시작 `x_film_inj`를 별도 provenance로 기록. V1에서는 수치적으로 같지만 물리적 의미는 구분
- `gas_side_segment_area_m2()`: piecewise-linear 곡면에서 `A=pi(r_i+r_{i+1})sqrt(dx^2+dr^2)`로 고온가스 측 면적 적분
- `partition_wet_dry_gas_area()`: `A_wet=phi A_g`, `A_dry=(1-phi)A_g`와 면적 잔차
- `audit_heat_transfer_area_bases()`: 고온가스 면적과 Axial/Helical 냉각면적을 별도 기준으로 보존
- `reconcile_wet_dry_with_regenerative_heat_W()`: Wet/Dry 개별 열량 합과 재생냉각 열량 잔차
- `assess_regenerative_coolant_phase()`: bulk subcooling과 coolant-side wall boiling을 분리. ONB 입력이 없으면 벽 판정 `INPUT_U`
- `build_v1_comparison_matrix()`: Film OFF 3개, Split Without Protection 18개, Film ON 54개를 구분
- `heat_transfer_model_sensitivity_interfaces()`: Stechman/전도 참고 모델의 상태만 노출하며 보정 배율은 허용하지 않음
- `separate_thermal_and_propulsion_outputs()`: 열성능과 추진성능을 별도 namespace로 기록하고 자동 최적화 금지

### `tests/test_liquid_film.py`

25개 독립 산술·오류 전파 테스트를 추가했다.

## 검증 범위

검증됨:

- 54개 Film ON ID의 결정론적 생성
- Film OFF 3개 기준선 구조
- `mdot_total = mdot_film + mdot_regen`
- 채널별 균등 분배 질량보존
- core O/F 산술
- 피복된 원주에 필름 유량이 한 번만 배분됨
- Couette 두께·속도·질량 항등식
- 음수/0 전단 차단
- 첫 셀 중심이 `s>0`이며 격자 세분화에도 특이점 회피
- Wet/Dry 면적 합산
- 단일 셀 에너지 잔차
- 물성범위·포화·압력 누락 상태 구분
- Stechman 미확인 입력에서 계산 강행 금지
- `x_head == x_film_inj` 기본값에서도 두 좌표의 의미와 출처 분리
- 주입 전 구간의 경계가 기존 Dry Wall임을 명시
- 곡면 station 면적이 축방향 투영면적이 아닌 자오선 거리로 계산됨
- Coverage 100/75/50%에서 Wet+Dry 면적보존 및 C100 Dry 면적 0
- 고온가스 면적과 재생냉각 채널 면적을 혼동하지 않음
- Wet/Dry 열량 합과 재생냉각 열량 잔차
- 재생냉각 bulk subcooling과 벽면 ONB 미확정을 별도 상태로 처리
- 필름 평균온도와 표면온도를 동일시하지 않음
- Film OFF / Split Without Protection / Film ON 사례 수와 경계조건 분리
- 열성능과 추진성능을 가중합하거나 자동 최적화하지 않음

미검증 또는 미구현:

- 실제 head 주입 좌표와 세 형상의 station 연결
- 각 필름 분율별 CEA core O/F 재실행
- 수축/확장 형상에 적용 가능한 가스 전단 closure
- Stechman 원문 단위계와 경험변수 provenance
- 실제 IPA `rho, mu, cp, k, h, T_sat(p)` backend
- Wet 영역의 가스–필름–벽–재생냉각 동시 반복
- Dry 영역과 Wet 영역의 독립 벽온도 및 최고온도
- 방사 입력
- 54개 Film ON 실제 실행
- N/2N/4N 공간분할 수렴
- 증발·비등·entrainment·원주방향 금속 전도

## 다음 입력 요구사항

1. 형상별 head 시작 station과 필름 진행 방향
2. station별 `r(x)`, 곡선거리, 가스 `rho/u/T/cp/mu/k/Pr`, 선정된 기존 `h_g`
3. 수축부·확장부에서 정당화된 가스–필름 전단응력 또는 마찰계수
4. 필름 주입 `T`, 절대압 및 station별 압력 취급 규칙
5. 실제 2-propanol 물성 범위와 포화상태 backend
6. Stechman 원문 Eq. 6의 단위계, `zeta`, `chi`, `g_s` 정의와 IPA 적용 근거
7. 복사 열유속의 기존 모델과 중복되지 않는 입력

이 입력이 확보되기 전에는 54개 사례의 벽온도를 생성하지 않는다. 합성 입력을 사용할 경우 결과는 `SYNTHETIC_NON_DESIGN`으로 분리해야 한다.
