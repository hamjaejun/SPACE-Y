# SPACE Y 냉각 측 `h_l` 상관식 적용성 검증 기록

## 1. 결론과 상태

이 기록은 냉각 측 계산이 실행되는 조건과 설계 검토용 경고를 분리한다. 코드의
Gnielinski 범위 판정은 `3000 <= Re <= 5e6`, `0.5 <= Pr <= 2000`이며 경계값을
포함한다. SPACE Y 팀의 예비 설계 검토 기준 `Re_min >= 5000`과 `Re < 10000`
천이 경고는 상관식 자체의 범위와 별도다. 계산 성공은 `MODEL_U`이며 실제 엔진
물리 검증, 안전 승인 또는 최종 설계 선정을 뜻하지 않는다.

모든 축방향 station이 명시적으로 계산된 경우에만 공급 station 집합에서 최소 Re를
기록한다. 최소값의 station ID, 좌표와 `Re_min - 5000` 여유를 함께 남긴다. station
하나라도 미완료이면 경로 판정은 `INPUT_U`이고, 계산되지 않은 연속 위치까지
만족한다는 주장은 항상 `false`다. station 사이 물성·CEA 상태를 보간하지 않는다.

## 2. 상관식과 근거의 분리

### Gnielinski

구현식은 기존 프로젝트의 displayed common form을 그대로 사용한다. 정책 파일은
사용자가 지정한 Incropera et al. 7판 8.5절을 팀 근거로 기록하지만, 라이선스 교재의
원문이나 표를 저장소에 재배포하지 않는다. 원 Gnielinski 논문의 서지는 V. Gnielinski,
“New equations for heat and mass transfer in turbulent pipe and channel flow,”
*International Chemical Engineering*, 16 (1976), 359–368로 기록한다.

현대 문헌은 원 상관식의 자료가 주로 충분히 난류인 영역에 집중되고 천이영역 예측에
추가 불확실성이 있음을 지적한다. 따라서 코드가 `Re >= 3000`에서 실행될 수 있다는
것과 SPACE Y 후보로 권고할 수 있다는 것을 동일시하지 않는다.

- 현대 천이영역 검토: <https://www.sciencedirect.com/science/article/pii/S0017931015303355>

### Colebrook Darcy 마찰계수

거친 채널과 매끈한 채널의 Darcy 계수는 같은 Re에서 각각 Colebrook 식으로 계산한다.
Fanning 계수가 아니다. Colebrook 수치 적용범위는 Gnielinski 범위를 복사하지 않고
각 실행의 `friction_domain`에 별도 출처와 함께 공급해야 한다. 이번 조사에서는 모든
경우에 적용할 하나의 보편적인 Re/상대조도 수치범위를 확정하지 않았다.

- Colebrook 1939 ICE 기록: <https://www.icevirtuallibrary.com/doi/pdf/10.1680/ijoti.1939.14509>
- Colebrook–White 1937 원 논문: <https://royalsocietypublishing.org/doi/pdf/10.1098/rspa.1937.0150>

### 직사각형 채널과 수력직경

직사각형 폐채널은 `D_h = 4A/P_w = 2wh/(w+h)`로 근사한다. 결과에는 `h/w`
종횡비를 기록한다. 수력직경 치환은 단면 형상 효과, 가열 벽 분포, 입구영역과 완전발달
조건을 제거하지 않는다. 완전발달 여부는 독립 근거가 필요한 입력 주장이다.

- ASHRAE Handbook의 비원형 덕트 수력직경 설명:
  <https://handbook.ashrae.org/Handbooks/F25/IP/F25_Ch21/F25_Ch21_ip.aspx>
- Leutheusser 1963 직사각형 덕트 형상 효과:
  <https://ascelibrary.org/doi/10.1061/JYCEAJ.0000866>

## 3. 거칠기 경고 정책

각 station에서 `f_rough/f_smooth`를 동일 Re로 계산한다. 비가 4 이상이면
`ROUGHNESS_HEAT_TRANSFER_OVERPREDICTION_WARNING`을 부여한다. 이는 거친 관에서
마찰계수를 Gnielinski 식에 직접 넣을 때 열전달 향상을 과대평가할 수 있다는 대략적인
주의 기준이다. 마찰계수나 `Nu`, `h_l`을 4배로 제한하거나 포화시키지 않는다.

정확한 교재 페이지는 독립 확인 완료로 주장하지 않는다. 공개된 보조 문헌에서 같은
Re의 거친/매끈 Darcy 계수 비교와 약 4배 주의가 확인되어 경고 근거로만 사용한다.

- TU Graz 공개 conference paper:
  <https://diglib.tugraz.at/download.php?id=6316ecaf27533&location=browse>
- INL 공개 학위논문 사본:
  <https://inldigitallibrary.inl.gov/content/uploads/50/2026/04/5806463.pdf>

## 4. 구현 인터페이스와 출력

- 정책: `config/SPACE_Y_cooling_correlation_policy.json`
- 정책 Schema: `schemas/SPACE_Y_cooling_correlation_policy.schema.json`
- 80개 참고 템플릿: `config/SPACE_Y_cooling_channel_template_grid.json`
- 템플릿 Schema: `schemas/SPACE_Y_cooling_channel_template_grid.schema.json`
- 국소/경로 판정: `src/space_y/cooling_applicability.py`
- 참고 폭 템플릿 및 인접 정수 채널수 계산:
  `src/space_y/cooling_channel_templates.py`

참고 템플릿은 `w_ref=[0.9,1.2,1.5,2.0,2.7] mm`,
`b=[0.9,1.0,1.2,1.5] mm`, `h=[1.2,1.5,2.0,3.0] mm`의 80개
조합이다. 실제 목 평균 채널 배치반경이 없으므로 채널수는 생성하지 않는다. 반경이
공급되면 `N_ideal=2*pi*r/(w_ref+b)`의 서로 다른 floor/ceil과 재계산 목 폭만 반환하며
어느 쪽도 자동 선택하지 않는다.

위치별 CSV는 Re, Pr, 거친/매끈 Darcy 계수, 비, 경고, `Nu`, `h_l`, 마찰 구배,
종횡비와 국소 적용성 상태를 기록한다. 후보 요약 CSV는 최소 Re 위치·여유,
천이경고 station, 최대 거칠기 비 위치와 경로 적용성 상태를 기록한다.

## 5. 남은 검증과 필수 입력

- 실제 재생냉각 통과 IPA 유량과 분기 위치
- 실제 목 및 전 구간 평균 채널 배치반경과 station 좌표
- 검증된 구간별 IPA `rho, mu, cp, k`, 엔탈피와 단상/상경계
- 제작 공정 기반 등가 모래거칠기와 국부손실
- 실제 가열 경계, 입구길이 및 완전발달 근거
- 직사각 종횡비와 가열벽 조건에 대한 별도 상관식 적합성 증거
- 실제 압력예산, 벽온도, 비등·상변화, 구조·제작 제약

이 입력과 증거가 확보되기 전에는 실제 SPACE Y `h_l`, 최적 형상 또는 안전한
냉각설계로 표현하지 않는다. V01–V12는 `NOT_RUN`, 실제 예측 검증은 `false`다.
