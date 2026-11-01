# SPACE Y PDR 설계 공간 탐색 구현 기록

작성일: 2026-11-01

상태: 소프트웨어 검산 완료, 실제 설계 탐색 `BLOCKED_BY_INPUT`

용도: PDR 후보 생성 전단. 제작·시험·안전 승인 자료가 아니다.

## 1. 결론

기존 `Design Decision Engine`은 사용자가 제공한 후보의 증거와 hard constraint를 판정하는 기능이었다. 승인된 범위에서 후보 조합을 생성하고, 같은 계산 경계의 결과만 연결하며, 제한적인 상대 트레이드오프를 산출하는 기능은 없었다.

이번에 `src/space_y/design_space.py`를 별도 전단으로 추가했다. 기존 판정 엔진은 변경하지 않았다. 탐색기는 후보를 `EXPLORATION_CANDIDATE`로만 생성하며 PDR 후보, 팀 잠정 기준안 또는 검증 설계로 자동 승격하지 않는다.

현재 SPACE Y 실제 탐색은 실행하지 않았다. Pc와 O/F의 팀 승인 범위, 공통 계산 경계, 필수 제약과 실제 동등조건 재계산 결과가 없기 때문이다. `config/SPACE_Y_design_space_readiness.json`의 실행 결과는 후보 0개와 `BLOCKED_BY_INPUT`이다. 팀 PPT 작업안과 기존 v5는 범위 끝점이 아니라 서로 다른 계산 경계를 가진 참고점이다.

## 2. 기존 시스템 기능 공백과 현재 상태

| 기능 | 이전 | 이번 반영 후 | 한계 |
|---|---|---|---|
| 사용자가 입력한 후보의 판정 | `IMPLEMENTED` | `IMPLEMENTED` | 실제 증거가 없으면 `UNKNOWN` |
| 설계 변수 탐색 범위 설정 | `NOT_IMPLEMENTED` | `IMPLEMENTED` | 승인 상태와 출처가 없으면 후보를 생성하지 않음 |
| 파라미터 조합 생성 | `NOT_IMPLEMENTED` | `IMPLEMENTED` | 결정론적 소규모 Cartesian grid만 지원 |
| CEA/RPA 후보별 자동 실행 | `NOT_IMPLEMENTED` | `BLOCKED_BY_INPUT` | 탐색기는 결과 record 연결만 수행. RPA 비활성, 공통 입력 미승인 |
| 후보별 성능 지표 추출 | `PARTIAL` | `PARTIAL` | 명시적 result record만 사용. 임의 결과 생성 없음 |
| 안정성 지표와 여유도 | `PARTIAL` | `PARTIAL` | 승인된 수치 constraint만 계산. 실제 공급·IPA·재료·연소 자료 부족 |
| 위반과 미확인 구분 | `IMPLEMENTED` | `IMPLEMENTED` | `FAIL`은 제외, 누락/단위/경계 불일치는 `UNKNOWN` |
| 후보 트레이드오프 | `NOT_IMPLEMENTED` | `PARTIAL` | 동일 단위·경계의 명시 지표에 한한 방향성/Pareto 상대 비교 |
| 유망 후보군 추출 | `NOT_IMPLEMENTED` | `PARTIAL` | 모든 포함 필수 constraint를 통과한 후보만 상대 shortlist; 실제 안전·최적 주장 금지 |

## 3. 설계 공간 정의표

### 3.1 실제 SPACE Y 준비 상태

| 구분 | 항목 | 현재 상태 | 필요한 팀 결정 또는 입력 |
|---|---|---|---|
| 변경 변수 | 연소실 압력 Pc | `INPUT_U` | 단위가 명시된 승인 grid와 출처. 시설 최대압력을 sweep 범위로 오인하지 않을 것 |
| 변경 변수 | O/F | `INPUT_U` | 성능·열·연소·운용 제약을 고려한 승인 grid와 출처 |
| 고정/종속 | 추진제 정체성 | `PROVISIONAL` | N2O와 exact 2-propanol(CAS 67-63-0)의 팀 확인 |
| 고정/종속 | 목표 추력 또는 유량 sizing basis | `INPUT_U` | 둘 중 무엇을 고정하고 무엇을 재계산할지 결정 |
| 고정조건 | 입구 온도·압력 및 ambient | `INPUT_U` | 후보 공통 경계와 허용편차 |
| 공통 가정 | RPA/CEA 버전·열화학·손실 모델 | `INPUT_U` | 재현 실행에 사용할 동일 설정 |
| 공통 가정 | 형상 기준 | `INPUT_U` | 공통으로 고정할 기준과 후보마다 재계산할 결과의 구분 |
| 제약 | 성능·공급·냉각·구조·연소·운용 기준 | `INPUT_U` | 값·단위·연산자·필수 여부·출처·승인 상태 |

탐색 계약은 임의의 명명된 이산 변수를 지원하지만, 현재 실제 준비 파일에는 Pc와 O/F만 placeholder로 둔다. 총유량, 팽창비, 수축비, 연소실 특성길이, 냉각 채널 변수 등을 독립 sweep 변수로 추가하려면 먼저 sizing 종속관계와 공통 형상 가정을 팀이 결정해야 한다.

### 3.2 참고점의 위치

| 참고점 | Pc | O/F | 결과 분류 | 탐색에서의 위치 |
|---|---:|---:|---|---|
| 팀 PPT RPA 작업안 | 30 bar(a) | 3.0 | `HISTORICAL_RESULT`, `PROVISIONAL` | 참고점. raw project와 재현 실행이 없고 범위 끝점이 아님 |
| 기존 v5 | 25 bar(a) | 2.5 | `HISTORICAL_RESULT`, `PROVISIONAL` | 참고점. CEA 기반이며 PPT RPA 결과와 공통 경계가 아님 |

두 점 사이 또는 두 점만으로 범위를 생성하지 않았다.

## 4. 지표 정의표

| 영역 | 지표 예 | 물리적 의미/방법 | 현재 정량 사용 상태 | 실제 사용에 필요한 자료 |
|---|---|---|---|---|
| 성능 | 추력, Isp, 총유량, 형상 출력 | 동일 RPA/CEA 설정의 정상상태 결과 | 역사값만 제한적으로 존재, 동등 비교 `NOT_RUN` | 목표·허용편차, sizing basis, 동일 프로그램/모델 재실행 |
| 공급 | 요구 유량, 요구 공급압, 압력 예산/손실 | 시설·탱크·배관·인젝터 압력-유량 관계 | 개별 시설 상한만으로 가능성 입증 불가 | 실제 압력-유량 포락선, 배관/밸브/인젝터 손실, 운전시간 변화 |
| 냉각 | 벽온도, 냉각 출구 상태, 압손, 적용범위 | CEA–Bartz–M0 marching | 합성 검산만 가능, 실제 평가는 `INPUT_U` | 실제 IPA backend, 냉각 형상·입구조건, 복사, 상평형/비등 적용성 |
| 구조 | 응력, 온도의존 허용강도, 구조 여유 | 압력·열하중의 1차 구조 검토 | `NOT_RUN` | 실제 형상, 재료, 온도별 물성·허용기준, 하중/경계조건 |
| 연소 | 정상상태 성능과 별도 동적 안정성 증거 | CEA/RPA는 정상상태, 동특성은 별도 | 정상상태 결과로 안정성 PASS 금지 | 인젝터/음향/동적 해석 또는 시험 증거와 승인 기준 |
| 운용 | 시험당 소비량, 시험 횟수, 재고 여유 | 유량×시간과 실제 가용량 비교 | 명목 산술은 가능, 실제 여유는 제한적 | 잔류량·비연소 소비·reserve·실제 가용량·시험계획 |

탐색기의 metric은 값, 단위, 방법, source ID, validation level, uncertainty, result class를 보존한다. `NOT_RUN`, `INPUT_U`, `OUTSIDE`, `NUM_U`, `MODEL_U`는 0점이나 PASS로 바꾸지 않는다.

## 5. 최소 구현

- `SPACE_Y_DESIGN_SPACE_V1` 입력 계약과 JSON Schema
- 승인된 `grid_values`의 결정론적 Cartesian product
- `max_candidates` 상한으로 우발적 대규모 sweep 차단
- 값과 단위를 포함한 정확한 파라미터 서명으로 결과 연결
- `comparison_basis_id`가 같은 결과만 metric 비교에 사용
- `HISTORICAL_RESULT`, `REPRODUCED_RESULT`, `SYNTHETIC_RESULT`, `NOT_RUN` 분리
- 성능·공급·냉각·구조·연소·운용 domain을 매 후보 결과에 항상 유지
- 승인 수치 constraint의 직접 비교와 hard-constraint 제외
- 동등하고 완전한 metric이 있을 때만 제한적인 Pareto 분석
- 실제 탐색에서 synthetic source/result 거부
- CLI `space-y design-space-run`

이 모듈은 CEA/RPA/M0를 자동 호출하지 않는다. 계산 실행기는 독립적으로 provenance가 있는 result record를 만든 뒤 탐색 입력에 연결해야 한다. 이후 PDR 후보로 올릴 때 기존 `Design Decision Engine`의 증거 정책을 다시 통과해야 한다.

## 6. 탐색 결과표

### 6.1 실제 SPACE Y

| 항목 | 결과 |
|---|---|
| 승인된 실제 grid | 없음 |
| 생성 후보 | 0 |
| 상태 | `BLOCKED_BY_INPUT` |
| 동등조건 CEA/RPA 계산 | `NOT_RUN` |
| 실제 트레이드오프 | `NOT_RUN` |
| 유망 실제 후보 | 없음 |
| 안전 설계 주장 | `false` |
| 실제 엔진 예측 검증 | `false` |
| V01–V12 | `NOT_RUN` |

주요 장애요인은 승인 Pc/O/F grid, 목표·허용편차, sizing basis, 입구·ambient 경계, 공통 RPA/CEA 모델, 형상 가정, 필수 constraint, 재현 가능한 RPA 실행 환경의 부재다.

### 6.2 비설계 합성 2×2 논리 검산

다음 숫자는 엔진 단위나 설계 의미가 없는 `u_*` 산술 fixture다.

| 후보 | SYN-X | SYN-Y | 성능 | 공급 | 냉각 | 구조 | 연소 | 운용 | 종합 |
|---|---:|---:|---|---|---|---|---|---|---|
| DSE-0001 | 1 | 10 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | `CONDITIONAL_REVIEW` |
| DSE-0002 | 1 | 20 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | `CONDITIONAL_REVIEW` |
| DSE-0003 | 2 | 10 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | `CONDITIONAL_REVIEW` |
| DSE-0004 | 2 | 20 | 포함지표 만족 | 위반 | 위반 | 포함지표 만족 | 포함지표 만족 | 포함지표 만족 | `EXCLUDED` |

성능이 가장 큰 DSE-0004도 공급과 냉각의 합성 필수 ceiling 위반을 상쇄하지 못한다. Pareto 결과 DSE-0001/0002/0003은 오직 합성 성능 최대화와 합성 냉각값 최소화의 상대 비지배 집합이며 실제 유망 엔진 후보가 아니다.

## 7. 트레이드오프와 유망 후보 분석

실제 트레이드오프 분석은 수행하지 않았다. PPT와 v5는 프로그램, sizing basis, 입구조건, 손실/형상 가정이 동일하다고 입증되지 않아 성능 우열을 계산할 수 없다. 실제 안전성 지표도 모두 미확인이다.

탐색기는 다음 조건을 모두 만족할 때만 상대 Pareto를 실행한다.

1. 후보가 포함된 모든 필수 constraint에서 `CONDITIONAL_REVIEW` 이상이어야 한다.
2. 모든 objective metric이 `AVAILABLE`이어야 한다.
3. 동일한 `comparison_basis_id`와 동일 단위여야 한다.
4. 결과는 포함 지표에 대한 `RELATIVE_ONLY`이며 전체 최적해, 안전 설계 또는 팀 선정안이 아니다.

## 8. RPA 예비 형상 설계 연결 조건

팀이 먼저 결정해야 할 최소 항목은 다음과 같다.

1. 목표 성능과 허용편차
2. 고정 추력 기준인지 고정 총유량 기준인지에 대한 sizing basis
3. 추진제 정체성·조성, 입구 온도·압력과 ambient
4. 첫 sweep의 Pc·O/F grid와 각 범위의 출처·승인 기록
5. RPA/CEA 프로그램·판본, 열화학·효율·손실 모델
6. 공통으로 유지할 수축비, 노즐 종류, characteristic length 등과 후보별 재계산 항목
7. 성능·공급·냉각·구조·연소·운용의 필수 constraint, 단위, 연산자, 승인 주체
8. RPA 라이선스 활성화와 raw project/ASCII 또는 동등한 provenance export 절차

잠정 기준안이 선택되면 후보 입력과 RPA 형상 결과를 같은 result record 및 기존 RPA handoff에 연결한다. 다른 형상 후보의 냉각·구조 결과를 재사용하지 않는다.

## 9. 검증 범위

자동 테스트는 결정론적 조합, 미승인 범위 차단, 단위 불일치, 계산 경계 불일치, `NOT_RUN`/누락의 `UNKNOWN` 전파, hard constraint, 합성 결과 격리, 안정성 domain 보존, Pareto 범위 제한과 grid 상한을 확인한다.

이는 소프트웨어 계약·분기 검산이다. CEA/RPA 성능, 실제 IPA 냉각, 공급, 구조, 동적 연소 안정성의 물리 검증은 수행하지 않았다.
