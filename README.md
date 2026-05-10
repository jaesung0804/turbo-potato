# 수도권 아파트 매물 선택 대시보드

서울, 경기, 인천 아파트 실거래 데이터를 기반으로 지역별 Hot/Cold 지도, 매물별 지표, AI 추천 후보를 확인하는 정적 웹 대시보드입니다.

## 주요 기능

- 수도권 행정동 지도 기반 Hot/Cold 시각화
- 연도, 시도, 시군구, 읍면동, 평형, 가격, 세대수, 거래수 필터
- 건물별/평형별 거래가, 평단가, 거래횟수, 전년 대비 상승률, 연식, 세대수 표시
- 초품아 500m 여부, 역세권 호선/거리 필터
- 상승률 요약 및 시군구/읍면동 상승률 랭킹
- AI 추천 후보 및 AI SCORE 대표지표
- GitHub Pages 배포 가능한 정적 웹 구조

## 실행 방법

전체 데이터와 AI 추천을 새로 만들고 웹을 실행합니다.

```powershell
cd C:\code
python run_real_estate_dashboard.py
```

이미 데이터가 만들어져 있을 때 빠르게 웹만 확인합니다.

```powershell
python run_real_estate_dashboard.py --skip-build --skip-ai
```

웹 주소:

```text
http://127.0.0.1:8000
```

## 파일 구조

```text
C:\code
├─ run_real_estate_dashboard.py          # 공식 실행 진입점
├─ build_real_estate_dashboard_data.py   # 실거래 CSV를 대시보드 JSON으로 변환
├─ train_house_match_model.py            # AI 추천 모델 학습 및 추천 JSON 생성
├─ get_molit_apt_trade_data.py           # 국토부 실거래 API 수집
├─ get_apt_basis_detail_data.py          # K-APT 단지/역세권 상세 정보 수집
├─ convert_bnd_adm_dong_map.py           # 행정동 SHP를 GeoJSON으로 변환
├─ web
│  ├─ index.html                         # 화면 구조
│  ├─ app.js                             # 지도/필터/목록/추천 렌더링
│  ├─ styles.css                         # UI 스타일
│  └─ data                               # 웹에서 읽는 정적 JSON/GeoJSON
└─ .github/workflows/pages.yml           # GitHub Pages 배포 워크플로
```

## 데이터 흐름

1. `get_molit_apt_trade_data.py`
   - 국토부 아파트 매매 실거래 API에서 수도권 거래 데이터를 수집합니다.
   - 결과는 `data/capital_area_apt_trade_transactions.csv`에 저장합니다.

2. `get_apt_basis_detail_data.py`
   - K-APT 단지코드와 상세 정보를 수집합니다.
   - 지하철 호선, 역명, 역 거리 정보를 대시보드 데이터 생성에 사용합니다.

3. `convert_bnd_adm_dong_map.py`
   - 행정동 경계 SHP를 웹 지도용 GeoJSON으로 변환합니다.

4. `build_real_estate_dashboard_data.py`
   - 실거래 CSV를 연도/시도/시군구/읍면동/건물/평형 단위로 요약합니다.
   - 직거래와 취소거래는 기본 제외합니다.
   - 세대수, 연식, 초품아, 역세권 정보를 매칭합니다.
   - 결과는 `web/data/seoul_real_estate_summary.json`입니다.

5. `train_house_match_model.py`
   - 요약 JSON과 외부 데이터를 이용해 AI 추천 점수를 계산합니다.
   - 결과는 `web/data/house_match_recommendations.json`입니다.

## AI SCORE 기준

AI SCORE는 매수 검토 우선순위입니다. 투자 확정 판단이 아니라 후보를 좁히기 위한 참고 지표입니다.

정식 모델 SCORE는 다음 요소를 사용합니다.

- 현재 적정 평단가 대비 저평가 정도
- 다음 연도 기대 평단가 상승률
- 전년 대비 상승률 및 전체 기간 상승률
- 거래 유동성
- 세대수
- 소득 데이터
- 직장인구 데이터
- 상권 데이터
- 초품아/역세권 입지 신호

정식 추천 JSON에 없는 매물은 화면 비교가 끊기지 않도록 보조 SCORE를 계산합니다.

보조 SCORE는 다음 값을 사용합니다.

- 거래수
- 세대수
- 전년 대비 상승률
- 전체 기간 상승률
- 초품아 여부
- 역세권 거리

전체 매물 목록은 건물 단위로 묶이지만, AI SCORE는 건물 내 평형 중 가장 높은 후보 점수를 사용합니다. 따라서 AI 추천 후보 탭의 특정 평형 SCORE와 전체 매물의 AI SCORE가 같은 기준으로 비교됩니다.

## AI 모델 학습

빠른 학습:

```powershell
python train_house_match_model.py --external-mode light
```

상권 데이터까지 포함한 전체 학습:

```powershell
python train_house_match_model.py --data-dir data --external-mode full
```

`full` 모드는 상권 CSV가 매우 커서 시간이 오래 걸릴 수 있습니다.

현재 모델은 sklearn 기반 앙상블입니다.

- Ridge
- ElasticNet
- ExtraTrees
- RandomForest
- GradientBoosting
- HistGradientBoosting
- AdaBoost

LightGBM, XGBoost, CatBoost는 현재 로컬 환경에 설치되어 있지 않아 제외했습니다.

## GitHub Pages 배포

GitHub 저장소 Settings > Pages에서 Source를 `GitHub Actions`로 설정합니다.

그 다음:

```powershell
cd C:\code
git add .gitignore .github README.md GITHUB_PAGES_DEPLOY.md *.py web
git commit -m "Prepare real estate dashboard for GitHub Pages"
git push origin main
```

배포 주소 예시:

```text
https://jaesung0804.github.io/turbo-potato/
```

## Git 관리 주의사항

`.gitignore`에서 다음 폴더는 제외합니다.

- `data/`
- `요건/`
- `__pycache__/`

원천 데이터는 용량이 크고 민감한 정보나 인증키가 섞일 수 있으므로 Git에 올리지 않습니다.

## 현재 한계와 다음 최적화

- `web/data/seoul_real_estate_summary.json`이 커서 첫 로딩이 느릴 수 있습니다.
- 다음 단계에서는 시도/시군구/연도별 JSON 분할과 지도 파일 경량화가 필요합니다.
- 정식 AI SCORE 커버리지는 추천 JSON 생성 범위에 영향을 받습니다. 현재 기본 저장 개수는 넉넉하게 늘려 두었습니다.
- 브라우저에서 모든 계산을 수행하므로 데이터가 더 커지면 lazy loading 구조가 필요합니다.
