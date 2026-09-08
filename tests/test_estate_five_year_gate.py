"""Protect the prespecified horizon-promotion gate from incomplete comparisons."""
from summarize_estate_five_year import assess


def rows(regime, count=6):
    records = []
    for i in range(count):
        for method, value in [("learned_price", 2.), ("laggard", 1.)]:
            records.append({"availability_regime": regime, "horizon_months": 60,
                            "origin": f"{2014+i}-01-01", "method": method,
                            "observed_complexes": 30, "selected": 100, "observed": 60,
                            "median_excess_pct": value, "spearman": .1})
    return records


def test_sparse_high_return_cohort_does_not_inflate_the_gate():
    records = rows("historical_policy") + rows("uniform61")
    for method in ("learned_price", "laggard"):
        records.append({"availability_regime": "historical_policy", "horizon_months": 60,
                        "origin": "2020-07-01", "method": method, "observed_complexes": 2,
                        "selected": 100, "observed": 2, "median_excess_pct": 1000., "spearman": 1.})
    out = assess({"results": records})
    assert out["passed"] is True
    assert out["regimes"]["historical_policy"]["model_equal_origin_mean_median_excess_pct"] == 2.
    assert out["regimes"]["historical_policy"]["excluded_origins"] == ["2020-07-01"]


def test_promotion_requires_lag_sensitivity_and_six_valid_comparisons():
    missing = assess({"results": rows("historical_policy")})
    assert missing["passed"] is False
    assert missing["missing_required_regimes"] == ["uniform61"]
    insufficient = assess({"results": rows("historical_policy") + rows("uniform61", 5)})
    assert insufficient["passed"] is False
    assert insufficient["regimes"]["uniform61"]["conditions"]["at_least_six_common_origins"] is False
