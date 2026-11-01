# SPACE Y 축방향 직사각형 냉각채널 후보 생성과 `h_l` 계산

## 1. 목적과 책임 경계

`cooling_channel_candidates.py`는 ⑥ 담당의 예비 후보 생성 계층이다. 입력된
`N`, 리브 두께 `b`, 채널 높이 `h` 조합과 명시적 평균 채널 배치반경 `r_m(x)`에서
직사각형 채널 폭을 생성하고, 기존 `evaluate_rectangular_cooling_hl()`을 호출해
위치별 `h_l`과 Darcy 마찰 압력손실 구배를 계산한다.

이 계층은 벽면온도, 실제 냉각열량, 냉각유체 엔탈피 상승, 전체 압력손실, 공급계,
구조·제작성 승인 또는 최종 형상 최적화를 수행하지 않는다. 높은 `h_l`만으로 후보를
최종 설계로 선정하지 않으며 점수·가중합·자동 순위를 만들지 않는다.

## 2. 구현 형상

현재 지원 형상은 Axial, 1-pass 직사각형 채널이다. Helical 채널은 `OUTSIDE`다.

Constant rib width 정책은 위치별로 다음을 계산한다.

`pitch(x) = 2*pi*r_m(x)/N`

`w(x) = pitch(x) - b`

`A_f(x) = N*w(x)*h`

`D_h(x) = 2*w(x)*h/(w(x)+h)`

`r_m(x)`는 노즐 내벽 반경과 자동으로 같다고 가정하지 않는다. 입력에는 평균 채널
배치반경의 정의와 출처가 필요하다. 폭이 공급된 최소·최대 범위를 벗어나면 폭을
잘라내지 않고 후보 전체를 `EXCLUDE_GEOMETRIC_CONSTRAINT`로 분류하며 `h_l`을
계산하지 않는다.

윤곽이 없는 경우 `CONSTANT_WIDTH_LOCAL` 정책으로 한 개 국소 단면을 검산할 수 있다.
이 경로는 원주 배치 검증을 수행하지 않으므로
`INPUT_U_CIRCUMFERENTIAL_PLACEMENT`를 유지한다.

## 3. 국소 유동·열전달과 마찰 구배

각 유효 위치에서 기존 `evaluate_rectangular_cooling_hl()`을 재사용한다.

- `V = m_dot_c/(rho*A_f)`
- `Re = rho*V*D_h/mu`
- `Pr = mu*cp/k`
- Colebrook Darcy `f_D`
- 공급된 적용범위의 Gnielinski `Nu`
- `h_l = Nu*k/D_h`
- `dP_f/ds = (f_D/D_h)*rho*V^2/2`

`dP_f/ds`의 단위는 `Pa/m`이다. 입출구·곡률·매니폴드·국부손실과 전체 경로 적분은
포함하지 않는다. `s` 방향 실제 유로 길이는 후속 통합 단계의 입력이다.

기본 물성은 모든 위치에 동일하게 적용할 수 있으며 이때 결과는 일정 물성에서 형상
변화만 반영한 분포다. `EXPLICIT_BY_STATION_WITH_DEFAULT` 정책에서는 각 station에
명시된 물성만 사용하고 station 사이를 보간하지 않는다.

## 4. 문헌·참고 입력의 구분

- Kose & Celik, ODREC, Applied Sciences 14(1), 71 (2024), DOI
  `10.3390/app14010071`: `N=90`, `w=1 mm`, `h=6 mm`, `epsilon=6.3 um`.
  다른 추진제의 연구 입력이며 SPACE Y 기본값이 아니다.
- 사용자 확인 부트캠프 설정: `b=0.9 mm`, `w_min=0.9 mm`, `w_max=2.7 mm`,
  `h=1.2 mm`, `epsilon=12 um`, Constant rib width, Axial, 1-pass. 채널 개수는
  확인되지 않았으므로 ODREC의 `N=90`과 자동 결합하지 않는다.
- Fagherazzi의 41개 가변 채널과 Wadel의 고종횡비 연구는 형상 위험·경향 참고자료다.
  유체, 엔진 규모, 제작법이 달라 자동 탐색 한계로 사용하지 않는다.

`config/SPACE_Y_cooling_channel_reference_presets.json`의 값은 모두
`not_space_y_default=true`다. 합성 예제의 `N=60/80`, `b=0.6/0.9 mm`, 합성 반경
프로파일은 코드 경로 검산용 탐색값이지 문헌 또는 팀 승인 범위가 아니다.

## 5. 입력·출력 인터페이스

주 함수:

```python
from space_y.cooling_channel_candidates import (
    generate_channel_geometry_candidates,
    solve_cooling_channel_candidates,
    solve_cooling_channel_candidates_file,
    write_candidate_results_csv,
    write_candidate_summary_csv,
)
from space_y.cooling_channel_recommendation import (
    classify_cooling_channel_candidates,
)
```

파일 실행:

```powershell
.\.venv\Scripts\space-y.exe cooling-channel-sweep `
  examples\cooling_channel_sweep_synthetic_axial.json `
  --output artifacts\cooling_channel_sweep\synthetic_axial_result.json `
  --csv-output artifacts\cooling_channel_sweep\synthetic_axial_stations.csv
```

JSON은 입력 provenance, 후보 기하, 위치별 결과, 요약, 상태와 제한사항을 보존한다.
CSV는 후보·위치별 한 행으로 통합 모델이 다시 읽을 수 있다. 현재 프로젝트는 plotting
라이브러리를 의존성으로 갖지 않으므로 PNG를 자동 생성하지 않는다. 실제 설계 그래프는
검증된 윤곽·물성 입력이 들어온 뒤 별도 시각화 계층에서 생성한다.

## 6. 합성 검산 결과

합성 예제는 실제 노즐이 아닌 `r_m=[30,20,40] mm` 세 위치, 합성 유량
`0.6 kg/s`, 합성 일정 물성을 사용한다. 후보는 4개다.

| 후보 | N | b (mm) | h (mm) | 결과 |
|---|---:|---:|---:|---|
| CH-0001 | 60 | 0.6 | 1.2 | 출구 폭이 2.7 mm 초과, 기하 제외 |
| CH-0002 | 60 | 0.9 | 1.2 | 출구 폭이 2.7 mm 초과, 기하 제외 |
| CH-0003 | 80 | 0.6 | 1.2 | 세 위치 산술 계산, 통합검토 유지 |
| CH-0004 | 80 | 0.9 | 1.2 | 목 폭이 0.9 mm 미만, 기하 제외 |

CH-0003 결과:

| 위치 | w (mm) | Re | h_l (W/m2K) | dP_f/ds (Pa/m) |
|---|---:|---:|---:|---:|
| SYN-INLET | 1.75619 | 5074.09 | 6005.68 | 253840 |
| SYN-THROAT | 0.970796 | 6909.91 | 11641.12 | 1114532 |
| SYN-EXIT | 2.54159 | 4008.99 | 3933.06 | 108494 |

좁은 합성 목 단면에서 `h_l`과 마찰 구배가 함께 증가하는 상충 경향을 확인한다.
이는 합성 산술 검산일 뿐 목 형상 선정이나 실제 성능 검증이 아니다.

## 7. 상태와 후속 통합

- 기하 위반: `OUTSIDE`, 계산 미실행
- 입력·형상 근거 누락: `INPUT_U`
- 상관식 범위 이탈: station `OUTSIDE`
- 계산 성공: `MODEL_U`
- `validated_engine_prediction=false`
- `physical_validation_v01_v12=NOT_RUN`
- `safety_approval=false`
- `final_design_selection=false`

⑥에서 `RETAIN_FOR_INTEGRATION_REVIEW`인 후보는 국소 `h_l`·마찰 구배 관점의 예비
후보다. 최종 엔진 후보는 가스 열부하, 벽 전도·핀 효율, IPA 상태 marching, 전체
압력 예산, 구조·가공 제약을 결합한 후 다시 판단해야 하며 결과가 달라질 수 있다.

실제 계산에는 시스템 담당의 재생냉각 통과유량, IPA 물성 담당의 구간별 물성·상평형,
챔버·노즐 형상 담당의 평균 채널 배치반경 정의와 프로파일, 재료·가공 담당의 실제
조도·폭·리브 제약이 필요하다. ⑦은 노즐 기본 형상 담당이며 전체 축방향 열해석
담당으로 지정하지 않는다.

## 8. Baseline 및 예비 추천 계층

`classify_cooling_channel_candidates()`는 물리 계산이 끝난 후보 목록을 받아 별도 비교
계층으로 동작한다. Baseline은 `baseline_reference` 입력에 출처·정책·설정·기하 상태를
명시한 경우에만 기록한다. 추천 결과가 Baseline을 바꾸지 않으며
`automatically_selected=false`, `design_approved=false`를 강제한다. ODREC의 일정 폭 문헌
형상과 채널 개수가 빠진 부트캠프 Constant-rib 설정을 자동 결합하지 않는다.

비교 위치는 `THROAT` 또는 명시적 `STATION_ID`다. 모든 후보는 같은 station에서만
비교한다. 기준 위치가 없거나, 경로 중 한 위치라도 기하·계산·상관식 조건을 만족하지
않으면 추천 대상에서 제외한다. 위치별 최대값끼리 비교하거나 전체 분포를 평균·가중합하지
않는다.

유효 후보에는 다음 상대 태그를 중복 부여할 수 있다.

- `HIGHEST_HL_AT_REFERENCE`
- `LOWEST_FRICTION_GRADIENT_AT_REFERENCE`
- `PARETO_CANDIDATE`

기하·상관식·입력 문제는 각각 `EXCLUDED_GEOMETRY`, `EXCLUDED_CORRELATION`,
`UNVERIFIED_INPUT`으로 구분한다. Pareto 지배는 같은 기준 위치에서 `h_l` 최대화와 국소
Darcy 마찰 구배 최소화만 사용한다. `h_l`과 압력구배는 단위가 다르므로 별도 절대
허용오차와 공통 상대 허용오차를 입력한다. 유효 후보가 하나뿐이면
`SINGLE_VALID_CANDIDATE_NO_TRADEOFF`, 없으면 `NO_VALID_CANDIDATES`다.

다중 후보 합성 fixture는 기존 반경 `[30,20,40] mm`와 폭 제한 `[0.9,2.7] mm`를
유지하고, 합성 탐색 배열만 별도로 정의했다. 목 `SYN-THROAT` 비교 결과는 다음과 같다.

| 후보 | N | b (mm) | h_l (W/m2K) | dP_f/ds (Pa/m) | 상대 태그 |
|---|---:|---:|---:|---:|---|
| CH-0003 | 80 | 0.5 | 10435.72 | 863751 | Pareto |
| CH-0004 | 80 | 0.6 | 11641.12 | 1114532 | 최고 h_l, Pareto |
| CH-0005 | 90 | 0.3 | 8918.46 | 653032 | 최소 마찰 구배, Pareto |
| CH-0006 | 90 | 0.4 | 9928.40 | 835861 | Pareto |
| CH-0009 | 100 | 0.3 | 9230.27 | 763716 | Pareto |

이 다섯 후보는 두 국소 지표 사이에서 서로 지배하지 않는다. 이는 Pareto 구현과
열전달–유동저항 상충을 확인하는 합성 결과일 뿐 제조 가능 후보, SPACE Y Baseline 또는
최종 설계가 아니다. 모든 출력은 `Preliminary candidate recommendation, not final design
optimization.`을 기록하고 `final_design_selection=false`를 유지한다.

```powershell
.\.venv\Scripts\space-y.exe cooling-channel-sweep `
  examples\cooling_channel_recommendation_synthetic_multi.json `
  --output artifacts\cooling_channel_sweep\synthetic_recommendation_result.json `
  --csv-output artifacts\cooling_channel_sweep\synthetic_recommendation_stations.csv `
  --summary-csv-output artifacts\cooling_channel_sweep\synthetic_recommendation_summary.csv
```
