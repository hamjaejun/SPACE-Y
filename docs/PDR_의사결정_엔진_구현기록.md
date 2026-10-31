# PDR 설계 의사결정 엔진 구현 기록

기준일: 2026-11-01

## 1. 목적과 사용 제한

이 단계의 목적은 자동 최적화가 아니라, 사용자가 제공한 후보를 같은 요구조건과 증거 정책으로 비교하고 **입력 → 모델·식 → 출처·가정 → 검증 수준 → 제약 판정 → 후보 판단 → CAD 인계**를 재현 가능하게 기록하는 것이다.

M0는 개발 중인 1D 재생냉각 모델이며 현재 검산은 주로 합성 산술 문제에 기반한다. 실제 IPA 물성과 실제 SPACE Y 형상·운전조건·시험자료에 의한 물리 검증은 완료되지 않았다. 이 엔진의 결과는 PDR 검토 기록이지 안전 승인, 제작 승인 또는 시험 승인이 아니다. `safe_design_claim`, `manufacture_release`, `test_safety_approval`, `validated_engine_prediction`은 모두 `false`이고 V01–V12는 `NOT_RUN`을 유지한다.

## 2. 재사용하는 현재 모델

| 계층 | 현재 활용 가능한 판단 | 아직 할 수 없는 판단 |
|---|---|---|
| CEA 및 RPA 인터페이스 | 명시 입력의 정상상태 열화학·성능 실행, 출처가 있는 station 값 전달 | 연소 안정성 PASS, 과도·음향·분사기 결합 안정성 |
| CEA–GAS–Bartz | 명시 station의 가스 대류 열전달 계산과 단위·출처 추적 | station 사이 임의 보간, 복사 미입력을 0으로 둔 실제 열설계 완료 판정 |
| GAS–WALL–COOLANT | 단일 구간 반복, 벽 열저항, 합성 단상 냉각 엔탈피·압손 산술 | 실제 IPA 상변화·비등·CHF, 실제 물성 기반 안전 판정 |
| 다구간 marching | 단일 냉각 경로의 상태 전달, 역류 순서, 전체 보존 잔차 | 병렬 채널 분배, 축방향 벽 전도, 실제 형상 검증 |
| N/2N/4N | 합성 공간분할 민감도와 정규화 잔차 기록 | asymptotic regime, 실제 엔진 격자 오차, 4N 참값 또는 GCI |
| Property Adapter | p,T / p,h / 수송물성 / 상판정 capability와 범위·출처 차단 | 검증된 실제 IPA backend. 현재 실제 capability는 `INPUT_U` |

## 3. 네 안정성 영역과 PDR 증거 요구

### 3.1 냉각 안정성

M0가 계산하는 벽온도, 냉각 벌크 상태, 압력손실, 보존 잔차를 후보 증거로 연결할 수 있다. 그러나 실제 IPA 물성, 복사, 상변화·벽면비등, 형상·입구조건, 재료 온도 제한과 독립 검증이 없으면 `SATISFIED` 또는 안전하다고 판정하지 않는다. 미입력은 `INPUT_U`, 물성·상관식 범위 이탈은 `OUTSIDE`, 수치 실패는 `NUM_U`, 중요한 모델 공백은 `MODEL_U`로 유지한다.

### 3.2 공급 안정성

이번 단계는 새 동적 공급 솔버를 만들지 않는다. 후보별 요구 유량, 소스/탱크 압력·온도 시간 이력, 라인·밸브·레귤레이터·분사기·냉각경로 손실, 출구/배압 경계와 측정 불확실성이 제공되면 정상 1D 압력 예산의 증거를 연결할 수 있다. 시동·정지, regulator dynamics, water hammer, cavitation, 탱크 열역학과 유량 분배는 별도 과도 해석 또는 시험 영역이다.

### 3.3 구조 안정성

압력·온도 하중, 실제 CAD 두께·곡률·구속·조인트, 제조 상태에 맞는 온도 의존 재료 물성과 허용조건이 제공된 경우에만 승인된 1차 식 또는 구조해석 결과를 연결한다. 상온 강도를 고온 허용값으로 대체하거나 안전계수를 임의 지정하지 않는다. 국소 열응력, 피로, 크리프, 접합·밀봉과 제작 공차가 단순 모델 범위 밖이면 `MODEL_U` 또는 추가 검증으로 남긴다.

### 3.4 연소 안정성

CEA/RPA 정상상태 성능이 계산된 사실은 연소 안정성 증거가 아니다. 분사기 압력강하·분무/혼합, feed coupling, chamber acoustic modes, time lag, ignition/startup/shutdown와 시험 계측이 별도 증거로 필요하다. 이번 구현은 동적 연소 불안정 모델을 추가하지 않으며 관련 자료가 없으면 `UNKNOWN` 또는 `CONDITIONAL_REVIEW`다.

## 4. Design Decision Engine

`src/space_y/design_decision.py`와 `space-y pdr-decide` 명령은 다음 정책을 강제한다.

1. 후보별 파라미터, 성능 및 네 안정성 영역을 모두 입력한다.
2. 각 criterion은 식/방법, model ID, source ID, assumption ID, 검증 수준, 불확실성과 주석을 갖는다.
3. 참조 ID가 없거나 중복되면 `INPUT_U`로 중단한다.
4. `PASS`라도 출처·모델의 실제 검증 수준 중 가장 낮은 수준으로 하향하고, 미승인 수준이나 미확인 가정이면 `CONDITIONAL_REVIEW` 또는 `UNKNOWN`으로 처리한다.
5. 필수 성능 또는 안정성 제약의 `FAIL`은 후보를 `EXCLUDED`한다. 장점 수나 가중 점수로 상쇄하지 않는다.
6. 필수 입력이나 증거가 없으면 `UNKNOWN`, 남은 위험 또는 제한된 증거이면 `CONDITIONAL_REVIEW`다.
7. 모든 필수 제약에 요청 정책상 승인된 증거가 있어야 실제 PDR 후보가 `ELIGIBLE_FOR_PDR_SELECTION`이 된다. 이 상태도 안전·제작·시험 승인은 아니다.
8. 합성 입력은 모든 가상 flag가 PASS여도 실제 후보로 승격하지 않는다.
9. 결과에는 입력과 Schema SHA-256, 정렬된 비교표, 후속 우선 후보, 문장형 이유와 CAD 인계 구조가 포함된다.

안정성은 성능과 가중합하지 않으며 `safety_is_hard_constraint=true`, `allow_weighted_safety_tradeoff=false`가 아니면 입력을 거부한다.

## 5. 합성 후보 비교 예제

`examples/pdr_decision_synthetic_candidates.json`은 판단 분기만 검산하는 비설계 fixture다. 어떤 값도 SPACE Y 형상·운전점·허용조건이 아니다.

| 후보 | 요구 성능 | 냉각 | 공급 | 구조 | 연소 | 종합 판단 |
|---|---|---|---|---|---|---|
| SYN-A | SATISFIED | SATISFIED | SATISFIED | SATISFIED | SATISFIED | `CONDITIONAL_REVIEW`: 모든 flag가 합성 자료이므로 실제 설계가 아님 |
| SYN-B | SATISFIED | VIOLATED | SATISFIED | SATISFIED | SATISFIED | `EXCLUDED`: 필수 냉각 제약 위반은 다른 장점으로 상쇄 불가 |
| SYN-C | SATISFIED | SATISFIED | UNKNOWN | SATISFIED | SATISFIED | `UNKNOWN`: 필수 공급 입력 누락 |

SYN-A는 알려진 위반이 없는 분기라 후속 검토 우선순위로만 표시된다. 실제 후보 추천, 안전 확보 또는 CAD 기준안은 아니다. 생성 결과는 `artifacts/pdr/pdr_decision_synthetic_candidates_run.json`에 보존한다.

## 6. RPA 2.3 exact IPA 단기 확인

로컬 RPA Standard Edition v.2.3.2 데이터베이스를 읽기 전용으로 확인했다. exact `C3H8O,2propanol` gas thermochemistry는 200–1000 K와 1000–6000 K 구간에 존재했다. 이는 연소용 기체 열화학 record다.

별도의 coolant properties와 transport 데이터베이스에서는 exact 2-propanol/CAS 67-63-0 record를 찾지 못했다. RPA 설명서도 extended NASA thermochemistry와 냉각유체에 필요한 밀도·비열·점도·열전도율·기화 관련 물성이 별도임을 구분한다. 검토 설치에는 이를 보완할 사용자 property database도 없었다.

따라서 RPA gas species를 액체 IPA Property Adapter로 연결하지 않는다. `state_pt`, `state_ph`, `rho`, `cp`, `mu`, `k`, enthalpy reference, saturation/phase, T-p 유효범위와 불확실성은 모두 `INPUT_U`다. 실행 파일·매뉴얼·DB·계수·설치 절대경로는 저장소에 포함하지 않고 해시와 판정만 `artifacts/property/rpa_2_3_ipa_property_audit.json`에 남긴다.

## 7. 실제 후보 입력과 CAD 인계

`config/SPACE_Y_PDR_input_readiness.json`은 실제 후보 평가에 필요한 항목을 성능, 냉각, 공급, 구조, 연소 안정성, CAD 인계로 나눈다. 현재 실제 값은 없고 모두 `INPUT_U`다. 핵심 우선 입력은 다음과 같다.

- 승인된 추력·운전시간·성능 요구와 candidate set
- 후보별 CEA/RPA 입력·출력·버전과 명시 station–geometry mapping
- exact IPA 조성·입구상태와 검증된 물성/상평형 provider
- 채널/벽 형상, 거칠기 정의, 복사 입력, 온도 의존 벽 물성·허용조건
- 소스 압력·온도 시간 이력과 전체 공급 압력손실 자료
- 구조 하중, 온도 의존 재료 allowables, 조인트·구속·margin 정책
- 분사기, 공급–챔버 동특성, 연소 안정성 기준과 시험/동적 해석 증거

`config/SPACE_Y_CAD_handoff_template.json`은 기준 후보를 `null`로 유지한다. 실제 의사결정 입력에서 승인된 임시 기준안이 있을 때만 `DECIDED`/`PROVISIONAL` 파라미터, 선정 이유, 모델·출처, 미확정 입력, 변경 trigger와 제작·시험 전 위험을 내보낸다. 합성 후보는 CAD 기준안이 될 수 없다.

## 8. 수치·물리적 한계

- 후보 비교 엔진의 자동 테스트는 분기, 상태 전파, Schema와 추적성을 검산한다. 물리 모델 정확도를 검증하지 않는다.
- 실제 M0의 복사, 비등/상변화, 병렬 분배, 축방향 벽 전도와 실험 상관은 미검증이다.
- 공급과 구조는 이번 단계에서 요구 interface와 증거 정책만 정의했으며 새 정량 solver를 구현하지 않았다.
- 연소 안정성은 정상상태 성능과 별도 영역이며 미확인 자료를 PASS로 바꾸지 않는다.
- N/2N/4N의 asymptotic regime은 `NOT_ESTABLISHED`; Richardson extrapolation/GCI는 수행하지 않는다.

## 9. 남은 2주의 우선순위

1. 요구조건 owner와 함께 실제 요구값, 허용조건, 단위, 위치·시간창, 판본을 동결한다.
2. 2–3개 실제 후보를 같은 JSON 계약으로 입력하고 모든 `INPUT_U`에 담당자와 확보 기한을 배정한다.
3. licensed/public exact IPA provider와 독립 기준점을 확보하거나, 부족하면 냉각 영역을 `UNKNOWN`으로 유지한 PDR 위험으로 상정한다.
4. 실제 CEA/RPA artifact와 CAD 좌표의 명시 mapping을 확정해 현재 M0를 후보별로 실행한다.
5. 공급 정상 압력 예산과 승인된 1차 구조 검토를 실제 입력이 확보된 범위에서만 연결한다.
6. 연소 안정성 동적/시험 근거와 제작·시험 전 closure plan을 분리해 기록한다.
7. 의사결정 결과를 검토해 위반 후보는 제외하고, 자료 부족 후보는 보류하며, 승인된 임시 기준안만 CAD에 전달한다.

고정밀 신규 모델, 자동 최적화, CFD, 고급 연소 동역학과 병렬 채널 해석은 이 기간의 우선순위가 아니다.
