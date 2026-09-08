"""Publish a compact, separately named view of an immutable research forecast.

This does not fit a model, alter valuations, or promote a rank to a probability.
The original forecast and its prediction timestamp remain unchanged.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path


FEATURES = [
    ("area", "전용면적", "같은 구 안에서도 면적별 수요와 가격 차이를 구분합니다."),
    ("age", "건물 연식", "판단 연도와 준공 연도의 차이입니다."),
    ("n90", "최근 90일 거래 수", "신고 지연 가정일 이전에 확인되는 동일 평형의 거래 수입니다."),
    ("n180", "최근 180일 거래 수", "대표가격을 만들 수 있는 관측량을 나타냅니다."),
    ("n365", "최근 365일 거래 수", "단기 거래 증가를 비교하는 연간 거래량입니다."),
    ("momentum", "반기 가격 변화", "최근 180일과 그 이전 180일의 대표가격 차이입니다."),
    ("relative_level", "주변 대비 가격 수준", "같은 구·15㎡ 면적 구간의 후보 대비 ㎡당 가격 차이입니다."),
    ("peer_momentum", "주변 가격 추세", "같은 구·면적 구간 후보의 가격 변화 중앙값입니다."),
    ("relative_momentum", "주변 대비 가격 추세", "자기 평형의 반기 변화에서 주변 변화를 뺀 값입니다."),
    ("activity", "단기 거래 활성도", "최근 90일 거래 수를 이전 275일의 분기 환산 거래 수와 비교합니다."),
    ("last_age", "마지막 거래 경과일", "판단일에서 가장 최근 관측 거래일까지 지난 일수입니다."),
]


def public_payload(snapshot: dict, source_sha256: str, report: dict) -> dict:
    """Validate the frozen identities before exposing any rank or result."""
    source = snapshot["records"]
    n = len(source)
    if n < 2:
        raise ValueError("At least two candidates are needed for a relative rank")
    if len({r["key"] for r in source}) != n:
        raise ValueError("Duplicate forecast identities")
    if sorted(r["research_rank"] for r in source) != list(range(1, n + 1)):
        raise ValueError("Forecast ranks must form a complete permutation")
    if snapshot["latest_training_label_available"] > snapshot["origin"]:
        raise ValueError("A training label was unavailable at the forecast origin")
    if snapshot["feature_cutoff"] >= snapshot["origin"]:
        raise ValueError("Feature cutoff must precede forecast origin")
    rows = []
    for raw in sorted(source, key=lambda r: r["research_rank"]):
        complex_name, _ = raw["key"].rsplit(" | ", 1)
        district, dong, lot, name = complex_name.split(" ", 3)
        values = [raw[k] for k in ("area", "entry_reference_oku", "predicted_relative_change_pct")]
        if not all(math.isfinite(v) for v in values):
            raise ValueError("Nonfinite forecast value")
        if raw["entry_n"] < 3 or raw["entry_reference_oku"] <= 0 or raw["area"] <= 0:
            raise ValueError("Invalid eligible forecast row")
        rank = raw["research_rank"]
        rows.append({**raw, "complex": complex_name, "district": district,
                     "dong": dong, "lot": lot, "name": name,
                     "percentile": round(100 * (n - rank) / (n - 1), 4),
                     "top_percent": round(100 * rank / n, 4)})

    results = []
    is_v2 = snapshot.get("schema_version", 1) >= 2
    if is_v2:
        for name in ("current", "older"):
            forecast_source = snapshot["source_sha256"][name]["source_sha256"]
            report_source = report.get("sources", {}).get(name, {}).get("source_sha256")
            if forecast_source != report_source:
                raise ValueError(f"Validation report source does not match frozen forecast: {name}")
        if report.get("features") != snapshot.get("features") or report.get("entry_window_days") != 180:
            raise ValueError("Validation report feature design does not match frozen forecast")
    for r in report["results"]:
        if is_v2:
            if r["horizon_months"] != 24 or r["method"] != "learned_price" or r["availability_regime"] != "historical_policy":
                continue
        elif r["entry_window_days"] != 180 or r["horizon_months"] != 24 or r["method"] != "price":
            continue
        # The January 2023 cohort has only two observed outcomes. Preserve the
        # count but withhold that estimate from the displayed headline metric.
        enough = r["observed_complexes"] >= 20
        results.append({"origin": r["origin"], "cohort": r["cohort"],
                        "selected": r["selected"], "observed": r["observed"],
                        "missing": r["selected"] - r["observed"],
                        "observed_complexes": r["observed_complexes"],
                        "median_excess_pct": r["median_excess_pct"] if enough else None,
                        "complex_median_bootstrap_95pct": r.get("complex_median_bootstrap_95pct", r.get("complex_bootstrap_95pct")) if enough else None,
                        "complex_median_excess_pct": r.get("complex_median_excess_pct", r.get("complex_balanced_median_excess_pct")) if enough else None,
                        "status": "observed" if enough else "insufficient_outcomes"})

    return {
        "schema_version": 1,
        "status": "research_candidate",
        **{k: snapshot[k] for k in ["origin", "feature_cutoff", "created_at", "training_rows",
                                    "latest_training_label_available", "outcome_start", "outcome_end",
                                    "label_maturity", "scope"]},
        "cohort_size": n,
        "source_sha256": source_sha256,
        "model": snapshot.get("model", "price_activity_relative_growth_24m_v1"),
        "availability_assumption": snapshot.get("availability_assumption", "이 고정 예측은 모든 과거 거래에 일률 31일 지연을 가정한 기존 연구 모델입니다. 거래별 실제 최초 공시일을 보유한 모델은 아닙니다."),
        "rank_meaning": "같은 판단 시점의 대상 평형을 예상 상대 가격 변화 순으로 정렬",
        "target_meaning": "18~24개월 뒤 동일 구·15㎡ 면적 구간의 다른 단지 대비 가격 변화. 비교군은 단지별 동일 가중치이며 자기 단지는 제외합니다.",
        "entry_reference_meaning": "판단일 31일 전을 마감으로 직전 180일 관측 거래의 대표 총액. 현재 적정가 또는 실제 매수 가능한 가격을 뜻하지 않습니다.",
        "weighting": "11개 가격·거래 입력의 관계를 과거 성숙한 결과로 학습합니다. 고정 가산점이나 공개된 임의 가중치 합산식은 사용하지 않습니다.",
        "features": [{"key": k, "label": label, "description": desc} for k, label, desc in FEATURES],
        "feature_importance": snapshot.get("feature_importance", []),
        "importance_meaning": snapshot.get("importance_meaning"),
        "lag_sensitivity": snapshot.get("lag_sensitivity"),
        "missing_dimensions": ["실제 최초 공시일까지의 거래별 지연 분포", "동일 매물의 호가 변경 이력",
                               "세대수 대비 거래 회전율", "정비사업의 당시 공표 단계", "5년 목표의 독립 시계열 검증"],
        "validation": results,
        "guidance": [
            "예산·지역·전용면적으로 후보를 좁힌 뒤 상대 순위와 180일 거래 수를 함께 봅니다.",
            "후보의 현재 매수가격은 저평가 분석 페이지에서 해당 시점 기준가와 따로 비교합니다.",
            "같은 생활권의 대체 단지를 나란히 놓고 실제 매물 조건·교통·정비사업 일정을 확인합니다.",
            "5년 갈아타기 계획에서는 향후 분기별 후보 순위와 실제 상대 가격 변화를 기록하며 보유 판단을 갱신합니다.",
        ],
        "rows": rows,
    }


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--snapshot", default="metadata/potential_shadow_2026-09_policy-v2.json.gz")
    p.add_argument("--report", default="reports/estate_potential_horizons.json")
    p.add_argument("--lag-audit", default="reports/estate_reporting_lag.json")
    p.add_argument("--five-year-summary", default="reports/estate_potential_five_year_summary.json")
    p.add_argument("--output", default="metadata/potential_candidates.json.gz")
    args = p.parse_args()
    source = Path(args.snapshot).read_bytes()
    result = public_payload(json.loads(gzip.decompress(source)), hashlib.sha256(source).hexdigest(),
                            json.loads(Path(args.report).read_text()))
    lag_path = Path(args.lag_audit)
    if lag_path.exists():
        audit = json.loads(lag_path.read_text())
        result["reporting_lag_audit"] = {
            "window_days": audit["window"]["elapsed_days"],
            "new_public_signatures": audit["counts"]["post_baseline_new_signatures"],
            "status": audit["status"],
            "correction_weight_fitted": False,
            "observed_through": audit["window"]["latest_observed_at"],
            "source_sha256": hashlib.sha256(lag_path.read_bytes()).hexdigest(),
            "meaning": "공개 행이 처음 관측된 이력입니다. 실제 고유 계약 수·최초 공개일·전체 시장 신고 지연 분포와 같지 않으며, 현재 순위에 관측 지연 보정 가중치를 학습해 넣지 않았습니다.",
        }
    five_path = Path(args.five_year_summary)
    if five_path.exists():
        five = json.loads(five_path.read_text())
        primary = five["regimes"]["historical_policy"]
        result["five_year_validation"] = {
            "status": "passed_research_candidate_gate" if five["passed"] else "completed_not_promoted",
            "raw_rows_added": five.get("added_history_raw_rows"),
            "history_start": five["sources"]["older"]["min_date"],
            "model_test_origins": primary["available_model_evaluations"],
            "common_eligible_origins": primary["common_origins"],
            "required_common_origins": 6,
            "model_median_excess_pct": primary["model_equal_origin_mean_median_excess_pct"],
            "benchmark_median_excess_pct": primary["laggard_equal_origin_mean_median_excess_pct"],
            "median_rank_correlation": primary["model_median_spearman"],
            "protocol_commit": five["protocol_commit"],
            "additional_sensitivity_status": five.get("additional_sensitivity_status"),
            "summary_sha256": hashlib.sha256(five_path.read_bytes()).hexdigest(),
            "outcome_window": "54~60개월 뒤의 마지막 6개월, 동일 평형 거래 3건 이상",
            "meaning": "2006년 이후 이력으로 5년 모형 학습·시험을 완료했으나, 이 사양은 사전에 정한 결과 관측량과 단순 소외 후보 대비 성능 기준을 통과하지 못했습니다. 현재 순위는 18~24개월 후보를 유지합니다.",
            "next_direction": "5년 전에 강하게 재평가되는 후보를 찾는 목적에 맞춰, 향후 더 넓은 결과 측정 기간과 상대 재평가 도달 시점을 별도 사전 설계로 검증합니다.",
        }
    dest = Path(args.output)
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    dest.write_bytes(gzip.compress(body, mtime=0))
    print(json.dumps({"path": str(dest), "rows": len(result["rows"]), "bytes": dest.stat().st_size}))


if __name__ == "__main__":
    main()
