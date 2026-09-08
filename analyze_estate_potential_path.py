"""One preregistered 24/36/48/60-month relative repricing path experiment.

Four fixed trailing-year checkpoints are separate outcomes. Never use an ex-post
maximum, replace a missing outcome with failure, or average observed outcomes
into a changing per-property target. Production predictions are not modified.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from analyze_estate_potential import CASES, COLS
from analyze_estate_potential_horizons import (
    LAW_CHANGE, cutoff_for, finite, maturity_for_window, measure, price_snapshot,
)
from estate_nowcast import load_transactions

HORIZONS = (24, 36, 48, 60)
METHODS = ("learned_path", "laggard", "cheap_peer", "momentum")
FEATURES = [*COLS, "horizon_months"]
PROTOCOL_SHA256 = "81caea7b9eca91a9c5d1708bee005bd76f9881acc10a02812d2c17b262acb92e"
DESIGN = "fixed_path_24_36_48_60_trailing12m_mature_key_weights_v1"


def path_labels(d, snapshot, origin, horizon, regime):
    end = origin + pd.DateOffset(months=horizon)
    start = end - pd.DateOffset(months=12)
    exits = d[(d.date > start) & (d.date <= end)].groupby("key").log_price.agg(["median", "count"])
    f = snapshot.copy()
    f["exit"] = exits["median"].reindex(f.index)
    f["exit_n"] = exits["count"].reindex(f.index).fillna(0)
    f["growth"] = (f.exit - f.entry).where(f.exit_n >= 3)
    f["benchmark"] = np.nan
    for _, group in f.groupby("peer"):
        pool = group[group.growth.notna()].groupby("complex").growth.median()
        for complex_name, rows in group.groupby("complex").groups.items():
            others = pool[pool.index != complex_name]
            if len(others) >= 5:
                f.loc[rows, "benchmark"] = others.median()
    f["target"] = f.growth - f.benchmark
    f["horizon_months"] = horizon
    f["exit_start_exclusive"] = str(start.date())
    f["exit_end_inclusive"] = str(end.date())
    f["label_available"] = str(maturity_for_window(start + pd.Timedelta(days=1), end, regime).date())
    return f.reset_index()


def observation_counts(d, snapshot, origin, horizon, regime):
    """Count measurement availability without calculating future prices."""
    end = origin + pd.DateOffset(months=horizon)
    start = end - pd.DateOffset(months=12)
    counts = d[(d.date > start) & (d.date <= end)].groupby("key").size()
    f = snapshot[["complex", "peer"]].copy()
    f["exit_observed"] = counts.reindex(f.index).fillna(0).ge(3)
    peer_complexes = f[f.exit_observed].groupby("peer").complex.nunique()
    f["relative_observed"] = f.exit_observed & f.peer.map(peer_complexes).fillna(0).ge(6)
    return {
        "origin": str(origin.date()), "horizon_months": horizon,
        "availability_regime": regime, "cohort": len(f),
        "exit_with_three_trades": int(f.exit_observed.sum()),
        "relative_outcome_observed": int(f.relative_observed.sum()),
        "relative_observation_rate_pct": 100 * f.relative_observed.mean() if len(f) else None,
    }


def mature_training(data, origin):
    train = data[(data.label_available <= str(origin)) & data.target.notna()
                 & ~data.complex.isin(CASES)].copy()
    support = {
        h: {"rows": int((train.horizon_months == h).sum()),
            "origins": int(train.loc[train.horizon_months == h, "origin"].nunique())}
        for h in HORIZONS
    }
    if any(s["rows"] < 500 or s["origins"] < 3 for s in support.values()):
        return None, support
    # Weights are recomputed at EACH prediction origin: an unobserved or still
    # immature future horizon must not change the weight of an available row.
    multiplicity = train.groupby(["origin", "key"]).horizon_months.transform("count")
    train["sample_weight"] = 1 / multiplicity
    return train, support


def mean_prediction_signal(rows, predictions):
    frame = rows[["key", "horizon_months"]].copy()
    frame["prediction"] = np.asarray(predictions)
    if frame.duplicated(["key", "horizon_months"]).any():
        raise ValueError("Duplicate prediction checkpoint")
    counts = frame.groupby("key").horizon_months.agg(["count", "nunique"])
    if not counts.eq(len(HORIZONS)).all().all() or set(frame.horizon_months) != set(HORIZONS):
        raise ValueError("Every candidate must have all four fixed predictions")
    if not np.isfinite(frame.prediction).all():
        raise ValueError("Every checkpoint prediction must be finite")
    return frame.groupby("key").prediction.mean()


def selected_keys(base, signal):
    f = base[["key"]].copy()
    f["signal"] = signal.reindex(f.key).to_numpy()
    f["signal"] = f.signal.fillna(f.signal.median()).fillna(0)
    return f.sort_values(["signal", "key"], ascending=[False, True]).head(max(1, len(f) // 10)).key.tolist()


def detection_diagnostics(rows, keys):
    table = rows.pivot(index="key", columns="horizon_months", values="target").reindex(index=keys, columns=HORIZONS)
    observed = table.notna()
    hits = table.ge(np.log1p(.10))
    hit_any = hits.any(axis=1)
    known_no_hit = ~hit_any & observed.all(axis=1)
    unresolved = ~hit_any & ~observed.all(axis=1)
    first, first_after_gap = {}, 0
    for key in table.index[hit_any]:
        h = next(h for h in HORIZONS if hits.loc[key, h])
        first[str(h)] = first.get(str(h), 0) + 1
        first_after_gap += int(not observed.loc[key, [v for v in HORIZONS if v < h]].all())
    n = len(table)
    return {
        "selected": n, "observed_hit": int(hit_any.sum()),
        "observed_no_hit_at_four_checkpoints": int(known_no_hit.sum()),
        "unresolved": int(unresolved.sum()), "first_observed_hit_month_counts": first,
        "first_observed_hit_with_prior_missing": first_after_gap,
        "hit_rate_lower_pct": 100 * hit_any.sum() / n if n else None,
        "hit_rate_upper_pct": 100 * (hit_any.sum() + unresolved.sum()) / n if n else None,
        "interpretation": "Detection at four fixed checkpoints, not first market crossing or executable exit.",
    }


def evaluate(data, asof):
    results, skipped, detections = [], [], []
    for origin, test in data.groupby("origin", sort=True):
        if test.label_available.max() > str(asof.date()):
            skipped.append({"origin": origin, "reason": "four_test_windows_not_mature"})
            continue
        train, support = mature_training(data, origin)
        if train is None:
            skipped.append({"origin": origin, "reason": "each_horizon_needs_500_mature_rows_and_3_origins", "support": support})
            continue
        model = LGBMRegressor(objective="regression_l1", n_estimators=120, num_leaves=7,
                              min_child_samples=100, reg_lambda=20, learning_rate=.04,
                              n_jobs=4, verbosity=-1, random_state=20260908)
        model.fit(train[FEATURES], train.target, sample_weight=train.sample_weight)
        signal = mean_prediction_signal(test, model.predict(test[FEATURES]))
        base = test[test.horizon_months == HORIZONS[0]].copy().set_index("key", drop=False)
        signals = {"learned_path": signal, "laggard": -base.relative_momentum,
                   "cheap_peer": -base.relative_level, "momentum": base.relative_momentum}
        for method, values in signals.items():
            selected = selected_keys(base.reset_index(drop=True), values)
            detections.append({"origin": origin, "method": method,
                               **detection_diagnostics(test, selected)})
            for horizon, rows in test.groupby("horizon_months"):
                measured = measure(rows, values.reindex(rows.key).to_numpy(), method,
                                   len(train) if method == "learned_path" else 0,
                                   int(train.origin.nunique()) if method == "learned_path" else 0)
                results.append({"origin": origin, "horizon_months": int(horizon),
                                "availability_regime": test.availability_regime.iloc[0],
                                "whole_cohort_observation_rate_pct": 100 * rows.target.notna().mean(),
                                **measured})
        print("evaluated path", test.availability_regime.iloc[0], origin,
              "train", len(train), "candidates", len(base), flush=True)
    return finite({"results": results, "skipped": skipped, "detections": detections})


def adoption_gate(results):
    by_horizon = []
    for horizon in HORIZONS:
        subset = [r for r in results if r["horizon_months"] == horizon]
        origin_rows = {}
        for row in subset:
            origin_rows.setdefault(row["origin"], {})[row["method"]] = row
        common = [origin for origin, rows in origin_rows.items()
                  if all(method in rows and rows[method]["observation_rate_pct"] >= 50
                         and rows[method]["observed_complexes"] >= 20 for method in METHODS)]
        means = {method: float(np.mean([origin_rows[o][method]["complex_median_excess_pct"] for o in common]))
                 if common else None for method in METHODS}
        rho_values = [origin_rows[o]["learned_path"]["spearman"] for o in common
                      if origin_rows[o]["learned_path"]["spearman"] is not None]
        by_horizon.append({"horizon_months": horizon, "common_origins": sorted(common),
                           "common_origin_count": len(common), "complex_median_excess_pct_origin_equal_mean": means,
                           "spearman_origin_median": float(np.median(rho_values)) if rho_values else None})
    coverage_pass = all(h["common_origin_count"] >= 6 for h in by_horizon)
    complete = all(h["common_origin_count"] > 0 for h in by_horizon)
    composite = {method: float(np.mean([h["complex_median_excess_pct_origin_equal_mean"][method] for h in by_horizon]))
                 if complete else None for method in METHODS}
    outperforming = sum(h["common_origin_count"] > 0
                        and h["complex_median_excess_pct_origin_equal_mean"]["learned_path"] > 0
                        and h["complex_median_excess_pct_origin_equal_mean"]["learned_path"] > h["complex_median_excess_pct_origin_equal_mean"]["laggard"]
                        for h in by_horizon)
    gates = {
        "four_horizons_each_have_six_common_origins": coverage_pass,
        "positive_composite_beats_best_fixed_baseline": bool(complete and composite["learned_path"] > 0
            and composite["learned_path"] > max(composite[m] for m in METHODS if m != "learned_path")),
        "three_horizons_positive_and_beat_laggard": outperforming >= 3,
        "all_four_horizon_median_spearman_positive": all(h["spearman_origin_median"] is not None
                                                       and h["spearman_origin_median"] > 0 for h in by_horizon),
    }
    return finite({"passed": all(gates.values()), "gates": gates, "by_horizon": by_horizon,
                   "horizon_equal_composite_pct": composite, "positive_outperforming_horizons": outperforming,
                   "status": "retrospective_hypothesis_gate_not_automatic_production_promotion"})


def load_sources(current_path, history_path, asof):
    current, current_quality = load_transactions(current_path)
    current = current[current.gu.str.startswith("11") | current.gu.eq("41210")]
    columns = ["key", "complex", "gu", "area", "built", "date", "day", "floor", "log_price"]
    current = current[columns].copy()
    older, older_quality = load_transactions(history_path)
    older = older[older.gu.str.startswith("11") | older.gu.eq("41210")][columns].copy()
    if older.date.max() >= current.date.min():
        raise ValueError("Source contract periods overlap")
    d = pd.concat([older, current], ignore_index=True)
    d = d[d.floor.between(3, 20) & (d.date <= asof)].copy()
    return d, {"current": current_quality, "older": older_quality, "asof": str(asof.date()), "analysis_rows": len(d)}


def verified_added_history_rows(older_source_sha256):
    provenance_path = Path("reports/estate_potential_five_year_sources.json")
    if provenance_path.exists():
        provenance = json.loads(provenance_path.read_text())
        if provenance.get("sha256") == older_source_sha256:
            return sum(s["rows"] for s in provenance["sources"]
                       if s["start"] >= "200601" and s["end"] <= "201512")
    return None


def build_features(d, origins, regime, coverage_only=False):
    d = d.copy()
    d["available_date"] = d.date + pd.to_timedelta(np.where((d.date < LAW_CHANGE) | (regime == "uniform61"), 61, 31), unit="D")
    outputs, coverage = [], []
    for origin in origins:
        snapshot = price_snapshot(d, origin, regime)
        for horizon in HORIZONS:
            coverage.append(observation_counts(d, snapshot, origin, horizon, regime))
            if not coverage_only:
                outputs.append(path_labels(d, snapshot, origin, horizon, regime))
        print("coverage" if coverage_only else "features", regime, origin.date(), len(snapshot), flush=True)
    return pd.concat(outputs, ignore_index=True) if outputs else None, finite(coverage)


def render_report(result):
    gate = result["experiments"]["historical_policy"]["gate"]
    primary = result["experiments"]["historical_policy"]
    tested_origins = sorted({r["origin"] for r in primary["results"] if r["method"] == "learned_path"})
    summary = summarize_result(result)
    bounds = summary["first_observed_hit_bounds"]["by_method"]
    count_text = "·".join(str(h["common_origin_count"]) for h in gate["by_horizon"])
    underperformed_all = all(h["common_origin_count"] and h["complex_median_excess_pct_origin_equal_mean"]["learned_path"]
                            < h["complex_median_excess_pct_origin_equal_mean"]["laggard"] for h in gate["by_horizon"])
    lines = ["# 5년 이내 상대 재평가 경로: 고정 후속 실험 결과", "",
             "2026-09-08. 24·36·48·60개월의 직전12개월 가격을 별도로 예측하고 네 예측의 고정 평균으로 선별했다. 성과를 확인한 뒤 모형·임계값·기간을 바꾸지 않았다.", "",
             f"기본 공시 가정의 사전 게이트: **{'통과' if gate['passed'] else '미통과'}**. 이 결과만으로 현재 운영 후보를 변경하지 않는다.", "",
             f"실제 순차학습은 {len(tested_origins)}개 판단 시점({tested_origins[0] if tested_origins else '없음'}~{tested_origins[-1] if tested_origins else '없음'})에서 완료했다. 운영 중인18~24개월 v2와 이 네 기간 공통 모형은 결과 창·학습 이력·선별 신호가 다르다. 아래24개월 행도 기존 운영 모형의 성능을 다시 측정한 값이 아니다.", "",
             "| 목표 시점 | 네 방법 공통 충분관측 원점 | 모델 상대 변화 | 소외 규칙 | 저가 규칙 | 추세 규칙 | 모델 순위상관 |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    def number(v, percent=False):
        return "미산출" if v is None else f"{v:+.2f}{'%' if percent else ''}"
    def bound(method, side):
        value = bounds[method][f"{side}_pct_origin_equal_mean"]
        return "미산출" if value is None else f"{value:.2f}%"
    for h in gate["by_horizon"]:
        m = h["complex_median_excess_pct_origin_equal_mean"]
        lines.append(f"| {h['horizon_months']}개월 | {h['common_origin_count']} | " + " | ".join(number(m[k], True) for k in METHODS) + f" | {number(h['spearman_origin_median'])} |")
    lines += ["", "수치는 각 원점에서 관측된 단지별 상대 변화의 중앙값을 구한 뒤 원점에 같은 가중치를 준 값이다. 네 방법 모두 관측률50%·관측단지20개를 충족한 같은 원점만 비교한다. 기간마다6개 이상을 요구했다. 충분관측 원점이6개 미만인 표의 값도 기술 통계이며 통과 근거가 아니다.", "",
              "## 고정 판정", ""]
    for name, passed in gate["gates"].items():
        lines.append(f"- `{name}`: {'통과' if passed else '미통과'}")
    lines += ["", f"전체61일 민감도: {result['uniform61_status']}", "",
              "## 해석과 남긴 한계", "",
              f"네 기간의 공통 충분관측 원점 수는 각각{count_text}개이며 각 기간 최소6개를 요구했다. " + ("관측이 더 확보된 짧은 기간에서도 모델이 단순 소외 규칙보다 낮았다. 따라서 이번 실패를 실행 시간이나 좁은 결과 창 하나의 문제로만 설명할 수 없다. 지표를 더 넣으면 해결된다고 미리 단정하지도 않는다." if underperformed_all else "관측량과 성과 판정을 각각 확인해야 하며 관측 창 확대만으로 예측 개선을 보장하지 않는다."), "",
              "결과 미관측 후보를 실패나0%로 바꾸지 않았고, 관측된 기간만 골라 평균 목표를 만들지 않았다. +10% 첫 관측 도달은 네 고정 시점의 보조 진단이며 최초 시장가격 도달일·매도 가능한 가격·최고 수익률이 아니다. 미관측이 있는 후보의 사건 여부는 미상으로 남기고 도달률 하한과 상한을 함께 기록했다.", "",
              f"모델 후보의 +10% 관측 도달률은 원점 동일가중 하한{bound('learned_path', 'lower')}~상한{bound('learned_path', 'upper')}, 단순 소외 후보는{bound('laggard', 'lower')}~{bound('laggard', 'upper')}다. 미관측이 많으면 범위가 넓어져 이 보조 진단으로 우열을 가리기 어렵다. 분모는 각 판단 시점의 사전 선정 후보이며 같은 단지가 여러 원점에 반복될 수 있다. 성공 확률이나 모든 중간 가격 상승의 발생률이 아니다.", "",
              "최종 수정 실거래와 가정한 공시 기한을 이용한 회고 진단이다. 과거 최초 공시본, 독립 미래 검증 또는 비용 차감 실현 수익률로 해석하지 않는다. 단지·반기 원점·결과 창 중복으로 원점 수는 독립 시장 국면 수와 다르다. 원점별 학습표본과 관측/미관측 개수, 모든 규칙의 성과 및 도달 진단은 같은 이름의 JSON에 보존한다.", "",
              "철산한신·선사현대는 학습 목표행에서 제외했다. 다른 단지의 과거 비교군·시장 피처에 등장하는 것까지 제거한 완전한 단지 홀드아웃으로 부르지는 않는다.", "",
              f"프로토콜 SHA256: `{result['protocol_sha256']}`. 새 자료를 수집하거나 기존24개월 후보·저평가 점수를 이 실험만으로 바꾸지 않았다.", ""]
    return "\n".join(lines)


def summarize_result(result):
    primary = result["experiments"]["historical_policy"]
    gate = primary["gate"]
    rows = primary["results"]
    by_horizon = []
    for h in gate["by_horizon"]:
        learned = [r for r in rows if r["horizon_months"] == h["horizon_months"] and r["method"] == "learned_path"]
        means = h["complex_median_excess_pct_origin_equal_mean"]
        baselines = {m: means[m] for m in METHODS if m != "learned_path"}
        available = {m: v for m, v in baselines.items() if v is not None}
        best = max(available, key=available.get) if available else None
        by_horizon.append({"horizon_months": h["horizon_months"], "model_test_origins": len(learned),
                           "common_eligible_origins": h["common_origin_count"],
                           "model_equal_origin_mean_median_excess_pct": means["learned_path"],
                           "baseline_equal_origin_mean_median_excess_pct": baselines,
                           "best_baseline": best, "best_baseline_excess_pct": available.get(best),
                           "spearman_origin_median": h["spearman_origin_median"],
                           "model_observation_rate_origin_median_pct": float(np.median([r["observation_rate_pct"] for r in learned])) if learned else None,
                           "model_observation_rate_origin_min_pct": min((r["observation_rate_pct"] for r in learned), default=None),
                           "model_selected_origin_candidates": sum(r["selected"] for r in learned),
                           "model_observed_origin_candidates": sum(r["observed"] for r in learned)})
    bounds = {}
    for method in METHODS:
        items = [r for r in primary["detections"] if r["method"] == method]
        bounds[method] = {"origins": len(items),
                          "lower_pct_origin_equal_mean": float(np.mean([r["hit_rate_lower_pct"] for r in items])) if items else None,
                          "upper_pct_origin_equal_mean": float(np.mean([r["hit_rate_upper_pct"] for r in items])) if items else None,
                          "observed_hit_origin_candidates": sum(r["observed_hit"] for r in items),
                          "unresolved_origin_candidates": sum(r["unresolved"] for r in items),
                          "selected_origin_candidates": sum(r["selected"] for r in items)}
    total_pass = gate["passed"] and result["uniform61_status"] == "completed_passed"
    return finite({"schema_version": 1,
                   "status": "completed_research_gate_passed_not_deployed" if total_pass else "completed_not_adopted",
                   "passed": total_pass, "primary_gate_passed": gate["passed"], "gates": gate["gates"],
                   "horizons_months": list(HORIZONS), "outcome_window_months": 12,
                   "features": FEATURES, "selection": "top_decile_of_equal_mean_four_predicted_log_excesses",
                   "protocol_commit": result["protocol_commit"], "protocol_sha256": result["protocol_sha256"],
                   "source_hashes": {name: result["sources"][name]["source_sha256"] for name in ("current", "older")},
                   "added_history_rows": verified_added_history_rows(result["sources"]["older"]["source_sha256"]),
                   "history_raw_rows": result["sources"]["older"]["raw_rows"],
                   "model_test_origins": len({r["origin"] for r in rows if r["method"] == "learned_path"}),
                   "by_horizon": by_horizon, "horizon_equal_composite_pct": gate["horizon_equal_composite_pct"],
                   "uniform61_status": result["uniform61_status"],
                   "first_observed_hit_bounds": {"threshold_excess_pct": 10, "checkpoint_months": list(HORIZONS),
                       "unit": "origin_candidate_not_unique_property", "by_method": bounds,
                       "meaning": "Bounds concern detection at four fixed checkpoints, not all between-checkpoint price crossings."},
                   "production_changed": False})


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--current", default="data/capital_area_apt_trade_transactions.csv")
    p.add_argument("--history", default=".work/history/transactions_2006_2020.csv")
    p.add_argument("--asof", default="2026-09-08")
    p.add_argument("--protocol", default="reports/estate_potential_path_protocol.md")
    p.add_argument("--protocol-commit", default="")
    p.add_argument("--output", default="reports/estate_potential_path.json")
    p.add_argument("--coverage-only", action="store_true")
    p.add_argument("--cache", default=".work/potential-path")
    args = p.parse_args()
    protocol_hash = hashlib.sha256(Path(args.protocol).read_bytes()).hexdigest()
    if protocol_hash != PROTOCOL_SHA256:
        raise ValueError("Protocol changed: do not silently alter a preregistered experiment")
    if not args.coverage_only and not args.protocol_commit:
        raise ValueError("Record the durable preregistration Git commit before computing performance")
    asof = pd.Timestamp(args.asof)
    d, sources = load_sources(args.current, args.history, asof)
    origins = pd.date_range("2007-01-01", "2021-07-01", freq="2QS")
    result = {"schema_version": 1, "design": DESIGN, "protocol_sha256": protocol_hash,
              "protocol_commit": args.protocol_commit, "sources": sources, "features": FEATURES,
              "horizons_months": list(HORIZONS), "origin_frequency": "six_months", "experiments": {}}
    if args.coverage_only:
        _, coverage = build_features(d, origins, "historical_policy", True)
        result.update({"status": "measurement_coverage_only_no_price_performance_calculated", "coverage": coverage})
    else:
        cache = Path(args.cache)
        cache.mkdir(parents=True, exist_ok=True)
        manifest = {"sources": sources, "design": DESIGN, "protocol_sha256": protocol_hash}
        manifest_path = cache / "manifest.json"
        valid = manifest_path.exists() and json.loads(manifest_path.read_text()) == manifest
        for regime in ("historical_policy", "uniform61"):
            if regime == "uniform61" and not result["experiments"]["historical_policy"]["gate"]["passed"]:
                result["uniform61_status"] = "not_run_because_preregistered_primary_gate_failed"
                break
            path = cache / f"features_{regime}.pkl"
            if valid and path.exists():
                features = pd.read_pickle(path)
                coverage = [{"origin": o, "horizon_months": int(h), "availability_regime": regime,
                             "cohort": len(g), "exit_with_three_trades": int(g.exit_n.ge(3).sum()),
                             "relative_outcome_observed": int(g.target.notna().sum()),
                             "relative_observation_rate_pct": 100 * g.target.notna().mean()}
                            for (o, h), g in features.groupby(["origin", "horizon_months"])]
            else:
                features, coverage = build_features(d, origins, regime)
                features.to_pickle(path)
            measured = evaluate(features, asof)
            measured["coverage"] = coverage
            measured["gate"] = adoption_gate(measured["results"])
            result["experiments"][regime] = measured
            if regime == "uniform61":
                result["uniform61_status"] = "completed_passed" if measured["gate"]["passed"] else "completed_failed"
            # Checkpoint results and exact input identity after each completed regime.
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
            Path(args.output).write_text(json.dumps(finite(result), ensure_ascii=False, indent=2, allow_nan=False))
        result["status"] = "completed_retrospective_hypothesis_test_no_production_change"
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(finite(result), ensure_ascii=False, indent=2, allow_nan=False))
    if not args.coverage_only:
        out.with_suffix(".md").write_text(render_report(result))
        out.with_name(out.stem + "_summary.json").write_text(json.dumps(summarize_result(result), ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({"output": str(out), "status": result["status"]}), flush=True)


if __name__ == "__main__":
    main()
