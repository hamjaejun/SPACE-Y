# Model H V1 제공 엔진 시나리오 연동 및 검증 기록

## 1. 판정 요약

제공 ZIP의 엔진·노즐·station·냉각 루프·IPA 코드를 확인하고 Model H V1에
연결했다. ZIP의 `h_l_V1.py`는 저장소의 최신 `cooling_model_b.py`와
Lee–Garimella 계수 표현이 달랐으므로 덮어쓰지 않았다. 실행 도구는 제공 엔진
코드를 원본 그대로 임시 위치에서 실행하되 `h_l_V1` import만 현재 브랜치의
Model B로 치환한다. 원본 ZIP과 비공개 물성표는 Git에 포함하지 않는다.

이번 결과는 `PROVIDED_ENGINE_SCENARIO_MODEL_U_NOT_VALIDATED_DESIGN`이다.
제공 코드 자체가 수축비, L*, 냉각 입구압을 `[BLANK]`, 벽·거칠기·가공 한계를
`[검증용]`으로 표시한다. 따라서 실제 엔진 형상 코드를 사용했지만 확정된 SPACE Y
설계나 실험 검증 결과로 취급하지 않는다.

## 2. 확인한 입력 및 소스

| 파일 | 사용 목적 |
|---|---|
| `liquid_engine_design_V1.py` | CEA 조건, 노즐/채널/냉각 경계 및 기준 후보 |
| `nozzle_contour_V1.py` | 실제 제공 노즐 좌표 생성 |
| `stations_v1.py` | 200 station 및 목 index 생성 |
| `cooling_loop_V1.py` | 출구→챔버 냉각 marching과 기준 온도 분포 |
| `ipa_props.py` | exact 2-propanol(CAS 67-63-0) 온도 상관식과 포화 경계 |
| `wall_material_V1.py`, `hg_candidates_v1.py` | 제공 기준 냉각 루프 실행 |

ZIP SHA-256과 각 소스 파일 SHA-256은
`artifacts/model_h_engine_integration/provided_engine_model_h_summary.json`에 기록한다.
로컬 절대경로는 결과 파일에 기록하지 않는다.

제공 시나리오의 주요 값은 총 유량 1.6 kg/s, O/F 2.5, 연소실 압력 25 bar,
IPA 냉각 유량 0.457142857 kg/s, 냉각 입구 온도 293.15 K이다. 냉각 입구압
40 bar는 원본에서 `[BLANK]`이며, 기존 루프는 압력손실을 계산하지 않아 전 station에
같은 압력을 사용한다.

## 3. 구현한 인터페이스

- Model H 후보 API에 기존 고정 평균반경 모드와 별도로
  `gas_side_radius_m + wall_thickness_m` 입력 모드를 추가했다.
- 후보 높이별 평균 채널 반경을 `r_gas + t_wall + h/2`로 계산하므로 기존 Model B
  실제 엔진 반경 정의를 유지한다.
- 후보별 평균반경으로 자오선 길이와 `s_H=s_B/cos(beta)`를 다시 계산한다.
- property gate가 `INPUT_U/OUTSIDE/NUM_U`인 station에는 `None` 물성을 허용하며,
  임의 값이나 외삽값을 만들어 계산하지 않는다.
- `model_h_engine_integration.py`가 제공 IPA 상태 gate, 선택 형상의 네 각도 비교,
  β=0 Model B 회귀 잔차를 담당한다.
- `tools/verify_model_h_engine_integration.py`가 ZIP 안전 추출, source hash, 제공 엔진
  실행, 결과 CSV/JSON 생성을 담당한다.

## 4. IPA 적용성

제공 `ipa_props.py`는 Chemeo 기반이라고 기재된 포화액 온도 상관식이다. 밀도,
점도, 비열, 열전도도에 압력 의존성이 없으며, 별도 원자료 물성표는 제공되지 않았다.
네 수송물성의 공통 온도 범위는 187.35–408 K이다. 이 범위와 `T<T_sat(p)`를
동시에 만족할 때만 수치 사용을 허용하되 상태는 `MODEL_U`로 유지한다.

제공 Model B 기준 냉각 루프는 IPA가 293.15 K에서 474.87 K까지 상승하는 결과를
냈다. 200개 station 중 116개만 공통 물성 범위 안의 `MODEL_U`이고, 84개는
408 K를 넘어 `OUTSIDE`다. 이 84개 station에는 Model H의 Re, Nu, h_l을 계산하지
않았다. 기준 루프의 포화온도는 497.19 K이나, 포화온도 미만이라는 사실만으로
수송물성 외삽을 허용하지 않는다.

## 5. 168개 템플릿 형상 결과

168개 `(w_ref,b,h)` 템플릿은 floor/ceil 규칙으로 336개 원시 후보, 335개 고유
`(N,b,h)` 후보를 만들었다. 네 각도 적용 후 1,340개 후보 중 596개가 제공된 1D
폭·리브·높이 제약을 통과했고 744개는 `OUTSIDE`였다.

| β | 전체 | 1D 유효 | OUTSIDE |
|---:|---:|---:|---:|
| 0° | 335 | 208 | 127 |
| 20° | 335 | 145 | 190 |
| 30° | 335 | 136 | 199 |
| 35° | 335 | 107 | 228 |

이는 제공된 예비 가공 한계에 대한 1D 판정일 뿐 3D 가공 가능성 검증이 아니다.

## 6. Model B 기준 후보의 회귀 및 각도 판정

제공 냉각 루프가 선택한 기준 후보는 `N=48`, `b=1.5 mm`, `h=1.2 mm`이다.
β=0°의 116개 계산 가능 station에서 Model B와 Model H의 Re, Pr, Nu, h_l 최대
절대 잔차는 모두 정확히 0이었다. 유동영역은 층류 6, Model B 천이 8, 난류
102 station이었다.

그러나 동일 `(N,b,h)`를 β=20/30/35°로 기울이면 최소 폭이 각각 약 0.748,
0.572, 0.460 mm로 제공 최소 폭 0.8 mm보다 작다. 따라서 세 비영각 후보는
`OUTSIDE`이며 h_l을 강행하지 않았다. β=0°의 수치 범위는 Re 1,918.6–23,027.3,
Nu 30.74–235.11, h_l 2,549.9–16,102.6 W/m²K이다. 84개 고온 station은 별도로
물성 `OUTSIDE`다.

## 7. 네 각도 비교용 비선정 참고 후보

각도 민감도 경로 자체를 확인하기 위해 네 각도에서 모두 1D 형상 제약을 통과하는
후보 중 결정론적 정렬의 첫 항목 `(N=34, b=1.5 mm, h=1.5 mm)`을 사용했다.
이는 추천 또는 최적화 결과가 아니다. 각 각도에서 동일한 제공 Model B 냉각온도
분포를 고정한 h_l 민감도이며, β>0 벽–냉각 재결합 결과가 아니다.

| β | s_H [m] | Re 범위 | Nu 범위 | h_l 범위 [W/m²K] | 층류/천이/난류 |
|---:|---:|---:|---:|---:|---:|
| 0° | 0.45653 | 1,782–21,425 | 31.00–207.40 | 1,925–9,855 | 9/7/100 |
| 20° | 0.48583 | 1,896–22,800 | 32.41–221.53 | 2,042–10,733 | 7/7/102 |
| 30° | 0.52716 | 2,058–24,740 | 34.47–241.67 | 2,233–12,044 | 4/8/104 |
| 35° | 0.55732 | 2,175–26,156 | 36.06–256.54 | 2,382–13,057 | 2/9/105 |

116개 공통 station에서 β=0° 대비 h_l 차이의 평균은 20°에서 +9.20%, 30°에서
+23.33%, 35°에서 +34.56%였다. 이는 Model H의 단면·유로길이 변환과 기존
Model B 상관식이 만든 모델 민감도이며, Dean vortex나 실험적 헬리컬 향상을
검증한 결과가 아니다.

## 8. 냉각 루프 인터페이스 확인

Model H의 `h_l_W_m2K`는 실제 채널 젖은 면적 기준 국소 계수이며 추가
`1/cos(beta)`를 곱하지 않는다. 기존 냉각 루프는 열량 계산에서 자오선 `ds`를
직접 계산하므로 β>0의 실제 헬리컬 젖은 면적을 받을 수 없다. 현재 결과는 h_l과
`s_H`를 각각 계산했지만, 열전달계수와 면적에 길이 보정을 중복 적용하지 않기 위해
β>0 전체 냉각 루프는 실행하지 않았다.

후속 통합에는 `cooling_loop_V1.march()`가 station별 실제 구간 길이 배열을 입력받아
열전달 면적에 한 번만 사용하도록 하는 인터페이스가 필요하다. 이 변경 전에는
β>0 벽온도, 냉각재 출구온도, 총열량을 Model H 실제 결과로 보고할 수 없다.

## 9. 검증 범위와 잔여 한계

- β=0 Model B/Model H 회귀: 수치적으로 검증됨.
- 실제 제공 노즐 좌표, 목 index, 냉각 방향, 후보별 반경 연결: 구현·검산됨.
- IPA exact identity와 비외삽 범위/포화 gate: 구현됨.
- 제공 IPA 상관식의 압력 의존성·원자료 불확실성·실험 검증: 미확인.
- β>0 열–벽–냉각 재결합, 헬리컬 압력손실, Dean 이차유동: 미구현.
- 곡률에 따른 천이 변화, 3D 가공 간섭, 매니폴드 분배: 미검증.
- 제공 `[BLANK]` 및 `[검증용]` 설계 입력: `INPUT_U/MODEL_U` 유지.
- V01–V12: `NOT_RUN`; 안전 승인 및 설계 승인 아님.

## 10. 재현 명령

```powershell
.\.venv\Scripts\python.exe .\tools\verify_model_h_engine_integration.py `
  --engine-zip <provided-engine-zip> `
  --output-dir .\artifacts\model_h_engine_integration
```

생성 결과:

- `provided_engine_model_h_summary.json`
- `provided_engine_selected_candidate_stations.csv`
- `provided_engine_ipa_property_gate.csv`
