# 수도권 아파트 실거래·임장 비교

서울·경기·인천의 실거래를 비교하고, 원하는 조건의 단지를 사진 양식의 임장 엑셀로 내려받습니다. 현재 가격 비교와 미래 상승잠재력은 서로 다른 모델입니다.

[대시보드](https://jaesung0804.github.io/turbo-potato/) · [상승잠재력](https://jaesung0804.github.io/turbo-potato/potential.html) · [가격 모델 검증](https://jaesung0804.github.io/turbo-potato/quantile-validation.html)

## 임장 엑셀 사용

1. 지역·가격·점수 등을 선택합니다. 지역·연식·평형·호선은 여러 개 선택할 수 있습니다.
2. **임장 비교표 Excel**로 전체 필터 결과를 받습니다. 파일명 끝은 한국시간 `yymmdd_hh_mm`이며 같은 분의 재다운로드에는 순번도 붙습니다.
3. **자동 채우기 받기**를 풀고 `fill.cmd`를 실행해 엑셀을 선택합니다. Python 3.12 이상과 Windows Edge가 필요하며 API 키는 필요 없습니다.

비교표 A~V는 제공된 사진의 열 순서입니다. **단지당 한 줄**, 20·30·40평대 호가, 주차대수·세대당 주차, 직주·강남역, 학교·시설·조합·빈 임장 칸을 유지합니다. 오른쪽에 용적률·대지지분·본가 이동·조건 충족 전용면적·모델 가격과 점수만 붙입니다. 홈페이지 링크와 출처는 `기준 및 출처`에 모읍니다.

호가는 **공급평형대별 실제 매물 최저가**입니다. 모델 가격은 조건을 충족한 평형 중 가격 비교점수가 가장 높은 **명시된 전용면적** 기준입니다. 모든 개별 전용면적의 결과는 별도 결과 CSV에 보존됩니다. 임장·메모는 직접 작성하며 자동 채우기가 덮어쓰지 않습니다.

직장·본가·강남역 이동은 평일 오전 8시 출발 대중교통입니다. 개인 목적지는 브라우저와 개인 파일에만 저장합니다. 출처에 없는 시설·대지지분을 추정값으로 채우지 않습니다. [자동 채우기 상세 사용법](excel_helper/사용법.md)

## 코드는 어디에 있나요?

```text
estate/
  data/collection/       국토부 CSV·API, 시설·세대수 수집
  data/preparation/      원본 정규화·과거자료 연결
  data/storage/          체크포인트·백엔드 복원·게시
  data/geography/        지도 경계 변환
  models/reference/v5/  과거 연간 가격 비교 모델
  models/nowcast/        현재 가격 평가·배포 공통 코드
    v1/                 기존 월별 가격 모델
    regional_v2/        지역별 전체 과거자료 중앙값 모델
    quantile_v1/        현재 P10·P50·P90 학습·평가
    confidence/         이전 오차 기반 신뢰구간
  models/potential/
    v1/                 초기 상승잠재력 실험·고정 예측
    v2/                 기존 24개월 상대 상승 모델
    quantile_v3/        로컬 분위수 상승잠재력 실험
    research/           1·3·5년, 재평가 시점 연구
  research/price/        가격 모델 비교 실험
  research/market/       시장지수·전세·거래 흐름 연구
  research/reporting/    검증 보고서·공시 지연 기록
  site/                 웹 자료 묶기·미리보기·배포·검증
  tools/                저장소 변경 검사
web/                    공개 화면·필터·엑셀 생성
excel_helper/           다운로드용 자동 채움·Excel VBA
tests/                  모델·자료·화면·엑셀 검사
metadata/               고정 모델·채택 명세·공개 기준 자료
reports/                날짜별 실험 결과
docs/                   사용법·운영·모델 지도
backend/                별도 백엔드 서비스
```

루트에 개별 실행용 Python 파일을 복제하지 않습니다. 이전에 배포한 모델 파일의 클래스 경로는 `estate/_legacy.py`에서 읽기 호환만 제공합니다. [모델 버전과 기능별 안내](docs/model-map.md)

상승잠재력은 2026년 10월 8일 로컬 분위수 실험 후 **v2를 유지하고 10월 후보를 재학습·갱신**했습니다. 보수적인 공시 시차에서 새 범위의 포함률이 부족했습니다. [선정 과정과 원수치](reports/potential/quantile_v3/results_20261008.md)

## 자주 쓰는 명령

저장소 루트에서 실행합니다. 각 명령에 `--help`를 붙이면 입력·출력 옵션이 나옵니다.

```powershell
python -m estate serve --site-dir .work/site --no-browser
python -m estate train-price --help
python -m estate train-potential --help
python -m estate collect-csv --help
python -m pytest -q tests
node tests/test_ui.cjs
node tests/test_excel_export.cjs
```

수집·학습·재빌드는 명시적으로 실행합니다. 저장된 페이지 조회나 코드 푸시만으로 전체 수집·학습을 시작하지 않습니다. 기존 일별 수집·월별 연구 일정은 유지합니다. [로컬 학습·배포](docs/local-model-and-release.md) · [운영 규칙](AGENTS.md) · [GitHub Pages 배포](GITHUB_PAGES_DEPLOY.md)

원자료·캐시·개인 엑셀·목적지·인증 정보는 Git에 올리지 않습니다. `.work/`, `data/`, `models/`는 로컬 작업 공간입니다. 공개 모델과 완성 사이트는 체크섬이 고정된 GitHub Release로 배포합니다.
