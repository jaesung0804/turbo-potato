"""Freeze a historical-reporting-policy candidate with visible lag sensitivity."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from analyze_estate_potential import CASES, COLS
from analyze_estate_potential_horizons import LAW_CHANGE, cutoff_for, finite, price_snapshot
from estate_nowcast import load_transactions


def fit_forecast(d, history, origin, regime):
    train = history[(history.horizon_months == 24) & (history.label_available <= str(origin.date()))
                    & history.target.notna() & ~history.complex.isin(CASES)]
    if len(train) < 500 or train.origin.nunique() < 3:
        raise ValueError("Insufficient mature historical training origins")
    d = d[d.date <= cutoff_for(origin, regime)].copy()
    d["available_date"] = d.date + pd.to_timedelta(
        np.where((d.date < LAW_CHANGE) | (regime == "uniform61"), 61, 31), unit="D")
    current = price_snapshot(d, origin, regime).reset_index()
    model = LGBMRegressor(objective="regression_l1", n_estimators=120, num_leaves=7,
                          min_child_samples=100, reg_lambda=20, learning_rate=.04,
                          n_jobs=4, verbosity=-1, random_state=20260908)
    model.fit(train[COLS], train.target)
    current["predicted_relative_change_pct"] = np.expm1(model.predict(current[COLS])) * 100
    current = current.sort_values(["predicted_relative_change_pct", "key"], ascending=[False, True]).reset_index(drop=True)
    current["research_rank"] = np.arange(1, len(current) + 1)
    current["top_percent"] = 100 * current.research_rank / len(current)
    return current, model, train


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--origin", default="2026-09-01")
    p.add_argument("--current", default="data/capital_area_apt_trade_transactions.csv")
    p.add_argument("--history", default=".work/history/transactions.csv")
    p.add_argument("--cache", default=".work/potential-horizons")
    p.add_argument("--output", default="metadata/potential_shadow_2026-09_policy-v2.json.gz")
    p.add_argument("--artifacts-dir", type=Path, help="Preserve fitted models and exact mature training tables for a new monthly snapshot")
    args = p.parse_args()
    dest = Path(args.output)
    if dest.exists():
        raise FileExistsError("Frozen forecasts cannot be overwritten")
    if args.artifacts_dir is not None and args.artifacts_dir.exists():
        raise FileExistsError("Frozen training artifacts cannot be overwritten")
    origin = pd.Timestamp(args.origin)
    if origin.day != 1:
        raise ValueError("Origin must be a month start")
    current, current_quality = load_transactions(args.current)
    older, older_quality = load_transactions(args.history)
    current = current[current.gu.str.startswith("11") | current.gu.eq("41210")]
    if older.date.max() >= current.date.min():
        raise ValueError("Source contract periods overlap")
    cache = Path(args.cache)
    manifest = json.loads((cache / "manifest.json").read_text())
    if manifest["current"] != current_quality or manifest["older"] != older_quality:
        raise ValueError("History features do not match these exact source files")
    if manifest.get("horizons") is not None and 24 not in manifest["horizons"]:
        raise ValueError("History features do not contain the required 24-month design")
    if manifest.get("regimes") is not None and not {"historical_policy", "uniform61"} <= set(manifest["regimes"]):
        raise ValueError("Both reporting assumptions are required")
    d = pd.concat([older, current], ignore_index=True)
    d = d[d.floor.between(3, 20)].copy()
    forecast, model, train = fit_forecast(d, pd.read_parquet(cache / "features_historical_policy.parquet"), origin, "historical_policy")
    alternate, alternate_model, alternate_train = fit_forecast(d, pd.read_parquet(cache / "features_uniform61.parquet"), origin, "uniform61")
    alternate = alternate.set_index("key")
    contributions = model.booster_.predict(forecast[COLS], pred_contrib=True)
    expected = model.booster_.predict(forecast[COLS], raw_score=True)
    if not np.allclose(contributions.sum(axis=1), expected):
        raise ValueError("Feature contributions do not reconstruct model output")
    importance = np.abs(contributions[:, :-1]).mean(axis=0)
    importance = 100 * importance / importance.sum()
    rows = []
    for i, r in forecast.iterrows():
        sensitive = alternate.loc[r.key] if r.key in alternate.index else None
        drivers = np.argsort(-np.abs(contributions[i, :-1]), kind="stable")[:3]
        row = {
            "key": r.key, "gu": r.gu, "area": r.area, "research_rank": int(r.research_rank),
            "entry_n": int(r.entry_n), "entry_reference_oku": float(np.exp(r.entry) * r.area / 10000),
            "predicted_relative_change_pct": r.predicted_relative_change_pct,
            "n90": int(r.n90), "n180": int(r.n180), "n365": int(r.n365),
            "age": r.age, "last_age": int(r.last_age),
            "momentum_pct": np.expm1(r.momentum) * 100,
            "relative_momentum_pct": np.expm1(r.relative_momentum) * 100,
            "relative_price_pct": np.expm1(r.relative_level) * 100,
            "activity_ratio": r.activity,
            "drivers": [{"key": COLS[j], "direction": "positive" if contributions[i, j] > 0 else "negative",
                         "log_contribution": float(contributions[i, j])} for j in drivers],
            "lag_sensitivity": {
                "alternate_eligible": sensitive is not None,
                "alternate_rank": int(sensitive.research_rank) if sensitive is not None else None,
                "alternate_top_percent": float(sensitive.top_percent) if sensitive is not None else None,
                "both_top_decile": bool(r.top_percent <= 10 and sensitive is not None and sensitive.top_percent <= 10),
            },
        }
        rows.append(finite(row))
    result = {
        "schema_version": 2, "status": "research_candidate", "model": "price_activity_relative_growth_24m_policy_v2",
        "created_at": datetime.now(timezone.utc).isoformat(), "origin": str(origin.date()),
        "feature_cutoff": str(cutoff_for(origin, "historical_policy").date()),
        "training_rows": len(train), "latest_training_label_available": str(train.label_available.max()),
        "outcome_start": str((origin + pd.DateOffset(months=18)).date()),
        "outcome_end": str((origin + pd.DateOffset(months=24)).date()),
        "label_maturity": str((origin + pd.DateOffset(months=24) + pd.Timedelta(days=31)).date()),
        "scope": "서울 25개 자치구·광명; 3–20층; 진입 전 180일에 동일 전용면적 매매 3건 이상",
        "features": COLS, "named_cases_excluded_from_training": CASES,
        "source_sha256": {"current": current_quality, "older": older_quality},
        "availability_assumption": "2020-02-21 이전 계약은 61일, 이후는 31일 지연 가정. 과거 신고기한에 다음날 공개를 더한 보수적 가정이며 거래별 실제 최초 공시일은 아닙니다.",
        "feature_importance": [{"key": key, "mean_abs_shap_share_pct": float(value)} for key, value in zip(COLS, importance)],
        "importance_meaning": "현재 후보 전체에서 절대 SHAP 기여도의 평균을 정규화한 비중입니다. 모델의 의존도를 설명하며 고정 점수 가중치나 인과 효과가 아닙니다.",
        "lag_sensitivity": {"alternative": "uniform61", "feature_cutoff": str(cutoff_for(origin, "uniform61").date()),
                            "cohort_size": len(alternate), "training_rows": len(alternate_train),
                            "meaning": "같은 모형을 모든 계약 61일 지연 가정으로 재학습해 후보 순위를 비교합니다. 실제 공시 지연의 추정 확률이 아닙니다."},
        "note": "Separate potential research candidate. Relative growth is not a probability, a current undervaluation measure, or a validated five-year forecast. Created later than its nominal month-start origin using corrected final source files.",
        "records": rows,
    }
    if args.artifacts_dir is not None:
        from importlib.metadata import version
        folder = args.artifacts_dir
        folder.mkdir(parents=True)
        training_manifest = {
            "schema_version": 1, "status": "preserved_fitted_models_and_mature_training_tables",
            "model": result["model"], "origin": result["origin"], "features": COLS,
            "source_sha256": result["source_sha256"],
            "feature_manifest_sha256": hashlib.sha256((cache / "manifest.json").read_bytes()).hexdigest(),
            "libraries": {name: version(name) for name in ("lightgbm", "numpy", "pandas", "pyarrow", "joblib")},
            "regimes": {},
        }
        for regime, fitted, table in (("historical_policy", model, train),
                                      ("uniform61", alternate_model, alternate_train)):
            model_path = folder / f"model_{regime}.joblib"
            table_path = folder / f"training_{regime}.parquet"
            joblib.dump({"model": fitted, "features": COLS, "origin": result["origin"],
                         "model_version": result["model"], "availability_regime": regime}, model_path, compress=3)
            table.to_parquet(table_path, index=False)
            training_manifest["regimes"][regime] = {
                "rows": len(table), "origins": int(table.origin.nunique()),
                "latest_label_available": str(table.label_available.max()),
                "files": [{"path": path.name, "bytes": path.stat().st_size,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                          for path in (model_path, table_path)],
            }
        manifest_body = json.dumps(training_manifest, ensure_ascii=False, indent=2, allow_nan=False).encode()
        (folder / "manifest.json").write_bytes(manifest_body)
        result["training_manifest_sha256"] = hashlib.sha256(manifest_body).hexdigest()
    dest.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(result, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()
    dest.write_bytes(gzip.compress(body, mtime=0))
    print(json.dumps({"path": str(dest), "cohort": len(rows), "training_rows": len(train),
                      "bytes": dest.stat().st_size, "sha256": hashlib.sha256(dest.read_bytes()).hexdigest()}))


if __name__ == "__main__":
    main()
