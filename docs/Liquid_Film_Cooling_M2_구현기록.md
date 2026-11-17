# SPACE Y Liquid Film Cooling M2 구현 기록

## 1. 범위와 결론

M2에서 추가한 기능은 다음 두 가지뿐이다.

1. 국소 가스 상태로부터 평판 기준 가스–액막 전단응력을 계산하고 기존
   `couette_film_state()`에 전달하는 제한적 기준 모델
2. 인젝터→출구 방향의 액막 계산과 출구→인젝터 방향의 재생냉각 계산을
   콜백으로 반복 연결하는 고정 `h_g` fixed-point Solver

증발, 액막 질량 감소·소진 계산, 증발 후 기체 필름, 기체 필름 혼합, 임의의
`h_g` 감소율은 구현하지 않았다. 기존 CEA, Bartz/`h_g`, Model B/H, IPA 물성,
벽 모델 및 재생냉각 marching 식도 변경하지 않았다.

현재 결과는 합성 열경계에서의 수치 검산이다. 실제 엔진의 Film ON 계산 및
실험적 열보호 검증은 수행하지 않았다.

## 2. 가스–액막 전단응력 기준 모델

### 2.1 문헌식

구현한 국소 smooth flat-plate turbulent skin-friction 식은 다음과 같다.

\[
C_{f,x}=0.0592 Re_x^{-1/5},\qquad
Re_x=\frac{\rho_g u_{rel}x_{BL}}{\mu_g}
\]

\[
\tau_g=\frac12 C_{f,x}\rho_g u_{rel}^2,qquad
u_{rel}=u_g-u_{f,surface}
\]

출처는 NASA CR-134814 *Theoretical Skin Friction Estimates* (1975), Eq. (4)이며,
보고서가 인용한 Schlichting의 평판식 정의를 따른다.

- NASA 기록: <https://ntrs.nasa.gov/citations/19750007533>
- 코드 correlation ID:
  `SCHLICHTING_LOCAL_TURBULENT_FLAT_PLATE_1_5_POWER`
- 구현 범위: `5e5 <= Re_x <= 1e7`
- `C_f,x`는 국소계수이다. 길이 평균계수 `0.074 Re_L^(-1/5)`를 사용하지 않는다.

압축성 보정이 없는 식이므로 NASA TM-85893의 incompressible 분류 근거에 따라
`Mach >= 0.3`에서는 계산을 차단한다.

- NASA 기록: <https://ntrs.nasa.gov/citations/19840015537>

### 2.2 적용성 게이트

`evaluate_local_gas_film_shear()`는 다음 조건을 모두 요구한다.

- 국소 가스 밀도, 속도, 점도 및 Mach 수가 명시됨
- 가스 경계층 발달거리와 그 출처가 명시됨
- 액막 분사 좌표의 출처가 별도로 명시됨
- `5e5 <= Re_x <= 1e7`
- `Mach < 0.3`
- `pressure_gradient_mode="ZERO_PRESSURE_GRADIENT_REFERENCE"`가 명시됨
- `u_g-u_f > 0`: 가스가 필름 marching 방향으로 액막을 끌어야 함

경계층 시작점은 액막 분사점에서 자동으로 생성하지 않는다. 압력구배가
`UNKNOWN`이면 `INPUT_U`, 알려진 비영(非零) 압력구배이면 `OUTSIDE`이다.
압축성 보정이나 수축·확장 압력구배 보정계수를 만들지 않았다.

위 게이트를 통과해도 움직이고 변형 가능한 액체 계면에 평판 고체벽 식을 적용한
근사이므로 반환 상태는 `MODEL_U`, `experimental_validation=false`이다.

### 2.3 기존 Couette 모델 연결

`evaluate_couette_from_gas_state()`가 전단응력을 기존
`liquid_film.couette_film_state()`에 전달한다.

\[
\delta_f=\sqrt{\frac{2\mu_f\Gamma_f}{\rho_f\tau_g}},\qquad
u_{f,avg}=\frac{\tau_g\delta_f}{2\mu_f}
\]

출력에는 `C_f`, `Re_x`, 상대속도, `tau_g`, 액막 두께, 평균·표면 속도,
상관식 ID, 적용성 상태와 가정이 포함된다. 질량 항등식
`rho_f*u_avg*delta=Gamma_f`를 자동 테스트로 확인한다.

## 3. 막냉각–재생냉각 반복 연성

### 3.1 방향과 데이터 흐름

`solve_counterflow_film_regenerative_coupling()`의 순서는 다음과 같다.

1. station을 인젝터→노즐 출구 순서로 입력한다.
2. 기존 계산에서 확정된 `h_g(x)`를 복사해 고정한다.
3. `film_marcher`를 인젝터→출구 순서로 호출한다.
4. station별 film→wall 및 dry gas→wall 열유속을 분리해 받는다.
5. 두 열유속의 합을 `regenerative_marcher`에 전달한다.
6. 재생냉각 콜백은 역순인 출구→인젝터로 호출된다.
7. 새 벽온도와 이전 벽온도, film 에너지 잔차 및 전체 연성 에너지 잔차를 검사한다.
8. 수렴하지 않으면 지정된 relaxation으로 벽온도만 갱신한다.

이 모듈은 기존 재생냉각 해석기를 다시 작성하지 않는다. 실제 통합 시 기존
solver를 `regenerative_marcher` 콜백으로 연결한다. 현재 기존 M0 solver는 film의
Wet/Dry station 열경계를 직접 받는 정식 adapter가 없으므로, 실제 엔진 실행은
해당 adapter와 검증된 film 열폐쇄식이 마련될 때까지 차단한다.

### 3.2 수렴 및 보존식

벽온도 잔차:

\[
\epsilon_T=\max_i|T_{w,i}^{new}-T_{w,i}^{old}|
\]

연성 에너지 잔차:

\[
R_Q=(\dot Q_{f\to w}+\dot Q_{g\to w,dry})-\dot Q_{coolant}
\]

정규화 잔차의 기준량은
`max(abs(Q_wall), abs(Q_coolant), 1 W)`이다. 다음 조건을 모두 만족해야만
`numerical_status="PASS"`로 처리한다.

- 벽온도 최대 변화가 설정 허용값 이하
- film 현열 에너지 잔차가 설정 허용값 이하
- 전체 연성 에너지 잔차가 설정 허용값 이하

최대 반복 횟수를 넘으면 `NUM_U`이며 정상 수렴으로 승격하지 않는다. 음수·0 K,
비유한 온도, 누락 station, upstream callback 실패도 상태와 함께 중단된다.

### 3.3 고정 h_g

`h_g`는 각 callback에 복사본으로 전달되며 Solver 내부에서 갱신되지 않는다.
최종 결과는 `fixed supplied h_g` 근사임을 명시한다. film이 `h_g` 자체에 미치는
영향은 계산하지 않는다.

## 4. 향후 증발·소진 모델 인터페이스

`ExternalFilmStationState`는 다음 데이터를 수용한다.

- station별 액체 film 유량과 온도
- film 소진 여부
- 액체 Couette 모델 활성 여부
- 외부 증발 모델이 계산한 증발 유량
- 외부 기체-film 열경계
- film→wall 열유속
- 적용성 상태

M2는 이 값들을 계산하지 않는다. `liquid_film_depleted=true`인데 액체 Couette가
활성 상태이면 `OUTSIDE`로 차단하며, 소진 후 액체 film 모델의 연장을 허용하지
않는다.

## 5. 자동 검산

신규 테스트 23개:

- `tests/test_gas_shear.py`: 11개
- `tests/test_film_coupling.py`: 12개

검산 항목:

- 국소 `C_f` 식과 전단응력 단위·정의
- 문헌 Reynolds 범위 외삽 차단
- Mach 및 압력구배 게이트
- 역방향 전단 차단
- 움직이는 계면 상대속도
- Couette 질량보존
- 전단응력 속도 민감도
- Film OFF 및 Split Only dry-wall 경로
- film/재생냉각 marching 방향
- 고정 `h_g` 불변성
- 합성 fixed-point 수렴
- Wet/Dry 및 전체 에너지 보존
- callback 보고값과 독립적으로 film 현열 에너지 잔차 재계산
- 에너지 불일치 및 미수렴 `NUM_U` 감지
- 소진 후 액체 Couette 사용 차단

2026-11-17 실행 결과:

- Liquid Film 선택 테스트: **48 PASS** (`V1 25 + M2 23`)
- 현재 로컬 작업공간 전체 discovery: **331 PASS**
- Python `compileall`: **PASS**
- `git diff --check`: **PASS**

현재 로컬 전체 discovery에는 이번 커밋에 포함하지 않은 기존 미추적 냉각채널
테스트 3개와 기존 산출물 JSON 갱신이 존재한다. 별도의 깨끗한 Windows checkout은
328개 중 324개가 통과하고, 기존 냉각채널 산출물 재현성 3건과 fixture SHA-256
1건이 줄바꿈/해시 차이로 실패한다. 동일한 4건은 M2 적용 전 기준 커밋
`9520478`의 깨끗한 checkout에서도 그대로 재현되었다. 따라서 이를 M2 회귀로
해석하거나, 해결을 위해 관련 없는 산출물 파일을 본 커밋에 포함하지 않았다.

이는 수치 구현 검산이며 열전달 실험 검증이 아니다. V01~V12는 `NOT_RUN`이다.

## 6. 실제 엔진 적용 상태와 남은 한계

| 항목 | 상태 |
|---|---|
| 전단식 산술 검산 | 완료 |
| Couette 연결과 질량보존 | 완료 |
| 합성 반대방향 연성 수렴 | 완료 |
| 기존 재생냉각 solver callback 경계 | 구현 |
| 실제 로켓 구간 전단응력 | `OUTSIDE/MODEL_U` 가능성이 큼 |
| 실제 엔진 Film ON 연성 계산 | 미수행 |
| 증발·소진·기체 film | 미구현(팀장 모델 범위) |
| 실제 IPA 및 film 표면 물성 검증 | 미완료 |
| 물리 실험 검증 | 미완료 |

실제 로켓의 압축성 유동과 수축·확장 압력구배는 현재 평판 기준식의 범위를
벗어난다. 따라서 본 M2 전단 모델을 실제 엔진 전단응력의 검증값으로 사용해서는
안 된다. 실제 계산에는 압축성·압력구배·분사 blowing·곡률·액체 계면의 영향을
다루는 별도 근거와 검증이 필요하다.
