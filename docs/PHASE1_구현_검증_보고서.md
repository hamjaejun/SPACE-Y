# SPACE Y Phase 1 구현·검증 보고서

작성일: 2026-10-31
기준 커밋: `f69ec75` (`main`, 작업 시작 시점)
기준 문서: v2.1 모델 선정 기준서, M0 입력·인터페이스 기록, M0 계약 JSON, `HANDOFF.md`

이 보고서는 Phase 1 소프트웨어 기능과 인터페이스 검증 기록이다. 새로운 SPACE Y 설계해, 안전 승인 또는 V01–V12 완료 기록이 아니다.

## 1. 시작 상태 확인

- 저장소에는 방법론 문서와 데이터 계약만 있었고 실행 가능한 M0 solver는 없었다.
- `solver_run`, `selected_case`, I01–I25 값, Y01–Y05 값은 비어 있었고 V01–V12는 모두 `NOT_RUN`이었다.
- 과거 로컬 산출물에는 NASA CEA 3.3.4 실행 기록이 있었지만 현재 M0 검증과 분리되어 있었다.
- 인수인계의 미완료 항목은 I02/I04 경계 역할, 출력별 무효화 의존성, 실제 JSON Schema/의미 검증, 수식 표시 확인이었다.
- 별도의 저장소 개발 지시 파일이나 기존 solver 코드는 발견되지 않았다.

## 2. 구현 결과

| 영역 | 구현·실행 결과 | 증거 | 해석 제한 |
|---|---|---|---|
| 입력 구조 | Draft 2020-12 JSON Schema와 I01–I25, Y01–Y05, 모듈 5개, V01–V12 정확 ID 검사를 구현. 현재 계약은 schema/ID/의미 검사를 통과 | [`contract_validation.json`](../artifacts/phase1/contract_validation.json) | 메타데이터 검사이며 V01 물리 검증이 아님 |
| 경계조건 독립성 | `PRESCRIBED_MASS_FLOW`와 `PRESSURE_DRIVEN` 모드, I02/I04 역할, 압력 구동 시 출구 절대압 요구를 명시. 현재 모드는 `null`이라 실행 준비도는 `BLOCKED` | [`SPACE_Y_M0_model_contract.json`](../SPACE_Y_M0_model_contract.json) | 실제 모드·압력·유량을 임의 선택하지 않음 |
| 무효화 전파 | Y01–Y05별 필수 입력 의존표와 `MISSING/INVALID→INPUT_U`, `OUTSIDE→OUTSIDE` 전파 구현 | [`qoi_dependencies.json`](../config/qoi_dependencies.json) | I24는 허용 비교, I25는 검증 자료로 QoI 계산 의존성과 분리 |
| 기본 물리식 | 총열유입 합, 벽 에너지 잔차, 냉각 엔탈피, Colebrook 잔차, Darcy–Weisbach 산술, Gnielinski 근거 미입력 중단 규칙 6건 통과 | [`physics_checks.json`](../artifacts/phase1/physics_checks.json) | 합성 산술 검산이며 실제 채널·유체 예측이 아님 |
| NASA CEA | 공식 `cea==3.3.4`를 CPython 3.13 환경에 설치. HP 예제와 RocketSolver RP‑1311 Example 13 자동 실행 성공 | [`cea_capability.json`](../artifacts/phase1/cea_capability.json), [`cea_rp1311_example13_rocket_run.json`](../artifacts/phase1/cea_rp1311_example13_rocket_run.json) | SPACE Y IPA/N₂O 활성 사례를 실행한 것이 아님 |
| RPA | 설치된 RPA 2.3 `rpas.exe`의 도움말과 제공 `performance1.js` 배치 실행 성공 | [`rpa_capability.json`](../artifacts/phase1/rpa_capability.json) | `PASS_WITH_WARNINGS`; SPACE Y 구성/열해석 실행 아님 |
| 자동 테스트 | 12개 단위 테스트 통과, 전체 Python 소스 compile 검사 통과 | `tests/` | 실험 검증이나 수치 격자 독립성 검사가 아님 |

## 3. 입력 검증과 상태 전파

현재 계약의 구조와 `null`/상태 일관성은 통과하지만 실행 준비도는 의도적으로 차단된다.

1. `flow_boundary_mode`가 `null`이다. 코드가 임의로 지정 유량 또는 압력 구동 문제를 선택하지 않는다.
2. I01–I23이 비어 있어 Y01–Y05는 모두 `INPUT_U`다.
3. I24와 I25도 비어 있으므로 설계 허용 판단과 실험/문헌 검증은 수행할 수 없다.
4. `solver_run`은 계속 `null`이고 V01–V12는 원 계약에서 변경하지 않았다.
5. 구조 검증 통과를 V01 `PASS`로 승격하지 않는다.

지정 유량 모드에서는 I02(입구 절대압)와 I04(경로 질량유량)를 서로 다른 역할의 독립 입력으로 사용해 상태와 압손을 계산한다. 압력 구동 모드에서는 I02와 명시적인 출구 절대압을 경계로 쓰고 I04는 계산값이어야 한다. 두 모드의 경계를 동시에 강제하면 의미 검증이 실패한다.

## 4. 기본 물리 계산 검산

`src/space_y/physics.py`는 v2.1에 적힌 보존식과 상관식 연결에 필요한 작은 계산 단위를 구현했다.

- `Q_g,total = Q_g,conv + Q_g,rad,net`
- 벽 에너지 잔차 `Q_g,total - Q_c - Q_loss + Q_axial,net - dU_w/dt`
- `h_out = h_in + Q_c / m_dot`
- 수력직경, Reynolds 수, Prandtl 수
- Darcy 형 Colebrook 반복해와 잔차
- Darcy–Weisbach 마찰+국부 압손
- 문서에 표시된 Gnielinski 기본형

Gnielinski 구현에는 Re/Pr 범위나 형상 조건을 내장 기본값으로 넣지 않았다. I18에 출처가 있는 판본/적용영역을 전달하지 않으면 값을 계산하지 않고 `INPUT_U`, 범위를 벗어나면 `OUTSIDE`를 반환한다. 층류/전이/비등 대체식을 자동 적용하지 않는다.

이번 6개 검사는 비설계 합성 산술이며 V02–V09를 통과시킨 것이 아니다. 실제 물성, 채널 형상, 가열, 격자와 수치 오차예산이 없으므로 M0 열유동 결과는 없다.

## 5. NASA CEA 자동 실행 인터페이스

공식 설치 문서가 권장하는 PyPI 패키지 `cea==3.3.4`를 선택적 의존성으로 고정했다. CLI는 별도 Python 프로세스에서 요청 JSON을 실행하고 다음을 기록한다.

- 요청 파일 경로와 SHA-256
- 선택한 Python 실행기와 CEA 버전
- 수렴/오류 상태
- 원시 station 배열과 조성
- 비설계 인터페이스 검증 표식
- 출판된 기준값과의 항목별 허용오차 비교

RP‑1311 Example 13은 공식 문서의 반응물, 3000 psi 챔버 압력, 압력비, insert species를 그대로 사용했다. 챔버/출구 온도, throat Mach, 최종 면적비, 최종 비추력의 5개 기준값이 모두 지정 허용오차 안이었다. 이것은 NASA CEA RocketSolver 연결과 데이터 수집의 회귀 검증이다. SPACE Y IPA/N₂O의 반응물 엔탈피, 조성, 실제 경계 또는 열손실 모델은 이번 실행에 넣지 않았다.

공식 자료:

- [NASA CEA 설치](https://nasa.github.io/cea/installation.html)
- [NASA CEA Python API](https://nasa.github.io/cea/interfaces/python_api.html)
- [NASA CEA RP‑1311 Example 13](https://nasa.github.io/cea/examples/rocket/example13.html)

## 6. RPA 연결 가능성

검사 당시 접근 가능한 RPA 2.3 실행 환경에는 scripting 실행 파일과 thermal/ECMAScript 예제가 존재했다. 설치본 매뉴얼과 실제 `--help`는 `rpas.exe -i SCRIPT -o PREFIX` 배치 실행을 지원한다. 제공 성능 예제를 실행해 종료코드 0과 info/warn 로그 생성을 확인했다. 공개 기록에는 사용자별 설치 위치를 남기지 않는다.

판정은 `PASS_WITH_WARNINGS`다. 표준 properties 파일에 정의됐지만 열화학 데이터베이스에서 찾지 못한 종 5개(`CCLF3`, `CCL2F2`, `CCL3F`, `CHCLF2`, `CHCL2F`) 경고가 생성됐다. 예제 계산 자체는 완료됐지만 데이터베이스 일관성 경고를 숨기지 않는다.

추가 제한:

- 설치본은 RPA 2.3이며 제작사 공지상 2.x 지원은 2024-12-31 종료됐다. 현재 배포 계열은 4.x이고 2.x cfg와 4.x JSON은 호환되지 않는다.
- 이번 테스트는 RPA 제공 성능 예제이며 SPACE Y 구성이나 thermal case가 아니다.
- RPA와 자체 코드가 상관식·열화학/물성 자료를 공유할 수 있으므로 향후 일치가 독립적인 물리 검증을 뜻하지 않는다.
- RPA thermal scripting API 연결은 설치본 예제에서 확인했지만 SPACE Y 채널/재료/냉각 경계가 없어 실행하지 않았다.

제작사 자료:

- [RPA Development Tools](https://rocket-propulsion.com/RPA/dev_tools.htm)
- [RPA 다운로드와 버전/제한](https://app.rocket-propulsion.com/RPA/download.htm)
- [RPA 2.x/4.x 구성 파일 이관](https://rocket-propulsion.com/blog.htm?id=27)

## 7. 미검증·차단 항목

다음 항목은 구현 또는 검증 완료로 보고하지 않는다.

- 실제 SPACE Y 입력 I01–I25의 확보·검토
- M0 결합 가스/벽/냉각/저항망 solver
- Bartz 판본과 Gnielinski 판본/정오표/적용 범위의 최종 고정
- 상태 의존 IPA 물성 모델과 단상/벽면 비등 적용성
- 복사, 리브/핀, 축방향 전도, 비균등 분배, 과도 영향
- 실제 격자/시간 분할, 수렴, 국소·전체 질량/에너지 보존
- SPACE Y 조건의 NASA CEA 재실행과 과거 v5 결과의 독립 재현
- SPACE Y RPA cfg 생성·성능/thermal 실행 및 자체 모델과의 정렬
- RPA 데이터베이스 경고의 원인/영향 해소
- 실험·문헌 대조와 불확실성 정량화
- 고온 재료/구조/공급/시험 허용 근거와 안전 판단
- V01–V12 전체와 `PDR_CONDITIONAL` 판정

## 8. 다음 단계의 입력 게이트

M0 계산을 시작하기 전에 최소한 다음을 실제 출처와 함께 채워야 한다.

1. 유동 경계 모드와 I02/I04 역할
2. 냉각 입출구 노드/방향, 공간 기준과 실제 경로 유량 또는 출구 압력 경계
3. 채널 네트워크, 구간 형상, 세 면적, 수력직경, 벽/리브 형상
4. IPA 조성·입구 상태·물성 모델/범위, 등가 모래 거칠기 근거
5. 구간 가스 상태, 복사 정의, 구성식 판본/적용 범위
6. 정상/과도 시간창, 수치 방법과 사전 오차예산
7. 판단 목적별 허용 근거와 독립 검증 자료

이 게이트를 통과하기 전에는 프로그램이 숫자를 만들도록 기본값을 채우지 않는다.

## 9. 재현 명령

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[validation,cea]"
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\space-y.exe validate-contract SPACE_Y_M0_model_contract.json --output artifacts\phase1\contract_validation.json
.\.venv\Scripts\space-y.exe physics-check --output artifacts\phase1\physics_checks.json
.\.venv\Scripts\space-y.exe cea-run examples\cea_rp1311_example13_rocket_non_design.json --python .\.venv\Scripts\python.exe --output artifacts\phase1\cea_rp1311_example13_rocket_run.json
```

RPA 설치본 검사는 경로를 환경에 맞춰 지정한다.

```powershell
.\.venv\Scripts\space-y.exe rpa-probe `
  --executable "<RPA_EXECUTABLE>" `
  --resources "<RPA_RESOURCES>" `
  --user-resources "<RPA_USER_RESOURCES>" `
  --smoke-script "<RPA_SMOKE_SCRIPT>" `
  --output-prefix "artifacts\runs\rpa_performance1" `
  --output "artifacts\phase1\rpa_capability.json"
```
