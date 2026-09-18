"""Compare already-produced replays; never collect, replay or authorize storage.

All declared strategies/seeds must be supplied, even when a run cannot exit.
This checks comparison consistency, not the truth of the source observations.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date
import math
from statistics import median

from .engine import Costs, Policy, ReplayError, digest

STRATEGIES = ("hold", "rotate", "cheapest_hold", "cash")


def _checked_row(result: dict) -> tuple[dict, dict]:
    checkpoint, identity, summary = result["checkpoint"], result["identity"], result["summary"]
    protocol, state = checkpoint["protocol"], checkpoint["state"]
    policy, costs = Policy(**protocol["policy"]), Costs(**protocol["costs"])
    if (checkpoint["identity"] != identity or identity["run_id"] != policy.run_id
            or digest(protocol) != identity["policy_sha256"]
            or digest(state) != checkpoint["state_sha256"] or result["ledger"] != state["ledger"]):
        raise ReplayError("Comparison input checkpoint or policy checksum mismatch")
    for key in ("input_sha256", "engine_sha256"):
        value = identity.get(key, "")
        if len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ReplayError("Comparison requires exact input and engine hashes")
    for key in ("strategy", "random_seed", "start", "end", "initial_cash_krw", "reserve_krw", "purchase_price_cap_krw"):
        if summary.get(key) != getattr(policy, key):
            raise ReplayError("Comparison summary differs from its recorded policy")
    for key, state_key in (("final_cash_krw", "cash_krw"), ("total_cost_krw", "total_cost_krw"),
                           ("holding", "holding"), ("unsettled_contract", "settlement"),
                           ("pending_order", "order"), ("failure", "failure")):
        if summary[key] != state[state_key]:
            raise ReplayError("Comparison summary differs from its recorded state")
    if summary["cost_scenario_id"] != costs.scenario_id:
        raise ReplayError("Comparison cost scenario mismatch")
    completed = date.fromisoformat(state["next_date"]) > date.fromisoformat(policy.end) and not state["failure"]
    liquidated = completed and state["holding"] is None and state["settlement"] is None
    expected_return = state["cash_krw"] / policy.initial_cash_krw - 1 if liquidated else None
    actual_return = summary["liquidated_return"]
    if (summary["completed"] != completed or (actual_return is None) != (expected_return is None)
            or (actual_return is not None and (isinstance(actual_return, bool) or not math.isfinite(actual_return)
                or not math.isclose(actual_return, expected_return, rel_tol=0, abs_tol=1e-12)))):
        raise ReplayError("Comparison return is not reconciled to completed settled cash")
    status, availability = summary["research_status"], summary["availability_mode"]
    if (status, availability) not in {
        ("synthetic_validation_only", "synthetic"),
        ("retrospective_scenario", "strict_observed"),
        ("retrospective_scenario", "reconstructed_lag_scenario"),
    }:
        raise ReplayError("Comparison requires explicit consistent research evidence labels")
    shared_policy = asdict(policy)
    for key in ("run_id", "strategy", "random_seed"):
        shared_policy.pop(key)
    contract = {"input_sha256": identity["input_sha256"], "engine_sha256": identity["engine_sha256"],
                "policy": shared_policy, "costs": asdict(costs),
                "research_status": status, "availability_mode": availability}
    buys = sum(r["kind"] == "contracted" and r.get("side") == "buy" for r in state["ledger"])
    return contract, {"run_id": policy.run_id, "strategy": policy.strategy, "random_seed": policy.random_seed,
                      "completed": completed, "fully_settled": liquidated,
                      "failure": state["failure"], "has_holding": state["holding"] is not None,
                      "has_unsettled_contract": state["settlement"] is not None,
                      "has_pending_order": state["order"] is not None,
                      "purchase_contracts": buys,
                      "no_purchase": policy.strategy != "cash" and buys == 0,
                      "final_cash_krw": state["cash_krw"], "total_cost_krw": state["total_cost_krw"],
                      "liquidated_return_pct": None if actual_return is None else 100 * actual_return}


def compare_replays(results: list[dict], *, random_seeds: tuple[int, ...]) -> dict:
    """Require the four controls plus every preregistered random-hold seed.

    Run IDs may differ; the exact panel, budget, costs, clock and engine may not.
    An unfinished or unliquidated seed suppresses the ensemble median. No-purchase
    outcomes remain in the denominator as cash outcomes; they are not dropped.
    The report cannot attest that seed registration preceded outcome inspection.
    """
    if (not 1 <= len(random_seeds) <= 60 or any(type(s) is not int or not 0 <= s < 2**32 for s in random_seeds)
            or len(set(random_seeds)) != len(random_seeds)):
        raise ReplayError("Declare 1 to 60 distinct uint32 random seeds before comparison")
    expected = {(s, None) for s in STRATEGIES} | {("random_hold", s) for s in random_seeds}
    if len(results) != len(expected):
        raise ReplayError("Comparison requires every strategy and declared seed, including failed runs")
    common, rows, seen, run_ids = None, [], set(), set()
    for result in results:
        contract, row = _checked_row(result)
        key = row["strategy"], row["random_seed"]
        if key not in expected or key in seen or row["run_id"] in run_ids:
            raise ReplayError("Comparison has an unexpected or duplicate strategy, seed or run ID")
        if common is not None and contract != common:
            raise ReplayError("Comparison must use the same input, engine, budget, costs and evaluation clock")
        common = contract
        seen.add(key)
        run_ids.add(row["run_id"])
        rows.append(row)
    rows.sort(key=lambda r: (list(STRATEGIES + ("random_hold",)).index(r["strategy"]), r["random_seed"] or 0))
    controls = {r["strategy"]: r["liquidated_return_pct"] for r in rows if r["strategy"] != "random_hold"}
    random_rows = [r for r in rows if r["strategy"] == "random_hold"]
    returns = [r["liquidated_return_pct"] for r in random_rows]
    all_random_settled = all(value is not None for value in returns)
    random_median = median(returns) if all_random_settled else None

    def gap(left, right):
        return None if left is None or right is None else left - right

    return {"schema_version": 1, "kind": "same_conditions_strategy_comparison",
            "research_status": common["research_status"], "availability_mode": common["availability_mode"],
            "input_sha256": common["input_sha256"], "engine_sha256": common["engine_sha256"],
            "comparison_contract_sha256": digest(common), "production_changed": False,
            "all_runs_completed": all(r["completed"] for r in rows),
            "all_runs_settled": all(r["fully_settled"] for r in rows), "rows": rows,
            "random_control": {"declared_seeds": sorted(random_seeds), "expected_runs": len(random_seeds),
                "settled_runs": sum(r["fully_settled"] for r in random_rows),
                "unsettled_or_incomplete_runs": sum(not r["fully_settled"] for r in random_rows),
                "no_purchase_runs": sum(r["no_purchase"] for r in random_rows),
                "median_return_pct": random_median},
            "paired_differences_pp": {
                "rotate_minus_hold": gap(controls["rotate"], controls["hold"]),
                "hold_minus_cheapest": gap(controls["hold"], controls["cheapest_hold"]),
                "hold_minus_cash": gap(controls["hold"], controls["cash"]),
                "hold_minus_random_median": gap(controls["hold"], random_median)},
            "limitations": ["No source-provenance or preregistration attestation from this consistency check.",
                            "Random seeds share one market path; they are not independent historical periods.",
                            "Unsettled outcomes are not zero returns; no-purchase cash outcomes remain included.",
                            "No model promotion, statutory tax calculation or investment-performance claim."]}
