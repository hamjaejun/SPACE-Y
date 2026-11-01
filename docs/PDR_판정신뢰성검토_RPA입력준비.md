# PDR 판정 신뢰성 검토 및 RPA 입력 준비

기준일: 2026-11-01

## 1. 이번 검토의 결론

기존 Design Decision Engine에서 다음 네 문제가 실제로 확인되어 최소 수정했다.

1. 입력자가 선언한 `PASS`를 실제 값과 요구조건의 비교 없이 수용할 수 있었다.
2. 요청 JSON이 `accepted_validation_levels`를 직접 지정해 실제 PDR에서 낮은 검증 수준을 승인할 수 있었다.
3. 평가 그룹에 필수 criterion이 하나도 없어도 `SATISFIED`가 될 수 있었다.
4. 같은 판단 등급의 후보 중 `candidate_id`가 사전식으로 앞선 후보를 자동 우선 후보로 선정했다.

새 물리 솔버, 최적화, CAD 자동 생성은 추가하지 않았다. 수정 범위는 판정 신뢰성, 실제 입력 준비, RPA 2.3 형상 단계의 입력/출력 구분이다.

## 2. PASS / FAIL / UNKNOWN 최종 기준

### 2.1 자동 수치 판정

`assessment_method=NUMERIC_COMPARISON`은 유한한 `actual_value`, `requirement`, 명시적인 `actual_unit`/`requirement_unit`/공통 `unit`과 연산자 `LT/LE/GT/GE/EQ`를 요구한다. 세 단위가 일치하지 않거나 코드가 계산한 상태와 선언한 `PASS/FAIL`이 다르면 전체 요청을 `INPUT_U`로 거부한다. 단위나 연산자가 없거나 잘못돼도 `INPUT_U`다. 허용값이나 비교 방향은 코드가 만들지 않는다.

### 2.2 외부 해석·전문가 판정

`assessment_method=EXTERNAL_REVIEW`는 `PASS/FAIL`에 `external_review_status=APPROVED`와 출처를 요구한다. 검토가 `PENDING` 또는 `NOT_REVIEWED`이면 미확인 상태로 남겨야 한다. 외부 결과의 실제 값, 기준, 식/방법, 출처와 검증 수준은 criterion에 함께 보존한다.

### 2.3 합성 검산

`assessment_method=SYNTHETIC_ASSERTION`은 `SYNTHETIC_DECISION_LOGIC_TEST`에서만 허용한다. 실제 PDR 요청에서 사용하면 `INPUT_U`다. 합성 criterion이 모두 PASS여도 실제 설계로 승격되지 않는다.

### 2.4 그룹과 후보 판정

- 필수 criterion의 계산 또는 승인된 외부 판정이 실패하면 `VIOLATED`, 후보는 `EXCLUDED`다.
- 필수 입력·증거·필수 criterion 자체가 없으면 `UNKNOWN`이다.
- 제한된 검증 수준이나 미종결 위험이 있으면 `CONDITIONAL_REVIEW`다.
- 실제 PDR에서 승인된 증거를 갖춘 모든 필수 제약만 `SATISFIED`가 될 수 있다.
- 안전성은 성능과 가중합하지 않는다.
- `global_missing_inputs`는 실제 PDR 후보의 성능 그룹에 전파되어 적합 판정을 차단한다.

## 3. 검증 정책

입력자가 검증 수준 whitelist를 만들 수 없도록 코드 소유의 정책 ID로 변경했다.

| 정책 | 용도 | 승인 가능한 result/source/model 최저 범위 |
|---|---|---|
| `SYNTHETIC_LOGIC_V1` | 합성 분기 검산 | `SYNTHETIC_ARITHMETIC` |
| `PDR_APPROVED_EVIDENCE_V1` | 실제 PDR 후보 | `SOURCE_REVIEWED`, `PHYSICAL_BENCHMARK`, `TEST_VALIDATED` |

정책 ID와 분석 목적이 맞지 않으면 `INPUT_U`다. 실제 PDR은 `SYNTHETIC_TEST_FIXTURE` 유형의 출처도 거부한다. criterion의 result validation, 각 model validation, 각 source validation을 별도로 기록하고 그중 가장 낮은 수준을 effective validation으로 사용한다. 코드가 `TEST_VALIDATED`를 생성하거나 물리 검증을 주장하지 않는다.

## 4. 후보 우선순위

필수 제약 위반 후보는 제외하고, 미확인 안정성을 성능으로 상쇄하지 않는다. 동일한 최상 판단 등급 후보가 둘 이상이면 `priority_candidate_id=null`로 두고 `co_priority_candidate_ids`에 함께 제시한다. ID는 표시 정렬에만 쓰며 우수성 기준이 아니다.

단일 후보만 해당 등급에 남았을 때도 결과는 “후속 검토 우선”이지 안전 승인이나 최종 선정이 아니다. 팀이 지정한 잠정 기준 후보는 `team_selected_provisional_baseline_candidate_id`로 자동 우선순위와 분리한다.

## 5. 현재 분석 자산의 활용 범위

| 자산 | 현재 가능한 판단 | 현재 불가능한 판단 |
|---|---|---|
| CEA | 명시 입력의 정상 열화학·추진 성능, station별 상태 | 연소 동적 안정성, 실제 형상 좌표 자동 보간 |
| RPA | 정상 성능, 문서화된 chamber/nozzle sizing·geometry 실행 준비 | 실제 후보 미입력 상태의 형상값, 안전 승인 |
| GAS/Bartz | 명시 station의 가스 대류 열전달 | 미입력 복사를 0으로 둔 실제 열설계 완료 |
| M0 | 단일/다구간 상태 전달, 보존식, 합성 수치 검산 | 실제 IPA 기반 냉각 안전성, 상변화·비등·병렬 분배 |
| Property Adapter | 공급자·범위·단위·엔탈피 기준 interface | 검증된 실제 IPA backend |
| Decision Engine | hard constraint, 상태·증거 추적, 후보 제외/보류 | 입력이 없는 실제 후보 추천 |

Y01–Y05 정의와 계산 경로는 존재하지만 실제 IPA, 형상, 경계조건과 station mapping이 없으므로 실제 엔진 출력은 아직 생성할 수 없다. `validated_engine_prediction=false`, V01–V12는 `NOT_RUN`이다.

## 6. RPA 2.3 형상 설계 연결

로컬 RPA Standard Edition v.2.3.2 User Manual 33–35쪽을 확인했다.

- Chamber Geometry 화면은 Design Parameters와 Size and Geometry 탭을 제공한다.
- sizing을 활성화하고 nominal thrust, total mass flow 또는 throat diameter 중 적어도 하나의 sizing basis가 필요하다.
- chamber count와 nozzle shape를 정의하며, nozzle inlet/exit condition과 shape는 Nozzle Flow Model 화면에서 관리한다.
- chamber length 또는 characteristic length, contraction angle과 normalized radii, nozzle contour 관련 입력을 사용한다.
- Size and Geometry는 chamber/nozzle 치수와 관련 결과를 표시한다.
- 결과의 ASCII 저장과 chamber contour의 DXF export가 문서화돼 있다.

DXF export는 매뉴얼에 문서화됐지만 실제 SPACE Y 입력으로 로컬 실행·파일 검증은 하지 않았다. 따라서 `DOCUMENTED_NOT_EXECUTED_FOR_SPACE_Y`다. CAD 자동 생성·승인 기능은 구현하지 않는다.

`config/SPACE_Y_RPA_design_handoff_template.json`은 다음을 분리한다.

1. 팀 승인 설계 요구조건
2. RPA에 직접 넣는 propellant/chamber pressure/mixture ratio/nozzle/sizing 입력
3. chamber characteristic length와 contraction/nozzle geometry 가정
4. RPA가 계산한 결과와 실행 artifact
5. ASCII/DXF export의 문서 상태와 실제 실행 상태
6. RPA가 결정하지 않는 injector, regenerative channel, wall/material, joints/ports/interface

모든 실제 값, source candidate와 실행 결과는 현재 `null`, `INPUT_U` 또는 `NOT_RUN`이다.

## 7. 실제 파라미터 선정에 필요한 자료

`config/SPACE_Y_PDR_input_readiness.json`에 항목별 `current_value`, `source`, `owner`, `due_date`, `decision_impact`를 추가했다. 현재 실제 설계값, 담당자와 확보 기한은 제공되지 않아 `null`이다.

RPA 예비 형상 설계를 시작하려면 최소한 다음이 팀 승인 상태여야 한다.

- 목표 thrust와 적용 ambient/back-pressure condition 또는 다른 sizing basis
- propellant exact identity와 inlet state
- chamber pressure와 mixture ratio
- nozzle exit/expansion design condition과 nozzle-shape 선택
- chamber count
- chamber length 또는 characteristic length의 근거
- contraction/nozzle contour 가정과 출처
- 선택 후보 ID, 선정 근거와 변경 trigger
- 단위, RPA 판본, 입력 파일과 run artifact 기록 방법

냉각·공급·구조·연소 안정성의 모든 증거가 완료되지 않아도 예비 RPA 형상 계산 자체는 수행할 수 있지만, 미확인 hard constraint를 가진 결과는 안전성이 확보된 기준 설계나 제작 입력으로 표현할 수 없다.

## 8. 남은 2주의 실행 우선순위

1. 즉시: 요구조건 owner가 thrust, duration, ambient condition, chamber-pressure/mixture-ratio 후보 범위를 승인한다.
2. 후보 정의: 2–3개 실제 후보와 각 입력의 출처·단위·가정·검증 수준을 decision JSON에 입력한다.
3. 정상 성능: 같은 CEA/RPA 판본과 입력 규칙으로 후보별 성능 결과를 생성한다.
4. 제약 검토: 실제 입력이 있는 범위에서 M0, 공급 압력 예산, 1차 구조 검토를 연결하고 나머지는 `UNKNOWN`으로 남긴다.
5. 후보 회의: 위반 후보 제외, 동급 후보는 함께 제시, 팀이 잠정 기준 후보를 명시적으로 결정한다.
6. RPA 형상: 승인된 잠정 후보만 RPA Size and Geometry에 입력하고 ASCII/DXF와 실행 provenance를 기록한다.
7. PDR 기록: 선정·제외·보류 이유와 제작·시험 전 미종결 위험을 고정한다.

새 고정밀 모델 개발보다 요구조건과 실제 후보 입력의 확보가 현재 임계경로다.
