"""One-home, cash-only counterfactual replay. This module does not collect or train.

Prices are scenario quotes, never proof that a particular home could be traded.
All decisions use public information available by the Korean calendar-day close.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, ROUND_CEILING
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Protocol

KST = timezone(timedelta(hours=9))
MAX_INPUT_BYTES = 5_000_000
MAX_ROWS = 10_000
MAX_DAYS = 3660


class ReplayError(ValueError):
    """Invalid inputs, missing storage verification, or failed replay invariant."""


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode()


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ReplayError("Availability timestamps require a timezone")
    return parsed.astimezone(timezone.utc)


def close(day: date) -> datetime:
    return datetime.combine(day, time.max, KST).astimezone(timezone.utc)


def won(value: Any, name: str, *, positive: bool = False) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < int(positive):
        raise ReplayError(f"{name} must be {'positive' if positive else 'nonnegative'} integer KRW")
    return value


def charge(amount: int, rate: str) -> int:
    return int((Decimal(amount) * Decimal(rate)).to_integral_value(rounding=ROUND_CEILING))


@dataclass(frozen=True)
class Costs:
    """Explicit sensitivity assumptions, not a Korean tax calculation."""
    scenario_id: str
    acquisition_tax_rate: str
    buy_broker_rate: str
    sell_broker_rate: str
    gain_tax_rate: str
    annual_carry_rate: str
    buy_fixed_krw: int
    sell_fixed_krw: int
    moving_krw: int

    def __post_init__(self):
        if not self.scenario_id:
            raise ReplayError("Name the cost scenario")
        for name in ("acquisition_tax_rate", "buy_broker_rate", "sell_broker_rate",
                     "gain_tax_rate", "annual_carry_rate"):
            value = Decimal(str(getattr(self, name)))
            if not value.is_finite() or not 0 <= value <= 1:
                raise ReplayError(f"Invalid scenario rate: {name}")
            object.__setattr__(self, name, str(value))
        for name in ("buy_fixed_krw", "sell_fixed_krw", "moving_krw"):
            won(getattr(self, name), name)

    def buy(self, price: int) -> dict:
        tax = charge(price, self.acquisition_tax_rate)
        broker = charge(price, self.buy_broker_rate)
        fees = tax + broker + self.buy_fixed_krw + self.moving_krw
        return {"price_krw": price, "fees_krw": fees, "total_krw": price + fees,
                "tax_krw": tax, "broker_krw": broker}

    def sell(self, price: int, holding: dict) -> dict:
        broker = charge(price, self.sell_broker_rate)
        # This deliberately named scenario basis is not a statutory deduction rule.
        gain = max(0, price - broker - self.sell_fixed_krw - holding["acquisition_total_krw"])
        tax = charge(gain, self.gain_tax_rate)
        fees = broker + tax + self.sell_fixed_krw
        return {"price_krw": price, "fees_krw": fees, "net_krw": price - fees,
                "tax_krw": tax, "broker_krw": broker}


@dataclass(frozen=True)
class Policy:
    run_id: str
    start: str
    end: str
    decision_dates: tuple[str, ...]
    strategy: str
    initial_cash_krw: int
    reserve_krw: int
    purchase_price_cap_krw: int
    execution_delay_days: int
    settlement_delay_days: int
    order_ttl_days: int
    min_holding_days: int
    max_observation_age_days: int
    max_quote_age_days: int
    switch_score_margin: float
    exit_signal_date: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "decision_dates", tuple(self.decision_dates))
        start, end = date.fromisoformat(self.start), date.fromisoformat(self.end)
        if end < start or (end - start).days > MAX_DAYS:
            raise ReplayError("Replay date range is invalid or too large")
        if self.strategy not in {"hold", "rotate", "cheapest_hold", "cash"}:
            raise ReplayError("Unknown preregistered strategy")
        if not self.run_id or len(self.run_id) > 100:
            raise ReplayError("A bounded run ID is required")
        for name in ("initial_cash_krw", "purchase_price_cap_krw"):
            won(getattr(self, name), name, positive=True)
        won(self.reserve_krw, "reserve_krw")
        if self.reserve_krw > self.initial_cash_krw:
            raise ReplayError("Reserve exceeds initial cash")
        for name in ("execution_delay_days", "settlement_delay_days", "order_ttl_days",
                     "max_observation_age_days", "max_quote_age_days"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 1:
                raise ReplayError(f"{name} must be a positive integer")
        if self.order_ttl_days < self.execution_delay_days:
            raise ReplayError("Order expires before its execution delay")
        if type(self.min_holding_days) is not int or self.min_holding_days < 0:
            raise ReplayError("Invalid minimum holding period")
        if not math.isfinite(self.switch_score_margin) or self.switch_score_margin < 0:
            raise ReplayError("Invalid switch margin")
        if list(self.decision_dates) != sorted(set(self.decision_dates)):
            raise ReplayError("Decision dates must be unique and ordered")
        for value in self.decision_dates + ((self.exit_signal_date,) if self.exit_signal_date else ()):
            if not start <= date.fromisoformat(value) <= end:
                raise ReplayError("Decision or exit date outside replay")


class StorageSession(Protocol):
    """Implemented by the authenticated storage adapter, never by a JSON receipt.

    authorize must do live free-capacity checks and verify the restored input SHA.
    A checkpoint conflict is an error, not permission to overwrite another run.
    """
    def authorize(self, identity: dict) -> dict: ...
    def load_checkpoint(self, run_id: str) -> dict | None: ...
    def save_checkpoint(self, run_id: str, state: dict, expected_version: int) -> int: ...


def validate_dataset(data: dict) -> None:
    if data.get("schema_version") != 1 or data.get("kind") not in {"synthetic_fixture", "historical_research"}:
        raise ReplayError("Unknown dataset kind or schema")
    if len(canonical(data)) > MAX_INPUT_BYTES:
        raise ReplayError("Bounded input exceeds 5 MB; use a smaller restored feature panel")
    observations, quotes = data.get("observations", []), data.get("quotes", [])
    if len(observations) + len(quotes) > MAX_ROWS:
        raise ReplayError("Input exceeds the bounded 10,000-row panel")
    provenance = data.get("provenance", {})
    if provenance.get("personal_preference_features") != []:
        raise ReplayError("Personal-preference features must be explicitly excluded")
    mode = provenance.get("availability_mode")
    if mode not in {"strict_observed", "reconstructed_lag_scenario", "synthetic"}:
        raise ReplayError("Declare the historical-availability evidence mode")
    if data["kind"] == "historical_research" and mode == "synthetic":
        raise ReplayError("Historical data cannot claim synthetic availability")
    if data["kind"] == "synthetic_fixture" and mode != "synthetic":
        raise ReplayError("Synthetic data must be explicitly labelled")
    for rows, key in ((observations, "observation_id"), (quotes, "quote_id")):
        ids = [r[key] for r in rows]
        if len(ids) != len(set(ids)):
            raise ReplayError("Duplicate observation or quote IDs")
        for row in rows:
            if not row.get("asset_id") or not row.get("source_id"):
                raise ReplayError("Each row needs asset and source identifiers")
            source_hash = row.get("source_sha256", "")
            if len(source_hash) != 64 or any(c not in "0123456789abcdef" for c in source_hash):
                raise ReplayError("Each row needs a source SHA-256")
            won(row["price_krw"], "price_krw", positive=True)
            known = timestamp(row["available_at"])
            if timestamp(row["observed_at"]) > known:
                raise ReplayError("Source observation cannot follow its availability")
            if data["kind"] == "historical_research" and row.get("evidence_kind") == "synthetic":
                raise ReplayError("Synthetic rows cannot enter a historical panel")
    for row in observations:
        if not math.isfinite(row["score"]) or not row.get("model_version"):
            raise ReplayError("A finite score and immutable model version are required")
        for key in ("max_feature_available_at", "max_training_label_available_at"):
            timestamp(row[key])
        if mode == "strict_observed" and row.get("evidence_kind") != "observed_archive":
            raise ReplayError("Strict replay requires archived historical observations")
    for row in quotes:
        if row["side"] not in {"buy", "sell"} or timestamp(row["valid_until"]) < timestamp(row["available_at"]):
            raise ReplayError("Invalid quote side or expiry")
        if row.get("evidence_kind") not in {"synthetic", "archived_offer", "transaction_proxy"}:
            raise ReplayError("Declare execution evidence or transaction-proxy status")


def observations_asof(data: dict, day: date, max_age_days: int) -> dict[str, dict]:
    result = {}
    cutoff = close(day)
    for row in data["observations"]:
        available = max(timestamp(row[key]) for key in
                        ("available_at", "max_feature_available_at", "max_training_label_available_at"))
        observed = timestamp(row["observed_at"])
        if available > cutoff or (cutoff - observed).total_seconds() > max_age_days * 86400:
            continue
        old = result.get(row["asset_id"])
        # A newly published correction supersedes the same vintage, not newer observations.
        order = (observed, timestamp(row["available_at"]), row["observation_id"])
        if old is None or order > (timestamp(old["observed_at"]), timestamp(old["available_at"]), old["observation_id"]):
            result[row["asset_id"]] = row
    return result


def _new_order(side: str, asset_id: str, day: date, p: Policy, *, next_asset: str | None = None) -> dict:
    return {"side": side, "asset_id": asset_id, "signal_date": str(day),
            "due": str(day + timedelta(days=p.execution_delay_days)),
            "expires": str(day + timedelta(days=p.order_ttl_days)), "next_asset": next_asset}


def _event(s: dict, day: date, kind: str, **values) -> None:
    s["ledger"].append({"date": str(day), "kind": kind, **values})


def _choose(s: dict, data: dict, day: date, p: Policy, c: Costs) -> None:
    holding = s["holding"]
    if p.strategy == "cash" or (holding and p.strategy in {"hold", "cheapest_hold"}):
        return
    obs = observations_asof(data, day, p.max_observation_age_days)
    budget = s["cash_krw"]
    current = obs.get(holding["asset_id"]) if holding else None
    if holding:
        if current is None or (day - date.fromisoformat(holding["settled_date"])).days < p.min_holding_days:
            _event(s, day, "decision_skipped", reason="missing_current_observation_or_minimum_hold")
            return
        budget += c.sell(current["price_krw"], holding)["net_krw"]
    candidates = [r for r in obs.values() if r["price_krw"] <= p.purchase_price_cap_krw
                  and c.buy(r["price_krw"])["total_krw"] + p.reserve_krw <= budget]
    candidates.sort(key=(lambda r: (r["price_krw"], r["asset_id"])) if p.strategy == "cheapest_hold"
                    else (lambda r: (-r["score"], r["asset_id"])))
    chosen = candidates[0] if candidates else None
    _event(s, day, "decision", strategy=p.strategy, selected_asset=chosen["asset_id"] if chosen else None,
           eligible_count=len(candidates), available_budget_krw=budget,
           observation_id=chosen["observation_id"] if chosen else None,
           model_version=chosen["model_version"] if chosen else None)
    if chosen is None:
        return
    if holding:
        if chosen["asset_id"] == holding["asset_id"] or chosen["score"] <= current["score"] + p.switch_score_margin:
            return
        s["order"] = _new_order("sell", holding["asset_id"], day, p, next_asset=chosen["asset_id"])
    else:
        s["order"] = _new_order("buy", chosen["asset_id"], day, p)
    _event(s, day, "order", **s["order"])


def _settle(s: dict, day: date, p: Policy) -> None:
    pending = s["settlement"]
    if pending is None or pending["date"] > str(day):
        return
    if pending["side"] == "buy":
        if s["holding"] is not None:
            raise ReplayError("A second home cannot settle")
        s["holding"] = {"asset_id": pending["asset_id"], "purchase_price_krw": pending["price_krw"],
                        "acquisition_total_krw": pending["total_krw"], "settled_date": str(day)}
    else:
        s["cash_krw"] += pending["net_krw"]
        s["holding"] = None
        if pending["next_asset"] and (p.exit_signal_date is None or str(day) < p.exit_signal_date):
            s["order"] = _new_order("buy", pending["next_asset"], day, p)
            _event(s, day, "order", origin_signal_date=pending["signal_date"], **s["order"])
    _event(s, day, "settled", side=pending["side"], asset_id=pending["asset_id"], cash_krw=s["cash_krw"])
    s["settlement"] = None


def _fill(s: dict, data: dict, day: date, p: Policy, c: Costs) -> None:
    order = s["order"]
    if order is None or str(day) < order["due"]:
        return
    if str(day) > order["expires"]:
        _event(s, day, "order_expired", side=order["side"], asset_id=order["asset_id"])
        s["order"] = None
        return
    cutoff = close(day)
    quotes = [r for r in data["quotes"] if r["asset_id"] == order["asset_id"] and r["side"] == order["side"]
              and timestamp(r["available_at"]) <= cutoff <= timestamp(r["valid_until"])
              and (cutoff - timestamp(r["observed_at"])).total_seconds() <= p.max_quote_age_days * 86400]
    # Use a deterministic latest known quote; do not search future quotes or cheapest future price.
    quotes.sort(key=lambda r: (timestamp(r["observed_at"]), timestamp(r["available_at"]), r["quote_id"]), reverse=True)
    if not quotes:
        return
    quote = quotes[0]
    if order["side"] == "buy":
        amount = c.buy(quote["price_krw"])
        if quote["price_krw"] > p.purchase_price_cap_krw or amount["total_krw"] + p.reserve_krw > s["cash_krw"]:
            _event(s, day, "fill_rejected", reason="cash_or_purchase_price_cap", quote_id=quote["quote_id"])
            return
        s["cash_krw"] -= amount["total_krw"]
    else:
        if s["holding"] is None or s["holding"]["asset_id"] != order["asset_id"]:
            raise ReplayError("Cannot sell an unowned home")
        amount = c.sell(quote["price_krw"], s["holding"])
        if amount["net_krw"] < 0:
            raise ReplayError("Sale proceeds cannot cover the declared scenario costs")
    s["total_cost_krw"] += amount["fees_krw"]
    s["settlement"] = {**order, **amount, "date": str(day + timedelta(days=p.settlement_delay_days))}
    _event(s, day, "contracted", side=order["side"], asset_id=order["asset_id"],
           signal_date=order["signal_date"], settlement_date=s["settlement"]["date"],
           quote_id=quote["quote_id"], execution_evidence=quote["evidence_kind"], **amount)
    s["order"] = None


def _day(s: dict, data: dict, day: date, p: Policy, c: Costs) -> None:
    if s["holding"] is not None:
        # Charge elapsed ownership days; acquisition settlement day has no carry charge.
        fee = charge(s["holding"]["purchase_price_krw"], str(Decimal(c.annual_carry_rate) / Decimal(365)))
        if fee > s["cash_krw"]:
            s["failure"] = "holding_cost_cash_shortfall"
            _event(s, day, "infeasible", reason=s["failure"], required_krw=fee, cash_krw=s["cash_krw"])
            return
        s["cash_krw"] -= fee
        s["total_cost_krw"] += fee
    _settle(s, day, p)
    at_exit = p.exit_signal_date and str(day) >= p.exit_signal_date
    if at_exit and s["settlement"] is None:
        if s["order"] and s["order"]["side"] == "buy":
            _event(s, day, "order_cancelled", reason="predeclared_exit_window")
            s["order"] = None
        old_enough = (s["holding"] is not None and
                      (day - date.fromisoformat(s["holding"]["settled_date"])).days >= p.min_holding_days)
        if old_enough and s["order"] is None and not s["exit_order_sent"]:
            s["order"] = _new_order("sell", s["holding"]["asset_id"], day, p)
            s["exit_order_sent"] = True
            _event(s, day, "order", reason="predeclared_exit_window", **s["order"])
    elif str(day) in p.decision_dates:
        if s["order"] is None and s["settlement"] is None:
            _choose(s, data, day, p, c)
        else:
            _event(s, day, "decision_skipped", reason="pending_contract_or_settlement")
    if s["settlement"] is None:
        _fill(s, data, day, p, c)
    if s["cash_krw"] < 0:
        raise ReplayError("Negative cash is forbidden")


def _summary(s: dict, data: dict, p: Policy, c: Costs, day: date) -> dict:
    liquid = (s["holding"] is None and s["settlement"] is None and not s["failure"]
              and s["next_date"] > p.end)
    mark = None
    if s["holding"]:
        row = observations_asof(data, day, p.max_observation_age_days).get(s["holding"]["asset_id"])
        if row:
            mark = s["cash_krw"] + c.sell(row["price_krw"], s["holding"])["net_krw"]
    return {"research_status": "synthetic_validation_only" if data["kind"] == "synthetic_fixture" else "retrospective_scenario",
            "strategy": p.strategy, "start": p.start, "end": p.end,
            "initial_cash_krw": p.initial_cash_krw, "reserve_krw": p.reserve_krw,
            "purchase_price_cap_krw": p.purchase_price_cap_krw, "cost_scenario_id": c.scenario_id,
            "availability_mode": data["provenance"]["availability_mode"],
            "cost_basis": "explicit_sensitivity_scenario_not_statutory_tax",
            "final_cash_krw": s["cash_krw"], "total_cost_krw": s["total_cost_krw"],
            "holding": s["holding"], "unsettled_contract": s["settlement"],
            "pending_order": s["order"], "failure": s["failure"],
            "liquidated_return": s["cash_krw"] / p.initial_cash_krw - 1 if liquid else None,
            "estimated_net_liquidation_value_krw": mark,
            "mark_is_executable_sale": False,
            "contract_count": sum(r["kind"] == "contracted" for r in s["ledger"]),
            "completed": s["next_date"] > p.end and not s["failure"]}


def replay(data: dict, policy: Policy, costs: Costs, *, session: StorageSession | None = None,
           checkpoint: dict | None = None, stop_after_days: int | None = None,
           checkpoint_every_days: int = 30) -> dict:
    """Run a bounded panel, requiring live verified storage for historical input.

    Synthetic callers may inspect/resume the returned checkpoint in memory.
    Historical checkpoints are loaded/saved only through the authenticated session.
    The exact panel, rules, costs and engine must match a resumed checkpoint.
    """
    validate_dataset(data)
    if data["kind"] == "synthetic_fixture" and session is not None:
        raise ReplayError("Synthetic validation must not write to a historical storage session")
    if checkpoint_every_days < 1 or (stop_after_days is not None and stop_after_days < 1):
        raise ReplayError("Checkpoint and stop intervals must be positive")
    identity = {"run_id": policy.run_id, "input_sha256": digest(data),
                "policy_sha256": digest({"policy": asdict(policy), "costs": asdict(costs)}),
                "engine_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    stored_version = 0
    if data["kind"] == "historical_research":
        if session is None or checkpoint is not None:
            raise ReplayError("Historical replay requires a live storage session and backend-only checkpoint restore")
        verified = session.authorize(identity)
        if verified.get("ready") is not True or verified.get("identity") != identity:
            raise ReplayError("Live storage verification did not authorize these exact inputs")
        saved = session.load_checkpoint(policy.run_id)
        if saved:
            checkpoint, stored_version = saved["state"], saved["version"]
    if checkpoint:
        if checkpoint.get("identity") != identity:
            raise ReplayError("Checkpoint belongs to different inputs, rules, costs or engine")
        if digest(checkpoint.get("protocol")) != identity["policy_sha256"]:
            raise ReplayError("Checkpoint policy/cost payload checksum mismatch")
        state = json.loads(canonical(checkpoint["state"]))
        if checkpoint.get("state_sha256") != digest(state):
            raise ReplayError("Checkpoint state checksum mismatch")
        if not policy.start <= state["next_date"] <= str(date.fromisoformat(policy.end) + timedelta(days=1)):
            raise ReplayError("Checkpoint resume date is outside the declared range")
    else:
        state = {"next_date": policy.start, "cash_krw": policy.initial_cash_krw,
                 "holding": None, "order": None, "settlement": None,
                 "total_cost_krw": 0, "failure": None, "ledger": [], "exit_order_sent": False}
    processed = 0
    last_day = date.fromisoformat(state["next_date"]) - timedelta(days=1)
    while state["next_date"] <= policy.end and state["failure"] is None:
        day = date.fromisoformat(state["next_date"])
        _day(state, data, day, policy, costs)
        state["next_date"] = str(day + timedelta(days=1))
        last_day = day
        processed += 1
        if session and processed % checkpoint_every_days == 0:
            saved = {"identity": identity, "protocol": {"policy": asdict(policy), "costs": asdict(costs)},
                     "state": state, "state_sha256": digest(state)}
            stored_version = session.save_checkpoint(policy.run_id, saved, stored_version)
        if stop_after_days is not None and processed >= stop_after_days:
            break
    saved = {"identity": identity, "protocol": {"policy": asdict(policy), "costs": asdict(costs)},
             "state": state, "state_sha256": digest(state)}
    if session:
        stored_version = session.save_checkpoint(policy.run_id, saved, stored_version)
    return {"identity": identity, "summary": _summary(state, data, policy, costs, last_day),
            "ledger": state["ledger"], "checkpoint": saved, "backend_checkpoint_version": stored_version}
