"""Clock, money, execution and persistence invariants using invented prices only."""
from copy import deepcopy
from dataclasses import replace
from datetime import date

import pytest

from simulation.engine import ReplayError, observations_asof, replay, validate_dataset
from simulation.fixtures import fixture


def historical_fixture():
    data, policy, costs = fixture()
    data["kind"] = "historical_research"
    data["provenance"]["availability_mode"] = "reconstructed_lag_scenario"
    for row in data["observations"]:
        row["evidence_kind"] = "lag_assumption"
    for row in data["quotes"]:
        row["evidence_kind"] = "transaction_proxy"
    return data, policy, costs


class FakeStorage:
    """A test double, not proof of live storage availability."""
    def __init__(self, ready=True):
        self.saved = None
        self.ready = ready
        self.saves = 0

    def authorize(self, identity):
        return {"ready": self.ready, "identity": identity}

    def load_checkpoint(self, run_id):
        return deepcopy(self.saved)

    def save_checkpoint(self, run_id, state, expected_version):
        current = self.saved["version"] if self.saved else 0
        if current != expected_version:
            raise ReplayError("409 checkpoint conflict")
        self.saved = {"version": current + 1, "state": deepcopy(state)}
        self.saves += 1
        return current + 1


def test_future_rows_and_prices_cannot_change_past_decisions_or_trades():
    data, policy, costs = fixture()
    original = replay(data, policy, costs)
    modified = deepcopy(data)
    for row in modified["observations"] + modified["quotes"]:
        if row["available_at"] > "2024-04-30":
            row["price_krw"] *= 20
            if "score" in row:
                row["score"] = 1e9
    updated = replay(modified, policy, costs)
    prefix = lambda result: [r for r in result["ledger"] if r["date"] <= "2024-04-30"]
    assert prefix(original) == prefix(updated)


@pytest.mark.parametrize("field", ["available_at", "max_feature_available_at", "max_training_label_available_at"])
def test_late_publication_features_and_immature_labels_are_excluded(field):
    data, _, _ = fixture()
    late = deepcopy(data["observations"][0])
    late.update(observation_id="late", asset_id="FUTURE_WINNER", score=1e9)
    late[field] = "2024-02-01T09:00:00+09:00"
    data["observations"].append(late)
    known = observations_asof(data, date(2024, 1, 1), 120)
    assert "FUTURE_WINNER" not in known
    assert "FUTURE_WINNER" in observations_asof(data, date(2024, 2, 1), 120)


def test_budget_includes_acquisition_cost_and_cash_reserve():
    data, policy, costs = fixture()
    limit = costs.buy(60_000_000)["total_krw"] + policy.reserve_krw - 1
    result = replay(data, replace(policy, initial_cash_krw=limit), costs)
    assert result["summary"]["contract_count"] == 0
    assert result["summary"]["final_cash_krw"] == limit


def test_execution_price_is_rechecked_against_actual_cash():
    data, policy, costs = fixture()
    for row in data["quotes"]:
        if row["side"] == "buy":
            row["price_krw"] = 200_000_000
    result = replay(data, policy, costs)
    assert result["summary"]["contract_count"] == 0
    assert any(r["kind"] == "fill_rejected" for r in result["ledger"])
    assert result["summary"]["final_cash_krw"] == policy.initial_cash_krw


def test_rotation_waits_for_sale_cash_and_never_uses_two_homes():
    data, policy, costs = fixture()
    result = replay(data, policy, costs)
    trades = [r for r in result["ledger"] if r["kind"] == "contracted"]
    assert [r["side"] for r in trades] == ["buy", "sell", "buy", "sell"]
    assert all(r["date"] > r["signal_date"] for r in trades)
    assert trades[2]["date"] > trades[1]["settlement_date"]
    assert all(r["cash_krw"] >= 0 for r in result["ledger"] if "cash_krw" in r)
    assert result["summary"]["holding"] is None


def test_cash_conservation_reconciles_round_trips_and_all_costs():
    data, policy, costs = fixture()
    result = replay(data, policy, costs)
    trades = [r for r in result["ledger"] if r["kind"] == "contracted"]
    gain = sum(r["price_krw"] * (1 if r["side"] == "sell" else -1) for r in trades)
    assert result["summary"]["final_cash_krw"] == policy.initial_cash_krw + gain - result["summary"]["total_cost_krw"]
    zero = replace(costs, acquisition_tax_rate="0", buy_broker_rate="0", sell_broker_rate="0",
                   gain_tax_rate="0", annual_carry_rate="0", buy_fixed_krw=0, sell_fixed_krw=0, moving_krw=0)
    free = replay(data, policy, zero)
    assert free["summary"]["final_cash_krw"] > result["summary"]["final_cash_krw"]
    assert free["summary"]["total_cost_krw"] == 0


def test_missing_exit_is_unliquidated_and_stale_mark_is_not_fabricated():
    data, policy, costs = fixture()
    data["quotes"] = [r for r in data["quotes"] if r["side"] == "buy"]
    data["observations"] = [r for r in data["observations"] if r["observed_at"] < "2024-02"]
    result = replay(data, replace(policy, strategy="hold", max_observation_age_days=30), costs)
    assert result["summary"]["holding"] is not None
    assert result["summary"]["liquidated_return"] is None
    assert result["summary"]["estimated_net_liquidation_value_krw"] is None


def test_partial_replay_resumes_exactly_and_rejects_modified_sources():
    data, policy, costs = fixture()
    whole = replay(data, policy, costs)
    part = replay(data, policy, costs, stop_after_days=93)
    resumed = replay(data, policy, costs, checkpoint=part["checkpoint"])
    assert resumed["summary"] == whole["summary"]
    assert resumed["ledger"] == whole["ledger"]
    data["observations"][0]["score"] += .01
    with pytest.raises(ReplayError, match="different inputs"):
        replay(data, policy, costs, checkpoint=part["checkpoint"])


def test_checkpoint_checksum_detects_cash_or_ledger_tampering():
    data, policy, costs = fixture()
    part = replay(data, policy, costs, stop_after_days=3)
    part["checkpoint"]["state"]["cash_krw"] += 100
    with pytest.raises(ReplayError, match="checksum"):
        replay(data, policy, costs, checkpoint=part["checkpoint"])


def test_historical_execution_fails_closed_without_live_storage():
    data, policy, costs = historical_fixture()
    with pytest.raises(ReplayError, match="live storage"):
        replay(data, policy, costs)
    session = FakeStorage(ready=False)
    with pytest.raises(ReplayError, match="verification"):
        replay(data, policy, costs, session=session)
    assert session.saves == 0


def test_mocked_backend_resume_preserves_results_and_conflicts_stop():
    data, policy, costs = historical_fixture()
    session = FakeStorage()
    part = replay(data, policy, costs, session=session, stop_after_days=93)
    assert part["backend_checkpoint_version"] > 0
    resumed = replay(data, policy, costs, session=session)
    assert resumed["ledger"] == replay(data, policy, costs, session=FakeStorage())["ledger"]
    class Conflicting(FakeStorage):
        def save_checkpoint(self, run_id, state, expected_version):
            raise ReplayError("409 checkpoint conflict")
    with pytest.raises(ReplayError, match="409"):
        replay(data, policy, costs, session=Conflicting())


def test_personal_preferences_and_claimed_strict_reconstruction_are_rejected():
    data, _, _ = fixture()
    data["provenance"]["personal_preference_features"] = ["private_commute_preference"]
    with pytest.raises(ReplayError, match="Personal"):
        validate_dataset(data)
    data, _, _ = historical_fixture()
    data["provenance"]["availability_mode"] = "strict_observed"
    with pytest.raises(ReplayError, match="archived"):
        validate_dataset(data)


def test_carry_shortfall_stays_visible_instead_of_inventing_credit():
    data, policy, costs = fixture()
    costs = replace(costs, annual_carry_rate="1")
    result = replay(data, replace(policy, initial_cash_krw=62_000_000, reserve_krw=0, strategy="hold"), costs)
    assert result["summary"]["failure"] == "holding_cost_cash_shortfall"
    assert result["summary"]["liquidated_return"] is None
    assert result["summary"]["completed"] is False


def test_no_same_day_execution_or_settlement_is_allowed():
    _, policy, _ = fixture()
    with pytest.raises(ReplayError):
        replace(policy, execution_delay_days=0)
    with pytest.raises(ReplayError):
        replace(policy, settlement_delay_days=0)


def test_terminal_exit_respects_minimum_hold_and_partial_return_is_unreported():
    data, policy, costs = fixture()
    partial = replay(data, policy, costs, stop_after_days=1)
    assert partial["summary"]["liquidated_return"] is None
    result = replay(data, replace(policy, strategy="hold", min_holding_days=366), costs)
    assert result["summary"]["holding"] is not None
    assert result["summary"]["liquidated_return"] is None
    assert all(r.get("side") != "sell" for r in result["ledger"] if r["kind"] == "contracted")
