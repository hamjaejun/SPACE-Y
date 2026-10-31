# GAS–WALL 단일 구간 결합 구현 기록

기준일: 2026-10-31

## 구현 범위와 상태

CEA rocket station의 명시적 상태, RPA 2012 기본 Bartz 식, 1차원 정상 벽 전도 저항과 처방된 냉각 측 Robin 경계를 한 구간에서 결합했다. 목적은 벽온도에 의존하는 Bartz `sigma` 반복과 구간 에너지 수지를 검산하는 것이다.

이 구현은 `NON_DESIGN_INTERFACE_VERIFICATION`만 허용하며 정상 종료 상태도 `MODEL_U`다. 예제의 형상, 전도도, 복사율, 냉각 측 열전달계수와 벌크온도는 모두 합성 산술 검사용이고 SPACE Y 설계값·물성·운전조건·허용값이 아니다.

## 계산 구조

가스 대류 저항, 벽 전도 저항과 냉각 대류 저항은 각각 다음과 같다.

\[
R_g=\frac{1}{h_g A_g},\qquad
R_w=\frac{t_w}{k_w A_w},\qquad
R_c=\frac{1}{h_c A_c}.
\]

순복사 열전달률의 부호는 가스에서 벽으로 향할 때 양수다. 정상 단일 구간 열전달률은 다음 저항식으로 구한다.

\[
\dot Q_{total}=\frac{T_{aw}-T_{b,c}+\dot Q_{rad,net}R_g}{R_g+R_w+R_c},
\qquad
\dot Q_{conv}=\dot Q_{total}-\dot Q_{rad,net}.
\]

가스 측 벽온도에서 Bartz `sigma`와 `h_g`를 다시 평가하고, 저항망에서 새 벽온도를 계산해 under-relaxation한 고정점 반복을 수행한다. 반복 종료에는 벽온 갱신량과 가스/벽/냉각 에너지 잔차를 동시에 확인한다.

## 독립 산술 검산

`tests/test_gas_wall.py`에는 Bartz나 CEA를 사용하지 않는 세 저항 기준문제가 있다. 지정한 `R_g=0.005 K/W`, `R_w=0.01 K/W`, `R_c=0.005 K/W`, `T_aw=1000 K`, `T_b=300 K`, 명시적 `Q_rad=100 W`에 대해 손계산값 `Q_total=35025 W`, `T_wg=825.375 K`, `T_wc=475.125 K`와 비교한다. 이는 코드 산술과 에너지 보존 검산이지 실제 엔진 검증이 아니다.

합성 CEA–Bartz 결합 예제는 수치적으로 수렴하고 가스 유입, 벽 전도, 냉각 경계 열률이 지정 잔차 안에서 일치한다. 예제 출력값은 설계 후보 판단에 사용하지 않는다.

## 입력 보호와 상태 전파

- CEA station은 정수 index로 명시하며 station 사이를 보간하지 않는다.
- CEA 원단위 provenance와 transport basis가 없으면 `INPUT_U`다.
- Bartz 회복온도 식에서 `gamma <= 1`인 station은 `OUTSIDE`다.
- 순복사 열전달률과 출처는 필수이며 누락 시 `INPUT_U`다. 0을 기본값으로 넣지 않는다.
- 최대 반복 안에 벽온 고정점과 에너지 잔차가 함께 만족되지 않으면 `NUM_U`다.
- 냉각 측 `h_c`와 벌크온도는 처방 경계일 뿐 Gnielinski 또는 물성 평가 결과로 취급하지 않는다.

## WALL→COOLANT 인터페이스

수렴한 벽 열률은 `SPACE_Y_WALL_COOLANT_V1` 구조로 내보낸다. 구간·공간 기준, 면적, 부호, 단위, 냉각 측 벽온과 열률을 포함한다. 실제 냉각 엔탈피와 압손 계산에 필요한 I02–I23 자료와 구성식 적용성 증거가 제공되지 않았으므로 `coolant_state_update`와 `pressure_loss_update`는 `NOT_RUN`이다.

## 미구현·미검증

- 실제 냉각 물성과 Gnielinski 적용범위를 사용한 `h_c` 갱신
- 냉각 엔탈피·온도·압력손실과 Bartz 벽온의 완전 결합 반복
- 온도 의존 벽 물성, 리브/핀, 축·주방향 전도
- 다구간 marching과 역류 냉각 좌표 연결
- 복사 모델 계산, CEA station 공간 보간
- 격자 독립성, 불확실성, 허용조건, 실험 대조

V01–V12는 모두 `NOT_RUN`이며 `validated_engine_prediction`은 `false`다.
