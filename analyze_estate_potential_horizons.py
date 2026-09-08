"""Bounded, price-only potential diagnostics at 1/2/3/5-year horizons.

Uses final corrected transaction files and explicitly assumed availability;
this is retrospective research, not a recreation of original public vintages.
Each model is fitted only to outcomes whose complete measurement window and
assumed publication lag ended before its prediction origin.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from analyze_estate_potential import CASES, COLS
from estate_nowcast import load_transactions

LAW_CHANGE = pd.Timestamp("2020-02-21")
HORIZONS = (12, 24, 36, 60)
REGIMES = ("historical_policy", "uniform61")


def assumed_lag(contract_date, regime):
    """Historical reporting deadline plus one publication day, not actual lag."""
    return 61 if regime == "uniform61" or pd.Timestamp(contract_date) < LAW_CHANGE else 31


def cutoff_for(origin, regime):
    candidate = origin - pd.Timedelta(days=31)
    lag = assumed_lag(candidate, regime)
    return origin - pd.Timedelta(days=lag)


def maturity_for_window(start, end, regime):
    """Wait for the latest deadline anywhere in an outcome window.

    When the reporting deadline shortened, an earlier old-law contract could
    become observable later than a newer contract at the window's endpoint.
    """
    end = pd.Timestamp(end)
    mature = end + pd.Timedelta(days=assumed_lag(end, regime))
    last_old_contract = LAW_CHANGE - pd.Timedelta(days=1)
    if pd.Timestamp(start) <= last_old_contract <= end:
        mature = max(mature, last_old_contract + pd.Timedelta(days=61))
    return mature


def price_snapshot(d, origin, regime, window=180):
    cutoff = cutoff_for(origin, regime)
    past = d[(d.date <= cutoff) & (d.available_date <= origin)]

    def aggregate(start, end):
        return past[past.date.between(start, end)].groupby("key").log_price.agg(["median", "count"])

    entry = aggregate(cutoff - pd.Timedelta(days=window - 1), cutoff)
    meta = past.sort_values("day").groupby("key").first()[["complex", "gu", "area", "built"]]
    f = meta.join(entry.rename(columns={"median": "entry", "count": "entry_n"}), how="inner")
    f = f[f.entry_n >= 3].copy()
    recent = aggregate(cutoff - pd.Timedelta(days=89), cutoff)
    half = aggregate(cutoff - pd.Timedelta(days=179), cutoff)
    prior = aggregate(cutoff - pd.Timedelta(days=359), cutoff - pd.Timedelta(days=180))
    year = aggregate(cutoff - pd.Timedelta(days=364), cutoff)
    for name, table in [("n90", recent), ("n180", half), ("n365", year)]:
        f[name] = table["count"].reindex(f.index).fillna(0)
    f["age"] = origin.year - f.built
    f["momentum"] = half["median"].reindex(f.index) - prior["median"].reindex(f.index)
    f["peer"] = f.gu + ":" + (f.area // 15).astype(int).astype(str)
    f["relative_level"] = f.entry - f.groupby("peer").entry.transform("median")
    f["peer_momentum"] = f.groupby("peer").momentum.transform("median")
    f["relative_momentum"] = f.momentum - f.peer_momentum
    f["activity"] = (f.n90 + 1) / ((f.n365 - f.n90 + 1) / 3)
    f["last_age"] = (origin - past.groupby("key").date.max().reindex(f.index)).dt.days
    f["origin"] = str(origin.date())
    f["cutoff"] = str(cutoff.date())
    f["availability_regime"] = regime
    return f


def labels(d, snapshot, origin, horizon, regime):
    end = origin + pd.DateOffset(months=horizon)
    exit_start = end - pd.DateOffset(months=6)
    exit_values = d[d.date.between(exit_start, end)].groupby("key").log_price.agg(["median", "count"])
    f = snapshot.copy()
    f["exit"] = exit_values["median"].reindex(f.index)
    f["exit_n"] = exit_values["count"].reindex(f.index).fillna(0)
    f["growth"] = (f.exit - f.entry).where(f.exit_n >= 3)
    f["benchmark"] = np.nan
    for _, group in f.groupby("peer"):
        pool = group[group.growth.notna()].groupby("complex").growth.median()
        for complex_name, rows in group.groupby("complex").groups.items():
            other = pool[pool.index != complex_name]
            if len(other) >= 5:
                f.loc[rows, "benchmark"] = other.median()
    f["target"] = f.growth - f.benchmark
    f["horizon_months"] = horizon
    f["label_available"] = str(maturity_for_window(exit_start, end, regime).date())
    return f.reset_index()


def finite(value):
    if isinstance(value, dict):
        return {k: finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [finite(v) for v in value]
    if isinstance(value, (float, np.floating)) and not np.isfinite(value):
        return None
    if isinstance(value, np.integer):
        return int(value)
    return value


def measure(test, signal, method, training_rows=0, training_origins=0):
    f = test.copy()
    f["signal"] = signal
    missing_signal = int(f.signal.isna().sum())
    # A baseline missing a prior price has a neutral cross-sectional signal;
    # candidates are never discarded based on whether a future sale occurs.
    f["signal"] = f.signal.fillna(f.signal.median()).fillna(0)
    top = f.sort_values(["signal", "key"], ascending=[False, True]).head(max(1, len(f) // 10))
    known = top[top.target.notna()]
    measured = f[f.target.notna()]
    complexes = known.groupby("complex").target.median()
    n = len(complexes)
    interval = None
    if n >= 20:
        rng = np.random.default_rng(20260909)
        values = complexes.to_numpy()
        draws = [np.median(values[rng.integers(n, size=n)]) for _ in range(300)]
        interval = (100 * np.expm1(np.quantile(draws, [.025, .975]))).tolist()
    return {
        "method": method, "training_rows": training_rows, "training_origins": training_origins,
        "cohort": len(f), "selected": len(top), "observed": len(known),
        "observed_complexes": n, "missing_outcomes": len(top) - len(known),
        "missing_signal_imputed": missing_signal,
        "median_excess_pct": 100 * np.expm1(known.target.median()),
        "complex_median_excess_pct": 100 * np.expm1(complexes.median()),
        "complex_bootstrap_95pct": interval,
        "all_observed_median_excess_pct": 100 * np.expm1(measured.target.median()),
        "median_growth_pct": 100 * np.expm1(known.growth.median()),
        "observed_positive_excess_pct": 100 * known.target.gt(0).mean(),
        "spearman": f.signal.corr(f.target, method="spearman") if len(measured) >= 3 else None,
        "observation_rate_pct": 100 * len(known) / len(top),
    }


def evaluate(features, asof):
    results, skipped, cases = [], [], []
    for (regime, horizon), data in features.groupby(["availability_regime", "horizon_months"]):
        for origin, test in data.groupby("origin"):
            if test.label_available.iloc[0] > str(asof.date()):
                continue
            context = {"availability_regime": regime, "horizon_months": int(horizon), "origin": origin}
            # Factor diagnostics can have mature 60-month outcomes even when a
            # trained 60-month forecasting model cannot be independently tested.
            for name, signal in [("laggard", -test.relative_momentum),
                                 ("cheap_peer", -test.relative_level),
                                 ("momentum", test.relative_momentum)]:
                results.append({**context, **measure(test, signal, name)})
            train = data[(data.label_available <= origin) & data.target.notna() & ~data.complex.isin(CASES)]
            n_origins = train.origin.nunique()
            if len(train) < 500 or n_origins < 3:
                skipped.append({**context, "train_rows": len(train), "train_origins": int(n_origins),
                                "reason": "need_500_mature_rows_and_3_prior_origins"})
                continue
            model = LGBMRegressor(objective="regression_l1", n_estimators=120,
                                  num_leaves=7, min_child_samples=100, reg_lambda=20,
                                  learning_rate=.04, n_jobs=4, verbosity=-1,
                                  random_state=20260908)
            model.fit(train[COLS], train.target)
            predictions = model.predict(test[COLS])
            result = measure(test, predictions, "learned_price", len(train), int(n_origins))
            results.append({**context, **result})
            ranked = test.assign(signal=predictions).sort_values(["signal", "key"], ascending=[False, True]).copy()
            ranked["research_rank"] = np.arange(1, len(ranked) + 1)
            for _, r in ranked[ranked.complex.isin(CASES)].iterrows():
                cases.append({**context, "key": r.key, "area": r.area,
                              "entry_n": int(r.entry_n), "exit_n": int(r.exit_n),
                              "research_rank": int(r.research_rank), "cohort": len(ranked),
                              "top_percent": 100 * r.research_rank / len(ranked),
                              "entry_oku": np.exp(r.entry) * r.area / 10000,
                              "exit_oku": np.exp(r.exit) * r.area / 10000,
                              "excess_pct": np.expm1(r.target) * 100})
            print("evaluated", regime, horizon, origin, "train", len(train), "test", len(test), flush=True)
    return finite({"results": results, "skipped_model_evaluations": skipped, "named_cases": cases})


def cohort_coverage(features):
    return [{"availability_regime": regime, "horizon_months": int(horizon), "origin": origin,
             "cohort": len(group), "exit_with_three_trades": int((group.exit_n >= 3).sum()),
             "relative_outcome_observed": int(group.target.notna().sum())}
            for (regime, horizon, origin), group in features.groupby(["availability_regime", "horizon_months", "origin"])]


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--current", default="data/capital_area_apt_trade_transactions.csv")
    p.add_argument("--history", default=".work/history/transactions.csv")
    p.add_argument("--asof", default="2026-09-08")
    p.add_argument("--origin-start", default="2017-01-01")
    p.add_argument("--origin-end", default="2025-07-01")
    p.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS), choices=HORIZONS)
    p.add_argument("--regimes", nargs="+", default=list(REGIMES), choices=REGIMES)
    p.add_argument("--output", default="reports/estate_potential_horizons.json")
    p.add_argument("--cache", default=".work/potential-horizons")
    args = p.parse_args()
    asof = pd.Timestamp(args.asof)
    current, current_quality = load_transactions(args.current)
    current = current[current.gu.str.startswith("11") | current.gu.eq("41210")]
    columns = ["key", "complex", "gu", "area", "built", "date", "day", "floor", "log_price"]
    current = current[columns].copy()
    older, older_quality = load_transactions(args.history)
    older = older[columns].copy()
    if older.date.max() >= current.date.min():
        raise ValueError("Source contract periods overlap")
    d = pd.concat([older, current], ignore_index=True)
    d = d[d.floor.between(3, 20) & (d.date <= asof)].copy()
    cache = Path(args.cache)
    cache.mkdir(parents=True, exist_ok=True)
    sources = {"current": current_quality, "older": older_quality, "asof": args.asof}
    # A cache is only valid for these exact input hashes, cut date, and design.
    manifest = {**sources, "design": "180d_horizons_policy61_31_uniform61_v2",
                "origin_start": args.origin_start, "origin_end": args.origin_end,
                "horizons": args.horizons, "regimes": args.regimes}
    manifest_path = cache / "manifest.json"
    cache_valid = manifest_path.exists() and json.loads(manifest_path.read_text()) == manifest
    frames = []
    for regime in args.regimes:
        path = cache / f"features_{regime}.parquet"
        if path.exists() and cache_valid:
            frames.append(pd.read_parquet(path))
            continue
        d["available_date"] = d.date + pd.to_timedelta(
            np.where((d.date < LAW_CHANGE) | (regime == "uniform61"), 61, 31), unit="D")
        outputs = []
        for origin in pd.date_range(args.origin_start, args.origin_end, freq="2QS"):
            f = price_snapshot(d, origin, regime)
            for horizon in args.horizons:
                outputs.append(labels(d, f, origin, horizon, regime))
            print("features", regime, origin.date(), len(f), flush=True)
        features = pd.concat(outputs, ignore_index=True)
        features.to_parquet(path, index=False)
        frames.append(features)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    combined_features = pd.concat(frames, ignore_index=True)
    result = evaluate(combined_features, asof)
    result["coverage"] = cohort_coverage(combined_features)
    result.update({"schema_version": 1, "status": "retrospective_diagnostic_not_model_selection_holdout",
                   "sources": sources, "features": COLS, "entry_window_days": 180,
                   "origin_frequency": "six_months", "horizons_months": args.horizons,
                   "origin_start": args.origin_start, "origin_end": args.origin_end,
                   "outcome_window": "last six months ending at each horizon",
                   "lag_source": "https://rt.molit.go.kr/pt/bbs/faqList.do",
                   "availability": "historical_policy: 61 days for contracts before 2020-02-21, 31 days after; uniform61: 61 days throughout. Deadlines plus next-day publication are assumptions, not observed release dates.",
                   "benchmark": "same gu and 15sqm area bin; other complexes only; minimum five; complex-equal-weight growth median",
                   "notes": ["Final corrected data are not original public vintages.",
                             "Five-year factor diagnostics are not validation of a fitted five-year forecast.",
                             "Semiannual origins overlap in properties and outcome periods; they are not independent market cycles.",
                             "Historical median entry and later median exit are not executable purchases or net returns.",
                             "Missing exit observations remain counted in preselected cohorts.",
                             "Named success cases were excluded from fitting, not from descriptive evaluation.",
                             "No model or horizon is automatically promoted by this diagnostic."]})
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({"output": args.output, "evaluations": len(result["results"]),
                      "skipped": len(result["skipped_model_evaluations"])}), flush=True)


if __name__ == "__main__":
    main()
