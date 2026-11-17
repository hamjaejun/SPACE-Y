# Model H V1 엔진 연동 및 전체 후보 검증

## 실행 분류

- 실행일: 2026-11-11
- 브랜치: `feature/model-h-engine-integration`
- 입력 ZIP SHA-256: `54aa4d9a1bec3441ad3a60a76b452fdaf27f58e5125a479266f37763616b85ea`
- 분류: `PROVIDED_ENGINE_AND_PPT_PARTIAL_SCENARIOS_MODEL_U`
- 실제 엔진 예측 검증: `false`
- 실험 검증: `false`
- 안전 승인: `false`
- V01–V12: `NOT_RUN`

원본 ZIP은 커밋하지 않았으며, 작업용 임시 사본에서만 실행했다. 제공 엔진의 CEA, 노즐, 가스 측 열전달, 벽 열저항망, 핀 효율 및 냉각재 에너지 방정식은 변경하지 않았다.

## 연결한 인터페이스

`COOLING_HL_MODEL` 어댑터는 기존 Model B와 Model H를 선택한다. 기존 `calc_h_l(i, T_l)` 호출 형식은 유지했다. Model H는 저장소의 기존 형상 생성기, 헬리컬 거리 계산, Model B 상관식 dispatcher 및 전체 후보 평가기를 재사용한다.

벽온도 계산은 다음 두 모드로 분리했다.

- `H_L_ONLY_SENSITIVITY`: 기존 자오선 열전달 면적을 유지하고 Model H의 `h_l`만 사용한다.
- `H_GEOMETRY_COUPLED_EXISTING_LOOP`: 기존 루프의 유일한 구간 길이 계산에 `ds_H/ds_B=1/cos(beta)`를 한 번 적용한다.

두 번째 모드에서도 `h_l`에 별도 길이 배율을 곱하지 않는다. 원본 `cooling_loop_V1.py`는 수정하지 않고 모듈 지역의 구간 길이 연산만 실행 중 어댑터로 교체한 뒤 복원한다.

## 제공 엔진 기준 시나리오

제공 코드의 입력은 총유량 1.6 kg/s, 챔버 압력 25 bar, O/F 2.5, IPA 입구온도 293.15 K 및 200 station이다. 냉각유량은 0.457142857 kg/s이다. 이 값은 확정 SPACE Y 설계조건이 아니라 제공 코드의 예비 입력이다.

전체 반복 결과:

- 템플릿: 168
- Model B 고유 형상: 335
- 각도 확장 후보: 1,340
- 1D 형상 유효: 596
- 형상 `OUTSIDE`: 744
- Station 행: 268,000
- 엄격 물성 정책에서 계산된 `MODEL_U`: 69,136
- 형상 또는 물성 범위로 중단된 `OUTSIDE`: 198,864

69,136행은 596개 유효 형상의 116개 물성 범위 내 station이다. 나머지는 744개 형상 제외 후보의 148,800행과 596개 형상의 온도 범위 밖 50,064행이다. 따라서 어느 후보도 모든 200 station에서 검증된 물성으로 완주한 것으로 분류하지 않았다.

## beta=0 회귀검산

엄격 물성 정책으로 양쪽 계산이 허용된 116개 station에서 Model B와 Model H를 비교했다. 다음 항목의 최대 절대잔차는 모두 0이었다.

- 채널 폭
- 수력직경
- 총 유동면적
- 냉각유체 유로거리
- Reynolds 수
- Prandtl 수
- Nusselt 수
- 냉각 측 열전달계수 `h_l`

이 결과는 코드 경로의 수치적 회귀검산이며 Model H의 실험적 정확도 검증이 아니다.

## 기준 후보의 각도별 벽온도 실행

제공 엔진 시나리오에서는 요청된 참고 형상 `N=34`, `b=1.5 mm`, `h=1.5 mm`가 네 각도 모두 1D 형상 제약을 통과했다. 아래 값은 기존 냉각 루프를 실행한 결과이지만, 408 K를 넘는 station에서 제공된 온도 전용 IPA 상관식을 명시적으로 외삽했으며 기존 벽온도·비등 검사를 통과하지 못했다. 따라서 모든 행은 `OUTSIDE`이며 설계 성능값으로 사용할 수 없다.

| 모드 | beta [deg] | Tout [K] | max Twg [K] | Q [kW] | 외삽 station | ds 배율 |
|---|---:|---:|---:|---:|---:|---:|
| H_L_ONLY_SENSITIVITY | 0 | 462.75 | 1007.50 | 276.74 | 71 | 1.0000 |
| H_L_ONLY_SENSITIVITY | 20 | 464.15 | 976.18 | 279.58 | 73 | 1.0000 |
| H_L_ONLY_SENSITIVITY | 30 | 466.08 | 933.68 | 283.51 | 75 | 1.0000 |
| H_L_ONLY_SENSITIVITY | 35 | 467.45 | 914.04 | 286.32 | 77 | 1.0000 |
| H_GEOMETRY_COUPLED_EXISTING_LOOP | 0 | 462.75 | 1007.50 | 276.74 | 71 | 1.0000 |
| H_GEOMETRY_COUPLED_EXISTING_LOOP | 20 | 473.87 | 965.52 | 299.49 | 81 | 1.0642 |
| H_GEOMETRY_COUPLED_EXISTING_LOOP | 30 | 489.30 | 923.90 | 331.61 | 93 | 1.1547 |
| H_GEOMETRY_COUPLED_EXISTING_LOOP | 35 | 497.80 | 903.46 | 349.49 | 96 | 1.2208 |

beta=0에서 B와 H의 벽온도 결과도 동일했다. beta=35의 기하 결합 실행은 기존 루프가 x=6.8 mm에서 IPA 498 K가 공급 상관식의 끓는점 497 K 이상이라고 판정했다. 다른 행도 기존 벽온도 또는 비등 검사를 만족하지 않아 `OUTSIDE`로 유지했다.

## PPT 부분 조건 실행

`Parameter.pptx`에서 명시된 30 bar, O/F 3.0, 총유량 1.6 kg/s만 제공 엔진 코드에 적용했다. PPT에는 총유량 1.2 kg/s도 다른 슬라이드에 존재하며, 액체 입구온도는 제공 코드와 일치하지 않는다. 따라서 이 실행은 `PPT_PARTIAL_30BAR_OF3P0`이고 PPT 사례의 완전 재현이 아니다.

이 입력에서는 참고 형상 `N=34`, `b=1.5 mm`, `h=1.5 mm`가 네 각도 모두 유효하지 않아, 결정론적으로 선택한 첫 전체각도 유효 형상 `N=35`, `b=1.2 mm`, `h=1.5 mm`를 비교에 사용했다. 전체 후보 수는 1,336개, 1D 형상 유효 후보는 544개였다. 모든 벽온도 실행은 제공 IPA 상관식 외삽과 기존 비등 판정 때문에 `OUTSIDE`였다.

`SPACE_Y_R2S_Model_B_Entry_V1_Briefing.pptx`의 목·최고 벽온도·출구 결과 세 점은 완전한 원본 형상·채널·열경계 입력을 포함하지 않으므로 `INPUT_U`로 유지했고 수치 회귀 목표로 사용하지 않았다.

## IPA 물성 상태

제공 `ipa_props.py`는 온도 기반 포화액 상관식이며 압력 의존 수송물성을 제공하지 않는다. 공통 온도 범위는 187.35–408 K이다.

- 전체 후보 CSV: 엄격 정책을 사용해 범위 밖 station은 계산하지 않고 `OUTSIDE`로 기록했다.
- 기존 벽온도 루프: 루프 완주 여부를 보기 위해 제공 상관식의 외삽을 명시적으로 허용했으며 해당 station을 `EXTRAPOLATED`, 전체 실행을 `OUTSIDE`로 기록했다.
- 포화 경계, 벽면비등 및 상변화가 확인되지 않은 상태에서 단상 액체 결과로 승인하지 않았다.

## 산출물

`artifacts/model_h_engine_full`에 다음을 생성했다.

- `model_h_station_results.csv`: 268,000개 station 행. 약 100 MB이므로 로컬 산출물로 유지한다.
- `model_h_candidate_summary.csv`
- `model_h_wall_temperature_results.csv`
- `model_h_verification_summary.json`
- `ppt_reference_comparison.csv`
- 각 시나리오의 `h_l`, Re, Nu, 벽온도, 냉각재 온도 그래프
- 후보별 평균·최대 `h_l` 그래프

## 검증 명령과 결과

```powershell
.\.venv\Scripts\python.exe tools\verify_model_h_engine_full.py `
  --engine-zip C:\path\to\liquid_engine_design_V1.zip `
  --output-dir artifacts\model_h_engine_full
.\.venv\Scripts\python.exe -m unittest discover -s tests -q
```

- 전체 자동 테스트: 283 PASS
- Python 컴파일: PASS
- JSON 구문 검사: PASS
- `git diff --check`: PASS

## 남은 한계

- 제공 코드의 수축비, 특성길이 및 냉각 입구압력 일부가 BLANK 또는 예비값이다.
- 실제 압력 의존 IPA 수송물성, 단상 적용범위 및 상평형 검증이 없다.
- Dean 이차유동, 곡률에 따른 천이 변화, 헬리컬 압력손실, 매니폴드 유량분배 및 3D 제작 간섭은 Model H V1 범위 밖이다.
- 기존 냉각 루프의 비등·벽온도 판정 실패 때문에 제시된 벽온도는 승인 가능한 설계 결과가 아니다.
- PPT 출력 세 점은 입력 부족으로 재현하지 않았다.
