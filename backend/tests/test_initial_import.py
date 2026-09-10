from argparse import Namespace
import csv
import importlib.util
import json
from pathlib import Path
import sys

import pytest
from sqlalchemy import func, select, update

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("prepare_live_rows", HERE.parent / "deploy" / "prepare_live_rows.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

from research_backend.config import Settings
from research_backend.database import Databases
from research_backend import ingest, schema as s
from research_backend.util import Conflict, now


def make_bundle(tmp_path):
    source = tmp_path / "prices.csv"
    with source.open("w", newline="", encoding="utf-8") as out:
        writer = csv.DictWriter(out, fieldnames=["date", "ticker", "close", "volume"])
        writer.writeheader()
        for index in range(1501):
            writer.writerow({"date": "2026-02-02", "ticker": "000020", "close": str(index), "volume": "7"})
        writer.writerow({"date": "2026-01-31", "ticker": "000020", "close": "1", "volume": "2"})
    output = tmp_path / "bundle"
    item = runner.prepare_source("test-kr", {"project": "investment", "dataset": "ohlcv-kr"},
                                 source, output, "2026-02")
    manifest = {"format": "live-row-bundle-v1", "normalizer": ingest.NORMALIZER, "entries": [item]}
    runner.write_json(output / "manifest.json", manifest)
    return output / "manifest.json", item


def test_complete_month_bundle_hash_and_duplicate_preservation(tmp_path):
    manifest, item = make_bundle(tmp_path)
    _, verified = runner.validate_bundle(manifest)
    assert verified[0]["rows"] == 1501
    assert item["source"]["rows"] == 1502
    assert item["duplicate_entity_days"] == 1500
    assert item["probe"]["entity"] == "000020"
    assert item["source"]["monthly_counts"] == {"2026-01": 1, "2026-02": 1501}
    artifact = manifest.parent / item["file"]
    artifact.write_bytes(artifact.read_bytes() + b"tampered")
    with pytest.raises(runner.PreparationError, match="bundle_sha256_verified"):
        runner.validate_bundle(manifest)


def test_interrupted_import_resumes_batches_and_rerun_is_unchanged(tmp_path, monkeypatch):
    manifest, item = make_bundle(tmp_path)
    runtime = tmp_path / "runtime"
    env = tmp_path / "local.env"
    env.write_text(f"BACKEND_DATABASE=sqlite\nBACKEND_DATA_DIR={runtime.as_posix()}\nBACKEND_BLOB_STORE=local\n", encoding="utf-8")
    databases = Databases(Settings(runtime), projects=("investment",))
    databases.initialize()
    with databases.engine("investment").begin() as db:
        db.execute(s.heads.insert().values(name="pipeline-state", snapshot_id="a" * 32, updated_at=now()))
    databases.close()
    args = Namespace(manifest=manifest, project="investment", env_file=env, max_rows=2000,
                     max_allocated_gib=12, report=tmp_path / "report.json",
                     source_snapshot_name="pipeline-state", source_snapshot_id="a" * 32)
    original = ingest._insert_batch
    calls = 0

    def interrupt_second_batch(service, rows):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic interruption")
        original(service, rows)

    monkeypatch.setattr(ingest, "_insert_batch", interrupt_second_batch)
    with pytest.raises(RuntimeError):
        runner.apply(args)
    databases = Databases(Settings(runtime), projects=("investment",))
    with databases.engine("investment").connect() as db:
        assert db.scalar(select(func.count()).select_from(s.observations)) == 500
        assert db.scalar(select(func.count()).select_from(s.datasets)) == 0
    databases.close()
    monkeypatch.setattr(ingest, "_insert_batch", original)
    report = runner.apply(args)
    assert report["status"] == "complete"
    assert report["partitions"][0]["rows"] == 1501
    assert report["partitions"][0]["queries_verified"] is True
    assert runner.apply(args)["partitions"][0]["changed"] is False


def test_row_budget_is_checked_before_credentials_or_db(tmp_path):
    manifest, _ = make_bundle(tmp_path)
    args = Namespace(manifest=manifest, project="investment", max_rows=1000,
                     env_file=tmp_path / "does-not-exist.env")
    with pytest.raises(runner.PreparationError, match="explicit_row_budget"):
        runner.apply(args)


def test_safe_errors_do_not_expose_database_message():
    assert runner.safe_error(RuntimeError("secret-value-and-sql")) == {"type": "RuntimeError"}


def test_transient_undo_failure_retries_with_delay_and_fresh_connections(monkeypatch):
    from types import SimpleNamespace
    class TemporaryUndoError(Exception):
        full_code = 'ORA-30036'
    calls, delays, disposed = [], [], []
    def operation(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            raise TemporaryUndoError('database details must stay hidden')
        return {'rows': 5, 'changed': True}
    monkeypatch.setattr(runner, 'import_rows', operation)
    monkeypatch.setattr(runner.time, 'sleep', delays.append)
    service = SimpleNamespace(engine=SimpleNamespace(dispose=lambda: disposed.append(1)))
    assert runner.import_with_retry(service, {'dataset': 'test/2026-02'}, Path('source.gz'))['rows'] == 5
    assert len(calls) == 2 and delays == [30] and disposed == [1]


def test_non_transient_error_is_not_retried(monkeypatch):
    from types import SimpleNamespace
    def operation(*args, **kwargs):
        raise ValueError('invalid source')
    monkeypatch.setattr(runner, 'import_rows', operation)
    with pytest.raises(ValueError, match='invalid source'):
        runner.import_with_retry(SimpleNamespace(), {'dataset': 'test/2026-02'}, Path('source.gz'))


def test_new_source_and_dataset_between_preflight_and_import_are_preserved(tmp_path, monkeypatch):
    manifest, item = make_bundle(tmp_path)
    runtime = tmp_path / "runtime"
    env = tmp_path / "app.env"
    env.write_text(f"BACKEND_DATABASE=sqlite\nBACKEND_DATA_DIR={runtime.as_posix()}\nBACKEND_BLOB_STORE=local\n", encoding="utf-8")
    databases = Databases(Settings(runtime), projects=("investment",))
    databases.initialize()
    with databases.engine("investment").begin() as db:
        db.execute(s.heads.insert().values(name="pipeline-state", snapshot_id="a" * 32, updated_at=now()))
    databases.close()
    newer = tmp_path / "newer.jsonl"
    newer.write_text(json.dumps({"date": "2026-02-02", "ticker": "000020", "close": "newer"}) + "\n", encoding="utf-8")
    args = Namespace(manifest=manifest, project="investment", env_file=env, max_rows=2000,
        max_allocated_gib=12, report=tmp_path / "report.json", retries=3,
        source_snapshot_name="pipeline-state", source_snapshot_id="a" * 32)
    calls = []
    latest_version = []

    def move_source_before_old_import_reads_its_previous_head(service, *positional, **keywords):
        calls.append(1)
        with service.engine.begin() as db:
            db.execute(update(s.heads).where(s.heads.c.name == "pipeline-state").values(snapshot_id="b" * 32))
        latest_version.append(ingest.import_rows(service, item["dataset"], newer, "jsonl")["version"])
        return ingest.import_rows(service, *positional, **keywords)

    monkeypatch.setattr(runner, "import_rows", move_source_before_old_import_reads_its_previous_head)
    with pytest.raises(Conflict, match="superseded"):
        runner.apply(args)
    assert calls == [1]  # A stale source is not a transient retry.
    databases = Databases(Settings(runtime), projects=("investment",))
    with databases.engine("investment").connect() as db:
        head = db.execute(select(s.datasets)).mappings().one()
        assert head["version_id"] == latest_version[0] and head["row_count"] == 1
        assert db.scalar(select(s.heads.c.snapshot_id)) == "b" * 32
        assert db.scalar(select(func.count()).select_from(s.dataset_versions).where(s.dataset_versions.c.status == "staging")) == 1
    databases.close()
    report = json.loads(args.report.read_text())
    assert report["error"] == {"type": "Conflict"} and report["partitions"] == []


def test_snapshot_lock_is_held_until_publication_transaction_exits(tmp_path):
    import threading
    databases = Databases(Settings(tmp_path), projects=("investment",))
    databases.initialize()
    engine = databases.engine("investment")
    with engine.begin() as db:
        db.execute(s.heads.insert().values(name="pipeline-state", snapshot_id="a" * 32, updated_at=now()))
    started, finished = threading.Event(), threading.Event()

    def publish_new_snapshot():
        with engine.begin() as other:
            started.set()
            other.execute(update(s.heads).where(s.heads.c.name == "pipeline-state").values(snapshot_id="b" * 32))
        finished.set()

    guard = runner.source_snapshot_guard("investment", "pipeline-state", "a" * 32)
    with engine.begin() as db:
        guard(db)
        thread = threading.Thread(target=publish_new_snapshot)
        thread.start()
        assert started.wait(1)
        assert not finished.wait(0.05)
        assert db.scalar(select(s.heads.c.snapshot_id)) == "a" * 32
    thread.join(timeout=2)
    assert finished.is_set()
    databases.close()


@pytest.mark.parametrize("name,snapshot_id", [(None, None), ("pipeline-state", None), (None, "a" * 32),
    ("other-state", "a" * 32), ("pipeline-state", "invalid")])
def test_investment_requires_valid_snapshot_option_pair(name, snapshot_id):
    with pytest.raises(runner.PreparationError):
        runner.source_snapshot_guard("investment", name, snapshot_id)
    assert runner.source_snapshot_guard("estate", None, None) is None


def test_all_months_single_scan_matches_representative_month_bytes(tmp_path):
    manifest, selected = make_bundle(tmp_path)
    entries = runner.prepare_all_source("test-kr", {"project": "investment", "dataset": "ohlcv-kr"},
                                        tmp_path / "prices.csv", tmp_path / "all")
    assert sum(item["rows"] for item in entries) == 1502
    assert [item["month"] for item in entries] == ["2026-01", "2026-02"]
    assert entries[1]["sha256"] == selected["sha256"]
    assert entries[1]["probe"]["entity_rows"] == 1501

