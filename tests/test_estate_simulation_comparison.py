"""Controls must share inputs and retain failed/unliquidated outcomes."""
from copy import deepcopy
from dataclasses import replace

import pytest

from simulation.comparison import compare_replays
from simulation.engine import ReplayError, replay
from simulation.fixtures import fixture

SEEDS = (7, 23, 41)


def suite(data=None, policy=None, costs=None):
    default_data, default_policy, default_costs = fixture()
    data, policy, costs = data or default_data, policy or default_policy, costs or default_costs
    result = [replay(data, replace(policy, strategy=s, run_id="fixture-" + s), costs)
              for s in ("hold", "rotate", "cheapest_hold", "cash")]
    result += [replay(data, replace(policy, strategy="random_hold", random_seed=seed,
                                  run_id="fixture-random-" + str(seed)), costs) for seed in SEEDS]
    return result


def test_seeded_control_is_order_independent_score_blind_and_resumable():
    data, policy, costs = fixture()
    policy = replace(policy, strategy="random_hold", random_seed=7)
    original = replay(data, policy, costs)
    changed = deepcopy(data)
    changed["observations"].reverse()
    changed["quotes"].reverse()
    for row in changed["observations"]:
        row["score"] = -row["score"] * 1000
    assert replay(changed, policy, costs)["ledger"] == original["ledger"]
    partial = replay(data, policy, costs, stop_after_days=93)
    resumed = replay(data, policy, costs, checkpoint=partial["checkpoint"])
    assert resumed["ledger"] == original["ledger"]
    assert resumed["summary"] == original["summary"]
    assert sum(r["kind"] == "contracted" and r.get("side") == "buy" for r in original["ledger"]) == 1
    with pytest.raises(ReplayError, match="different inputs"):
        replay(data, replace(policy, random_seed=23), costs, checkpoint=partial["checkpoint"])


def test_random_control_does_not_use_future_or_unaffordable_candidates():
    data, policy, costs = fixture()
    policy = replace(policy, strategy="random_hold", random_seed=7)
    original = replay(data, policy, costs)
    for row in list(data["observations"]):
        late = {**row, "asset_id": "FUTURE", "observation_id": "future-" + row["observation_id"],
                "available_at": "2025-01-01T00:00:00+09:00", "score": 1e10}
        expensive = {**row, "asset_id": "EXPENSIVE", "observation_id": "expensive-" + row["observation_id"],
                     "price_krw": policy.initial_cash_krw}
        data["observations"] += [late, expensive]
    assert replay(data, policy, costs)["ledger"] == original["ledger"]


@pytest.mark.parametrize("seed", [None, True, -1, 2**32, 7.0])
def test_random_control_requires_explicit_bounded_seed(seed):
    _, policy, _ = fixture()
    with pytest.raises(ReplayError, match="seed"):
        replace(policy, strategy="random_hold", random_seed=seed)


def test_complete_comparison_reconciles_cash_and_reports_all_seeds():
    results = suite()
    report = compare_replays(results, random_seeds=SEEDS)
    assert report["research_status"] == "synthetic_validation_only"
    assert report["all_runs_completed"] and report["all_runs_settled"]
    assert len(report["rows"]) == 7
    assert report["random_control"]["settled_runs"] == 3
    assert report["random_control"]["median_return_pct"] is not None
    hold, rotate = results[0]["summary"], results[1]["summary"]
    expected = (rotate["final_cash_krw"] - hold["final_cash_krw"]) / hold["initial_cash_krw"] * 100
    assert report["paired_differences_pp"]["rotate_minus_hold"] == pytest.approx(expected)
    assert compare_replays(list(reversed(results)), random_seeds=tuple(reversed(SEEDS))) == report


@pytest.mark.parametrize("field,value", [("initial_cash_krw", 101_000_000), ("reserve_krw", 6_000_000),
                                        ("settlement_delay_days", 4), ("exit_signal_date", "2024-07-02")])
def test_comparison_rejects_different_budget_or_clock(field, value):
    results = suite()
    data, policy, costs = fixture()
    results[0] = replay(data, replace(policy, strategy="hold", run_id="changed", **{field: value}), costs)
    with pytest.raises(ReplayError, match="same input"):
        compare_replays(results, random_seeds=SEEDS)


def test_same_cost_name_cannot_hide_different_fees_or_input_panel():
    results = suite()
    data, policy, costs = fixture()
    results[0] = replay(data, replace(policy, strategy="hold", run_id="changed"),
                        replace(costs, buy_fixed_krw=costs.buy_fixed_krw + 1))
    with pytest.raises(ReplayError, match="same input"):
        compare_replays(results, random_seeds=SEEDS)
    data["observations"][0]["score"] += 0.001
    results[0] = replay(data, replace(policy, strategy="hold", run_id="changed"), costs)
    with pytest.raises(ReplayError, match="same input"):
        compare_replays(results, random_seeds=SEEDS)


def test_missing_or_duplicated_seed_cannot_cherry_pick_winners():
    results = suite()
    with pytest.raises(ReplayError, match="every strategy"):
        compare_replays(results[:-1], random_seeds=SEEDS)
    results[-1] = deepcopy(results[-2])
    with pytest.raises(ReplayError, match="duplicate"):
        compare_replays(results, random_seeds=SEEDS)
    with pytest.raises(ReplayError, match="distinct"):
        compare_replays(suite(), random_seeds=(7, 7, 41))


def test_no_exit_keeps_denominator_and_suppresses_ensemble_returns():
    data, _, _ = fixture()
    data["quotes"] = [q for q in data["quotes"] if q["side"] == "buy"]
    report = compare_replays(suite(data=data), random_seeds=SEEDS)
    assert report["all_runs_completed"] and not report["all_runs_settled"]
    assert report["random_control"]["unsettled_or_incomplete_runs"] == 3
    assert report["random_control"]["expected_runs"] == 3
    assert report["random_control"]["median_return_pct"] is None
    assert report["paired_differences_pp"]["hold_minus_random_median"] is None
    assert [r for r in report["rows"] if r["strategy"] == "cash"][0]["liquidated_return_pct"] == 0


def test_one_incomplete_seed_cannot_be_removed_from_ensemble_median():
    results = suite()
    data, policy, costs = fixture()
    results[-1] = replay(data, replace(policy, strategy="random_hold", random_seed=41,
                                      run_id="partial"), costs, stop_after_days=10)
    report = compare_replays(results, random_seeds=SEEDS)
    assert not report["all_runs_completed"]
    assert report["random_control"]["settled_runs"] == 2
    assert report["random_control"]["unsettled_or_incomplete_runs"] == 1
    assert report["random_control"]["median_return_pct"] is None


def test_unfilled_purchase_is_retained_as_cash_outcome_with_coverage_flag():
    data, _, _ = fixture()
    data["quotes"] = []
    report = compare_replays(suite(data=data), random_seeds=SEEDS)
    assert report["random_control"]["expected_runs"] == 3
    assert report["random_control"]["no_purchase_runs"] == 3
    assert report["random_control"]["median_return_pct"] == 0
    assert all(r["no_purchase"] for r in report["rows"] if r["strategy"] != "cash")


def test_report_cannot_invent_returns_or_modify_checkpoint_cash():
    results = suite()
    results[0]["summary"]["liquidated_return"] = 100
    with pytest.raises(ReplayError, match="reconciled"):
        compare_replays(results, random_seeds=SEEDS)
    results = suite()
    results[0]["checkpoint"]["state"]["cash_krw"] += 1
    with pytest.raises(ReplayError, match="checksum"):
        compare_replays(results, random_seeds=SEEDS)


def test_historical_random_control_still_requires_live_storage():
    data, policy, costs = fixture()
    data["kind"] = "historical_research"
    data["provenance"]["availability_mode"] = "reconstructed_lag_scenario"
    for row in data["observations"]:
        row["evidence_kind"] = "lag_assumption"
    for row in data["quotes"]:
        row["evidence_kind"] = "transaction_proxy"
    with pytest.raises(ReplayError, match="live storage"):
        replay(data, replace(policy, strategy="random_hold", random_seed=7), costs)
