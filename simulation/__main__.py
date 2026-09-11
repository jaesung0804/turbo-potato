"""Explicit replay CLI. Prints compact summaries; never starts from site requests."""
import argparse
from dataclasses import replace
import json
from pathlib import Path

from research_backend_client import BackendError

from .engine import Costs, Policy, ReplayError, replay
from .fixtures import fixture
from .storage import BackendSession, MAX_INPUT_BYTES, read_gzip_json


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("demo", help="Run tiny invented prices only; no network or data writes")
    real = commands.add_parser("run", help="Run a restored bounded historical panel after live DB checks")
    real.add_argument("--state-dir", type=Path, required=True)
    real.add_argument("--snapshot-name", required=True)
    real.add_argument("--input-path", required=True, help="Restored relative .json.gz path")
    real.add_argument("--policy", type=Path, required=True, help="Small JSON containing policy and costs")
    real.add_argument("--checkpoint-key", required=True)
    real.add_argument("--expected-checkpoint-version", type=int, required=True)
    real.add_argument("--required-growth-bytes", type=int, default=5_000_000)
    real.add_argument("--stop-after-days", type=int)
    args = parser.parse_args()
    try:
        if args.command == "demo":
            data, policy, costs = fixture()
            results = {}
            for strategy in ("hold", "rotate", "cheapest_hold", "cash"):
                result = replay(data, replace(policy, run_id="synthetic-demo-" + strategy, strategy=strategy), costs)
                results[strategy] = result["summary"]
            output = {"status": "synthetic_validation_only", "actual_historical_experiments": 0,
                      "notice": "Invented prices and cost assumptions; no evidence of model performance.",
                      "scenarios": results}
        else:
            if args.policy.stat().st_size > 65_536:
                raise ReplayError("Policy exceeds 64 KiB")
            document = json.loads(args.policy.read_text(encoding="utf-8"))
            policy_data = document["policy"]
            policy_data["decision_dates"] = tuple(policy_data["decision_dates"])
            policy, costs = Policy(**policy_data), Costs(**document["costs"])
            session = BackendSession(state_dir=args.state_dir, snapshot_name=args.snapshot_name,
                                     input_path=args.input_path, checkpoint_key=args.checkpoint_key,
                                     expected_checkpoint_version=args.expected_checkpoint_version,
                                     required_growth_bytes=args.required_growth_bytes)
            data, _ = read_gzip_json(session.path, MAX_INPUT_BYTES)
            if data.get("kind") != "historical_research":
                raise ReplayError("Use demo for synthetic input; run requires a declared historical panel")
            result = replay(data, policy, costs, session=session, stop_after_days=args.stop_after_days)
            output = {"identity": result["identity"], "summary": result["summary"],
                      "backend_checkpoint_version": result["backend_checkpoint_version"]}
        print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    except (ReplayError, BackendError, OSError, KeyError, ValueError) as error:
        # Do not print environment values or response bodies that may contain private material.
        detail = str(error) if isinstance(error, ReplayError) else (
            "backend_http_" + str(error.status) if isinstance(error, BackendError) else type(error).__name__)
        print(json.dumps({"status": "blocked", "reason": detail}, ensure_ascii=False))
        raise SystemExit(2) from None


if __name__ == "__main__":
    main()
