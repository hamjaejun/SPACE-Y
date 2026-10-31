# SPACE Y 재생냉각 모델링 검토

2026-10-31 기준 내부 모델링 자료입니다.

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

RPA 검사는 설치 위치와 라이선스 상태에 의존하므로 보고서의 명령과 제한을 확인한다.
