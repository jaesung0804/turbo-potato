import numpy as np
import pandas as pd
import pytest

from analyze_estate_potential import CASES
from analyze_estate_potential_horizons import price_snapshot
from analyze_estate_potential_path import (
    HORIZONS, METHODS, adoption_gate, detection_diagnostics, mature_training,
    mean_prediction_signal, observation_counts, path_labels, selected_keys,
)


def snapshot_fixture():
    rows = []
    for i in range(7):
        rows.append({"key": f"k{i}", "complex": f"c{i}", "peer": "p", "entry": 0.,
                     "origin": "2010-01-01", "availability_regime": "historical_policy"})
    # An extreme alternate area in the subject complex must not enter its peers.
    rows.append({"key": "k0other", "complex": "c0", "peer": "p", "entry": 0.,
                 "origin": "2010-01-01", "availability_regime": "historical_policy"})
    return pd.DataFrame(rows).set_index("key")


def test_trailing_year_boundary_excludes_start_and_keeps_missing_and_own_complex_out():
    snapshot = snapshot_fixture()
    rows = []
    for i in range(7):
        for day in ("2011-01-02", "2011-06-01", "2012-01-01"):
            rows.append({"key": f"k{i}", "date": pd.Timestamp(day), "log_price": float(i)})
    rows += [{"key": "k0other", "date": pd.Timestamp("2011-06-01"), "log_price": 100.} for _ in range(3)]
    # One missing candidate still belongs to the pre-origin snapshot.
    rows = [r for r in rows if r["key"] != "k6"]
    rows += [{"key": "k6", "date": pd.Timestamp("2011-01-01"), "log_price": 3.} for _ in range(3)]
    d = pd.DataFrame(rows)
    f = path_labels(d, snapshot, pd.Timestamp("2010-01-01"), 24, "historical_policy").set_index("key")
    assert len(f) == len(snapshot)
    assert f.loc["k6", "exit_n"] == 0
    assert np.isnan(f.loc["k6", "target"])
    assert f.loc["k0", "benchmark"] == 3.
    assert f.loc["k0", "label_available"] == "2012-03-02"
    counts = observation_counts(d, snapshot, pd.Timestamp("2010-01-01"), 24, "historical_policy")
    assert counts["relative_outcome_observed"] == f.target.notna().sum()


def test_snapshot_ignores_future_contracts_and_unavailable_inputs():
    dates = pd.to_datetime(["2021-10-01", "2021-11-01", "2021-12-01"])
    old = pd.DataFrame({"key": ["a"] * 3, "complex": ["a"] * 3, "gu": ["11110"] * 3,
                        "area": [59.] * 3, "built": [2000] * 3, "date": dates,
                        "day": dates.values.astype("datetime64[D]").astype("int64"),
                        "available_date": dates + pd.Timedelta(days=31), "log_price": [1., 2., 3.]})
    later = old.iloc[[0]].copy()
    later["date"] = pd.Timestamp("2022-01-05")
    later["available_date"] = pd.Timestamp("2022-01-06")
    later["log_price"] = 10000.
    unavailable = old.iloc[[0]].copy()
    unavailable["available_date"] = pd.Timestamp("2022-01-02")
    unavailable["log_price"] = -10000.
    origin = pd.Timestamp("2022-01-01")
    a = price_snapshot(old, origin, "historical_policy")
    b = price_snapshot(pd.concat([old, later, unavailable]), origin, "historical_policy")
    pd.testing.assert_frame_equal(a, b)


def test_training_maturity_exclusion_and_weights_use_only_mature_observed_horizons():
    rows = []
    for origin in ("2007-01-01", "2007-07-01", "2008-01-01"):
        for i in range(180):
            for h in HORIZONS:
                rows.append({"origin": origin, "key": str(i), "complex": "ordinary",
                             "horizon_months": h, "target": 0., "label_available": "2013-01-01"})
    rows += [{"origin": "2007-01-01", "key": "partial", "complex": "ordinary",
              "horizon_months": h, "target": 0. if h != 48 else np.nan,
              "label_available": "2020-01-01" if h == 60 else "2013-01-01"} for h in HORIZONS]
    rows += [{"origin": "2007-01-01", "key": "case", "complex": CASES[0],
              "horizon_months": 24, "target": 100., "label_available": "2013-01-01"}]
    train, support = mature_training(pd.DataFrame(rows), "2014-01-01")
    assert train is not None
    assert set(train.loc[train.key == "partial", "horizon_months"]) == {24, 36}
    assert train.loc[train.key == "partial", "sample_weight"].tolist() == [.5, .5]
    assert np.allclose(train.groupby(["origin", "key"]).sample_weight.sum(), 1)
    assert not train.complex.isin(CASES).any()
    assert all(v["origins"] == 3 for v in support.values())


def test_prediction_average_requires_all_four_finite_checkpoints():
    rows = pd.DataFrame({"key": ["a"] * 4, "horizon_months": HORIZONS})
    assert mean_prediction_signal(rows, [1, 2, 3, 4]).loc["a"] == 2.5
    with pytest.raises(ValueError, match="all four"):
        mean_prediction_signal(rows.iloc[:3], [1, 2, 3])
    with pytest.raises(ValueError, match="finite"):
        mean_prediction_signal(rows, [1, 2, 3, np.nan])


def test_selection_is_fixed_before_outcomes_and_uses_key_tie_break():
    base = pd.DataFrame({"key": [f"k{i:02}" for i in range(20)]})
    signal = pd.Series(1., index=base.key)
    assert selected_keys(base, signal) == ["k00", "k01"]


def test_first_observed_hit_missingness_is_unknown_and_bounds_keep_full_denominator():
    values = {"hit": [np.nan, np.log1p(.2), -.1, -.1],
              "no": [0., 0., 0., 0.], "unknown": [0., 0., np.nan, 0.]}
    rows = pd.DataFrame([{"key": key, "horizon_months": h, "target": value}
                         for key, path in values.items() for h, value in zip(HORIZONS, path)])
    d = detection_diagnostics(rows, list(values))
    assert (d["observed_hit"], d["observed_no_hit_at_four_checkpoints"], d["unresolved"]) == (1, 1, 1)
    assert d["first_observed_hit_month_counts"] == {"36": 1}
    assert d["first_observed_hit_with_prior_missing"] == 1
    assert d["hit_rate_lower_pct"] == pytest.approx(100 / 3)
    assert d["hit_rate_upper_pct"] == pytest.approx(200 / 3)


def gate_rows():
    return [{"horizon_months": h, "origin": f"201{i}-01-01", "method": method,
             "observation_rate_pct": 70., "observed_complexes": 30,
             "complex_median_excess_pct": 4. if method == "learned_path" else 1.,
             "spearman": .2}
            for h in HORIZONS for i in range(6) for method in METHODS]


def test_gate_requires_all_periods_and_all_fixed_comparators_on_common_origins():
    rows = gate_rows()
    assert adoption_gate(rows)["passed"]
    rows[0]["observation_rate_pct"] = 49.9
    g = adoption_gate(rows)
    assert not g["passed"]
    assert g["by_horizon"][0]["common_origin_count"] == 5


def test_composite_cannot_drop_an_unobserved_horizon_or_ignore_a_stronger_baseline():
    rows = [r for r in gate_rows() if r["horizon_months"] != 60]
    assert adoption_gate(rows)["horizon_equal_composite_pct"]["learned_path"] is None
    rows = gate_rows()
    for row in rows:
        if row["method"] == "momentum":
            row["complex_median_excess_pct"] = 5.
    assert not adoption_gate(rows)["gates"]["positive_composite_beats_best_fixed_baseline"]
