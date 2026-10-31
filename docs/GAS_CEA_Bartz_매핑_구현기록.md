# CEA–GAS–Bartz 매핑 구현 기록

기준일: 2026-10-31

## 결론

NASA CEA `rocket` 출력에서 사용자가 명시한 station을 선택하고, RPA 2012 문서의 `Basic Bartz equation`을 계산해 M0의 `gas_boundary` 형식으로 내보내는 단방향 인터페이스를 구현했다. 이 기능은 비설계 인터페이스 검산이며 상태는 항상 `MODEL_U`다. SPACE Y 형상, 벽온도, 복사열 또는 안전 허용값은 설정하지 않았다.

## 고정한 식과 단위

식 버전은 `RPA_2012_BASIC_BARTZ`로 고정했다. 열전달계수는 다음 표시식을 사용한다.

\[
h_g = \frac{0.026}{D_t^{0.2}}\frac{\mu_0^{0.2}c_{p,0}}{Pr_0^{0.6}}
\left(\frac{p_0}{c^*}\right)^{0.8}
\left(\frac{D_t}{R}\right)^{0.1}
\left(\frac{A_t}{A}\right)^{0.9}\sigma
\]

\[
\sigma = \left[\frac{1}{2}\frac{T_w}{T_0}
\left(1+\frac{\gamma-1}{2}M^2\right)+\frac{1}{2}\right]^{-0.68}
\left(1+\frac{\gamma-1}{2}M^2\right)^{-0.12}
\]

회복온도는 문서 표시식의 `Pr^0.33`을 선택 station의 명시된 transport basis에 적용했다. Bartz 계수의 `mu_0`, `cp_0`, `Pr_0`는 chamber station 0의 값이다. station 간 공간 보간은 하지 않는다.

CEA 원출력 단위는 NASA RP-1311 표기와 같이 압력 `bar(a)`, 점도 `millipoise`, 열전도도 `mW/(cm K)`, 비열 `kJ/(kg K)`다. 압력은 `1e5 Pa`, 나머지 SI 변환은 각각 `1e-4 Pa s`, `0.1 W/(m K)`, `1e3 J/(kg K)`를 곱한다. CEA artifact에는 station과 transport의 원단위를 함께 기록한다. 열전도도를 `microW/(cm K)`로 해석하는 1,000배 단위 오류를 방지하는 회귀검사를 추가했다.

## 입력 보호

- 성공하고 수렴한 `NASA_CEA_PYTHON` rocket 실행 artifact만 허용한다.
- CEA reference check가 하나라도 실패하면 계산하지 않는다.
- `frozen` 또는 `equilibrium` transport basis를 반드시 선택한다.
- throat 직경, Bartz 곡률반경, 각 segment의 CEA station, 벽온도, 가열면적, 순복사율과 각각의 출처를 요구한다.
- station 0을 flow station으로 재사용하거나 배열 범위를 벗어나면 중단한다.
- `gamma <= 1`이면 회복온도 식 적용범위 밖인 `OUTSIDE`로 중단한다.
- 복사는 `EXPLICIT_NET_RATE_GAS_TO_WALL_POSITIVE` 정의만 허용한다.

## 구현과 미검증의 구분

구현·자동검사 완료:

- CEA transport 원단위와 SI 값을 artifact에 함께 기록
- 명시적 station mapping, Bartz `sigma`, `h_g`, 회복온도, 지정 벽온도에서의 대류율 계산
- M0 입력과 같은 `gas_boundary` 출력
- 누락 transport, 잘못된 station, 누락 곡률반경 출처, 단위 변환 회귀검사

미구현·미검증:

- M0 계산 벽온도와 Bartz `sigma`의 결합 반복
- 실제 노즐 좌표에서 CEA station으로의 보간
- 원 Bartz/RPA 기준 데이터와의 상관식 수치 검증 및 보정계수 식별
- 연소가스 복사 모델
- 실제 SPACE Y 입력 및 V01–V12 검증
- 불확실성 전파, 재료 허용조건 또는 안전 판정

예제 `examples/gas_mapping_synthetic_non_design.json`의 숫자는 코드 경로 검사용 합성값이며 실제 SPACE Y 설계값이나 허용값이 아니다.
