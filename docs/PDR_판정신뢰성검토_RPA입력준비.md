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
| RPA | 정상 성능, chamber/nozzle sizing·geometry, 팀 PPT에 보존된 작업 후보의 입력·출력 스크린샷 검토 | 승인 후보의 재현 실행, raw project/ASCII/DXF 검증, 안전 승인 |
| GAS/Bartz | 명시 station의 가스 대류 열전달 | 미입력 복사를 0으로 둔 실제 열설계 완료 |
| M0 | 단일/다구간 상태 전달, 보존식, 합성 수치 검산 | 실제 IPA 기반 냉각 안전성, 상변화·비등·병렬 분배 |
| Property Adapter | 공급자·범위·단위·엔탈피 기준 interface | 검증된 실제 IPA backend |
| Decision Engine | hard constraint, 상태·증거 추적, 후보 제외/보류 | 승인되지 않은 PPT/v5 작업안을 실제 PDR 후보로 자동 승격 |

Y01–Y05 정의와 계산 경로는 존재하지만 실제 IPA, 형상, 경계조건과 station mapping이 없으므로 실제 엔진 출력은 아직 생성할 수 없다. `validated_engine_prediction=false`, V01–V12는 `NOT_RUN`이다.

## 6. RPA 2.3 형상 설계 연결

로컬 RPA Standard Edition v.2.3.2 User Manual 33–35쪽을 확인했다.

- Chamber Geometry 화면은 Design Parameters와 Size and Geometry 탭을 제공한다.
- sizing을 활성화하고 nominal thrust, total mass flow 또는 throat diameter 중 적어도 하나의 sizing basis가 필요하다.
- chamber count와 nozzle shape를 정의하며, nozzle inlet/exit condition과 shape는 Nozzle Flow Model 화면에서 관리한다.
- chamber length 또는 characteristic length, contraction angle과 normalized radii, nozzle contour 관련 입력을 사용한다.
- Size and Geometry는 chamber/nozzle 치수와 관련 결과를 표시한다.
- 결과의 ASCII 저장과 chamber contour의 DXF export가 문서화돼 있다.

DXF export는 매뉴얼에 문서화됐지만 SPACE Y 입력으로 파일 검증은 하지 않았다. 팀 `Parameter.pptx`에는 RPA 입력·결과 화면이 있어 과거 작업 후보 실행 자체는 확인되지만, raw project·ASCII·DXF와 실행 로그는 확보되지 않았다. 현재 열린 RPA는 `product is not activated` 상태라 재현 실행도 완료하지 못했다. CAD 자동 생성·승인 기능은 구현하지 않는다.

`config/SPACE_Y_RPA_design_handoff_template.json`은 다음을 분리한다.

1. 팀 승인 설계 요구조건
2. RPA에 직접 넣는 propellant/chamber pressure/mixture ratio/nozzle/sizing 입력
3. chamber characteristic length와 contraction/nozzle geometry 가정
4. RPA가 계산한 결과와 실행 artifact
5. ASCII/DXF export의 문서 상태와 실제 실행 상태
6. RPA가 결정하지 않는 injector, regenerative channel, wall/material, joints/ports/interface

팀 PPT의 작업값과 화면에 보이는 RPA 결과는 `PROVISIONAL`/`CALCULATED`로 기록했다. `source_candidate_id=TEAM-PPT-RPA-WORKING-CASE`지만 `team_selected_baseline=false`, 전체 status는 `INPUT_U`다. 화면 캡처의 존재를 팀 승인, 재현 가능한 원본 설정 또는 검증된 엔진 예측으로 해석하지 않는다.

## 7. 실제 파라미터 선정에 필요한 자료

`config/SPACE_Y_PDR_input_readiness.json`에는 공식 요구조건, 과거 v5와 팀 PPT 작업 후보, 즉시 결정 gate를 분리했다. 팀 PPT 값은 존재하지만 승인자·승인일·결정 기록, 담당자와 확보 기한은 제공되지 않았다.

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

## 9. 2026-11-01 실제 요구조건 재검토 기준

이번 재검토는 새 물리 모델이나 최적화 기능을 추가하지 않고, 공식 대회 안내서·AEL ICD·RPA 2.3 문서·팀 회의 전사·과거 v5 계산 기록을 서로 다른 증거 등급으로 분리했다. 기계 판독 기록은 `config/SPACE_Y_PDR_input_readiness.json` V2에 남긴다.

| 상태 | 이 문서에서의 의미 |
|---|---|
| `CONFIRMED` | 원문과 페이지/절을 직접 확인한 외부 요구조건·시설 능력·인터페이스. SPACE Y 목표값으로 자동 승격하지 않음 |
| `PROVISIONAL` | 팀이 검토할 수 있는 과거 계산 케이스 또는 출처가 있는 예비 가정. 승인·안전 입증 아님 |
| `INPUT_U` | 필요한 값이나 결정이 아직 입력되지 않음 |
| `SOURCE_NOT_FOUND` | 현재 workspace와 저장소에서 팀 승인 기록을 찾지 못함. 값이 없다는 뜻이지 0이라는 뜻이 아님 |

출처 ID는 다음과 같다.

| ID | 문서·판본 | 이번 검토 위치 | 용도 |
|---|---|---|---|
| `GUIDE-R2SK-PDR-2026-01` | R2S Korea 2026 Rapid Season 통합 안내서 v1.0, 2026-10-06 | PDF 2–8쪽 | 대회 분야, 압력 기준, 제원표, 제출·평가 기준 |
| `AEL-AEDN-1904-I1` | AEL Race2Space ICD, AEDN-1904 Issue 1, 2025-11-17 | PDF 1–7쪽 | 시설 공급·장착·계측·점화 인터페이스 |
| `RPA-USER-MANUAL-2.3` | 로컬 RPA Standard Edition v.2.3.2 User Manual | 인쇄 33–35쪽 | RPA chamber/nozzle sizing 입력·출력 기능 |
| `MEET-2026-10-26` | 팀 설계 방향 회의 전사 | 15:21, 37:43–38:09, 45:29 | 안정성 우선 방향과 결정 필요성. 정식 승인 기록은 아님 |
| `TEAM-PARAMETER-PPT-2026-10-31` | `Parameter.pptx`, SHA-256 `45e618...d964b8` | 슬라이드 5–10과 내장 RPA 화면 | 3 MPa·O/F 3·1.6 kg/s 작업 후보와 RPA 결과. 승인 기록 없음 |
| `HIST-SY-V5-2026-10-29` | SPACE Y v5 개인 검토안과 CEA 계산 근거 | 초기 제안서·`calculation_basis.json` | 과거 3 kN·25 bar(a)·O/F 2.50·10 s 케이스. 팀 확정값 아님 |

## 10. A — 요구조건 정리표

### 10.1 대회 필수·권고와 제출 요구

| 항목 | 값 | 단위 | 근거 | 요구조건 유형 | 확정 상태 |
|---|---:|---|---|---|---|
| 액체 이원추진 시험 분야 | N2O/IPA 또는 LOX/IPA | - | `GUIDE` PDF 3쪽, §02 시험 분야 | 지원 시험 분야 | `CONFIRMED` |
| 연소실 압력 상한 | 50 | bar(a) | `GUIDE` PDF 3쪽, 압력 기준 | 강제 상한 | `CONFIRMED` |
| 공급압력 계획 | 70 미만 권장 | bar(g) | `GUIDE` PDF 3쪽, 압력 기준 | 권고. 해당 ICD 제한도 만족 | `CONFIRMED` |
| 최종 PDR | 영문 PDF, Part A 8–12쪽 | pages | `GUIDE` PDF 2·4·8쪽 | 필수 제출 | `CONFIRMED` |
| 최종 영문 PDR 제출일 | 2026-12-31 | date | `GUIDE` PDF 2·8쪽 | 필수 일정 | `CONFIRMED` |
| 엔진 제원표 | 추진제·냉각·성분별 목표 유량·포트·추력·최대 연소시간·성분별 공급압력·연소실압력·점화방식 | - | `GUIDE` PDF 5쪽, §04 엔진 제원표 | 필수 기재 | `CONFIRMED` |
| AEL 시험 엔진 인터페이스 | AEL ASI 사용, 점화기 장착부·연결 규격을 ICD에 맞춤 | - | `GUIDE` PDF 4쪽 하단 | 시설 인터페이스 | `CONFIRMED` |

`70 bar(g) 미만`은 안내서 문구상 **권장**이며 강제 상한으로 재해석하지 않는다. 반대로 `50 bar(a)`는 엔진 설계의 최대 연소실 압력이다. 어느 값도 SPACE Y의 목표 작동점은 아니다.

### 10.2 AEL 시설·공급·인터페이스

| 항목 | 값 | 단위 | 근거 | 요구조건 유형 | 확정 상태 |
|---|---:|---|---|---|---|
| AEL 최대 추력 | 10 | kN | `AEL` PDF 1쪽; `GUIDE` PDF 3쪽 | 시설 능력 | `CONFIRMED` |
| 권고 전달압력 범위 | 20–60 | bar(g) | `AEL` PDF 4쪽, §3 | 시설 권고 | `CONFIRMED` |
| N2O 개별 최대 전달압력/유량 | 90 / 2.6 | bar(g), kg/s | `AEL` PDF 4쪽, §3.1 | 시설 능력 | `CONFIRMED` |
| N2O 통상 전달온도 | 10 | °C | `AEL` PDF 4쪽, §3.1 | 명목 시설 조건 | `CONFIRMED` |
| N2O 탱크 용량 | J1 8 / J2 32 | kg | `AEL` PDF 4쪽, §3.1 | 명목 재고 | `CONFIRMED` |
| IPA 개별 최대 전달압력/유량 | 90 / 2.0 | bar(g), kg/s | `AEL` PDF 5쪽, §3.3 | 시설 능력 | `CONFIRMED` |
| IPA 통상 전달온도 | 20 | °C | `AEL` PDF 5쪽, §3.3 | 명목 시설 조건 | `CONFIRMED` |
| IPA 탱크 용량 | J1 9 / J2 14 | kg | `AEL` PDF 5쪽, §3.3 | 명목 재고 | `CONFIRMED` |
| N2O/IPA 권고 엔진 포트 | N2O 1/2 또는 3/4, IPA 1/4–1/2 BSPP female | inch | `AEL` PDF 4–5쪽, §3.1·3.3 | 포트 인터페이스 | `CONFIRMED` |
| 장착 | M10, 135×50 pitch; 팀 장착대 제공·추력축 정렬 | mm | `AEL` PDF 2–3쪽, §2 | 기계 인터페이스 | `CONFIRMED` |
| 계측 | 10 kHz·16 bit; 공급 p/T/유량·추력, 추가 PT 8·TC 16채널까지 | - | `AEL` PDF 6쪽, §4 | 시설 계측 능력 | `CONFIRMED` |
| 점화 | 시설 또는 팀 점화기; 시설 torch는 1/4 BSPP | inch | `AEL` PDF 7쪽, §5 | 점화 인터페이스 | `CONFIRMED` |

위 최대 압력·유량은 각각의 시설 표기값이다. AEL은 고압에서 최대 유량이 감소한다고 명시하므로, 이 값들을 직사각형 동시 운전 포락선으로 사용하지 않는다. 명목 탱크 질량도 실가용량·잔류량·비연소 소비·배정 확정값이 아니다. J1/J2 실제 배정은 `INPUT_U`다.

### 10.3 팀 목표와 열·유체·구조 제약

| 항목 | 값 | 단위 | 근거 | 요구조건 유형 | 확정 상태 |
|---|---:|---|---|---|---|
| 팀 승인 목표 추력·허용편차 | 미입력 | N | 승인 기록 미발견 | 팀 성능 목표 | `SOURCE_NOT_FOUND` |
| 팀 승인 연소실압력 후보 | 미입력 | bar(a) | 승인 기록 미발견 | 팀 설계 변수 | `SOURCE_NOT_FOUND` |
| 팀 승인 O/F 후보 | 미입력 | - | 승인 기록 미발견 | 팀 설계 변수 | `SOURCE_NOT_FOUND` |
| 팀 승인 연소시간·duty | 미입력 | s, cycle | 승인 기록 미발견 | 팀 시험 목표 | `SOURCE_NOT_FOUND` |
| 팀 PPT 작업 후보 | N2O(L)/IPA, Pc 3 MPa, O/F 3, 총유량 1.6 kg/s, 산화제/연료 1.2/0.4 kg/s | 복합 | `TEAM-PARAMETER-PPT` 슬라이드 5–8 | 현재 작업 후보 | `PROVISIONAL` |
| 팀 PPT 추진제 온도·노즐 조건 | N2O 288.15 K, IPA 298.15 K, Ac/At 4, 출구압 1 atm | 복합 | `TEAM-PARAMETER-PPT` 슬라이드 6–7 | 작업 입력·가정 | `PROVISIONAL` |
| 팀 PPT 시험 수량 가정 | 10 s × 2회, N2O 1.2 kg/s | s, kg/s | `TEAM-PARAMETER-PPT` 슬라이드 5 | 재고 기반 가정 | `PROVISIONAL` |
| 과거 v5 검토 케이스 | N2O/IPA, 3 kN, 25 bar(a), O/F 2.50, 10 s | 복합 | `HIST-SY-V5`, 2026-10-29 개인 검토안 | 과거 예비 케이스 | `PROVISIONAL` |
| 검증된 실제 IPA 냉각 물성 | 미입력 | SI | Property Adapter 감사 기록 | 냉각 제약 | `INPUT_U` |
| 냉각채널·벽·재료·허용기준 | 미입력 | 복합 | PDR readiness C/T 항목 | 열·구조 제약 | `INPUT_U` |
| 공급 압력–유량 포락선·압손 | 미입력 | Pa, kg/s | PDR readiness S 항목 | 공급 제약 | `INPUT_U` |
| 연소 동적 안정성 기준·증거 | 미입력 | 복합 | PDR readiness B 항목 | 연소 안정성 | `INPUT_U` |

회의 전사에는 “안정성”과 설명 가능한 논리를 우선한다는 방향이 있으나(`MEET` 37:43–38:09), 서명·승인된 수치 요구조건은 아니다. 팀 PPT는 실제 RPA 작업 후보를 담고 있어 v5보다 RPA 착수에 가까운 자료지만, 승인자·선정 기준·raw project가 없다. 두 케이스 모두 팀 확정값·최적값·안전 설계가 아니다.

## 11. B — 파라미터 선정 근거표

| 설계 항목 | 분류·결정 주체 | 필요한 이유와 연결 요구조건 | 물리 영향·의존 관계 | 사용할 수 있는 도구·증거 | 현재 결정 가능 여부 / 변경 영향 |
|---|---|---|---|---|---|
| 추진제 조합과 정확한 입구 상태 | RPA/CEA 입력, 팀 결정 | 시험 분야·시설 배정·성능·공급·냉각의 출발점 | 반응물 엔탈피, O/F, 밀도·유량, 상상태가 모두 변함 | `GUIDE`·`AEL`; CEA/RPA. 액체 냉각 물성은 별도 | 조합은 선택 가능하나 현재 미승인. 변경 시 모든 성능·공급·냉각 재검토 |
| 목표 추력·ambient/back pressure | RPA sizing 입력, 팀 결정 | 제원표와 예비 형상 크기 결정 | 요구 유량·목면적·팽창 조건·시설 추력 여유에 영향 | CEA/RPA와 AEL 10 kN 경계 | 즉시 결정 필요. 10 kN를 목표로 자동 채택하지 않음 |
| 연소실 압력 | CEA/RPA 입력, 팀 결정 | `Pc <= 50 bar(a)`와 공급 가능성 동시 만족 | 성능·질량유량·목면적·인젝터 차압·벽하중·공급압력에 영향 | CEA/RPA, 공급 압력예산, 구조 1차 검토 | 후보 범위를 지금 결정할 수 있으나 단일 최적값 근거는 없음 |
| O/F | CEA/RPA 입력, 팀 결정 | 성능, 성분별 유량·재고와 열환경 비교 | Isp·Tc·조성·총/성분 유량·냉각 연료량에 영향 | CEA 민감도, RPA 성능, 시설 유량/재고 대조 | 후보 범위 결정 필요. 과거 2.50은 승인값 아님 |
| 연소시간·duty cycle | 시험 요구, 팀 결정 | 제원표·재고·열적 지속성·시험계획 | 총 소비량, 벽 축열, 냉각 지속성, 반복 시험에 영향 | CEA/RPA 유량, AEL 탱크 용량, 후속 M0/시험계획 | 즉시 결정 필요. 과거 10 s는 예시 케이스 |
| 성분별 목표 유량 | RPA/CEA 계산 결과 후 팀 검토 | 시설 최대 유량·재고·제원표 | 추력·Isp·O/F에 종속. 실제 효율/손실에 따라 변함 | CEA/RPA 정상 성능 | 승인 입력을 넣은 뒤 계산. 현재 과거 케이스만 존재 |
| 공급압력 목표 | 시스템 설계, 팀 결정 | GUIDE 권고·AEL 능력·Pc+압손 예산 만족 | 인젝터 차압, line/valve/cooling 압손과 regulator droop에 의존 | 공급 1D 예산, AEL 실제 포락선·부품 자료 | RPA 형상 직접 입력은 아니나 후보 실현성에 필요. 현재 정량 결정 불가 |
| nozzle exit/design condition·shape | RPA 입력, 팀 결정 | 노즐 팽창·형상·성능 산출 | ambient, Pc, 손실 모델, 길이·출구직경에 영향 | RPA Nozzle Flow Model, CEA 성능 대조 | 예비 형상 전 즉시 결정. 출처 없는 기본값 금지 |
| chamber count·sizing control | RPA 입력, 팀 결정 | RPA Size and Geometry 실행에 필요 | 총 추력 분배, 개별 throat/chamber 크기 결정 | RPA 매뉴얼 33–35쪽 | 즉시 결정 필요 |
| chamber length 또는 L* | RPA geometry 입력, 팀 잠정 결정 | 체류·연소실 체적과 형상 산출 | chamber 길이/직경, 열부하 면적, 연소 완결성 가정에 영향 | RPA + 적용 가능한 문헌·유사시험 | 출처·범위·민감도를 기록하면 조건부 잠정 가정 가능 |
| contraction angle/radii·nozzle contour | RPA geometry 입력, 팀 잠정 결정 | chamber/nozzle 축형상 계산 | 손실, 길이, 열유속 분포, 제작성에 영향 | RPA + 형상 문헌·민감도 | 출처가 있을 때만 잠정 적용; 변경 시 RPA/M0/CAD 갱신 |
| injector 요소·차압 | 별도 설계 | 혼합·유량 분배·공급압력·연소 안정성 | Pc, 성분 유량, 점화·동특성에 의존 | 별도 인젝터 계산·시험; RPA 형상 결과로 자동 결정되지 않음 | RPA 예비 형상과 병행 가능하나 시험 전 필수 검증 |
| 재생채널·벽·재료·접합 | 별도 설계 | 실제 냉각·구조 건전성 | 열유속, IPA 상태, 압손, 고온 강도·제작성에 의존 | M0는 실제 물성/형상 확보 후; 구조 계산·시험 별도 | 현재 안전 판정 불가. RPA 예비 형상 후 상세설계 입력으로 연결 |
| 장착·포트·계측·점화 | 별도 인터페이스 설계 | AEL ICD 적합성 | 배관 손실·하중·센서 위치·운전 절차에 영향 | AEL ICD, CAD/시험계획 | 시설 규격은 확인됨. 팀 구현·검증은 미결정 |

RPA의 계산 결과와 팀 입력을 혼동하지 않는다. 승인 입력이 주어지면 RPA가 throat/chamber/exit 치수, 축방향 contour와 일부 thrust-loss 결과를 산출할 수 있다. injector, 재생채널, 재료·벽두께, 접합·씰·포트·장착과 안전 승인은 RPA 출력 밖의 별도 설계다.

## 12. C — 실제 후보 비교 준비표

| 비교 항목 | 현재 확보 자료 | 지금 가능한 비교 | 판단 불가능한 부분 | 준비 상태 |
|---|---|---|---|---|
| 실제 후보 정의 | 팀 PPT RPA 작업 후보와 과거 v5 CEA 후보 | 입력·재고·명목 시설 경계의 사전 비교 | 승인된 후보 우열·최종 선정 | `PROVISIONAL` 2건 |
| 정상 성능 | PPT의 RPA 결과 화면, v5 CEA 원출력 | 서로 다른 입력/모델임을 명시한 1차 대조 | 공통 경계의 직접 성능 비교·실증 성능 | `CONDITIONAL_REVIEW` |
| 시설 상한/재고 | 공식 GUIDE·AEL ICD | Pc·추력·개별 명목 유량·명목 탱크 용량 대조 | 동시 압력–유량, 실가용량, bay 배정, 과도 | `CONFIRMED` 경계 / 후보 입력 `INPUT_U` |
| 냉각 | M0 수치 구조·합성 검산 | 형식·상태전파·보존식 점검 | 실제 IPA 냉각 안전성·벽비등·실제 벽온 | `INPUT_U` |
| 공급 | 요구 인터페이스와 압력예산 항목 | 필요한 데이터와 노드 정의 | 목표시간 공급 유지·droop·과도 | `INPUT_U` |
| 구조 | 1차 검토 요구 항목 | 하중 케이스 입력 구조 | 온도 의존 허용강도·열응력·joint margin | `INPUT_U` |
| 연소 안정성 | steady CEA/RPA 범위와 미확인 위험 정의 | 정상 열화학 결과 확인 | 동적 안정성·점화/종료·acoustic coupling | `INPUT_U` |
| Decision Engine | hard constraint·증거 정책 구현 | 팀 승인 후 두 후보를 실제 PDR 입력으로 평가 | 승인 전 자동 승격·안전 보증 | 소프트웨어 `AVAILABLE`, 승인 `INPUT_U` |

### 12.1 승인 전 사전 비교

| 항목 | `TEAM-PPT-RPA-WORKING-CASE` | `SY-V5-PROPOSAL` | 해석 |
|---|---:|---:|---|
| 상태 | 팀 작업 PPT, 승인 없음 | 개인 검토안, 승인 없음 | 둘 다 `PROVISIONAL` |
| Pc | 30 bar(a) | 25 bar(a) | 둘 다 50 bar(a) 상한 아래. 공급·과도·구조 여유는 미검증 |
| O/F | 3.0 | 2.50 | PPT는 연료 유량이 더 적어 냉각유량 관점에서 불리할 수 있으나 실제 냉각 분기·열부하는 미확인 |
| 총/산화제/연료 유량 | 1.6 / 1.2 / 0.4 kg/s | 1.5463 / 1.1045 / 0.4418 kg/s | 모두 AEL 개별 명목 최대 아래. 동시 압력–유량 포락선은 미확인 |
| 10 s 소비 | N2O 12 / IPA 4 kg | N2O 11.0448 / IPA 4.4179 kg | 두 안 모두 J1 N2O 8 kg를 초과하고 J2 명목량 안 |
| 반복 시험 | N2O 24 kg로 10 s 2회 가정 | 10 s 단일 검토 | PPT 안은 팀 N2O 제공량 24 kg를 정확히 소진해 비연소 소비·잔량·예비가 0이므로 그대로 승인할 수 없음 |
| 정상 성능 | RPA opt thrust 3.14030 kN, opt Isp 200.13844 s | CEA target 3.000 kN, Isp 197.83944 s | 입력·온도·Pc·O/F·프로그램이 달라 직접 모델 검증이나 우월성 판단 불가 |
| 형상 | Dc 61.89, Dt 30.95, De 64.05, Lc 266.53, Le 53.38 mm, Ae/At 4.28 | 없음 | PPT는 예비 형상 출발점이 있으나 L*=1 m·parabolic default의 선정 근거와 raw run이 없음 |
| 냉각·공급·구조·연소 안정성 | `UNKNOWN` | `UNKNOWN` | 합성 M0·steady 성능으로 안전성을 PASS 처리하지 않음 |

현재 가능한 것은 위 **사전 비교**다. 실제 Design Decision Engine 평가는 두 후보 ID, 공통 ambient/exit 경계, 목표 추력·시간, 입력 출처와 팀 승인 기록이 정해진 뒤 수행한다. 승인 전에는 어느 안도 `ELIGIBLE_FOR_PDR_SELECTION`으로 생성하지 않는다.

`ELIGIBLE_FOR_PDR_SELECTION`은 PDR에서 계속 검토할 자격이지 안전성 확보나 제작 승인 상태가 아니다. 냉각·공급·구조·연소의 필수 증거가 없으면 `UNKNOWN` 또는 `CONDITIONAL_REVIEW`를 유지한다.

## 13. D — RPA 입력 준비표

`config/SPACE_Y_RPA_design_handoff_template.json`에 팀 PPT 작업 후보의 입력과 화면에서 확인한 결과를 `PROVISIONAL`/`CALCULATED`로 기록했다. 승인 상태는 올리지 않았다.

| 구분 | 항목 | 현재 상태 | 다음 조치 |
|---|---|---|---|
| 현재 확정된 RPA 입력 | 없음 | `INPUT_U` | 팀 승인 기록 필요 |
| 외부 경계로 확정된 사항 | Pc 최대 50 bar(a), AEL 최대 추력 10 kN, 시설 propellant/flow/interface 능력 | `CONFIRMED` | 후보 생성 범위와 사전 필터로만 사용 |
| PPT direct input | N2O(L)/IPA, 288.15/298.15 K, Pc 3 MPa, O/F 3, 총유량 1.6 kg/s, 1 chamber, Ac/At 4, Pe 1 atm | `PROVISIONAL` | 압력·조성·선정기준과 팀 승인 보완 |
| 형상 관련 가정 | L*=1 m와 parabolic bell이 결과에 나타남. contraction/nozzle 상세 근거 없음 | 일부 `PROVISIONAL`, 나머지 `INPUT_U` | 문헌·적용범위·민감도·변경 trigger 기록 |
| RPA 계산 결과 | opt thrust/Isp와 chamber/throat/exit/length가 PPT 화면에 존재 | `CALCULATED`, screenshot evidence | raw project/ASCII로 재현하고 단위 변환·버전 기록 |
| RPA가 제공하지 않는 상세설계 | injector, regenerative channel, wall/material, joint/seal/port/mount/instrumentation | `INPUT_U` | M0·구조·제조·시험 계획과 별도 연결 |
| 현재 재실행·export | 현재 RPA `product is not activated`; ASCII/DXF 미확보 | `INPUT_U` / `NOT_RUN` | 실행 가능한 라이선스 복구 후 raw artifact 보존 |

PPT 화면은 RPA가 한 번 계산됐다는 증거지만 재현 가능한 설계 artifact는 아니다. 현재 장애물은 **팀 승인 입력 부재, raw run 부재, 로컬 RPA 비활성 상태**다. 실제 IPA 냉각 backend는 상세 열설계의 장애물이지만, 승인·실행환경이 확보되면 조건부 chamber/nozzle 예비 형상을 냉각 검증과 병행할 수 있다.

## 14. E — 팀 결정 사항 요약

담당자와 기한은 제공되지 않았으므로 임의 지정하지 않았다. `owner`와 `due_date`는 JSON에서도 `null`이다.

### 14.1 즉시 결정 필요

1. 팀 PPT 안을 `TEAM-PPT-RPA-WORKING-CASE`로 승인할지, v5를 두 번째 후보로 유지할지 결정
2. 목표 추력과 허용편차 결정. PPT는 목표 추력이 아니라 1.6 kg/s sizing의 결과로 3.14030 kN을 제시함
3. N2O 24 kg를 10 s 2회에 전량 배정하는 가정을 승인·수정. 현재 비연소 소비·잔류량·예비가 0임
4. 공통 비교 경계 확정: ambient/Pe, 추진제 온도·상태, equilibrium/frozen, 손실 포함 범위
5. Pc/O/F 후보 승인: PPT 30 bar(a)/3.0, v5 25 bar(a)/2.50 또는 팀이 승인한 대안
6. Ac/At=4, L*=1 m, parabolic bell default, 1 atm exit 조건의 출처·민감도·승인 상태 결정
7. 예상 AEL J2 배정 확인과 RPA 활성화, raw project/ASCII/DXF 보존 방법 결정

이 결정을 마치면 PPT 안을 조건부 예비 형상 기준으로 재실행하거나, 두 안을 공통 CEA/RPA 경계에서 비교할 수 있다.

### 14.2 근거를 기록하면 잠정 가정 가능

- chamber length 또는 L* 문헌 범위와 sensitivity case
- contraction angle/radii 및 nozzle contour 가정
- AEL의 통상 N2O 10 °C·IPA 20 °C를 보증값이 아닌 예비 run case로 쓰는지 여부
- 미종결 냉각·공급·구조·연소 항목을 가진 RPA 결과를 `CONDITIONAL_PRELIMINARY_GEOMETRY`로만 취급하는 규칙

### 14.3 예비 형상 후에도 제작·시험 전에 필수 확인

- 실제 압력–유량 동시 포락선, regulator droop, line/valve/injector/cooling-path 압손
- 실가용 추진제량·비연소 소비·잔량·반복 시험 예산
- exact IPA 조성·상태와 검증된 열역학/수송/상평형 물성
- 실제 노즐 좌표–CEA station mapping, 복사 입력, 채널 형상·분배·벽비등 위험
- 온도 의존 재료 물성·allowable, thermal/pressure load, joints/seals/mount
- injector/ignition/startup/shutdown/acoustic stability와 계측·abort criteria

## 15. 설계 착수 판정과 남은 2주 우선순위

### 15.1 지금 결정할 수 있는 것

- 공식 상한과 시설 능력으로 후보 생성 범위를 제한할 수 있다.
- 팀 PPT 안은 현재 유일하게 RPA 형상 화면까지 존재하므로 **첫 승인·재현 검토 대상**으로 둘 수 있다. 이는 잠정 기준안 선정이나 안전성 PASS가 아니다.
- v5는 더 낮은 Pc·O/F와 더 많은 연료유량을 가진 비교 대안으로 유지할 수 있다. 서로 다른 모델 결과만으로 냉각 우위나 성능 우위를 확정하지 않는다.
- 출처가 있는 geometry 가정을 `PROVISIONAL`로 명시하면, 안전성 완료와 구분된 조건부 예비 RPA 형상 계산을 진행할 수 있다.
- Decision Engine은 승인 입력이 들어오면 제약 위반 후보를 제외하고 근거 부족 후보를 보류 상태로 재현 가능하게 기록할 수 있다.

### 15.2 지금 결정할 수 없는 것

- PPT 안이나 v5가 최적 또는 팀 기준안이라는 결론
- PPT의 10 s 2회가 실제 가능한 시험계획이라는 결론. N2O 명목 제공량 여유가 0임
- AEL 개별 최대값을 동시에 만족하는 실제 공급 작동점
- 실제 IPA 재생냉각의 안전성, 고온 구조 건전성 또는 연소 동적 안정성
- RPA 형상 결과만으로 한 제작·시험 안전 승인

### 15.3 임계경로

1. **1일:** PPT 안의 수치·2회 시험 가정·RPA 기본 형상을 팀이 후보로 승인할지 결정하고, v5의 비교 후보 유지 여부를 함께 기록한다.
2. **1–3일:** RPA 활성 상태를 복구하고 승인 후보를 같은 CEA/RPA 경계로 재실행해 raw project·ASCII·DXF와 버전을 보존한다.
3. **4–6일:** Decision Engine으로 hard constraint·근거·미확인 위험을 고정하고 잠정 기준 후보를 팀이 선택한다.
4. **6–9일:** 잠정 기준 후보로 RPA Size and Geometry를 실행하고 ASCII/DXF provenance를 남긴다. 결과는 조건부 예비 형상으로 표시한다.
5. **병행:** 공급 포락선/배정·실가용량, IPA 물성, 재료·제조·인젝터·점화·계측 자료를 확보한다.
6. **9–14일:** RPA 형상을 CAD에 전달하되 결정값·잠정값·미확인값과 변경 trigger를 분리하고, PDR 본문에 선정·보류 이유를 기록한다.

가장 먼저 필요한 결정은 PPT의 `Pc=30 bar(a), O/F=3, total flow=1.6 kg/s, 10 s×2`를 실제 후보로 승인할지 여부다. 동시에 목표 추력과 N2O 예비량 기준을 정해야 한다. 승인 후에는 기존 RPA 화면값을 그대로 확정하지 않고, 출처가 불명확한 L*=1 m·Ac/At=4·parabolic default를 표시한 채 재현 실행한다. 이 단계가 완료되면 조건부 예비 형상을 CAD 검토에 넘길 수 있고, 냉각·공급·구조·연소 검증은 계속 `UNKNOWN`으로 병행한다.
