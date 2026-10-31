# IPA 물성자료 검토 및 Property Adapter 구현 기록

기준일: 2026-10-31

## 1. 결론과 현재 상태

대상 유체는 **2-propanol, CAS 67-63-0**으로 고정했다. 1-propanol(CAS 71-23-8)이나 식별자가 없는 generic propanol은 대체물로 허용하지 않는다.

공개 자료와 현재 실행환경만으로는 M0가 요구하는 `h(p,T)`, `T(p,h)`, `rho`, `mu`, `cp`, `k`, 포화상태 및 단상 판정을 하나의 일관된 기준상태와 검증 범위로 제공할 수 없다. 따라서 실제 IPA capability는 모두 `INPUT_U`이며 실제 M0 엔진 계산은 수행하지 않았다. 구현 완료 범위는 공급자 독립 인터페이스, 범위/단위/기준상태 보호, 합성 산술 검산과 실제 공급자 readiness 차단이다.

자동 테스트 통과는 코드의 상태·오류 전파와 합성 산술을 확인한 것이며 물성 모델의 정확도나 실제 엔진 물리 검증을 뜻하지 않는다. V01–V12는 `NOT_RUN`이다.

## 2. 자료별 검토

| 후보 | 확인된 내용 | M0에 부족한 내용 | 공개/라이선스 판단 | 현재 상태 |
|---|---|---|---|---|
| [NIST Chemistry WebBook SRD 69](https://webbook.nist.gov/cgi/cbook.cgi?ID=C67630&Mask=4) | CAS 67-63-0, 정상비점·임계점·증발엔탈피, 두 Antoine 구간 | 압축액 `h(p,T)`, 역산, `rho`, `mu`, `cp`, `k`, 완전한 포화 액/증기 상태 | 공개 열람 가능하지만 페이지가 data compilation copyright를 명시한다. 계수/표 재배포는 별도 약관 검토 전 링크·메타데이터만 저장 | `INPUT_U` |
| [NIST REFPROP 10](https://www.nist.gov/srd/refprop) | 일반적으로 열역학·수송물성, 포화표, 기준상태 설정을 지원하는 유료 제품 | 공개 [V10 fluid list](https://pages.nist.gov/REFPROP-docs/)에서 2-propanol/isopropanol을 확인하지 못했다. NIST 소개의 generic “propanol”은 CAS 식별 근거가 아니다 | 유료/라이선스 제품. DLL·fluid file을 공개 저장소에 포함하지 않는다 | `INPUT_U` |
| [AIChE DIPPR 801](https://www.aiche.org/dippr/events-products/801-database/thermophysical-properties) | 공개 compound list에 ISOPROPANOL, CAS 67-63-0이 1-PROPANOL과 별도 등재. LCP, LDN, LTC, LVS, VP 등의 속성 유형 제공 | 실제 licensed record, 판본, 계수, 각 유효범위, 압력 의존성, p,h 역산과 공통 엔탈피 기준 미확보 | proprietary database. 허가 없이 계수/레코드를 공개 저장소에 넣지 않는다 | `INPUT_U` |
| [CoolProp 8.0.0 PC-SAFT](https://coolprop.org/coolprop/PCSAFT.html) | upstream PC-SAFT parameter file에 `2-PROPANOL`, CAS 67-63-0 항목 존재. 코드 배포는 MIT | 문서가 현재 입력쌍을 TP/PQ/DT/QT로 제한하고 flash robustness를 경고한다. p,h, 점도, 열전도도, 적용범위·불확실성은 확인되지 않았다 | adapter 코드는 공개 가능하나 upstream 데이터 출처를 인용하고 물성별 검증이 필요 | `INPUT_U` |
| [chemicals](https://github.com/CalebBell/chemicals) / [thermo](https://github.com/CalebBell/thermo) | CAS 기반 후보 상관식과 databank 탐색 가능, 공개 예제에 isopropanol 존재 | upstream이 자료를 critically evaluated recommendation으로 보지 않으며 누락 가능성을 경고. 방법·범위·기준상태를 고정하지 않으면 일관된 provider가 아님 | 코드 라이선스와 개별 databank 출처를 따로 확인해야 함 | 탐색/교차검토만, `INPUT_U` |

WebBook Antoine 식은 `log10(P/bar)=A-B/(T+C)`이고 공개 페이지가 제시한 구간은 329.92–362.41 K와 395.1–508.24 K다. 중간 공백을 연결하거나 압축액 모델로 전용하지 않는다. 이 식만으로 M0의 단상 열유동 물성을 구성할 수 없다.

REFPROP는 후보 제품으로서는 강력하지만 **REFPROP 10의 공개 fluid list에서 exact IPA를 찾지 못했기 때문에 이번 검토에서는 지원한다고 판정하지 않았다**. 별도 판본·사용자 fluid file 또는 향후 판본이 제공되면 파일명, CAS, 모델 참고문헌, 수송모델과 유효범위를 설치환경에서 다시 확인해야 한다.

## 3. Property Adapter 계약

`src/space_y/property_adapter.py`에 다음 경계를 구현했다.

- `state_pt(p,T)`와 `state_ph(p,h)`
- 밀도, 점도, 정압비열, 열전도율을 포함하는 단일 `PropertyState`
- 엔탈피 기준 식별자를 모든 state에 포함
- `saturation_at_temperature`, `saturation_at_pressure`를 독립 capability로 분리
- `classify_phase_pt`, `classify_phase_ph`를 독립 interface로 분리
- `density_pt`, `viscosity_pt`, `cp_pt`, `conductivity_pt`를 개별 조회할 수 있게 분리
- capability별 `AVAILABLE`/`INPUT_U`, 근거, 온도·절대압 범위와 상 영역 기록
- provider, dataset/model, version, source URL, license/redistribution, SI 단위계 기록
- 잘못된/미확인 입력은 `INPUT_U`, 제공 범위 이탈은 `OUTSIDE`, backend 미결합은 `MODEL_U`
- partial state를 반환하지 않고 `PropertyAdapterStop`으로 계산 중단

계약 단위는 Pa(abs), K, J/kg, kg/m³, Pa·s, J/(kg·K), W/(m·K)다. 엔탈피는 음수일 수 있으므로 양수 조건을 강제하지 않지만, 동일 계산 경로에서 `enthalpy_reference`가 바뀌면 사용할 수 없다.

기존 M0 솔버는 `M0PropertyAdapter` 최소 경계와 `build_m0_property_adapter` factory를 통해 `state_pt`/`state_ph`를 호출한다. 현재 factory에 연결된 공급자는 기존 합성 검산용 bounded table뿐이다. 실제 IPA backend는 아래 승인 조건을 통과하기 전 factory에 연결하지 않는다.

## 4. 구현된 공급자와 차단

### 4.1 합성 공급자

`SyntheticTabulatedPropertyAdapter`는 `purpose=SYNTHETIC_VERIFICATION_ONLY`, source, version과 enthalpy reference가 모두 있어야 초기화된다. 선형 보간은 공급된 온도·압력·엔탈피 범위 안에서만 수행하며 외삽은 `OUTSIDE`다. provenance에는 `SYNTHETIC_FLUID_NOT_IPA`와 `NOT_ENGINE_PREDICTION`을 명시한다.

합성 phase 판정은 공급 영역 안이라는 fixture 선언만 확인한다. 포화모델이나 상평형 증거가 아니며 포화 조회는 `INPUT_U`다.

### 4.2 실제 IPA readiness 공급자

`ReviewedIPAReadinessAdapter`는 exact name/CAS와 capability 선언을 검사한다. 현재 `config/SPACE_Y_IPA_property_sources.json`은 모든 실제 capability가 `INPUT_U`라서 property call을 실행하지 않는다. 후보 자료가 존재한다는 이유만으로 계산 기능을 열지 않는다.

해당 JSON은 `schemas/SPACE_Y_IPA_property_sources.schema.json`으로 검증하며 다음 상태도 고정한다.

- `physical_validation_v01_v12 = NOT_RUN`
- `asymptotic_regime = NOT_ESTABLISHED`
- `four_n_is_truth = false`
- Richardson extrapolation과 GCI는 `NOT_PERFORMED`

## 5. 자동 검산

새 테스트는 다음을 확인한다.

- 1-propanol/generic propanol identity 거부
- 합성 provider의 실제 IPA 오인 방지 표식
- p,T → h와 p,h → T 상호 역산 일관성
- SI 단위와 엔탈피 기준 추적
- 온도·압력·엔탈피 경계 밖 `OUTSIDE`
- NaN, 0 K 등 잘못된 입력의 `INPUT_U`
- 포화/phase capability 독립성
- 실제 IPA readiness record의 실행 차단
- source manifest Schema 검증
- V01–V12 및 N/2N/4N 미확립 상태 유지

이 시험은 합성 선형표의 산술과 interface 정책을 검산한다. 실제 IPA 데이터 오차, 고압 과냉액, 임계영역, 상평형, 열전달·압력손실 예측은 검증하지 않는다.

## 6. 실제 M0 연결 승인에 필요한 입력

1. 실제 냉각유체 사양: 2-propanol 순도, 수분, 첨가제, lot/시험성적서 또는 승인 출처
2. 선택 provider와 exact dataset/model/version, CAS 식별자
3. 각 물성의 식/데이터 참고문헌, T–p–phase 유효범위와 불확실성
4. 일관된 `h(p,T)`와 `T(p,h)` 및 엔탈피 기준상태/offset
5. `rho`, `mu`, `cp`, `k`의 압력 의존성 처리 근거
6. `p_sat(T)`, `T_sat(p)`, 포화 액/증기 엔탈피 및 단상 margin 판정
7. intended M0 domain을 포함하는 독립 reference points와 허용 오차
8. 라이선스상 실행환경 배포와 결과/계수 공개 범위

## 7. 다음 개발 단계

1. 사용 가능한 licensed dataset 또는 공개 평가모델을 한 개 선택하고 exact IPA 지원을 확인한다.
2. provider별 executable backend를 별도 모듈로 구현하되 capability를 개별 probe한다.
3. 승인된 domain 안의 독립 reference points로 p,T/p,h round-trip과 각 물성을 교차 검산한다.
4. enthalpy reference와 phase margin이 일치할 때만 `build_m0_property_adapter` factory에 연결한다.
5. 합성 fixture와 실제 provider 실행 artifact를 별도 경로·상태로 저장한다.
6. 실제 형상·경계조건·명시 CEA station mapping이 확보된 후에만 M0 실제 입력 dry run을 검토한다.

비등 상관식, 병렬 채널, 설계 최적화는 이 단계에 포함하지 않았다.

## 8. CoolProp 8.0.0 로컬 capability probe

공개 PyPI wheel을 Git에 포함되지 않는 로컬 `.venv`에 설치해 `PCSAFT::2-PROPANOL`을 비설계 조건 300 K, 101325 Pa에서 실행했다. 이 조건은 SPACE Y 입력이나 운전점이 아니라 API 기능 존재 여부만 확인하기 위한 점이다.

| 기능 | 실행 결과 | M0 판정 |
|---|---|---|
| exact `2-PROPANOL` 초기화 | 성공 | 식별자 확인만 `AVAILABLE` |
| p,T 밀도 | finite output 반환 | 범위·불확실성 독립 검증이 없어 `AVAILABLE_NOT_VALIDATED` |
| p,T phase flag | 반환 | phase boundary 검증이 없어 `AVAILABLE_NOT_VALIDATED` |
| T 기반 포화압 | finite output 반환 | 범위·불확실성 검증이 없어 `AVAILABLE_NOT_VALIDATED` |
| p,T 엔탈피 | `calc_hmolar is not implemented` | `INPUT_U` |
| p,T 비열 | `calc_cpmolar is not implemented` | `INPUT_U` |
| 점도 | `calc_viscosity is not implemented` | `INPUT_U` |
| 열전도율 | `calc_conductivity is not implemented` | `INPUT_U` |
| p,h flash | `HmolarP_INPUTS` 미지원 | `INPUT_U` |
| 포화 액/증기 엔탈피 | 엔탈피 미구현으로 계산 불가 | `INPUT_U` |

따라서 CoolProp PC-SAFT 8.0.0 단독으로는 현재 Property Adapter 계약을 만족하지 않는다. 밀도와 포화압 일부가 실행된다는 이유로 다른 데이터셋의 `h`, `cp`, `mu`, `k`를 근거 없이 결합하지 않았고 실제 backend도 구현하지 않았다. 실행 기록은 `artifacts/property/ipa_coolprop_pcsaft_capability_probe.json`에 물성값 없이 capability evidence만 보존한다.

같은 환경에서 REFPROP/DIPPR 설치, 관련 환경변수, 일반 설치 프로그램 항목과 workspace fluid file은 확인되지 않았다. 이는 이 PC에 대한 결과일 뿐 학교 기관 라이선스 부재를 증명하지 않는다. 기관 담당자에게 제품명·판본·좌석/네트워크 라이선스·2-propanol record 접근 여부·공개 가능한 결과 범위를 확인해야 한다.
