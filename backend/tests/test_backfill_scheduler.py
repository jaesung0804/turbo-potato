import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location("initial_backfill_scheduler",
    Path(__file__).resolve().parents[1] / "deploy/investment-backfill.py")
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)


@pytest.fixture
def args(tmp_path):
    importer = tmp_path / "importer.py"
    importer.write_text("# Test input; never executed", encoding="utf-8")
    environment = tmp_path / "app.env"
    environment.write_text("# No credentials used in tests", encoding="utf-8")
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps({"format": "live-row-bundle-v1", "normalizer": "research-rows-v1",
        "entries": [{"project": "investment", "dataset": "ohlcv-kr/2026-08", "rows": 3}]}), encoding="utf-8")
    return SimpleNamespace(import_script=importer, manifest=manifest,
        manifest_sha256=hashlib.sha256(manifest.read_bytes()).hexdigest(), env_file=environment,
        state_dir=tmp_path / "state", snapshot_id="a" * 32)


def successful_child(command, **kwargs):
    assert command[command.index("--batch-size") + 1] == "500"
    assert command[command.index("--retries") + 1] == "0"
    assert command[command.index("--max-rows") + 1] == "9100000"
    assert command[command.index("--project") + 1] == "investment"
    assert command[command.index("--source-snapshot-name") + 1] == "pipeline-state"
    snapshot_id = command[command.index("--source-snapshot-id") + 1]
    path = Path(command[command.index("--report") + 1])
    path.write_text(json.dumps({"project": "investment", "status": "complete",
        "source_snapshot_name": "pipeline-state", "source_snapshot_id": snapshot_id, "partitions": [
        {"dataset": "ohlcv-kr/2026-08", "rows": 3, "queries_verified": True}]}), encoding="utf-8")
    return SimpleNamespace(returncode=0)


def test_completed_bundle_never_starts_another_import(args):
    assert runner.run_once(args, execute=successful_child)[0] == 0
    assert (args.state_dir / "investment-complete.json").is_file()
    code, summary = runner.run_once(args, execute=lambda *a, **kw: pytest.fail("Already complete"))
    assert code == 0 and summary["rows"] == 3


def test_undo_failure_waits_at_least_fifteen_minutes(args):
    current = [10000]

    def failed_child(command, **kwargs):
        path = Path(command[command.index("--report") + 1])
        path.write_text(json.dumps({"status": "failed_resumable", "error": {"type": "DatabaseError",
            "database_code": "ORA-30036", "message": "DO_NOT_LOG_SYNTHETIC_SECRET"}}), encoding="utf-8")
        return SimpleNamespace(returncode=1)

    code, result = runner.run_once(args, clock=lambda: current[0], execute=failed_child)
    assert code == 75 and result["retry_after_seconds"] == 900
    assert "DO_NOT_LOG" not in json.dumps(result)
    assert "DO_NOT_LOG" not in (args.state_dir / "investment-state.json").read_text()
    current[0] += 899
    assert runner.run_once(args, clock=lambda: current[0], execute=lambda *a, **kw: pytest.fail("Too soon"))[0] == 75
    current[0] += 1
    assert runner.run_once(args, clock=lambda: current[0], execute=successful_child)[0] == 0


def test_non_retryable_failure_needs_attention(args):
    def failed_child(command, **kwargs):
        path = Path(command[command.index("--report") + 1])
        path.write_text(json.dumps({"status": "failed_resumable", "error": {"type": "ValueError"}}), encoding="utf-8")
        return SimpleNamespace(returncode=1)

    assert runner.run_once(args, execute=failed_child)[0] == 78
    assert (args.state_dir / "investment-needs-attention.json").is_file()
    assert runner.run_once(args, execute=lambda *a, **kw: pytest.fail("Must wait for review"))[0] == 78


def test_attempt_budget_stops_instead_of_crashing_forever(args):
    args.state_dir.mkdir()
    runner.write_json(args.state_dir / "investment-state.json", {
        "manifest_sha256": args.manifest_sha256, "snapshot_id": args.snapshot_id, "attempts": 96, "status": "waiting"})
    code, result = runner.run_once(args, execute=lambda *a, **kw: pytest.fail("Budget exhausted"))
    assert code == 78 and result["reason"] == "attempt_budget_exhausted"


def test_changed_manifest_is_refused_before_child_execution(args):
    args.manifest.write_text("{}", encoding="utf-8")
    with pytest.raises(ValueError, match="manifest hash"):
        runner.run_once(args, execute=lambda *a, **kw: pytest.fail("Manifest changed"))


def test_recovers_completion_marker_after_interrupted_wrapper(args):
    args.state_dir.mkdir()
    report = args.state_dir / "investment-attempt-001.json"
    successful_child(["--batch-size", "500", "--retries", "0", "--max-rows", "9100000",
        "--project", "investment", "--source-snapshot-name", "pipeline-state", "--source-snapshot-id", args.snapshot_id,
        "--report", str(report)])
    runner.write_json(args.state_dir / "investment-state.json", {
        "manifest_sha256": args.manifest_sha256, "snapshot_id": args.snapshot_id, "attempts": 1, "status": "running"})
    assert runner.run_once(args, execute=lambda *a, **kw: pytest.fail("Child already completed"))[0] == 0


def test_missing_child_report_retries_with_same_cooldown(args):
    code, result = runner.run_once(args, execute=lambda *a, **kw: SimpleNamespace(returncode=-15))
    assert code == 75 and result["retry_after_seconds"] == 900
    assert result["error"]["type"] == "RunnerExitedBeforeReport"


def test_scheduler_cannot_relabel_old_bundle_as_a_new_snapshot(args):
    assert runner.run_once(args, execute=successful_child)[0] == 0
    args.snapshot_id = "b" * 32
    with pytest.raises(ValueError, match="source snapshot"):
        runner.run_once(args, execute=lambda *a, **kw: pytest.fail("Snapshot pin changed"))


def test_unfenced_completion_report_is_not_accepted(args):
    expected = {"ohlcv-kr/2026-08": 3}
    report = {"project": "investment", "status": "complete", "partitions": [
        {"dataset": "ohlcv-kr/2026-08", "rows": 3, "queries_verified": True}]}
    assert not runner.complete_report(report, expected, args.snapshot_id)
