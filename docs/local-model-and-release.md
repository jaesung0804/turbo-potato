# 로컬 학습과 공개 배포

학습·평가는 로컬에서 명시적으로 실행한다. GitHub에는 코드·작은 보고서·명세만 저장하고, 선택한 공개 모델과 이미 계산한 사이트는 불변 Release 파일로 배포한다. 개인 목적지·엑셀·브라우저 캐시는 공개하지 않는다. 백엔드 원본 상태는 이 절차에서 덮어쓰지 않는다.

## 준비

Python 3.12와 Node.js 22를 사용한다. Windows에서는 검증한 LightGBM 4.6.0을 설치하며 운영체제별 버전은 `requirements.txt`에 고정했다.

```powershell
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pyarrow pytest
```

원본 CSV와 `.manifest.json`은 같은 폴더에 둔다. 완전성·범위·체크섬이 검증된 원본만 사용한다. 국토부 인증키 없이 공개 내려받기로 갱신할 때는 `estate/data/preparation/refresh_public_csv.py`를 사용하며 기존 전체 체크포인트를 먼저 복원한다. 빈 폴더에서 일부 월만 받은 것을 전체 자료로 표시하지 않는다.

## 재현

```powershell
.venv/Scripts/python -X utf8 -m estate.models.nowcast.quantile_v1.prepare --source PATH/transactions.csv --output .work/new-features.parquet
.venv/Scripts/python -X utf8 -m estate.models.nowcast.quantile_v1.train --features .work/new-features.parquet --output .work/new-candidate --retained-median --trees 600 --leaves 31 --calibration-period 2026-spring
```

기존 파일을 덮어쓰지 않는 새 경로를 사용한다. 후보 보고서의 `production_approved`는 자동으로 참이 되지 않는다. 전체·지역·월·거래 희소·층별 결과와 평가 자료 재사용을 검토한 뒤 채택 명세와 모델 해시를 함께 고정한다. **학습 실행은 자동 배포가 아니다.**

현재 채택 명세는 `metadata/nowcast_quantiles_2026.json`이다. 빌드 시 `estate/models/nowcast/artifact.py`가 명세의 공개 Release에서 모델을 받아 크기와 SHA-256을 확인한 뒤 `models/nowcast/`에 캐시한다. 이전 중앙값 모델은 계약 당시 평가 원장과 비교 기준으로 보존한다. 새 분위수의 보정 기간이 섞인 과거 거래에 새 모델을 소급 적용하지 않는다.

## 현재 출력 교체와 배포

```powershell
.venv/Scripts/python -X utf8 -m estate.site.refresh_model --source PATH/transactions.csv --output .work/new-site --sha COMMIT_SHA
node tests/check_bundle.cjs .work/new-site
python -m estate.site.local_release pack --site .work/new-site --output .work/site.zip --sha COMMIT_SHA
```

공개 카탈로그·과거 집계·계약별 원장을 체크섬으로 검증해 재사용한다. 현재 가격을 계산한 로컬 원본의 해시는 별도로 기록한다. 선택한 `model.joblib`과 `site.zip`을 해당 불변 GitHub Release에 올리고, **로컬 검증 결과 배포** 워크플로에 태그와 실제 ZIP 해시를 입력한다. CI는 계산 결과를 검증·공개하며 재학습하지 않는다. UI의 배포 커밋은 실제 체크아웃 커밋으로 기록하고 로컬 계산 커밋은 별도 보존한다.

정기 수집·출력 갱신은 기존 Pages 워크플로를 유지하며 같은 채택 명세를 사용한다. 원자료/API 문제는 수집 상태에 표시하고 이전 완전한 자료를 보존한다. 페이지를 열거나 코드를 푸시했다고 전체 수집·학습이 실행되지 않는다.

## 확인

```powershell
.venv/Scripts/python -m pytest -q tests
node tests/test_ui.cjs
node tests/test_excel_export.cjs
python -m estate.site.verify --url https://jaesung0804.github.io/turbo-potato/ --sha COMMIT_SHA --manifest .work/site/data/dashboard_manifest.json
```

공개 사이트의 코드 커밋, 모든 UI·데이터 파일 해시, 완전한 결과 수, 가격과 점수의 일관성을 확인한다. 로컬 편집이 남은 다른 작업 브랜치는 초기화하지 않고 보존한다.
