# 모델 버전과 기능 지도

## 현재 가격

| 구분 | 코드 | 명세·검증 | 사용 범위 |
|---|---|---|---|
| 연간 기준가격 v3~v5 | [reference/v5](../estate/models/reference/v5/) | [설명](../reports/estate_model_explained.md) | v5 중심, v3·v4 비교 호환 포함 |
| 월별 가격 v1 | [nowcast/v1](../estate/models/nowcast/v1/) | [명세](../metadata/nowcast_2026.json) | 기존 정책·비교 기준 |
| 지역별 중앙값 v2 | [regional_v2](../estate/models/nowcast/regional_v2/) | [명세](../metadata/nowcast_2026_capital_v2.json) | 현재 중앙값의 기반·과거 계약월 평가 |
| 분위수 v1 | [quantile_v1](../estate/models/nowcast/quantile_v1/) | [채택 명세](../metadata/nowcast_quantiles_2026.json), [비교 결과](../reports/estate_quantile_results_20261008.md) | 현재 P10·P50·P90 |
| 기존 신뢰구간 | [confidence](../estate/models/nowcast/confidence/) | [명세](../metadata/price_confidence_2026.json) | 과거 화면·검증 호환. 직접 학습 분위수와 구분 |

현재 선택 가격 모델은 `estate-quantile-capital-v1`입니다. 중앙값은 지역별 기존 모델을 유지하고 양쪽 꼬리를 별도로 학습합니다. `nowcast/release.py`가 현재 기준가를 연결하고 `nowcast/valuation.py`가 거래가격과 점수를 비교합니다.

## 미래 상승잠재력

| 구분 | 코드 | 결과·용도 |
|---|---|---|
| 초기 v1 | [potential/v1](../estate/models/potential/v1/) | [초기 검증](../reports/estate_potential_validation.json), 2026-09 이전 비교용 |
| 24개월 v2 | [potential/v2](../estate/models/potential/v2/) | [설계](../reports/estate_potential_design_v2.md), 기존 고정 예측·월별 갱신 |
| 분위수 v3 | [potential/quantile_v3](../estate/models/potential/quantile_v3/) | [10월 8일 실험](../reports/potential/quantile_v3/results_20261008.md), 검증 기준 미달로 미채택 |
| 장기·경로 연구 | [potential/research](../estate/models/potential/research/) | 5년 검증과 재평가 도달 시점. 현재 가격 점수에 가산하지 않음 |

현재 가격은 **지금의 거래 수준**, 잠재력은 **판단 후 18~24개월의 같은 생활권 대비 상대 변화**를 추정합니다. 상승잠재력 분위수는 가격 분위수와 단위·기간이 다릅니다. 가격이 낮다는 이유만으로 증여라고 판정하거나 원본 거래를 제거하지 않습니다.

## 파일 변경 시 함께 확인할 곳

- 가격 모델 교체: `estate/models/nowcast/` → 명세 `metadata/` → `estate/site/refresh_model.py` → `tests/check_bundle.cjs`.
- 상승잠재력 교체: `estate/models/potential/` → 고정 예측·검증 → `web/potential.js` → 월별 갱신 경로.
- 임장 양식: `web/estate-export.js`, `web/xlsx-export.js` → `excel_helper/fill_workbook.py` → 엑셀 검사.
- 수집 변경: `estate/data/collection/`, `preparation/` → 원본·출처·완전성 검사 → 저장 정책.
- 배포 변경: `estate/site/` → `.github/workflows/` → 실제 공개 파일 해시 검증.

옛 보고서의 날짜는 그 실험 당시의 범위입니다. 현재 선택 모델·수집 마감일을 뜻하지 않습니다.
