# Train House Match recommendations:
#   cd C:\code
#   python train_house_match_model.py
#
# Full external-data training:
#   python train_house_match_model.py --data-dir data --external-mode full --model-profile full --output web/data/house_match_recommendations_full.json
#
# Fast training without very large commercial-store files:
#   python train_house_match_model.py --external-mode light
#
# This trains static ensemble recommendations and writes:
#   web\data\house_match_recommendations.json
# The dashboard can read that JSON directly on GitHub Pages.

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import (
    AdaBoostRegressor,
    ExtraTreesRegressor,
    GradientBoostingRegressor,
    HistGradientBoostingRegressor,
    RandomForestRegressor,
)
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, Ridge
from sklearn.metrics import mean_absolute_error, r2_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, OrdinalEncoder, StandardScaler


DEFAULT_SUMMARY = Path("web/data/seoul_real_estate_summary.json")
DEFAULT_OUTPUT = Path("web/data/house_match_recommendations.json")
DEFAULT_DATA_DIR = Path("data")
RANDOM_STATE = 42
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
SCORE_WEIGHTS = CONSENSUS_WEIGHTS

NUMERIC_FEATURES = [
    "year",
    "area_pyeong",
    "age",
    "households",
    "trade_count",
    "elementary_500m",
    "has_subway_info",
    "subway_distance_m",
    "has_bus_stop_info",
    "bus_stop_distance_m",
    "education_facility_count",
    "region_price_per_pyeong",
    "region_trade_count",
    "region_yoy_rate",
    "region_period_rate",
    "income_high_rate",
    "income_low_rate",
    "income_score",
    "workplace_total",
    "workplace_2030_ratio",
    "workplace_3040_ratio",
    "store_total",
    "store_food",
    "store_education",
    "store_medical",
    "store_real_estate",
    "store_density_score",
    "latitude",
    "longitude",
]

CATEGORICAL_FEATURES = [
    "sido_name",
    "gu_name",
    "dong_name",
    "area_band",
    "subway_line_main",
    "subway_station",
]

ACTIVE_NUMERIC_FEATURES = NUMERIC_FEATURES

# 이 파일의 의도:
# - 대시보드 JSON을 학습 데이터셋으로 변환합니다.
# - 소득, 직장인구, 상권 같은 외부 데이터를 있으면 자동으로 붙입니다.
# - 현재 적정 평단가 모델과 다음 연도 상승률 모델을 각각 학습합니다.
# - 여러 회귀 모델의 예측을 검증 오차 기준으로 가중 평균해 한쪽 모델에 과하게 끌리지 않게 합니다.
# - 최종 SCORE는 모델 예측값과 거래 유동성, 세대수, 입지 신호를 섞은 검토 우선순위입니다.

STORE_CATEGORY_MAP = {
    "음식": "store_food",
    "교육": "store_education",
    "보건의료": "store_medical",
    "부동산": "store_real_estate",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train static apartment recommendation results for the dashboard.")
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY), help="Dashboard summary JSON path.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT), help="Recommendation JSON output path.")
    parser.add_argument("--data-dir", default=str(DEFAULT_DATA_DIR), help="Directory containing optional external data files.")
    parser.add_argument("--external-mode", choices=["full", "light", "off"], default="full")
    parser.add_argument("--model-profile", choices=["fast", "full"], default="fast", help="fast keeps the lightweight ensemble; full runs the deeper overnight ensemble.")
    parser.add_argument("--top-n", type=int, default=50000, help="Maximum recommendations to write.")
    parser.add_argument("--min-trade-count", type=int, default=2, help="Minimum type-level trades for training/recommendation.")
    return parser.parse_args()


def metric_avg(building: dict[str, Any], metric: str) -> float | None:
    value = building.get("metrics", {}).get(metric, {}).get("avg")
    return float(value) if value is not None else None


def safe_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        parsed = float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def clean_code(value: Any, width: int | None = None) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if text.endswith(".0"):
        text = text[:-2]
    text = "".join(ch for ch in text if ch.isdigit())
    return text.zfill(width) if width and text else text


def area_band(area: float | None) -> str:
    if area is None:
        return "unknown"
    if area <= 14:
        return "lte14"
    if area <= 26:
        return "gt14_lte26"
    if area <= 34:
        return "gt26_lte34"
    return "gt34"


def change_rate(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100


def year_list(summary: dict[str, Any]) -> list[str]:
    return sorted((str(year) for year in summary.get("years", [])), key=int)


def address_map(region: dict[str, Any], year: str) -> dict[str, dict[str, Any]]:
    return {
        item.get("key", item.get("address")): item
        for item in region.get("years", {}).get(year, {}).get("addresses", [])
    }


def type_yoy_rate(region: dict[str, Any], year: str, building: dict[str, Any], years: list[str]) -> float | None:
    idx = years.index(year)
    if idx == 0:
        return None
    previous = years[idx - 1]
    previous_building = address_map(region, previous).get(building.get("key", building.get("address")))
    return change_rate(metric_avg(building, "price_billion"), metric_avg(previous_building or {}, "price_billion"))


def type_period_rate(region: dict[str, Any], building: dict[str, Any], years: list[str]) -> float | None:
    if len(years) < 2:
        return None
    start_building = address_map(region, years[0]).get(building.get("key", building.get("address")))
    end_value = metric_avg(building, "price_billion")
    start_value = metric_avg(start_building or {}, "price_billion")
    return change_rate(end_value, start_value)


def type_next_year_rate(region: dict[str, Any], year: str, building: dict[str, Any], years: list[str]) -> float | None:
    idx = years.index(year)
    if idx >= len(years) - 1:
        return None
    next_year = years[idx + 1]
    next_building = address_map(region, next_year).get(building.get("key", building.get("address")))
    return change_rate(metric_avg(next_building or {}, "price_per_pyeong"), metric_avg(building, "price_per_pyeong"))


def region_year_rate(region: dict[str, Any], year: str, years: list[str]) -> float | None:
    bucket = region.get("years", {}).get(year)
    if not bucket:
        return None
    values = [type_yoy_rate(region, year, building, years) for building in bucket.get("addresses", [])]
    values = [value for value in values if value is not None]
    return mean(values) if values else None


def region_period_rate(region: dict[str, Any], years: list[str]) -> float | None:
    end = years[-1] if years else None
    bucket = region.get("years", {}).get(end, {}) if end else {}
    values = [type_period_rate(region, building, years) for building in bucket.get("addresses", [])]
    values = [value for value in values if value is not None]
    return mean(values) if values else None


def read_csv_best(path: Path, **kwargs: Any) -> pd.DataFrame:
    for encoding in ("utf-8-sig", "cp949", "euc-kr"):
        try:
            return pd.read_csv(path, encoding=encoding, **kwargs)
        except UnicodeDecodeError:
            continue
    return pd.read_csv(path, **kwargs)


def find_first(data_dir: Path, patterns: list[str]) -> Path | None:
    for pattern in patterns:
        matches = sorted(data_dir.glob(pattern))
        if matches:
            return matches[0]
    return None


def load_income_features(data_dir: Path) -> dict[str, dict[str, float]]:
    path = find_first(data_dir, ["KCB_SIGNGU_DATA*.csv", "*KCB*SIGNGU*.csv"])
    if not path:
        return {}
    df = read_csv_best(path)
    result: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        code = clean_code(row.get("SIGNGU_CD"), 5)
        if not code:
            continue
        low = sum(safe_float(row.get(col)) or 0 for col in ["INCOME_TWO_TMW_INHBT_RATE", "INCOME_THREE_TMW_INHBT_RATE"])
        high = sum(safe_float(row.get(col)) or 0 for col in ["INCOME_SIX_TMW_INHBT_RATE", "INCOME_SEVEN_TMW_INHBT_RATE", "INCOME_SEVEN_TMW_ABOVE_INHBT_RATE"])
        middle = sum(safe_float(row.get(col)) or 0 for col in ["INCOME_FOUR_TMW_INHBT_RATE", "INCOME_FIVE_TMW_INHBT_RATE"])
        result[code] = {
            "income_low_rate": low,
            "income_high_rate": high,
            "income_score": high * 1.5 + middle - low,
        }
    return result


def load_workplace_features(data_dir: Path) -> dict[str, dict[str, float]]:
    path = find_first(data_dir, ["*직장인구-행정동*.csv", "*직장인구*.csv"])
    if not path:
        return {}
    df = read_csv_best(path)
    result: dict[str, dict[str, float]] = {}
    for _, row in df.iterrows():
        code = clean_code(row.get("행정동_코드"), 8)
        total = safe_float(row.get("총_직장_인구_수"))
        if not code or not total:
            continue
        age20 = safe_float(row.get("연령대_20_직장_인구_수")) or 0
        age30 = safe_float(row.get("연령대_30_직장_인구_수")) or 0
        age40 = safe_float(row.get("연령대_40_직장_인구_수")) or 0
        result[code] = {
            "workplace_total": total,
            "workplace_2030_ratio": (age20 + age30) / total,
            "workplace_3040_ratio": (age30 + age40) / total,
        }
    return result


def load_store_features(data_dir: Path, mode: str) -> tuple[dict[tuple[str, str], dict[str, float]], dict[str, dict[str, float]]]:
    if mode != "full":
        return {}, {}
    paths = sorted(data_dir.glob("소상공인시장진흥공단_상가*202603.csv"))
    if not paths:
        paths = sorted(data_dir.glob("*상가*상권*.csv"))
    by_dong: dict[tuple[str, str], dict[str, float]] = {}
    by_gu: dict[str, dict[str, float]] = {}

    columns = ["시군구코드", "시군구명", "법정동명", "상권업종대분류명"]
    for path in paths:
        for chunk in pd.read_csv(path, encoding="utf-8-sig", usecols=lambda col: col in columns, chunksize=250_000):
            for _, row in chunk.iterrows():
                gu_code = clean_code(row.get("시군구코드"), 5)
                dong_name = str(row.get("법정동명") or "").strip()
                category = str(row.get("상권업종대분류명") or "").strip()
                if not gu_code:
                    continue
                for target in (by_gu.setdefault(gu_code, {"store_total": 0}), by_dong.setdefault((gu_code, dong_name), {"store_total": 0})):
                    target["store_total"] += 1
                    mapped = STORE_CATEGORY_MAP.get(category)
                    if mapped:
                        target[mapped] = target.get(mapped, 0) + 1
    return by_dong, by_gu


def load_external_features(data_dir: Path, mode: str) -> dict[str, Any]:
    if mode == "off":
        return {"income": {}, "workplace": {}, "stores_by_dong": {}, "stores_by_gu": {}}
    stores_by_dong, stores_by_gu = load_store_features(data_dir, mode)
    return {
        "income": load_income_features(data_dir),
        "workplace": load_workplace_features(data_dir),
        "stores_by_dong": stores_by_dong,
        "stores_by_gu": stores_by_gu,
    }


def region_external_features(region: dict[str, Any], external: dict[str, Any], households: float | None) -> dict[str, float | None]:
    gu_code = clean_code(region.get("gu_code"), 5)
    dong_name = str(region.get("dong_name") or "").strip()
    map_codes = [clean_code(code, 8) for code in region.get("map_codes", [])]

    income = external.get("income", {}).get(gu_code, {})
    workplace = next((external.get("workplace", {}).get(code) for code in map_codes if external.get("workplace", {}).get(code)), {})
    stores = external.get("stores_by_dong", {}).get((gu_code, dong_name)) or external.get("stores_by_gu", {}).get(gu_code, {})
    store_total = safe_float(stores.get("store_total")) if stores else None
    household_base = households if households and households > 0 else None

    return {
        "income_high_rate": safe_float(income.get("income_high_rate")),
        "income_low_rate": safe_float(income.get("income_low_rate")),
        "income_score": safe_float(income.get("income_score")),
        "workplace_total": safe_float(workplace.get("workplace_total")),
        "workplace_2030_ratio": safe_float(workplace.get("workplace_2030_ratio")),
        "workplace_3040_ratio": safe_float(workplace.get("workplace_3040_ratio")),
        "store_total": store_total,
        "store_food": safe_float(stores.get("store_food")) if stores else None,
        "store_education": safe_float(stores.get("store_education")) if stores else None,
        "store_medical": safe_float(stores.get("store_medical")) if stores else None,
        "store_real_estate": safe_float(stores.get("store_real_estate")) if stores else None,
        "store_density_score": store_total / household_base if store_total is not None and household_base else None,
    }


def build_dataset(
    summary: dict[str, Any],
    min_trade_count: int,
    external: dict[str, Any],
) -> tuple[list[dict[str, Any]], np.ndarray, list[float | None], list[dict[str, Any]]]:
    years = year_list(summary)
    generated_year = int(str(summary.get("generated_at", "2026"))[:4] or "2026")
    rows: list[dict[str, Any]] = []
    price_targets: list[float] = []
    next_year_targets: list[float | None] = []
    payloads: list[dict[str, Any]] = []

    for region in summary.get("regions", []):
        period_rate = region_period_rate(region, years)
        for year in years:
            bucket = region.get("years", {}).get(year)
            if not bucket:
                continue
            region_price = bucket.get("metrics", {}).get("price_per_pyeong", {}).get("avg")
            region_count = bucket.get("count")
            region_yoy = region_year_rate(region, year, years)

            for building in bucket.get("addresses", []):
                if int(building.get("count") or 0) < min_trade_count:
                    continue
                price_per_pyeong = metric_avg(building, "price_per_pyeong")
                price_billion = metric_avg(building, "price_billion")
                area = metric_avg(building, "area_pyeong")
                if not price_per_pyeong or not price_billion or not area:
                    continue

                built_year = safe_float(building.get("built_year"))
                households = safe_float(building.get("households"))
                subway_lines = building.get("subway_lines") or []
                extra = region_external_features(region, external, households)
                feature = {
                    "year": int(year),
                    "area_pyeong": area,
                    "age": generated_year - built_year if built_year else None,
                    "households": households,
                    "trade_count": int(building.get("count") or 0),
                    "elementary_500m": 1 if building.get("elementary_500m") else 0,
                    "has_subway_info": 1 if building.get("subway_distance_m") is not None or subway_lines else 0,
                    "subway_distance_m": safe_float(building.get("subway_distance_m")),
                    "has_bus_stop_info": 1 if building.get("bus_stop_distance_m") is not None else 0,
                    "bus_stop_distance_m": safe_float(building.get("bus_stop_distance_m")),
                    "education_facility_count": safe_float(building.get("education_facility_count")),
                    "region_price_per_pyeong": safe_float(region_price),
                    "region_trade_count": safe_float(region_count),
                    "region_yoy_rate": region_yoy,
                    "region_period_rate": period_rate,
                    "latitude": safe_float(building.get("latitude")),
                    "longitude": safe_float(building.get("longitude")),
                    "sido_name": region.get("sido_name") or "unknown",
                    "gu_name": region.get("gu_name") or "unknown",
                    "dong_name": region.get("dong_name") or "unknown",
                    "area_band": area_band(area),
                    "subway_line_main": str(subway_lines[0]) if subway_lines else "none",
                    "subway_station": str(building.get("subway_station") or "none"),
                    **extra,
                }
                rows.append(feature)
                price_targets.append(math.log(price_per_pyeong))
                next_year_targets.append(type_next_year_rate(region, year, building, years))
                payloads.append(
                    {
                        "year": year,
                        "region_code": region.get("code", ""),
                        "sido_name": region.get("sido_name"),
                        "gu_code": region.get("gu_code"),
                        "gu_name": region.get("gu_name"),
                        "dong_name": region.get("dong_name"),
                        "building_key": building.get("key", building.get("address", "")),
                        "building_name": building.get("building_name"),
                        "area_type": building.get("area_type"),
                        "price_billion": round(price_billion, 3),
                        "price_per_pyeong": round(price_per_pyeong, 1),
                        "area_pyeong": round(area, 1),
                        "trade_count": int(building.get("count") or 0),
                        "households": building.get("households"),
                        "built_year": building.get("built_year"),
                        "elementary_500m": bool(building.get("elementary_500m")),
                        "subway_lines": subway_lines,
                        "subway_station": building.get("subway_station"),
                        "subway_distance_m": building.get("subway_distance_m"),
                        "bus_stop_distance_m": building.get("bus_stop_distance_m"),
                        "education_facilities": building.get("education_facilities"),
                        "education_facility_count": building.get("education_facility_count"),
                        "latitude": building.get("latitude"),
                        "longitude": building.get("longitude"),
                        "yoy_rate": type_yoy_rate(region, year, building, years),
                        "period_rate": type_period_rate(region, building, years),
                        "external_features": {key: round(value, 4) for key, value in extra.items() if value is not None},
                    }
                )
    return rows, np.array(price_targets), next_year_targets, payloads


def row_matrix(rows: list[dict[str, Any]]) -> list[list[Any]]:
    columns = ACTIVE_NUMERIC_FEATURES + CATEGORICAL_FEATURES
    return [[row.get(column) for column in columns] for row in rows]


def onehot_model(estimator: Any) -> Pipeline:
    numeric = Pipeline(steps=[("imputer", SimpleImputer(strategy="median")), ("scaler", StandardScaler())])
    categorical = Pipeline(
        steps=[("imputer", SimpleImputer(strategy="most_frequent")), ("onehot", OneHotEncoder(handle_unknown="ignore", min_frequency=5))]
    )
    preprocess = ColumnTransformer(
        transformers=[
            ("num", numeric, list(range(len(ACTIVE_NUMERIC_FEATURES)))),
            ("cat", categorical, list(range(len(ACTIVE_NUMERIC_FEATURES), len(ACTIVE_NUMERIC_FEATURES) + len(CATEGORICAL_FEATURES)))),
        ]
    )
    return Pipeline(steps=[("preprocess", preprocess), ("model", estimator)])


def ordinal_model(estimator: Any) -> Pipeline:
    numeric = Pipeline(steps=[("imputer", SimpleImputer(strategy="median"))])
    categorical = Pipeline(
        steps=[("imputer", SimpleImputer(strategy="most_frequent")), ("ordinal", OrdinalEncoder(handle_unknown="use_encoded_value", unknown_value=-1))]
    )
    preprocess = ColumnTransformer(
        transformers=[
            ("num", numeric, list(range(len(ACTIVE_NUMERIC_FEATURES)))),
            ("cat", categorical, list(range(len(ACTIVE_NUMERIC_FEATURES), len(ACTIVE_NUMERIC_FEATURES) + len(CATEGORICAL_FEATURES)))),
        ]
    )
    return Pipeline(steps=[("preprocess", preprocess), ("model", estimator)])


def make_models(row_count: int, profile: str) -> list[tuple[str, Pipeline]]:
    if profile == "full":
        estimators = max(180, min(420, row_count // 120))
        return [
            ("ridge", onehot_model(Ridge(alpha=1.0))),
            ("elastic_net", onehot_model(ElasticNet(alpha=0.0007, l1_ratio=0.12, random_state=RANDOM_STATE, max_iter=7000))),
            ("extra_trees", ordinal_model(ExtraTreesRegressor(n_estimators=estimators, min_samples_leaf=1, max_features=0.85, random_state=RANDOM_STATE, n_jobs=-1))),
            ("random_forest", ordinal_model(RandomForestRegressor(n_estimators=max(140, estimators // 2), min_samples_leaf=2, max_features=0.8, random_state=RANDOM_STATE, n_jobs=-1))),
            ("gradient_boosting", ordinal_model(GradientBoostingRegressor(n_estimators=260, learning_rate=0.045, max_depth=3, random_state=RANDOM_STATE))),
            ("hist_gradient", ordinal_model(HistGradientBoostingRegressor(learning_rate=0.045, max_iter=350, l2_regularization=0.035, random_state=RANDOM_STATE))),
            ("adaboost", ordinal_model(AdaBoostRegressor(n_estimators=160, learning_rate=0.035, random_state=RANDOM_STATE))),
        ]
    estimators = max(30, min(70, row_count // 700))
    return [
        ("ridge", onehot_model(Ridge(alpha=1.5))),
        ("extra_trees", ordinal_model(ExtraTreesRegressor(n_estimators=estimators, min_samples_leaf=2, random_state=RANDOM_STATE, n_jobs=-1))),
        ("hist_gradient", ordinal_model(HistGradientBoostingRegressor(learning_rate=0.08, max_iter=70, l2_regularization=0.08, random_state=RANDOM_STATE))),
    ]


def weighted_prediction(predictions: dict[str, np.ndarray], evaluations: list[dict[str, Any]], metric_name: str) -> np.ndarray:
    if not predictions:
        raise RuntimeError("No model predictions available.")
    weights = {}
    for row in evaluations:
        error = safe_float(row.get(metric_name))
        if error and error > 0:
            weights[row["model"]] = 1 / error
    if not weights:
        return np.mean(np.vstack(list(predictions.values())), axis=0)
    total = sum(weights.get(name, 0) for name in predictions)
    if not total:
        return np.mean(np.vstack(list(predictions.values())), axis=0)
    return sum(pred * (weights.get(name, 0) / total) for name, pred in predictions.items())


def normalize(values: list[float | None], default: float = 0.0) -> list[float]:
    clean = [value for value in values if value is not None and math.isfinite(value)]
    if not clean:
        return [default for _ in values]
    low, high = np.percentile(clean, [5, 95])
    if high <= low:
        return [default for _ in values]
    return [default if value is None or not math.isfinite(value) else float(np.clip((value - low) / (high - low), 0, 1)) for value in values]


def train_price_models(x_all: list[list[Any]], y: np.ndarray, years: list[int], model_profile: str) -> tuple[np.ndarray, list[dict[str, Any]]]:
    latest = max(years)
    train_mask = np.array(years) < latest
    test_mask = np.array(years) == latest
    if train_mask.sum() < 100:
        train_mask = np.ones(len(years), dtype=bool)
    x_train = [x_all[i] for i, keep in enumerate(train_mask) if keep]
    y_train = y[train_mask]
    x_test = [x_all[i] for i, keep in enumerate(test_mask) if keep]
    y_test = y[test_mask]

    predictions: dict[str, np.ndarray] = {}
    evaluations: list[dict[str, Any]] = []
    for name, model in make_models(len(x_train), model_profile):
        model.fit(x_train, y_train)
        predictions[name] = np.exp(model.predict(x_all))
        if x_test:
            pred_test = np.exp(model.predict(x_test))
            actual_test = np.exp(y_test)
            evaluations.append(
                {
                    "model": name,
                    "mae_price_per_pyeong": round(float(mean_absolute_error(actual_test, pred_test)), 1),
                    "r2": round(float(r2_score(actual_test, pred_test)), 4),
                }
            )
    return weighted_prediction(predictions, evaluations, "mae_price_per_pyeong"), evaluations


def train_growth_models(x_all: list[list[Any]], targets: list[float | None], years: list[int], model_profile: str) -> tuple[np.ndarray, list[dict[str, Any]]]:
    valid = np.array([target is not None and math.isfinite(target) for target in targets])
    if valid.sum() < 100:
        return np.zeros(len(x_all)), []
    valid_years = np.array(years)[valid]
    holdout_year = max(valid_years)
    train_mask = valid & (np.array(years) < holdout_year)
    test_mask = valid & (np.array(years) == holdout_year)
    if train_mask.sum() < 100:
        train_mask = valid
    x_train = [x_all[i] for i, keep in enumerate(train_mask) if keep]
    y_train = np.array([targets[i] for i, keep in enumerate(train_mask) if keep], dtype=float)
    x_test = [x_all[i] for i, keep in enumerate(test_mask) if keep]
    y_test = np.array([targets[i] for i, keep in enumerate(test_mask) if keep], dtype=float)

    predictions: dict[str, np.ndarray] = {}
    evaluations: list[dict[str, Any]] = []
    for name, model in make_models(len(x_train), model_profile):
        model.fit(x_train, y_train)
        predictions[name] = model.predict(x_all)
        if x_test:
            pred_test = model.predict(x_test)
            evaluations.append(
                {
                    "model": name,
                    "mae_next_year_growth_pct": round(float(mean_absolute_error(y_test, pred_test)), 2),
                    "r2": round(float(r2_score(y_test, pred_test)), 4),
                }
            )
    return weighted_prediction(predictions, evaluations, "mae_next_year_growth_pct"), evaluations


def main() -> None:
    global ACTIVE_NUMERIC_FEATURES
    args = parse_args()
    with Path(args.summary).open("r", encoding="utf-8") as file:
        summary = json.load(file)

    external = load_external_features(Path(args.data_dir), args.external_mode)
    rows, price_y, next_year_targets, payloads = build_dataset(summary, args.min_trade_count, external)
    if len(rows) < 100:
        raise RuntimeError(f"Not enough rows to train recommendations: {len(rows)}")

    ACTIVE_NUMERIC_FEATURES = [
        feature
        for feature in NUMERIC_FEATURES
        if any(row.get(feature) is not None for row in rows)
    ]
    years = [int(row["year"]) for row in rows]
    latest_year = max(years)
    forecast_target_year = latest_year + 1
    x_all = row_matrix(rows)

    fair_price, price_evals = train_price_models(x_all, price_y, years, args.model_profile)
    next_growth, growth_evals = train_growth_models(x_all, next_year_targets, years, args.model_profile)
    actual_pp = np.exp(price_y)
    undervalue_pct = ((fair_price - actual_pp) / actual_pp) * 100
    forecast_pp = np.maximum(actual_pp * (1 + next_growth / 100), 0)

    discount_norm = normalize([float(value) for value in undervalue_pct])
    expected_growth_norm = normalize([float(value) for value in next_growth])
    yoy_norm = normalize([payload.get("yoy_rate") for payload in payloads])
    period_norm = normalize([payload.get("period_rate") for payload in payloads])
    liquidity_norm = normalize([float(payload.get("trade_count") or 0) for payload in payloads])
    households_norm = normalize([safe_float(payload.get("households")) for payload in payloads])
    income_norm = normalize([rows[i].get("income_score") for i in range(len(rows))])
    workplace_norm = normalize([rows[i].get("workplace_total") for i in range(len(rows))])
    store_norm = normalize([rows[i].get("store_density_score") for i in range(len(rows))])
    infra_norm = normalize(
        [
            (1.0 if payload.get("elementary_500m") else 0.0)
            + (1.0 - min(float(payload.get("subway_distance_m") or 1600), 1600) / 1600)
            + (0.5 * (1.0 - min(float(payload.get("bus_stop_distance_m") or 1200), 1200) / 1200))
            + min(float(payload.get("education_facility_count") or 0), 5) / 5
            for payload in payloads
        ]
    )

    recommendations: list[dict[str, Any]] = []
    for idx, payload in enumerate(payloads):
        if int(payload["year"]) != latest_year:
            continue
        score = (
            discount_norm[idx] * SCORE_WEIGHTS["undervalue"]
            + expected_growth_norm[idx] * SCORE_WEIGHTS["next_year_growth"]
            + yoy_norm[idx] * SCORE_WEIGHTS["yoy_momentum"]
            + period_norm[idx] * SCORE_WEIGHTS["period_momentum"]
            + liquidity_norm[idx] * SCORE_WEIGHTS["liquidity"]
            + households_norm[idx] * SCORE_WEIGHTS["households"]
            + income_norm[idx] * SCORE_WEIGHTS["income"]
            + workplace_norm[idx] * SCORE_WEIGHTS["workplace"]
            + store_norm[idx] * SCORE_WEIGHTS["commercial"]
            + infra_norm[idx] * SCORE_WEIGHTS["infra"]
        )
        item = {
            **payload,
            "fair_price_per_pyeong": round(float(fair_price[idx]), 1),
            "undervalue_pct": round(float(undervalue_pct[idx]), 1),
            "forecast_target_year": forecast_target_year,
            "expected_growth_pct": round(float(next_growth[idx]), 1),
            "forecast_price_per_pyeong": round(float(forecast_pp[idx]), 1),
            "house_match_score": round(float(score), 1),
            "signals": {
                "undervalue": round(float(discount_norm[idx] * 100), 1),
                "expected_growth": round(float(expected_growth_norm[idx] * 100), 1),
                "momentum": round(float((yoy_norm[idx] * 0.5 + period_norm[idx] * 0.5) * 100), 1),
                "liquidity": round(float(liquidity_norm[idx] * 100), 1),
                "scale": round(float(households_norm[idx] * 100), 1),
                "income": round(float(income_norm[idx] * 100), 1),
                "workplace": round(float(workplace_norm[idx] * 100), 1),
                "commercial": round(float(store_norm[idx] * 100), 1),
                "infra": round(float(infra_norm[idx] * 100), 1),
            },
        }
        recommendations.append(item)

    recommendations.sort(key=lambda item: item["house_match_score"], reverse=True)
    output = {
        "generated_at": summary.get("generated_at"),
        "summary_source": str(args.summary),
        "data_dir": str(args.data_dir),
        "external_mode": args.external_mode,
        "model_profile": args.model_profile,
        "target_year": str(latest_year),
        "forecast_target_year": str(forecast_target_year),
        "method": "Robust ensemble: fair-value price model plus next-year growth model, blended with liquidity, scale, income, workplace, commercial, school, subway, bus, and education-facility signals.",
        "score_note": "SCORE는 저평가, 다음 연도 기대상승, 과거 상승, 거래 유동성, 세대수, 소득, 직장인구, 상권, 초품아/지하철/버스/교육시설을 0-100으로 표준화해 합산한 검토 우선순위입니다.",
        "features_used": {
            "numeric": ACTIVE_NUMERIC_FEATURES,
            "categorical": CATEGORICAL_FEATURES,
        },
        "models": {
            "fair_price": price_evals,
            "next_year_growth": growth_evals,
        },
        "weights": SCORE_WEIGHTS,
        "recommendations": recommendations[: args.top_n],
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, separators=(",", ":"))

    print(f"Trained fair-value ensemble on {len(rows):,} rows.")
    print(f"External mode: {args.external_mode}")
    print(f"Model profile: {args.model_profile}")
    print(f"Wrote {min(len(recommendations), args.top_n):,} recommendations to {output_path}")


if __name__ == "__main__":
    main()
