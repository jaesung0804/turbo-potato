# 부동산 조사 기록 이관과 실험 저장 준비

이 문서는 실행 절차이며 실제 DB 적재·무료 설정 확인·대용량 실험 성공 확인서가 아니다.
현재 회차에는 비공개 backend URL/token이 없고, 기존 API에는 제공자 전체
사용량·무료 자원 설정을 실시간 증명하는 기능도 없다. 따라서 **작은 조사 기록
반영 도구와 오프라인 검증까지만 준비하고 실제 과거 실자료 실험은 차단한다.**

## 3차 조사 기록 25건

기존 비공개 `apartment_round3_db_handoff_2026-09-11.json`의 구조는
`fieldwork_round3_handoff_v1`, `target_project=estate`, `records[]`이며,
각 기록은 `kind/key/summary/expected_version:null/data`를 가진다.
연구 16건·과제 7건·출처 1건·마감 체크포인트 1건, 총 25건이다.
원문과 개인 상황은 Git에 복사하지 않는다. 기록의 `model_eligible=false`,
실제 관측일·출처·현재 가용성 미확인 상태는 그대로 보존한다. 과거 마감 기록의
`backend_rows_written:0`도 당시 사실이므로 이관 성공을 이유로 바꾸지 않는다.

```bash
# 기본: 입력/크기/출처 구조만 검사한다. 네트워크 요청과 쓰기는 없다.
python sync_estate_research_records.py --input /private/round3-handoff.json

# 비공개 실행 환경에 RESEARCH_STORAGE=backend, estate용 URL/token 설정 후:
python sync_estate_research_records.py --input /private/round3-handoff.json --check-backend

# 실제 작은 기록 반영을 명시적으로 실행할 때만:
python sync_estate_research_records.py --input /private/round3-handoff.json --apply
```

스크립트는 관련 kind를 `list?limit=20`으로 확인하고 선택한 키만 get한다.
서버에 같은 payload가 있으면 그 버전을 재사용한다. 없는 키임을 실제 GET의
404로 확인한 경우에만 `expected_version=0`으로 PUT한다. 기존 내용이 다르면
덮어쓰거나 임의 병합하지 않고 중단한다. HTTP 409에는 다시 GET하여 동일한
내용일 때만 재사용하고, 다른 내용이면 다음 회차에 충돌을 검토하도록 남긴다.
성공 PUT 뒤에도 GET으로 확인한 버전·payload가 일치해야 완료로 계산한다.

기본 인수인계는 Git에서 제외된 `.work/estate-round3-sync-report.json`에
원자적으로 저장한다. 원본 payload를 포함하지 않고 원본의 정규화 SHA-256,
각 키·payload 지문·확인 버전·쓰기 시도 수·실패 코드·다음 키를 남긴다.
PUT 응답을 잃으면 `write_pending`은 성공도 실패도 확정되지 않은 상태다.
재개할 때 같은 입력을 다시 실행하면 서버를 다시 읽어 이미 반영된 키를
재사용한다. 보고서의 진행 표시만 믿고 키를 건너뛰지 않는다.

## 실자료 실험을 시작하기 위한 조건

`estate_research_storage.run_preflight()`는 아래 조건을 모두 확인해야
`ready=true`를 반환한다. 실패 항목은 `blockers[]`로 분리한다.

| 조건 | 현재 확인 방법 |
| --- | --- |
| 올바른 프로젝트·backend 모드 | private runtime 설정과 `Client(project='estate')` |
| Oracle·OCI 연결 | 기존 `/ready` 응답을 실제 읽기 |
| 완결된 현재 source snapshot | 기존 head와 committed snapshot entries |
| 작업 시작 시 복원한 원본 | `Client.pull`이 만든 원래 receipt와 현재 head 일치 |
| 압축 보관·복원 무결성 | snapshot SHA·로컬 SHA·서버 파일 metadata·실제 다운로드 SHA·gzip CRC와 복원 SHA |
| 해당 실험 입력 | 복원 SHA와 실험의 `input_sha256` 일치 |
| 체크포인트 존재·원본 일치 | 기존 record의 version·source snapshot·input/policy/engine identity |
| 검사 도중 동시 변경 없음 | head와 체크포인트를 끝에서 다시 읽기 |
| 무료·용량·보존·CAS 쓰기 준비 | **기존 API에서 확인 불가 → 차단** |

확인은 이미 만들어진 작은 압축 패널에 한한다. 최대 64개 파일·압축 합계
64 MiB·해제 합계 256 MiB이며, 전체 실거래 corpus를 내려받거나 수집하지 않는다.
`backend_state.py pull`로 상태를 복원하는 작업도 별도 명시된 실행이어야 한다.
빈 상태·없는 snapshot을 새로 만들거나 성공 receipt를 꾸며 통과시키지 않는다.
checkpoint payload는 최소한 다음의 원본 연결을 보존해야 한다.

```json
{
  "source_snapshot_id": "<actual restored immutable snapshot id>",
  "identity": {
    "run_id": "<the run id>",
    "input_sha256": "<decompressed panel bytes SHA-256>",
    "policy_sha256": "<simulation policy SHA-256>",
    "engine_sha256": "<engine version SHA-256>"
  }
}
```

## 다음에 필요한 서버 운영 작업

`LiveCapacityAdapter`는 향후 실제 운영 계측을 연결하기 위한 인터페이스일 뿐,
이번 구현에는 작동하는 provider adapter가 없다. CLI로 `ready=true` 파일이나
자가 작성 확인서를 입력할 수 없다. 일반 research record의 `verified=true`도
통과 증거로 쓰지 않는다. 모의 adapter는 테스트 파일 안에서만 사용한다.

다음 운영 회차에서 기존 계정을 직접 조회하여 다음 사항을 검증하고 연결한다.

1. Oracle 실제 사용량·한도와 OCI 파일/버전/임시 업로드/백업 사용량·한도.
   투자·부동산이 같은 자원을 사용하면 두 프로젝트를 합산한다.
2. 무료 자원 구성과 유료 확장·자동 과금 비활성 여부. 용량 한도 도달 시
   과거 자료가 자동 삭제된다고 가정하지 말고 실제 보존 정책을 확인한다.
3. 현 source snapshot과 재개 checkpoint를 보존하는 정책.
4. 사전 합의된 작은 임시 checkpoint의 실제 쓰기/재읽기/구버전 충돌 시험.
5. 15분 이내 측정값과 최악 증가량을 비교해 각 한도의 80% 이하인지 확인.
   증가량에는 새 revision, 미완료 업로드, 실험 checkpoint도 포함한다.

이 조건이 확인된 뒤에도 원본·중간 피처·큰 결과는 압축한 인증 파일 저장소에,
SHA와 진행 상태는 작은 DB record에 둔다. Git에는 코드·작은 검증 요약·배포에
필요한 최종 데이터만 반영한다. 기존 snapshot CAS와 `backend_rows.py` 절차를
우회하지 않는다. 조사·부분 전세 자료는 canonical `apt-trades`에 넣지 않는다.

## 이번 회차 검증 범위와 인수인계

2026-09-11: 기존 원본의 텍스트 읽기본을 Git 밖에서 재구성하여 25건의 구조와
크기를 dry-run 검증했다. 네트워크 조회·쓰기 모두 0회이며 원본 JSON의
정규화 SHA-256은
`a594ad9e2ec141c79e613c9ab73f52c6437b90debe14926e126df7dd0f16fa79`다.
이는 다운로드한 원본 파일 바이트의 SHA가 아닌 JSON 의미 내용의 지문이다.

오프라인 테스트는 압축손상·원본 변경·checkpoint 충돌·무료/용량 증거 누락을
차단하는지, 같은 기록 재실행·409 재조회·PUT 응답 소실 후 재개가 원문을
덮지 않는지만 작은 합성 데이터로 확인한다. 실제 무료 계정 안정성, DB 이관
완료, 과거 수익률 또는 잠재력 모델 개선 성과를 증명하는 결과가 아니다.

다음 담당자는 먼저 비공개 연결 환경을 확인하여 25건을 read-only 점검하고,
충돌 없는 작은 기록만 명시적 적용한다. 대용량 수집/학습은 위 운영 계측이
완성되기 전 계속 차단한다. 회차 보고에는 DB 확인·미확인 수, 잠재력 모델과
매물 학습에 기여한 점, 다음에 해결할 장애만 짧게 쓴다.
