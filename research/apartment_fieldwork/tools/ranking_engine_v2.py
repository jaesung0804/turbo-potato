"""Offline, explicit-weight ranking of already researched property candidates.

No network, model training, personal defaults, or credential use. Scores are
provided by the caller; this module never turns missing evidence into a score.
The caller must name each score's meaning and retain evidence and observation
dates. A current-price model score is not a future-return forecast.

Version 2 only changes interpretation metadata: every result is exploratory,
even when an advertisement was observed. The legacy current_listing_verified
gate is preserved for compatibility and does not verify present availability.
"""

from __future__ import annotations

import argparse
import copy
import json
import math
from pathlib import Path


MIXES = ((1.0, 0.0), (0.75, 0.25), (0.5, 0.5), (0.25, 0.75), (0.0, 1.0))


def number(value, name, *, lo=None, hi=None, allow_none=True):
    if value is None and allow_none:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name}: expected a finite number")
    if not math.isfinite(value) or (lo is not None and value < lo) or (hi is not None and value > hi):
        raise ValueError(f"{name}: outside allowed range")
    return float(value)


def weighted_score(values, weights):
    """No per-candidate weight renormalization and no imputation."""
    if not weights:
        raise ValueError("At least one explicit weight is required")
    checked = {k: number(v, f"weight.{k}", lo=0, hi=1, allow_none=False) for k, v in weights.items()}
    if not math.isclose(sum(checked.values()), 1.0, abs_tol=1e-9):
        raise ValueError("Weights must sum to one")
    contributions, missing = {}, []
    for key, weight in checked.items():
        score = number(values.get(key), key, lo=0, hi=100)
        if weight == 0:
            contributions[key] = 0.0
        elif score is None:
            missing.append(key)
            contributions[key] = None
        else:
            contributions[key] = weight * score
    return {"score": None if missing else sum(contributions.values()), "contributions": contributions, "missing": missing}


def gate(candidate, constraints):
    reasons = []
    if candidate.get("identity_match") != "confirmed":
        reasons.append("identity_not_confirmed")
    if candidate.get("track") != "candidate":
        reasons.append("not_candidate_track")
    if candidate.get("geographic_gate") is not True:
        reasons.append("geographic_gate_unconfirmed_or_failed")
    price = number(candidate.get("reference_price_oku"), "reference_price_oku", lo=0)
    lower = number(constraints.get("price_min_oku"), "price_min_oku", lo=0)
    upper = number(constraints.get("price_max_oku"), "price_max_oku", lo=0)
    if lower is not None and upper is not None and lower > upper:
        raise ValueError("Reversed budget interval")
    if price is None:
        reasons.append("price_missing")
    elif (lower is not None and price < lower) or (upper is not None and price > upper):
        reasons.append("outside_reference_price_range")
    if constraints.get("require_current_listing", True) and not candidate.get("current_listing_verified", False):
        reasons.append("current_listing_not_verified")
    if constraints.get("require_current_listing", True) and candidate.get("reference_price_kind") != "current_listing_asking":
        reasons.append("current_listing_price_not_selected")
    if not candidate.get("price_area_date_linked", False):
        reasons.append("price_area_date_not_linked")
    return reasons


def rank_one(candidates, constraints, living_weight, investment_weight, *, require_both=True):
    weights = {"L0": living_weight, "I0": investment_weight}
    ranked, held = [], []
    for candidate in candidates:
        if not isinstance(candidate.get("id"), str) or not candidate["id"]:
            raise ValueError("Each candidate requires a nonempty id")
        result = weighted_score(candidate, weights)
        reasons = gate(candidate, constraints)
        if require_both:
            reasons.extend(f"missing_{k}" for k in ("L0", "I0") if candidate.get(k) is None)
        reasons.extend(f"missing_{k}" for k in result["missing"])
        row = {"id": candidate["id"], "name": candidate.get("name"), "area_m2": candidate.get("area_m2"), **result}
        row["provisional"] = True
        row["analysis_status"] = "exploratory"
        row["listing_metadata"] = {
            "legacy_current_listing_verified": candidate.get("current_listing_verified"),
            "listing_observed_at": candidate.get("listing_observed_at"),
            "advertiser_confirmed_at": candidate.get("advertiser_confirmed_at"),
            "availability_confirmed_at": candidate.get("availability_confirmed_at"),
            "availability_source": candidate.get("availability_source"),
            "occupancy_claim": candidate.get("occupancy_claim"),
            "occupancy_source": candidate.get("occupancy_source"),
        }
        if reasons:
            row["score"] = None
            row["hold_reasons"] = sorted(set(reasons))
            held.append(row)
        else:
            ranked.append(row)
    ranked.sort(key=lambda r: (-r["score"], r["id"]))
    previous = None
    for position, row in enumerate(ranked, start=1):
        if previous is not None and math.isclose(row["score"], previous["score"], abs_tol=1e-9):
            row["rank"] = previous["rank"]
        else:
            row["rank"] = position
        previous = row
    return {"weights": weights, "analysis_status": "exploratory", "provisional": True, "ranked": ranked, "held": held}


def rank_five(candidates, constraints):
    ids = [c.get("id") for c in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate candidate id")
    return [rank_one(candidates, constraints, *weights) for weights in MIXES]


def feature_sensitivity(candidates, constraints, base_feature_weights, delta=0.10):
    """Change one L0 feature's weight; distribute the remainder proportionally.

    Uses a constant candidate universe and a 50:50 L0/I0 blend. Missing feature
    values hold the row. The output is rubric sensitivity, not model SHAP.
    """
    weighted_score({k: 0.0 for k in base_feature_weights}, base_feature_weights)
    delta = number(delta, "delta", lo=0, hi=1, allow_none=False)
    output = []
    for feature, old_weight in base_feature_weights.items():
        for sign in (-1, 1):
            new_weight = min(1.0, max(0.0, old_weight + sign * delta))
            if old_weight == 1.0:
                continue
            weights = {k: (new_weight if k == feature else v * (1 - new_weight) / (1 - old_weight)) for k, v in base_feature_weights.items()}
            altered = copy.deepcopy(candidates)
            for candidate in altered:
                candidate["L0"] = weighted_score(candidate.get("living_features", {}), weights)["score"]
            output.append({"changed_feature": feature, "feature_weights": weights, "ranking": rank_one(altered, constraints, 0.5, 0.5)})
    return output


def crossover_weights(candidates, constraints):
    """L0 weight at which two eligible candidates have equal mixed scores."""
    eligible = [c for c in candidates if not gate(c, constraints) and c.get("L0") is not None and c.get("I0") is not None]
    results = []
    for i, left in enumerate(eligible):
        for right in eligible[i + 1:]:
            a = number(left["L0"], "L0", lo=0, hi=100) - number(right["L0"], "L0", lo=0, hi=100)
            b = number(left["I0"], "I0", lo=0, hi=100) - number(right["I0"], "I0", lo=0, hi=100)
            if abs(a - b) < 1e-12:
                continue
            weight = -b / (a - b)
            if 0 <= weight <= 1:
                results.append({"left": left["id"], "right": right["id"], "living_weight": weight})
    return results


def unknown_feature_bounds(candidates, constraints, additional_living_weight, *, living_weight=0.5):
    """Decision bounds for an unobserved feature, never an imputed observation.

    Assume the new feature's utility may lie anywhere in [0, 100]. The bounds
    show what checking it could change. They are not statistical confidence
    intervals and do not create a central score or a definitive ordering.
    """
    added = number(additional_living_weight, "additional_living_weight", lo=0, hi=1, allow_none=False)
    mixed = number(living_weight, "living_weight", lo=0, hi=1, allow_none=False)
    rows = []
    for candidate in candidates:
        if gate(candidate, constraints) or candidate.get("L0") is None or candidate.get("I0") is None:
            continue
        lower = mixed * (1 - added) * number(candidate["L0"], "L0", lo=0, hi=100)
        lower += (1 - mixed) * number(candidate["I0"], "I0", lo=0, hi=100)
        rows.append({"id": candidate["id"], "lower": lower, "upper": lower + mixed * added * 100})
    for row in rows:
        others = [other for other in rows if other["id"] != row["id"]]
        row["possible_best_rank"] = 1 + sum(other["lower"] > row["upper"] for other in others)
        row["possible_worst_rank"] = len(rows) - sum(other["upper"] < row["lower"] for other in others)
    return {"interpretation": "Assumption bounds, not observations or probabilities", "additional_living_weight": added,
            "living_weight": mixed, "new_feature_score": None, "rows": rows}


def run(payload):
    candidates = payload["candidates"]
    constraints = payload["constraints"]
    result = {"schema_version": 2, "analysis_status": "exploratory", "provisional": True,
              "score_meanings": payload["score_meanings"], "constraints": constraints,
              "rankings": rank_five(candidates, constraints), "crossovers": crossover_weights(candidates, constraints)}
    if payload.get("living_feature_weights"):
        result["feature_sensitivity"] = feature_sensitivity(candidates, constraints, payload["living_feature_weights"])
    if payload.get("unknown_feature_sensitivity"):
        result["unknown_feature_sensitivity"] = unknown_feature_bounds(candidates, constraints, **payload["unknown_feature_sensitivity"])
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    if args.input.resolve() == args.output.resolve():
        raise ValueError("Input and output paths must differ")
    payload = json.loads(args.input.read_text(encoding="utf-8"))
    args.output.write_text(json.dumps(run(payload), ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
