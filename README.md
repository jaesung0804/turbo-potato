# 수도권 아파트 매수 후보 대시보드

서울/경기/인천 아파트 실거래 데이터를 기반으로 동별 Hot/Cold 지도, 건물/평형별 상세 지표, AI 추천 후보를 확인하는 정적 웹 대시보드입니다.

배포 페이지:

```text
https://jaesung0804.github.io/turbo-potato/
```

## 주요 기능

- 서울/경기/인천 행정동 지도 기반 가격 Hot/Cold 시각화
- 연도, 지표, 자치구, 평형, 가격, 세대수, 최소 거래수 필터
- 신축/준신축/중간연식/구축/노후 구축 필터
- 호선, 역세권 도보 시간, 초품아 필터
- 건물/평형별 거래가, 평단가, 거래횟수, 전용평수, 연식, 세대수, 초품아/역세권 표시
- AI 추천 후보, 전체 매물, Hot, Cold 목록을 탭으로 전환
- Walk-forward 방식 검증 리포트와 ablation/지역별 성능 점검 데이터 생성

## 빠른 실행

이미 `web/data` 데이터가 생성되어 있다면 아래 명령으로 로컬 서버만 실행합니다.

```powershell
cd C:\code
python run_real_estate_dashboard.py --skip-build --skip-ai
```

브라우저에서 아래 주소를 엽니다.

```text
http://localhost:8000/
```

파일을 직접 여는 `file://` 방식은 브라우저 보안 정책 때문에 데이터 로딩이 실패할 수 있습니다.

## 전체 데이터 재생성

대시보드 요약 데이터와 AI 추천 데이터를 다시 만들고 서버까지 실행합니다.

```powershell
cd C:\code
python run_real_estate_dashboard.py
```

AI 추천만 빠르게 다시 생성하려면:

```powershell
python train_house_match_model.py --external-mode light
```

상권 데이터와 깊은 7개 모델 앙상블까지 쓰는 overnight full 학습:

```powershell
python train_house_match_model.py --data-dir data --external-mode full --model-profile full --output web\data\house_match_recommendations_full.json
```

검증 리포트를 다시 생성하려면:

```powershell
python validate_house_match_model.py --summary web\data\seoul_real_estate_summary.json --output web\data\house_match_validation.json
```

지도 경계 파일을 경량화하려면:

```powershell
python make_light_map_geojson.py
```

## 파일 구조

```text
C:\code
├─ run_real_estate_dashboard.py          # 로컬 실행 진입점
├─ build_real_estate_dashboard_data.py   # 실거래 CSV를 대시보드 JSON으로 요약
├─ train_house_match_model.py            # AI 추천 모델 학습 및 추천 JSON 생성
├─ validate_house_match_model.py         # Walk-forward 검증, Top-N 비교, ablation 검증
├─ make_light_map_geojson.py             # 지도 GeoJSON 경량화
├─ get_seoul_real_estate_data.py         # 서울시 실거래 API 수집
├─ get_molit_apt_trade_data.py           # 국토부 아파트 실거래 API 수집
├─ get_apt_basis_detail_data.py          # K-APT 단지 상세 정보 수집
├─ convert_bnd_adm_dong_map.py           # 행정동 SHP를 GeoJSON으로 변환
├─ web
│  ├─ index.html                         # 정적 웹 화면
│  ├─ app.js                             # 지도, 필터, 목록, 추천 렌더링
│  ├─ styles.css                         # 반응형 UI 스타일
│  └─ data                               # GitHub Pages에서 읽는 JSON/GeoJSON
└─ .github/workflows/pages.yml           # GitHub Pages 자동 배포
```

## 데이터 흐름

1. `get_molit_apt_trade_data.py` 또는 `get_seoul_real_estate_data.py`로 실거래 원천 데이터를 수집합니다.
2. `get_apt_basis_detail_data.py`로 단지 정보, 세대수, 역세권, 교육 정보를 보강합니다.
3. `build_real_estate_dashboard_data.py`가 원천 CSV를 연도/시도/자치구/동/건물/평형 단위로 요약합니다.
4. `train_house_match_model.py`가 추천 점수와 후보 목록을 생성합니다.
5. `validate_house_match_model.py`가 미래 데이터 누수를 피하는 방식으로 추천 성능을 검증합니다.
6. `web` 폴더가 GitHub Pages에 정적 사이트로 배포됩니다.

## AI SCORE

AI SCORE는 매수 후보를 좁히기 위한 참고 지표입니다. 투자 판단을 대체하지 않으며, 실거주 조건, 대출, 세금, 정비사업 리스크는 별도로 확인해야 합니다.

현재 추천 모델의 합의 가중치는 다음과 같습니다.

```python
CONSENSUS_WEIGHTS = {
    "undervalue": 21,
    "next_year_growth": 16,
    "yoy_momentum": 6,
    "period_momentum": 4,
    "liquidity": 13,
    "households": 9,
    "income": 5,
    "workplace": 8,
    "commercial": 4,
    "infra": 14,
}
```

검증은 다음 관점으로 수행합니다.

- Walk-forward backtest
- 추천 상위 10%, 상위 20%, 전체 평균, 랜덤, 단순 역세권, 단순 저평가 모델 비교
- 평균 상승률, 중앙값 상승률, hit rate, 하락률, 거래량 점검
- 주요 요소 제거 ablation test
- 서울 핵심지, 서울 외곽, 수도권 신도시, 업무지구 접근권, 학군지 등 지역별 성능 점검

## 연식 기준

| 구분 | 기준 |
| --- | --- |
| 신축 | 준공 후 0~5년 |
| 준신축 | 준공 후 5~15년 |
| 중간연식 | 준공 후 15~20년 |
| 구축 | 준공 후 20~30년 |
| 노후 구축 | 준공 후 30년 이상 |

## GitHub Pages 배포

`main` 브랜치에 push하면 `.github/workflows/pages.yml`이 `web` 폴더를 GitHub Pages로 자동 배포합니다.

```powershell
git add README.md .github web *.py
git commit -m "Update real estate dashboard"
git push origin main
```

배포 상태는 GitHub 저장소의 `Actions` 탭에서 확인할 수 있습니다.

## 주의사항

- `data/` 폴더의 원천 데이터는 용량과 민감도 때문에 Git에 올리지 않습니다.
- GitHub Pages는 `web/data`에 있는 정적 JSON/GeoJSON만 읽습니다.
- 화면의 `거래횟수`는 전체 집계 기준이고, `표시후보`는 성능을 위해 화면에 싣는 후보 수입니다.
- 데이터 파일이 크기 때문에 첫 로딩은 네트워크 상태에 따라 시간이 걸릴 수 있습니다.
