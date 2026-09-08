"""Apply the externally preserved five-year research gate without retuning it."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import statistics


PROTOCOL_COMMIT = "0fef1ed9f6effb4b1c7fa08ab1e6e399c1a8fe35"


def assess(report):
    regimes = {}
    for regime in ("historical_policy", "uniform61"):
        rows = [r for r in report["results"] if r["availability_regime"] == regime and r["horizon_months"] == 60]
        by_origin = {}
        for r in rows:
            if r["method"] in ("learned_price", "laggard"):
                by_origin.setdefault(r["origin"], {})[r["method"]] = r
        common, excluded = [], []
        for origin, pair in sorted(by_origin.items()):
            eligible = all(name in pair and pair[name]["observed_complexes"] >= 20
                           and pair[name]["observed"] / max(1, pair[name]["selected"]) >= .5
                           for name in ("learned_price", "laggard"))
            if not eligible:
                excluded.append(origin)
                continue
            model, baseline = pair["learned_price"], pair["laggard"]
            common.append({"origin": origin, "selected": model["selected"],
                           "observed": model["observed"], "observed_complexes": model["observed_complexes"],
                           "model_median_excess_pct": model["median_excess_pct"],
                           "laggard_median_excess_pct": baseline["median_excess_pct"],
                           "model_spearman": model["spearman"]})
        n = len(common)
        positive = sum(r["model_median_excess_pct"] > 0 for r in common)
        model_mean = statistics.mean(r["model_median_excess_pct"] for r in common) if n else None
        baseline_mean = statistics.mean(r["laggard_median_excess_pct"] for r in common) if n else None
        correlations = [r["model_spearman"] for r in common if r["model_spearman"] is not None]
        correlation = statistics.median(correlations) if correlations else None
        conditions = {"at_least_six_common_origins": n >= 6,
                      "positive_in_at_least_two_thirds": n > 0 and positive / n >= 2 / 3,
                      "beats_laggard_equal_origin_mean": n > 0 and model_mean > baseline_mean,
                      "median_spearman_positive": correlation is not None and correlation > 0}
        regimes[regime] = {"common_origins": n, "positive_origins": positive,
                           "positive_fraction": positive / n if n else None,
                           "model_equal_origin_mean_median_excess_pct": model_mean,
                           "laggard_equal_origin_mean_median_excess_pct": baseline_mean,
                           "model_median_spearman": correlation,
                           "conditions": conditions, "passed": all(conditions.values()),
                           "comparisons": common, "excluded_origins": excluded,
                           "available_model_evaluations": sum(r["method"] == "learned_price" for r in rows)}
    passed = all(r["passed"] for r in regimes.values())
    sensitivity_status = "completed"
    if regimes["uniform61"]["available_model_evaluations"] == 0:
        sensitivity_status = ("stopped_after_primary_failure" if regimes["historical_policy"]["available_model_evaluations"] > 0
                              and not regimes["historical_policy"]["passed"] else "not_evaluated")
    return {"schema_version": 1, "status": "passed_research_candidate_gate" if passed else "did_not_pass_research_candidate_gate",
            "passed": passed, "regimes": regimes,
            "additional_sensitivity_status": sensitivity_status,
            "missing_required_regimes": [k for k, r in regimes.items() if r["available_model_evaluations"] == 0],
            "meaning": "Predeclared operational gate for adding a five-year research candidate; not proof of causal or executable investment outperformance."}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--report", default="reports/estate_potential_five_year.json")
    p.add_argument("--protocol", default="reports/estate_potential_five_year_protocol.md")
    p.add_argument("--source-manifest", default="reports/estate_potential_five_year_sources.json")
    p.add_argument("--output", default="reports/estate_potential_five_year_summary.json")
    args = p.parse_args()
    report_path, protocol_path = Path(args.report), Path(args.protocol)
    report = json.loads(report_path.read_text())
    result = assess(report)
    result.update({"sources": report["sources"], "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
                   "protocol_sha256": hashlib.sha256(protocol_path.read_bytes()).hexdigest(),
                   "protocol_commit": PROTOCOL_COMMIT})
    source_manifest = Path(args.source_manifest)
    if source_manifest.exists():
        sources = json.loads(source_manifest.read_text())
        result["added_history_raw_rows"] = sum(s["rows"] for s in sources["sources"] if s["end"] < "201601")
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
    print(json.dumps({"output": args.output, "passed": result["passed"], "regimes": {
        k: {key: v[key] for key in ("common_origins", "positive_origins", "model_equal_origin_mean_median_excess_pct", "laggard_equal_origin_mean_median_excess_pct", "model_median_spearman", "passed")}
        for k, v in result["regimes"].items()}}))


if __name__ == "__main__":
    main()
