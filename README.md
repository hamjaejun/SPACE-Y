# SPACE Y 재생냉각 모델링 검토

2026-10-31 기준 내부 모델링 자료입니다.

## 현재 상태와 사용 제한

- M0는 개발 중인 1D 재생냉각 해석 모델이다.
- 현재 소프트웨어 검산은 주로 비설계 합성 문제와 공개 예제에 기반한다.
- 실제 2-propanol(IPA) 물성, 실제 SPACE Y 형상·운전조건 및 실험자료에 의한 물리 검증은 완료되지 않았다.
- 자동 테스트 통과와 수치 수렴은 안전성 승인, 설계 적합성 또는 실험 검증을 의미하지 않는다.
- 실제 엔진 예측 검증은 `false`, V01–V12는 `NOT_RUN`이며 미확인 기능은 `INPUT_U`, `OUTSIDE`, `NUM_U`, `MODEL_U`로 구분한다.

| 문서 | 역할 |
|---|---|
| [모델 선정 및 PDR 판단 기준서 v2.1](SPACE_Y_재생냉각_모델선정과_PDR판단기준_v2_1.md) | 모델·가정의 선정 이유, 보존식, 경험식 적용 범위, 결과 활용 기준 |
| [M0 입력·인터페이스·검증 기록](SPACE_Y_M0_입력_인터페이스_검증기록.md) | 입력 25개, 평가량 5개, 모듈 간 정보 교환, 검증 항목 12개 |
| [M0 모델 입력·검증 양식](SPACE_Y_M0_model_contract.json) | 문서와 같은 ID를 사용하는 기계 판독용 데이터 구조 |

기존 세 문서는 모델링 방법론과 실행 준비 명세입니다. 2026-10-31 Phase 1에서 입력 계약 검증, 기본 물리식 검산, NASA CEA 및 RPA 실행 어댑터를 추가했습니다. 실제 SPACE Y 재생냉각 계산은 아직 실행하지 않았고 V01–V12는 `NOT_RUN`, 미확인 입력과 계산 결과는 `null`입니다. 외부 프로그램 연결 성공은 SPACE Y 엔진의 성능이나 안전성 검증을 의미하지 않습니다.

원본 문서는 보존하고 이 업로드 사본의 수식 구분자만 GitHub Markdown용으로 정리했습니다. 참고 문헌은 각 문서에 연결되어 있습니다.

## Phase 1 구현

- [Phase 1 구현·검증 보고서](docs/PHASE1_구현_검증_보고서.md)
- [실제 JSON Schema](schemas/SPACE_Y_M0_model_contract.schema.json)와 [Y01–Y05 의존표](config/qoi_dependencies.json)
- `src/space_y/`: 계약/의미 검증, 물리식 검산, NASA CEA·RPA 어댑터와 CLI
- `examples/`: SPACE Y 설계값이 아닌 공식/비설계 인터페이스 회귀 입력
- `artifacts/phase1/`: 이번 환경에서 생성한 실행 증거. `validated_engine_prediction`은 모두 `false`
- `tests/`: 입력 상태·경계 모드·무효화 전파·기본 물리식·외부 요청 보호 검사

## M0 계산 코어 구현

- [정상 단일경로 M0 구현 기록](docs/M0_정상_단일경로_구현기록.md)
- `src/space_y/m0.py`: 지정 유량 1D 정상 단일경로의 가스 열입력–벽 저항–냉각 엔탈피–압손 결합
- [M0 실행 입력 Schema](schemas/SPACE_Y_M0_run.schema.json)
- [비설계 합성 검산 입력](examples/m0_synthetic_single_path.json)과 [실행 증거](artifacts/m0/m0_synthetic_single_path_run.json)

현재 M0 코어는 실제 SPACE Y 예측에 사용할 준비가 끝난 것이 아니다. 출력은 `MODEL_U`이며 압력 구동, 병렬 분배, 과도, 비등/2상, 다구역 전도, M0 벽온도 반복 내부의 Bartz 갱신과 불확실성/허용조건 평가는 미구현이다.

## CEA–GAS–Bartz 매핑

- [CEA–GAS–Bartz 매핑 구현 기록](docs/GAS_CEA_Bartz_매핑_구현기록.md)
- `src/space_y/gas.py`: CEA station과 transport 출력을 RPA 2012 기본 Bartz 식에 명시적으로 매핑
- [GAS 매핑 Schema](schemas/SPACE_Y_GAS_mapping.schema.json)
- [비설계 합성 매핑 입력](examples/gas_mapping_synthetic_non_design.json)

이 단방향 매핑 계층 자체는 처방 벽온도를 사용한다. 아래의 단일 구간 솔버에서 계산 벽온도와 `sigma`의 반복을 구현했고, 다구간 계층은 각 구간에 명시된 CEA station만 사용한다. 실제 형상과 station의 물리적 매핑 검증 및 공간 보간은 구현하지 않았다. 따라서 출력은 `MODEL_U`이며 설계나 안전 판정에 사용할 수 없다.

## 단일 구간 GAS–WALL 결합

- [단일 구간 결합 구현 기록](docs/GAS_WALL_단일구간_결합_구현기록.md)
- `src/space_y/bartz.py`, `wall.py`, `gas_wall.py`: Bartz 식, 독립 열저항 해와 벽온 고정점 반복을 분리 구현
- [GAS–WALL 실행 Schema](schemas/SPACE_Y_GAS_WALL_run.schema.json)
- [WALL→COOLANT 인터페이스 Schema](schemas/SPACE_Y_WALL_COOLANT_interface.schema.json)
- [비설계 합성 입력](examples/gas_wall_synthetic_single_segment.json)과 `artifacts/gas_wall/` 실행 증거

냉각 측 경계는 합성 `h_c`와 벌크온도를 처방한 검산 경계다. 실제 냉각 물성·상관식 적용성·엔탈피·압손은 계산하지 않고 `NOT_RUN`으로 전달한다.

## 단일 구간 GAS–WALL–COOLANT 완전 결합

- [단일 구간 완전 결합 구현 기록](docs/M0_단일구간_GAS_WALL_COOLANT_결합_구현기록.md)
- `src/space_y/coolant.py`: 단상 벌크영역 및 Gnielinski/Colebrook 공급 영역 검사
- `src/space_y/cooling_hc.py`: 외부 제공 냉각유량·물성과 직사각 채널의 독립 냉각 측 `Re/Pr/f/Nu/h_l(=h_c)` 인터페이스. `evaluate_rectangular_cooling_hl()`은 비엔탈피·길이 없이 국소 `h_l`을 계산하고, 기존 통합 API는 압손 기능을 유지함. 균등분배는 명시적 가정이며 실제 매니폴드·IPA 검증을 뜻하지 않음
- `src/space_y/cooling_channel_candidates.py`: Axial 직사각 채널의 `N/b/h` 조합과 명시적 `r_m(x)`에서 Constant-rib-width 후보를 생성하고 위치별 `h_l`과 Darcy 마찰 압력손실 구배를 계산함
- `src/space_y/cooling_channel_recommendation.py`: 동일 기준 station의 최고 `h_l`, 최소 국소 마찰 구배와 2목적 Pareto 후보를 상대 분류함. Baseline·안전 승인·최종 설계 선정과 분리됨
- `src/space_y/m0_coupled.py`: GAS–WALL과 가변물성 냉각 엔탈피·압손의 중첩 반복
- [결합 실행 Schema](schemas/SPACE_Y_M0_coupled_single_segment.schema.json)
- [비설계 합성 입력](examples/m0_coupled_synthetic_single_segment.json)과 `artifacts/m0_coupled/` 실행 증거

벌크 상태는 공급된 합성 단상영역 안에서만 계산한다. 벽면비등·ONB·CHF·드라이아웃은 미구현이므로 `NOT_EVALUATED`이고 전체 결과는 `MODEL_U`다.

## 단일 냉각 경로 다구간 축방향 marching

- [다구간 marching 구현 기록](docs/M0_다구간_축방향_marching_구현기록.md)
- `src/space_y/m0_marching.py`: 명시된 냉각 진행 순서로 단일 구간 솔버를 호출하고 출구 엔탈피·압력을 다음 구간으로 전달
- [다구간 실행 Schema](schemas/SPACE_Y_M0_multisegment.schema.json)
- [비설계 3구간 역류 합성 입력](examples/m0_multisegment_synthetic_counterflow.json)과 `artifacts/m0_marching/` 실행 증거

가스 축 좌표 순서와 냉각 흐름 순서를 별도로 입력하며, 감소 가스 좌표 방향의 역류도 명시적으로 검사한다. 각 구간은 출처가 있는 `EXPLICIT_NO_INTERPOLATION` CEA station 매핑을 요구한다. 상류 `INPUT_U`, `OUTSIDE`, `NUM_U`는 후속 구간을 `BLOCKED_BY_UPSTREAM`으로 차단한다. 병렬 분배, 축방향 벽 전도와 실제 엔진 검증은 포함하지 않는다.

## N/2N/4N 수치 검증과 실제 입력 준비

- [수치 검증·실제 입력 연동 준비 기록](docs/M0_수치검증_실제입력_연동준비.md)
- `src/space_y/m0_refinement.py`: 합성 N/2N/4N 실행, 정규화 보존식 잔차와 Y01–Y05 변화 기록
- [refinement suite Schema](schemas/SPACE_Y_M0_refinement_suite.schema.json)와 [합성 suite](examples/m0_refinement_synthetic_n_2n_4n.json)
- [실제 입력 준비 Schema](schemas/SPACE_Y_M0_real_input_readiness.schema.json)와 [미입력 템플릿](config/SPACE_Y_M0_real_input_readiness.json)

refinement fixture는 각 셀에 명시된 동일 CEA station을 사용하는 합성 상수 가스장이다. 보존식 잔차가 작아도 일부 QoI가 asymptotic regime를 보이지 않으므로 격자독립 또는 V08 통과로 판정하지 않는다. 실제 IPA 물성·상평형·노즐 좌표–CEA 매핑은 `INPUT_U`다.

## IPA 물성 검토 및 Property Adapter

- [IPA 물성자료 검토·구현 기록](docs/IPA_물성검토_Property_Adapter_구현기록.md)
- `src/space_y/property_adapter.py`: SI 단위, 엔탈피 기준, 기능별 지원 상태, 범위 검사와 출처를 강제하는 공급자 독립 인터페이스
- [IPA 후보 및 capability 기록](config/SPACE_Y_IPA_property_sources.json)과 [Schema](schemas/SPACE_Y_IPA_property_sources.schema.json)

NIST WebBook, REFPROP, DIPPR, CoolProp PC-SAFT와 chemicals/thermo를 검토했으나 현재 확보 자료만으로 M0에 필요한 실제 IPA 물성 전체를 일관되게 제공할 수 없다. 실제 capability는 `INPUT_U`이며 현재 M0 factory에는 합성 bounded table만 연결된다. 1-propanol과 generic propanol은 exact identity 검사에서 거부한다. 테스트 통과는 실제 IPA 또는 엔진 물리 검증을 뜻하지 않는다.

CoolProp 8.0.0의 exact `PCSAFT::2-PROPANOL` 로컬 capability probe에서는 p,T 밀도·phase flag와 T 기반 포화압 경로만 실행됐다. 엔탈피·비열·점도·열전도율은 해당 backend에 구현되지 않았고 p,h flash도 지원되지 않아 실제 M0 backend로 연결하지 않았다. 상세 결과는 `artifacts/property/ipa_coolprop_pcsaft_capability_probe.json`에 기록한다.

로컬 재현 명령:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[validation,cea]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\space-y.exe validate-contract SPACE_Y_M0_model_contract.json
.\.venv\Scripts\space-y.exe physics-check
.\.venv\Scripts\space-y.exe cea-run examples\cea_rp1311_example13_rocket_non_design.json --python .\.venv\Scripts\python.exe
.\.venv\Scripts\space-y.exe gas-map examples\gas_mapping_synthetic_non_design.json
.\.venv\Scripts\space-y.exe gas-wall-run examples\gas_wall_synthetic_single_segment.json
.\.venv\Scripts\space-y.exe m0-coupled-run examples\m0_coupled_synthetic_single_segment.json
.\.venv\Scripts\space-y.exe m0-marching-run examples\m0_multisegment_synthetic_counterflow.json
.\.venv\Scripts\space-y.exe m0-refinement-run examples\m0_refinement_synthetic_n_2n_4n.json
.\.venv\Scripts\space-y.exe m0-run examples\m0_synthetic_single_path.json
```

RPA 검사는 설치 위치와 라이선스 상태에 의존하므로 보고서의 명령과 제한을 확인한다. 실행 파일, 설치 절대경로와 라이선스 자료는 이 저장소에 포함하지 않는다.

## PDR 후보 의사결정과 RPA 형상 입력 준비

- [PDR 의사결정 엔진 구현 기록](docs/PDR_의사결정_엔진_구현기록.md)
- [판정 신뢰성·R2S 실제 요구조건 및 RPA 입력 준비](docs/PDR_판정신뢰성검토_RPA입력준비.md)
- `src/space_y/design_decision.py`: 요구 성능과 냉각·공급·구조·연소 안정성을 hard constraint로 비교하고 출처·모델·가정·검증 수준을 추적
- [PDR 입력 Schema](schemas/SPACE_Y_PDR_decision.schema.json), [실제 입력 준비표](config/SPACE_Y_PDR_input_readiness.json), [CAD 인계 Schema](schemas/SPACE_Y_CAD_handoff.schema.json)
- [RPA 형상 입력·결과 템플릿](config/SPACE_Y_RPA_design_handoff_template.json)과 [Schema](schemas/SPACE_Y_RPA_design_handoff.schema.json)
- [합성 A/B/C 분기 예제](examples/pdr_decision_synthetic_candidates.json)와 `artifacts/pdr/` 실행 증거

필수 제약 위반은 다른 장점이나 가중점수로 상쇄하지 않는다. 증거 또는 필수 입력이 없으면 `UNKNOWN`, 제한된 근거나 미종결 위험은 `CONDITIONAL_REVIEW`로 유지한다. 합성 fixture가 모두 PASS여도 실제 설계, 안전 확보 또는 CAD 기준안으로 승격하지 않는다.

자동 수치 criterion은 값·기준·단위·비교 연산자를 코드가 확인하고, 외부 판정 PASS/FAIL은 승인된 검토 증거를 요구한다. 실제 PDR 검증 정책은 코드 소유의 `PDR_APPROVED_EVIDENCE_V1`이며 요청 입력이 낮출 수 없다. 필수 criterion이 없는 안정성 영역은 `UNKNOWN`이고, 동일 판단 등급 후보에는 ID 기반 자동 우선순위를 부여하지 않는다.

로컬 RPA 2.3 확인에서는 exact 2-propanol 기체 열화학 record만 확인했고 액체 냉각에 필요한 exact 물성 record는 확인하지 못했다. RPA→Property Adapter는 `INPUT_U`를 유지하며 저장소에는 실행 파일·database·계수·설치 경로가 아닌 비식별 capability 감사 결과만 포함한다.

PDR 합성 분기 실행:

```powershell
.\.venv\Scripts\space-y.exe pdr-decide examples\pdr_decision_synthetic_candidates.json
```

이 기능은 PDR 후보 선정 근거를 기록하는 도구다. 실제 IPA 물성 및 실제 엔진 조건 검증은 미완료이며 결과는 안전성 승인, 제작 승인 또는 실험 검증을 의미하지 않는다.

## PDR 설계 공간 탐색

- [설계 공간 탐색 구현 기록](docs/PDR_설계공간_탐색_구현기록.md)
- `src/space_y/design_space.py`: 승인된 이산 범위의 결정론적 grid 생성, 동등 계산경계 결과 연결, hard constraint와 제한적 Pareto 비교
- [탐색 입력 Schema](schemas/SPACE_Y_design_space.schema.json), [실제 입력 준비 파일](config/SPACE_Y_design_space_readiness.json), [비설계 합성 2×2 fixture](examples/design_space_synthetic_grid.json)

탐색기는 기존 `Design Decision Engine` 앞단의 경량 기능이다. 미승인 범위에서는 후보를 만들지 않고, 단위나 `comparison_basis_id`가 다른 결과를 비교하지 않으며, 합성 결과를 실제 탐색에 사용할 수 없다. 팀 PPT의 `30 bar(a), O/F 3.0`과 기존 v5의 `25 bar(a), O/F 2.5`는 `REFERENCE_POINT_NOT_RANGE_BOUND`이다. 현재 실제 Pc/O/F grid와 공통 계산경계가 승인되지 않아 실제 결과는 후보 0개, `BLOCKED_BY_INPUT`, Pareto `NOT_RUN`이다.

```powershell
.\.venv\Scripts\space-y.exe design-space-run examples\design_space_synthetic_grid.json
.\.venv\Scripts\space-y.exe design-space-run config\SPACE_Y_design_space_readiness.json
```

합성 grid의 상대 shortlist는 논리 검산일 뿐 실제 유망 엔진이나 안전 설계가 아니다. 실제 후보는 동일 조건 CEA/RPA/M0 결과와 승인 제약을 연결한 뒤 기존 PDR 증거 정책을 별도로 통과해야 한다.

공식 R2S 안내서와 AEL ICD의 대회 상한·시설 능력·권고·인터페이스는 PDR 입력 준비표 V2에 출처와 페이지별로 분리했다. 과거 `3 kN / 25 bar(a) / O/F 2.50 / 10 s` 케이스와 팀 `Parameter.pptx`의 `Pc 3 MPa / O/F 3 / total flow 1.6 kg/s` RPA 작업 케이스는 모두 팀 승인값이 아닌 `PROVISIONAL`이다. PPT에는 RPA 형상 결과 화면이 있지만 raw project·ASCII/DXF·승인 기록이 없고 현재 로컬 RPA는 비활성 상태다. 따라서 실제 후보 판정과 승인된 RPA 재현 실행은 계속 `INPUT_U`다.

## 냉각 측 `h_l` (`h_c`) 독립 인터페이스

- [구현·문헌·적용범위 기록](docs/냉각측_대류_hc_독립모델_구현기록.md)
- [입력 Schema](schemas/SPACE_Y_cooling_hc.schema.json), [실제 입력 준비 상태](config/SPACE_Y_cooling_hc_input_readiness.json), [문헌 형상 프리셋](config/SPACE_Y_cooling_channel_reference_presets.json)

```powershell
.\.venv\Scripts\space-y.exe cooling-hc-run examples\cooling_hc_rectangular_synthetic.json
```

직접 냉각유량 또는 `beta * 연료유량`, 상·하류 분기 회계, 직사각 폐채널 기하와 기존 Gnielinski/Colebrook/Darcy–Weisbach 폐쇄식을 연결한다. 균등 채널분배는 `ASSUMED_EQUAL_DISTRIBUTION`이고 실제 매니폴드 해석이 아니다. 합성 fixture 결과는 `MODEL_U`, 실제 입력 준비 상태는 `INPUT_U`다.

냉각유체 측 계수는 이번 업무 정의에서 `h_l`, 기존 M0 호환 출력에서는 `h_c_W_m2K`로 표기하며 둘은 같은 양이다. 실제 재생냉각 통과유량 `coolant_mass_flow_kg_s`가 기본 입력이고 `beta` 및 분기 입력은 선택적인 질량회계 검증 경로다. 전체 IPA/N2O 유량 결정은 이 모듈의 책임이 아니다.

2026-11-01 냉각 `h_l` 독립 인터페이스 추가 후 전체 자동 테스트를 재실행했다. 테스트 수는 소프트웨어 회귀검사 결과이며 실제 엔진 물리 검증 건수가 아니다.

후보 생성 합성 예제:

```powershell
.\.venv\Scripts\space-y.exe cooling-channel-sweep examples\cooling_channel_sweep_synthetic_axial.json --output artifacts\cooling_channel_sweep\synthetic_axial_result.json --csv-output artifacts\cooling_channel_sweep\synthetic_axial_stations.csv
```

[후보 생성 구현 기록](docs/냉각채널_후보생성_hl_구현기록.md)의 탐색 범위와 반경은 합성·참고 입력이며 실제 SPACE Y 승인 설계범위가 아니다. 높은 `h_l`은 최종 형상 선정 기준이 아니며 전체 벽온도·상변화·압력예산·제작·구조 검토가 남아 있다.

다중 후보 예비 추천 합성 예제:

```powershell
.\.venv\Scripts\space-y.exe cooling-channel-sweep examples\cooling_channel_recommendation_synthetic_multi.json --output artifacts\cooling_channel_sweep\synthetic_recommendation_result.json --csv-output artifacts\cooling_channel_sweep\synthetic_recommendation_stations.csv --summary-csv-output artifacts\cooling_channel_sweep\synthetic_recommendation_summary.csv
```

Baseline은 문헌·부트캠프 참고자료이며 자동 추천 후보와 별개다. 추천은 같은 station과 같은 공급 유량·물성에서의 상대 비교만 수행하며 `Preliminary candidate recommendation, not final design optimization.`이다.
