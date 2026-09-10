# 검증 기록

검증일: 2026-09-11 한국 시간. 비밀값과 실제 원본 데이터는 코드·배포 묶음에서 제외한다.

| 범위 | 실제 결과 |
|---|---|
| 공통 백엔드 | 97개 통과: record CAS, 크기 제한, snapshot, lease, monthly worker, 원자적 reference 저장, 원본 보존 retention, 작은 배치 재개 |
| 초기 이관 runner | 7개 통과: 결정적 gzip, 행 수·해시, 배치 중단과 재개, 일시 오류만 제한 재시도 |
| 투자 상태·연구 연동 | 39개 통과: pack/unpack/recovery, reference facade, 변경 월 enqueue. 외부 소켓 차단 환경 |
| 부동산 연동 | 선택 unit 68개 통과·Windows symlink 1개 skip, localhost HTTP 2개 통과. 정상 웹 조회가 builder를 import하지 않는 경계 포함 |
| Workflow | 부동산 실행 경로 40개, Bash 42개, Python 블록 5개, 완전성 gate 3개. 투자 저장 workflow 6개의 KR/US enqueue 12곳 검증 |
| 실제 Oracle | 두 APP 계정에 테이블 10개씩 생성. 한글 CLOB, 토큰 분리, 기록 충돌·재시도, 페이지 제한 및 조회가 작업을 만들지 않음 확인 |
| 실제 OCI·HTTPS | 두 프로젝트 원본 이관·별도 폴더 복원 후 전 파일 크기·SHA 확인. HTTPS 업로드·다운로드·중복 재사용·다른 프로젝트 차단 성공 |
| 실제 reference transaction | 조직/과제/회의 동일 내용 원자 저장·재시도와 잘못된 version 409 확인. 기존 내용·version 변화 없음 |
| 서버 | 설치된 Python SHA 일치, instance principal 접근, 공인 IP TLS, 인증서 갱신 모의 실행, 설정 키 해제 후 파일 쓰기 확인 |

부동산은 249개월·2,364,052행 전체 적재와 월별 행 수·단지/지역 조회 검증을 완료했다. 투자 원본은 155개 압축 파일·479,915,197바이트 모두 보관했고 reference JSON 10개·개별 기록 112개도 운영 DB에 저장했다. 투자 SQL 적재는 34개월·1,620,394행 게시 후 다음 월 staging에서 ORA-30036으로 중단됐다. 전체 목표 9,058,324행 완료로 해석하지 않는다.

관리자 읽기 전용 진단에서 투자 PDB는 약 9.71 GB/21.47 GB, UNDO는 약 2.30 GB가 사용 중이었다. APP 활성 트랜잭션·임시 LOB는 없고 UNDO 대부분이 UNEXPIRED였다. 50행 배치와 APPEND_VALUES 시험에서도 오류가 반복됐다. 제약·인덱스 제거, UNDO 설정 변경, 유료 확장은 하지 않았다. [Oracle 오류 설명](https://docs.oracle.com/en/error-help/db/ora-30036/)

원본·기존 Git 이력·현재 게시 버전은 보존한다. 부분 capital-history 31/81, capital-normalized 4/81 자료를 완전한 거래 데이터에 합치지 않았다. 저장소에서 접근할 수 없었던 ChatGPT 첨부 사진까지 이관했다고 간주하지 않는다.

Linux CI·운영 모드 전환의 최종 결과는 CLOUD-STATUS.md와 PR checks를 따른다. 기존 모델 학습을 SQL 방식으로 재작성하지 않았으며 학습 배치는 필요한 저장 상태를 복원한다. Starlette 관련 기존 deprecation 경고 2개는 기능 검사 실패가 아니다.
