"""Protect independent potential ranks and historical information boundaries."""
import copy
import gzip
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from publish_estate_potential import public_payload
from analyze_estate_potential_horizons import assumed_lag, cutoff_for, price_snapshot, evaluate


ROOT = Path(__file__).resolve().parents[1]


def frozen():
    body = (ROOT / "metadata/potential_shadow_2026-09.json.gz").read_bytes()
    return json.loads(gzip.decompress(body)), hashlib.sha256(body).hexdigest()


def test_publishing_preserves_original_forecast_and_keeps_rank_distinct():
    original, digest = frozen()
    untouched = copy.deepcopy(original)
    report = json.loads((ROOT / "reports/estate_extension_potential.json").read_text())
    out = public_payload(original, digest, report)
    assert original == untouched
    assert out["cohort_size"] == 3697
    assert out["rows"][0]["research_rank"] == 1
    assert out["rows"][0]["predicted_relative_change_pct"] == original["records"][0]["predicted_relative_change_pct"]
    assert "score" not in out["rows"][0]
    assert "probability" not in out["rows"][0]
    assert out["validation"][0]["median_excess_pct"] is None
    assert out["validation"][0]["missing"] == 13


def test_publishing_rejects_future_training_labels():
    original, digest = frozen()
    original["latest_training_label_available"] = "2027-01-01"
    with pytest.raises(ValueError, match="unavailable"):
        public_payload(original, digest, {"results": []})


def test_publishing_rejects_duplicate_candidates():
    original, digest = frozen()
    original["records"][1]["key"] = original["records"][0]["key"]
    with pytest.raises(ValueError, match="Duplicate"):
        public_payload(original, digest, {"results": []})


def test_historical_lag_handles_change_in_reporting_deadline():
    assert assumed_lag("2020-02-20", "historical_policy") == 61
    assert assumed_lag("2020-02-21", "historical_policy") == 31
    assert assumed_lag("2026-08-01", "uniform61") == 61
    assert cutoff_for(pd.Timestamp("2020-03-01"), "historical_policy") == pd.Timestamp("2019-12-31")


def test_snapshot_does_not_read_unavailable_pre_change_or_future_prices():
    dates = pd.to_datetime(["2020-02-01", "2020-02-22", "2020-02-23", "2020-02-25", "2020-05-01"])
    d = pd.DataFrame({"key": "A | 59㎡", "complex": "A", "gu": "11110", "area": 59.,
                      "built": 2000, "date": dates,
                      "log_price": np.log([1000000, 100, 100, 100, 2000000])})
    d["day"] = d.date.values.astype("datetime64[D]").astype("int64")
    d["available_date"] = d.date + pd.to_timedelta([61, 31, 31, 31, 31], unit="D")
    out = price_snapshot(d, pd.Timestamp("2020-04-01"), "historical_policy")
    assert out.iloc[0].entry_n == 3
    assert np.isclose(np.exp(out.iloc[0].entry), 100)
    assert out.iloc[0].n365 == 3


def test_five_year_backtest_cannot_use_labels_that_mature_after_prediction(monkeypatch):
    frames = []
    for origin, maturity in [("2017-01-01", "2022-02-01"),
                             ("2017-07-01", "2022-08-01"),
                             ("2018-01-01", "2023-02-01")]:
        frames.append(pd.DataFrame({"availability_regime": "historical_policy",
                                    "horizon_months": 60, "origin": origin, "label_available": maturity,
                                    "key": [f"key-{i}" for i in range(600)],
                                    "complex": [f"complex-{i}" for i in range(600)],
                                    "target": np.linspace(-.1, .1, 600), "growth": .2,
                                    "relative_momentum": np.linspace(-.1, .1, 600),
                                    "relative_level": np.linspace(-.2, .2, 600)}))

    def forbidden_fit(*args, **kwargs):
        raise AssertionError("No five-year training labels existed at any prediction origin")

    monkeypatch.setattr("analyze_estate_potential_horizons.LGBMRegressor", forbidden_fit)
    out = evaluate(pd.concat(frames), pd.Timestamp("2026-09-08"))
    assert len(out["skipped_model_evaluations"]) == 3
    assert all(r["train_rows"] == 0 for r in out["skipped_model_evaluations"])
    assert all(r["method"] != "learned_price" for r in out["results"])
