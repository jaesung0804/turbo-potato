from __future__ import annotations

import argparse
import json
import random
from pathlib import Path
from statistics import mean, median
from typing import Any


DEFAULT_SUMMARY = Path("web/data/seoul_real_estate_summary.json")
DEFAULT_OUTPUT = Path("web/data/house_match_validation.json")
RANDOM_STATE = 42

WEIGHTS = {
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


def safe_float(value: Any) -> float | None:
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed == parsed else None


def metric_avg(building: dict[str, Any], metric: str) -> float | None:
    return safe_float(building.get("metrics", {}).get(metric, {}).get("avg"))


def address_map(region: dict[str, Any], year: str) -> dict[str, dict[str, Any]]:
    return {
        item.get("key", item.get("address")): item
        for item in region.get("years", {}).get(year, {}).get("addresses", [])
    }


def change_rate(current: float | None, previous: float | None) -> float | None:
    if current is None or previous in (None, 0):
        return None
    return ((current - previous) / previous) * 100


def normalize(values: list[float | None]) -> list[float]:
    clean = sorted(value for value in values if value is not None)
    if not clean:
        return [0.0 for _ in values]
    low = clean[max(0, int(len(clean) * 0.05) - 1)]
    high = clean[min(len(clean) - 1, int(len(clean) * 0.95))]
    if high <= low:
        return [0.0 for _ in values]
    return [0.0 if value is None else max(0.0, min(1.0, (value - low) / (high - low))) for value in values]


def build_fold_candidates(summary: dict[str, Any], train_until: str, recommend_year: str, evaluate_year: str) -> list[dict[str, Any]]:
    candidates = []
    years_before = [year for year in summary["years"] if int(year) <= int(train_until)]
    if not years_before:
        return candidates

    for region in summary["regions"]:
        rec_map = address_map(region, recommend_year)
        eval_map = address_map(region, evaluate_year)
        if not rec_map or not eval_map:
            continue

        previous_year = max((year for year in years_before if int(year) < int(recommend_year)), default=None)
        prev_map = address_map(region, previous_year) if previous_year else {}
        first_year = min(years_before, key=int)
        first_map = address_map(region, first_year)

        region_prices = [
            metric_avg(item, "price_per_pyeong")
            for item in rec_map.values()
            if metric_avg(item, "price_per_pyeong") is not None
        ]
        region_median = median(region_prices) if region_prices else None

        for key, building in rec_map.items():
            future = eval_map.get(key)
            if not future:
                continue
            current_pp = metric_avg(building, "price_per_pyeong")
            future_pp = metric_avg(future, "price_per_pyeong")
            if current_pp is None or future_pp is None:
                continue
            previous = prev_map.get(key)
            first = first_map.get(key)
            subway_distance = safe_float(building.get("subway_distance_m"))
            infra = (
                (1.0 if building.get("elementary_500m") else 0.0)
                + (1.0 - min(subway_distance if subway_distance is not None else 1600, 1600) / 1600)
            )
            candidates.append(
                {
                    "region_code": region["code"],
                    "sido_name": region.get("sido_name"),
                    "gu_name": region.get("gu_name"),
                    "dong_name": region.get("dong_name"),
                    "key": key,
                    "actual_growth": change_rate(future_pp, current_pp),
                    "trade_count": building.get("count") or 0,
                    "jeonse_ratio_change": None,
                    "features": {
                        "undervalue": change_rate(region_median, current_pp),
                        "next_year_growth": change_rate(current_pp, metric_avg(previous or {}, "price_per_pyeong")),
                        "yoy_momentum": change_rate(metric_avg(building, "price_billion"), metric_avg(previous or {}, "price_billion")),
                        "period_momentum": change_rate(metric_avg(building, "price_billion"), metric_avg(first or {}, "price_billion")),
                        "liquidity": building.get("count") or 0,
                        "households": safe_float(building.get("households")),
                        "income": None,
                        "workplace": None,
                        "commercial": None,
                        "infra": infra,
                    },
                }
            )
    return [row for row in candidates if row["actual_growth"] is not None]


def score_candidates(candidates: list[dict[str, Any]], weights: dict[str, int]) -> list[dict[str, Any]]:
    norms = {key: normalize([row["features"].get(key) for row in candidates]) for key in weights}
    scored = []
    for idx, row in enumerate(candidates):
        score = sum(norms[key][idx] * weight for key, weight in weights.items())
        scored.append({**row, "score": score})
    return sorted(scored, key=lambda item: item["score"], reverse=True)


def summarize_group(rows: list[dict[str, Any]], baseline: float) -> dict[str, Any]:
    growth = [row["actual_growth"] for row in rows if row["actual_growth"] is not None]
    if not growth:
        return {"count": 0}
    return {
        "count": len(rows),
        "avg_growth": round(mean(growth), 3),
        "median_growth": round(median(growth), 3),
        "hit_rate_vs_all": round(sum(value > baseline for value in growth) / len(growth), 4),
        "loss_rate": round(sum(value < 0 for value in growth) / len(growth), 4),
        "avg_trade_count": round(mean([row.get("trade_count") or 0 for row in rows]), 2),
        "jeonse_ratio_change": None,
    }


def evaluate_fold(candidates: list[dict[str, Any]], weights: dict[str, int]) -> dict[str, Any]:
    scored = score_candidates(candidates, weights)
    baseline = mean([row["actual_growth"] for row in scored])
    rng = random.Random(RANDOM_STATE)
    random_rows = rng.sample(scored, min(len(scored), max(1, int(len(scored) * 0.1))))
    subway_rows = sorted(scored, key=lambda row: row["features"].get("infra") or 0, reverse=True)
    undervalue_rows = sorted(scored, key=lambda row: row["features"].get("undervalue") or -999, reverse=True)

    top10 = scored[: max(1, int(len(scored) * 0.1))]
    top20 = scored[: max(1, int(len(scored) * 0.2))]
    return {
        "all": summarize_group(scored, baseline),
        "score_top_10pct": summarize_group(top10, baseline),
        "score_top_20pct": summarize_group(top20, baseline),
        "random_10pct": summarize_group(random_rows, baseline),
        "simple_subway_model_10pct": summarize_group(subway_rows[: len(top10)], baseline),
        "simple_undervalue_model_10pct": summarize_group(undervalue_rows[: len(top10)], baseline),
    }


def region_segment(row: dict[str, Any]) -> str:
    sido = row.get("sido_name")
    gu = row.get("gu_name") or ""
    if sido == "서울특별시" and gu in {"강남구", "서초구", "송파구", "용산구", "마포구", "성동구", "양천구"}:
        return "서울 핵심지"
    if sido == "서울특별시":
        return "서울 외곽"
    if any(token in gu for token in ("성남", "용인", "고양", "김포", "화성", "수원", "군포")):
        return "수도권 신도시"
    return "기타 수도권"


def main() -> None:
    parser = argparse.ArgumentParser(description="Walk-forward validation for House Match scoring.")
    parser.add_argument("--summary", default=str(DEFAULT_SUMMARY))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    args = parser.parse_args()

    summary = json.load(open(args.summary, encoding="utf-8"))
    years = sorted(summary.get("years", []), key=int)
    folds = []
    for idx in range(len(years) - 2):
        train_until, recommend_year, evaluate_year = years[idx], years[idx + 1], years[idx + 2]
        candidates = build_fold_candidates(summary, train_until, recommend_year, evaluate_year)
        if len(candidates) < 30:
            continue
        fold = {
            "train_until": train_until,
            "recommend_year": recommend_year,
            "evaluate_year": evaluate_year,
            "candidate_count": len(candidates),
            "top_n": evaluate_fold(candidates, WEIGHTS),
            "ablation": {},
            "segments": {},
        }
        for remove_key in [
            "undervalue",
            "next_year_growth",
            "yoy_momentum",
            "period_momentum",
            "liquidity",
            "income",
            "workplace",
            "infra",
        ]:
            ablated = {key: value for key, value in WEIGHTS.items() if key != remove_key}
            fold["ablation"][f"without_{remove_key}"] = evaluate_fold(candidates, ablated)["score_top_10pct"]
        for segment in sorted({region_segment(row) for row in candidates}):
            segment_rows = [row for row in candidates if region_segment(row) == segment]
            if len(segment_rows) >= 20:
                fold["segments"][segment] = evaluate_fold(segment_rows, WEIGHTS)["score_top_10pct"]
        folds.append(fold)

    output = {
        "weights": WEIGHTS,
        "leakage_controls": [
            "Each fold scores candidates using features available up to train_until/recommend_year.",
            "Evaluation uses the next year after recommendation only.",
            "Normalization is fit inside each fold, not on the full period.",
        ],
        "folds": folds,
    }
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    with open(args.output, "w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, indent=2)
    print(f"Wrote validation report to {args.output}")


if __name__ == "__main__":
    main()
