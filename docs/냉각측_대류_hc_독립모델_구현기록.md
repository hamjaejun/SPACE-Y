# SPACE Y 냉각 측 대류 `h_c` 독립 모델 구현 기록

## 1. 책임 범위

이 증분은 Property Adapter 또는 ⑦ 축방향 모델이 제공한 한 구간의 벌크 상태와
처방 유량을 받아 다음 값을 계산한다.

- 채널 속도, Reynolds 수, Prandtl 수
- Colebrook Darcy 마찰계수
- 공급된 적용범위 안의 Gnielinski Nusselt 수와 `h_c`
- Darcy–Weisbach 마찰손실과 명시적 국부손실
- 적용범위, 입력 출처, 가정, 미확인 상태

가스 측 Bartz, 벽 전도, 물성 데이터베이스, 비등/상변화, 매니폴드 유량분배,
공급계, 구조, 채널 치수 최적화는 이 모듈의 책임이 아니다. 기존
`coolant.py`, `physics.py`, `m0_coupled.py`, `m0_marching.py`의 폐쇄식과 상태
체계를 재사용한다.

## 2. 입력 경계

인터페이스 `SPACE_Y_COOLING_HC_V1`은 네 묶음을 요구한다.

1. **Fluid State**: `p,T,h,rho,mu,cp,k`, 정확한 유체 식별, 물성 출처와 판본
2. **Flow Conditions**: 직접 냉각유량 또는 `beta * 연료유량`, 상·하류 분기와 출처
3. **Channel Geometry**: `w,h,N,L`, 등가모래조도, 국부손실계수, 선택적 리브와 평균반경
4. **Model Conditions**: 독립 단상영역, Gnielinski/Colebrook 공급 범위, 형상,
   열적 경계조건, 완전발달 근거

실제 실행은 네 묶음이 모두 근거와 함께 제공되기 전에는 설계 예측으로 승인하지
않는다. `config/SPACE_Y_cooling_hc_input_readiness.json`의 현재 종합 상태는
`INPUT_U`이다.

## 3. 유량 분기 규칙

재생냉각 통과유량은 다음 두 방법 중 하나 이상으로 입력한다.

`m_dot_cool = beta * m_dot_fuel`, `0 < beta <= 1`

직접 유량과 `beta`를 동시에 주면 일치 여부를 검사한다. 상류 바이패스가 있으면
`m_dot_cool = m_dot_fuel - m_dot_upstream`도 동시에 만족해야 한다. 재생냉각 이후의
필름 분기는 이미 채널을 통과한 유량이므로 `h_c`와 압손 계산에서 다시 차감하지
않는다. 하류 인젝터 도달량만 `m_dot_cool - m_dot_film_downstream`으로 기록한다.

채널별 유량은 `m_dot_ch = m_dot_cool/N`이다. 이는 매니폴드 해석 결과가 아니라
`ASSUMED_EQUAL_DISTRIBUTION` 가정이다. 실제 분배 근거가 없으므로 안전 판정에
사용할 수 없다.

## 4. 직사각 폐채널 기하

한 채널에 대해 다음 항등식을 사용한다.

- `A_ch = w h`
- `A_f,total = N w h`
- `P_wet,ch = 2(w+h)`
- `D_h = 4 A_ch/P_wet,ch = 2wh/(w+h)`
- `V = m_dot_cool/(rho A_f,total)`

`N P_wet,ch L`은 전체 내부 젖은면적이고 `N w L`은 명목 열벽 바닥면적이다.
두 면적을 서로 대체하지 않는다. 리브 두께와 평균반경이 모두 있을 때만 원주 피치
`2*pi*r_m/N`과 `w+b`의 기하학적 겹침을 검사한다. 이 검사는 제작성·구조 여유를
입증하지 않는다.

## 5. 폐쇄식과 적용범위

기존 구현을 그대로 호출한다.

- `Re = (m_dot/A_f) D_h/mu`
- `Pr = cp mu/k`
- Darcy 형식 Colebrook 반복해
- 공급된 판본/범위의 Gnielinski 표시식
- `h_c = Nu k/D_h`
- `DeltaP = (f L/D_h + sum K) rho V^2/2`

상관식에 내장 설계 기본범위를 새로 추가하지 않았다. 실행 입력의 Re/Pr, 상대조도,
형상, 완전발달 조건, 열적 경계조건 중 하나라도 공급 범위와 다르면 `OUTSIDE`이다.
근거가 없으면 `INPUT_U`, 수치 반복 실패는 `NUM_U`, 물리 검증이 남아 있는 정상
산술 결과는 `MODEL_U`이다.

Cardiff PDR은 냉각 측 Sieder–Tate 형태를 제시하지만 벽면 점도와 정확한 판본·범위가
현재 SPACE Y 인터페이스에 고정되지 않았다. Dittus–Boelter도 독립 적용 근거가 없다.
따라서 둘 다 자동 대체식으로 구현하지 않았다.

## 6. 문헌 형상 확인

모든 값은 `config/SPACE_Y_cooling_channel_reference_presets.json`에서
`REFERENCE_PRESET` 또는 `INCOMPLETE_REFERENCE_PRESET`으로만 기록한다.

- Kose & Celik, ODREC, Applied Sciences 14(1), 71,
  DOI `10.3390/app14010071`, Table 1: `N=90`, `w=1 mm`, `h=6 mm`,
  등가조도 `6.3 um`. LOX/LCH4 및 LOX/LC3H8 비교 입력이며 IPA/SPACE Y 설계값이 아니다.
- Fagherazzi et al., Aerospace 10(5), 403,
  DOI `10.3390/aerospace10050403`: 41개 축방향 가변 채널, 물 냉각, 약 450 N급
  시험체, 냉류 시험 기반 평균 등가조도 약 `20 um`. 단일 `w,h` 프리셋으로 축약하지
  않았다.
- Wadel, NASA TM-107473 / AIAA-97-2913, NTRS `19970024873`: 액체수소 냉각
  89 kN급 연구, 100/200채널 설계와 기준 종횡비 2.5. 연구의 제작 제약과 예측 오차는
  SPACE Y 자동 제한으로 승격하지 않았다.

이 자료는 형상 규모와 모델 위험을 비교하는 근거일 뿐 IPA 상관식 검증이나 SPACE Y
치수 선정 근거가 아니다.

## 7. IPA 물성 입력 상태

팀 제공 물성표는 정확한 물질을 2-propanol, CAS `67-63-0`으로 식별하고 액체 밀도,
비열, 점도, 열전도도 후보 상관식을 포함한다. 그러나 다수 상관식은 온도 함수이고
압력 의존 자료와 독립 상평형 경계가 충분하지 않다. 따라서 현재 자료를 검증된
`p,T` Property Adapter 또는 단상 판정기로 자동 연결하지 않았다. 원본 유료 데이터나
재배포 제한 자료도 저장소에 포함하지 않았다.

실제 연결에는 최소한 다음이 필요하다.

- IPA 순도/수분과 냉각 입구 `p,T`
- 전체 경로의 `rho,mu,cp,k,h` 유효 `p,T` 범위와 불확실성
- 엔탈피 기준상태와 `p,h -> T` 역산
- 포화선/임계점 및 단상 여유의 독립 근거
- 데이터 판본, 라이선스, 공개 가능한 provenance

## 8. 합성 검산

`examples/cooling_hc_rectangular_synthetic.json`은 실제 엔진과 분리된 산술 fixture다.
20개 `1 mm x 2 mm` 채널과 합성 물성을 사용하며 다음 결과를 낸다.
재현 실행 결과는 `artifacts/cooling_hc/synthetic_rectangular_result.json`에 기록했다.

- `A_f,total = 4.0e-5 m2`, `D_h = 1.333e-3 m`
- `Re = 10000`, `Pr = 13.333`
- `f_D = 0.0320159`, `Nu = 101.873`
- `h_c = 11460.7 W/(m2 K)`, `DeltaP = 101995 Pa`

이 결과는 형상 항등식, 질량보존, 단위 연결, 기존 폐쇄식 호출을 검산할 뿐 실제 IPA
또는 SPACE Y 물리 검증이 아니다. `V01–V12=NOT_RUN`, `validated_engine_prediction=false`,
`safety_approval=false`를 유지한다.

## 9. ⑦ 모델 연결

⑦은 각 축방향 구간마다 Property Adapter가 확정한 Fluid State, 실제 냉각 진행 순서,
구간 `w,h,N,L`, 조도/국부손실, 열적 경계와 발달 조건을 이 인터페이스에 전달할 수
있다. 반환되는 `h_c`와 `DeltaP`는 기존 `m0_coupled`의 입력 구조와 같은 단위다.
다만 실제 연결 전에는 분기 위치, 동일 채널 경로 여부, 구간별 형상 provenance,
상류 오류의 상태 전파 규칙을 고정해야 한다.

## 10. 남은 한계

- 실제 매니폴드 불균일 분배, 채널 막힘, 제작 공차
- 입구 발달영역과 곡률/회전/거칠기 증강 효과
- 벽면 점도 보정 및 비등·상변화·막비등
- 축방향/원주방향 벽 전도와 핀 효율
- 실제 IPA 데이터 및 실제 채널에 대한 독립 기준값/시험 검증

따라서 정상 계산 성공이나 공급 범위 통과를 냉각 안전성 `PASS`로 해석하지 않는다.
