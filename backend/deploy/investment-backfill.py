"""Bounded, explicit initial backfill scheduler; no network/DB work on import.

The existing apply runner owns the same project flock as the monthly worker.
Only a hash-pinned investment bundle is accepted. Child output is suppressed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

COOLDOWN_SECONDS = 900
MAX_ATTEMPTS = 96
MAX_ROWS = 9100000
RETRYABLE = {"ORA-30036", "ORA-03113", "ORA-03114", "DPY-4024", "DPY-4011", "DPY-1001"}


def write_json(path, value):
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def read_json(path):
    if not path.is_file():
        return {}
    if path.stat().st_size > 16 * 1024**2:
        raise ValueError("Metadata budget exceeded")
    return json.loads(path.read_text(encoding="utf-8"))


def safe_error(report):
    error = report.get("error", {})
    result = {}
    if isinstance(error, dict):
        kind = error.get("type")
        if isinstance(kind, str) and re.fullmatch(r"[A-Za-z][A-Za-z0-9_]{0,63}", kind):
            result["type"] = kind
        code = error.get("database_code")
        if isinstance(code, str) and re.fullmatch(r"(?:ORA|DPY|DPI)-\d{4,5}", code):
            result["database_code"] = code
    return result


def expected_partitions(args):
    if not isinstance(args.snapshot_id, str) or re.fullmatch(r"[a-f0-9]{32}", args.snapshot_id) is None:
        raise ValueError("A pinned pipeline-state snapshot id is required")
    if not args.import_script.is_file() or not args.env_file.is_file():
        raise ValueError("Backfill inputs are unavailable")
    with args.manifest.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != args.manifest_sha256:
        raise ValueError("Pinned manifest hash mismatch")
    manifest = read_json(args.manifest)
    if manifest.get("format") != "live-row-bundle-v1" or manifest.get("normalizer") != "research-rows-v1":
        raise ValueError("Unsupported bundle")
    entries = manifest.get("entries", [])
    if not entries or any(item.get("project") != "investment" for item in entries):
        raise ValueError("Investment-only bundle required")
    expected = {item["dataset"]: item["rows"] for item in entries}
    if len(expected) != len(entries) or any(type(rows) is not int or rows < 1 for rows in expected.values()):
        raise ValueError("Invalid bundle partitions")
    if not 1 <= sum(expected.values()) <= MAX_ROWS:
        raise ValueError("Explicit row budget exceeded")
    return expected


def complete_report(report, expected, snapshot_id):
    parts = report.get("partitions", [])
    return (report.get("project") == "investment" and report.get("status") == "complete"
        and report.get("source_snapshot_name") == "pipeline-state" and report.get("source_snapshot_id") == snapshot_id
        and len(parts) == len(expected)
        and all(item.get("queries_verified") is True for item in parts)
        and {item.get("dataset"): item.get("rows") for item in parts} == expected)


def run_once(args, *, clock=time.time, execute=subprocess.run):
    """Caller holds the scheduler lock; the child takes the project import lock."""
    directory = args.state_dir
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    state_path = directory / "investment-state.json"
    state = read_json(state_path)
    expected = expected_partitions(args)
    if state.get("manifest_sha256") not in (None, args.manifest_sha256):
        raise ValueError("Existing scheduler state belongs to another manifest")
    if state and state.get("snapshot_id") != args.snapshot_id:
        raise ValueError("Existing scheduler state belongs to another source snapshot")
    state.setdefault("manifest_sha256", args.manifest_sha256)
    state.setdefault("snapshot_id", args.snapshot_id)
    state.setdefault("attempts", 0)

    def completed():
        state.update(status="complete", rows=sum(expected.values()), months=len(expected), finished_at=clock())
        write_json(state_path, state)
        write_json(directory / "investment-complete.json", {key: state[key]
            for key in ("manifest_sha256", "snapshot_id", "rows", "months", "finished_at")})
        return 0, {"status": "complete", "rows": state["rows"], "months": state["months"]}

    def blocked(reason, error=None):
        state.update(status="needs_attention", reason=reason, error=error or {}, finished_at=clock())
        write_json(state_path, state)
        write_json(directory / "investment-needs-attention.json", state)
        return 78, {"status": "needs_attention", "reason": reason, "error": error or {}}

    # Recover after power loss between a successful child exit and our marker.
    previous = directory / f"investment-attempt-{state['attempts']:03d}.json"
    if state.get("status") == "complete" or complete_report(read_json(previous), expected, args.snapshot_id):
        return completed()
    if state.get("status") == "needs_attention":
        return 78, {"status": "needs_attention", "reason": state.get("reason", "operator_review")}
    if clock() < state.get("next_attempt_not_before", 0):
        return 75, {"status": "cooldown", "retry_after_seconds": max(1, int(state["next_attempt_not_before"] - clock()))}
    if state["attempts"] >= MAX_ATTEMPTS:
        return blocked("attempt_budget_exhausted")
    state["attempts"] += 1
    report_path = directory / f"investment-attempt-{state['attempts']:03d}.json"
    if report_path.exists():
        return blocked("attempt_report_already_exists")
    state.update(status="running", started_at=clock(), next_attempt_not_before=clock() + COOLDOWN_SECONDS)
    write_json(state_path, state)
    command = [sys.executable, str(args.import_script), "apply", "--manifest", str(args.manifest),
        "--env-file", str(args.env_file), "--project", "investment", "--max-rows", str(MAX_ROWS),
        "--max-allocated-gib", "12", "--batch-size", "500", "--retries", "0",
        "--source-snapshot-name", "pipeline-state", "--source-snapshot-id", args.snapshot_id,
        "--month-pause-seconds", "1", "--report", str(report_path)]
    # The importer checks all bundle hashes, enforces APP-only access, takes
    # row-import-investment.lock, and resumes its committed prefix itself.
    result = execute(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    report = read_json(report_path)
    if result.returncode == 0 and complete_report(report, expected, args.snapshot_id):
        return completed()
    error = safe_error(report)
    if error.get("database_code") not in RETRYABLE and report:
        return blocked("non_retryable_import_failure", error)
    if result.returncode == 0:
        return blocked("completion_report_mismatch")
    # A missing report may be a project-lock race or systemd interruption;
    # retry boundedly instead of guessing from suppressed exception text.
    state.update(status="waiting", error=error, next_attempt_not_before=clock() + COOLDOWN_SECONDS)
    write_json(state_path, state)
    return 75, {"status": "waiting", "attempt": state["attempts"], "retry_after_seconds": COOLDOWN_SECONDS,
        "error": error or {"type": "RunnerExitedBeforeReport"}}


@contextmanager
def scheduler_lock(directory):
    import fcntl
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (directory / "investment-scheduler.lock").open("a+b") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--import-script", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--snapshot-id", required=True, help="Pinned pipeline-state source snapshot")
    parser.add_argument("--env-file", type=Path, default=Path("/etc/research-backend.env"))
    parser.add_argument("--state-dir", type=Path, default=Path("/var/lib/research-backend/backfill"))
    args = parser.parse_args()
    try:
        if os.geteuid() == 0:
            raise ValueError("Use the non-root research service account")
        with scheduler_lock(args.state_dir):
            code, result = run_once(args)
    except BlockingIOError:
        code, result = 75, {"status": "another_scheduler_active"}
    except Exception as error:
        code, result = 78, {"status": "needs_attention", "error": {"type": type(error).__name__}}
        args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        write_json(args.state_dir / "investment-needs-attention.json", result)
    print(json.dumps(result), flush=True)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
