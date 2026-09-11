# 시뮬레이션 실행·인수인계

## 현재 검증된 실행

저장소 루트에서 실행한다. 합성자료는 모두 코드에 정의된 가상 가격이며 실제 단지·모델 성과가 아니다.

```bash
python -m simulation demo
python -m pytest -q tests/test_estate_simulation.py tests/test_estate_simulation_storage.py
```

`demo`는 합성자료의 보유·갈아타기·최저가보유·현금 시나리오만 요약 출력한다. 네트워크·DB 쓰기·수집·학습·홈페이지 배포를 하지 않는다. 실제 실행 요청은 명시적인 `run` 하위명령이다. 새 자동실행 스케줄이나 홈페이지 트리거는 추가하지 않았다.

## 후속 실제자료 준비 계약

1. `PROTOCOL.md`의 컷오프·모집단·예산·기간·대조전략·비용·가설 버전을 사전 기록한다.
2. 기존 `backend_state.py pull` 경로로 해당 DB/OCI 상태를 복원한다. 과거 공표시점 원장이 없으면 `reconstructed_lag_scenario`를 선택하고 공식 신고 지연과 정정·취소 한계를 기록한다.
3. 단지×정확면적 ID와 공개 정보만으로 작은 패널을 만든다. 패널은 `simulation.engine.canonical(data)` 바이트를 gzip 압축하고 불변 스냅샷에 저장한다. 일반 JSON의 공백이나 마지막 줄바꿈이 다른 바이트를 해시와 혼동하지 않는다. 입력은 비압축 5MB·10,000행 이하이다.
4. 엔진·입력·정책/비용의 정확한 SHA를 고정한다. 새 실험의 준비 레코드는 명시적인 초기화 작업으로 `checkpoints`에 생성한다. 최소 payload는 `status=prepared`, `identity`, `source_snapshot_id`이다. 기존 실험이 있으면 먼저 읽고 재개한다. 체크포인트 누락을 빈 상태 재시작으로 숨기지 않는다.
5. 무료 사용 설정, DB·파일 저장소 용량, 필요한 증분과 보존 여유를 실제 환경에서 검증한다. 현재는 이를 제공하는 `live_capacity_adapter`가 없어서 **실제자료 CLI는 실패하도록 닫혀 있다.** 정확한 계측이 구현·검증된 후 `BackendSession`의 `run_preflight` 호출에 연결한다. CLI 플래그로 체크를 건너뛰지 않는다.
6. 인증환경에서 아래 명령을 명시적으로 실행한다. 인증값은 명령 인수나 로그에 쓰지 않고 기존 비공개 실행환경에서 읽는다.

```bash
python -m simulation run \
  --state-dir /path/to/restored-state \
  --snapshot-name verified-simulation-input \
  --input-path experiments/registered-run/panel.json.gz \
  --policy /path/to/registered-policy.json \
  --checkpoint-key simulation-registered-run \
  --expected-checkpoint-version 1 \
  --required-growth-bytes 5000000 \
  --stop-after-days 90
```

위 경로·버전은 예시이며 실제 존재하는 아티팩트나 완료된 작업을 뜻하지 않는다. 현재 환경에서 실제자료 명령은 실행하지 않았다. 재개할 때 동일 입력·정책·엔진과 마지막 확인한 DB 버전을 사용한다. 409나 head 변경은 실행 중단과 검토 사유이다.

## 패널의 핵심 필드

- 최상위: `schema_version=1`, `kind=historical_research`, `observations`, `quotes`, `provenance`.
- provenance: `availability_mode=strict_observed` 또는 `reconstructed_lag_scenario`, `personal_preference_features=[]`. 공식 원문, 모집단 규칙, 스냅샷, 실험 버전·가설은 연계 연구 레코드에 보존한다.
- observation: `observation_id`, `asset_id`, `price_krw`, `score`, `model_version`, `observed_at`, `available_at`, `max_feature_available_at`, `max_training_label_available_at`, `source_id`, `source_sha256`, `evidence_kind`.
- quote: `quote_id`, `asset_id`, `side=buy|sell`, `price_krw`, `observed_at`, `available_at`, `valid_until`, `source_id`, `source_sha256`, `evidence_kind=archived_offer|transaction_proxy`.
- 시각은 시간대가 있는 ISO 8601이며, 각 결정 날짜의 한국시간 마감 이하만 읽는다. `observed_at`은 계약일을 대신 붙이는 칸이 아니다. 뒤늦게 수집한 재구성 자료라면 그 근거와 공표 가정은 따로 표시한다.
- 모든 KRW 값은 원 단위 정수다. 세율·비용 비율은 명시된 민감도 시나리오다. `Costs`의 단순 이득 과세식은 법정 세액을 재현하지 않는다.
- 정책 JSON은 `policy`와 `costs` 두 객체를 갖는다. 필드는 `simulation.engine.Policy`, `Costs`와 `simulation.fixtures.fixture()`에서 확인할 수 있다. 실제 정책은 합성 예시의 비용 숫자를 그대로 쓰지 않는다.

## 2026-09-11 인수인계

완료: 독립팀 정우(SIMULATION)·민재(검수)·서진(모델 연결), 시간순 전략 규약, 예산/비용/잔금 엔진, DB 게이트·압축 체크포인트 CAS 어댑터, 작은 합성자료 테스트. 실제 연구 성과·모델 개선·단지 추천은 아직 산출하지 않았다.

현재 차단: backend의 무료 사용·실제 용량 계측 없음. 이 회차 실행환경의 DB 접속 설정 없음. 2023·2024년 실제 관측 원장 범위, 비용 규정 시나리오, 후보 패널과 등록된 실험 체크포인트도 미확인이다.

다음 행동: 저장 담당이 계측·복원·파일 왕복을 확인 → 정우가 최초 한 기간·한 예산·작은 후보 패널 등록 → 민재가 당시 가용성/취소·정정/실행 가정을 검수 → 동일 패널로 보유·갈아타기·대조전략 실행 → 서진이 피처별 개선과 기각을 기록. 그 다음에 연도·예산·기간을 확장한다.

남은 기능: 실제 세제/대출·임시거주·점유·인도일 모듈, 사후 최적 상한의 독립 집계, 무작위/지역·가격대 대조군, 피처 학습 파이프라인 연결, 실제자료 성능검증. 현재 가격 모형과 대표 홈페이지 배포는 이 엔진을 import하는 것만으로 변하지 않는다.
