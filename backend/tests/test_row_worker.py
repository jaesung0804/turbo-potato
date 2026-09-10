import gzip
import hashlib
import json
from pathlib import Path
import time
from uuid import uuid4

import pytest
from sqlalchemy import func, select, update

from research_backend import ingest, schema as s
from research_backend import row_worker
from research_backend.retention import dataset_version
from research_backend.config import Settings
from research_backend.database import Databases
from research_backend.objects import LocalObjects
from research_backend.row_worker import JOB_TYPE, RowWorker, project_lock
from research_backend.service import Service
from research_backend.util import digest


@pytest.fixture
def worker_env(tmp_path):
    settings = Settings(tmp_path / "runtime")
    databases = Databases(settings, projects=("investment",))
    databases.initialize()
    service = Service(databases.engine("investment"), LocalObjects(settings.data_dir / "objects"), "investment")
    worker = RowWorker(service, settings.data_dir)
    yield tmp_path, service, worker
    databases.close()


def publish_source(tmp_path, service):
    path = tmp_path / "manifest.json"
    path.write_text('{"complete":true}', encoding="utf-8")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    info = service.register_file(sha, path)
    old = service.snapshot_head("pipeline-state")["snapshot_id"]
    sid = service.begin_snapshot("pipeline-state", old)["snapshot_id"]
    service.add_snapshot_files(sid, [{"relative_path": "state-manifest.json", "sha256": sha, "byte_size": info["byte_size"]}])
    service.commit_snapshot(sid, 1)
    return sid, sha


def enqueue_month(tmp_path, service, sid, manifest_sha, rows=1001, *, wrong_month=False):
    path = tmp_path / (uuid4().hex + ".jsonl.gz")
    with path.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6) as stream:
        for i in range(rows):
            item = {"date": "2026-07-03" if wrong_month else "2026-08-03", "ticker": "000020", "close": str(i)}
            stream.write(json.dumps(item, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode() + b"\n")
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    service.register_file(sha, path)
    payload = {"schema": 1, "normalizer": ingest.NORMALIZER, "dataset": "ohlcv-kr/2026-08", "rows": rows,
               "sha256": sha, "snapshot_name": "pipeline-state", "snapshot_id": sid, "manifest_sha256": manifest_sha}
    jid = digest(json.dumps(payload, sort_keys=True).encode())
    service.enqueue(jid, JOB_TYPE, payload)
    return jid, payload


def table_count(service, table):
    with service.engine.connect() as db:
        return db.scalar(select(func.count()).select_from(table))


def test_worker_imports_and_queries_complete_month(worker_env):
    tmp, service, worker = worker_env
    sid, manifest_sha = publish_source(tmp, service)
    jid, payload = enqueue_month(tmp, service, sid, manifest_sha)
    result = worker.run_once("worker-a")
    assert result == {"status": "imported", "dataset": "ohlcv-kr/2026-08", "rows": 1001, "changed": True}
    page = ingest.query_rows(service, payload["dataset"], entity="000020", limit=2)
    assert len(page["items"]) == 2 and page["next_cursor"] == 2
    assert page["items"][0]["payload"]["ticker"] == "000020"
    with service.engine.connect() as db:
        assert db.scalar(select(s.jobs.c.status).where(s.jobs.c.job_id == jid)) == "completed"


def test_superseded_queued_job_skips_without_loading_rows(worker_env):
    tmp, service, worker = worker_env
    sid, manifest_sha = publish_source(tmp, service)
    enqueue_month(tmp, service, sid, manifest_sha)
    publish_source(tmp, service)
    assert worker.run_once("worker-a")["status"] == "skipped_superseded_source"
    assert table_count(service, s.observations) == 0


def test_source_change_during_batches_cannot_publish(worker_env, monkeypatch):
    tmp, service, worker = worker_env
    sid, manifest_sha = publish_source(tmp, service)
    enqueue_month(tmp, service, sid, manifest_sha)
    original = ingest._insert_batch
    moved = False

    def move_source_after_batch(current_service, rows):
        nonlocal moved
        original(current_service, rows)
        if not moved:
            moved = True
            publish_source(tmp, service)

    monkeypatch.setattr(ingest, "_insert_batch", move_source_after_batch)
    assert worker.run_once("worker-a")["status"] == "skipped_superseded_source"
    assert table_count(service, s.observations) == 1001
    assert table_count(service, s.datasets) == 0
    with service.engine.connect() as db:
        assert db.scalar(select(s.dataset_versions.c.status)) == "staging"


@pytest.mark.parametrize("reason", ["stolen", "expired"])
def test_lease_theft_or_expiry_during_import_blocks_publication(worker_env, monkeypatch, reason):
    tmp, service, worker = worker_env
    sid, manifest_sha = publish_source(tmp, service)
    jid, _ = enqueue_month(tmp, service, sid, manifest_sha)
    original = ingest._insert_batch

    def invalidate_lease(current_service, rows):
        original(current_service, rows)
        with service.engine.begin() as db:
            values = {"lease_token": uuid4().hex} if reason == "stolen" else {"lease_until": "2000-01-01T00:00:00.000000Z"}
            db.execute(update(s.jobs).where(s.jobs.c.job_id == jid).values(**values))

    monkeypatch.setattr(ingest, "_insert_batch", invalidate_lease)
    assert worker.run_once("worker-a")["status"] == "lease_lost"
    assert table_count(service, s.datasets) == 0


def test_failed_heartbeat_cannot_publish(worker_env, monkeypatch):
    tmp, service, worker = worker_env
    worker.heartbeat_interval = 0.01
    sid, manifest_sha = publish_source(tmp, service)
    enqueue_month(tmp, service, sid, manifest_sha)
    original = ingest._insert_batch

    def failed_renew(*args):
        raise RuntimeError("synthetic transport failure")

    def slow_batch(current_service, rows):
        original(current_service, rows)
        time.sleep(0.03)

    monkeypatch.setattr(service, "renew_job", failed_renew)
    monkeypatch.setattr(ingest, "_insert_batch", slow_batch)
    assert worker.run_once("worker-a")["status"] == "lease_lost"
    assert table_count(service, s.datasets) == 0


def test_wrong_month_is_rejected_before_any_rows_are_inserted(worker_env):
    tmp, service, worker = worker_env
    sid, manifest_sha = publish_source(tmp, service)
    enqueue_month(tmp, service, sid, manifest_sha, wrong_month=True)
    assert worker.run_once("worker-a")["status"] == "failed"
    assert table_count(service, s.observations) == 0


def test_transient_failure_retains_batches_and_retries_after_lease(worker_env, monkeypatch):
    tmp, service, worker = worker_env
    sid, manifest_sha = publish_source(tmp, service)
    jid, _ = enqueue_month(tmp, service, sid, manifest_sha)
    original = ingest._insert_batch
    calls = 0

    def fail_after_committed_batch(current_service, rows):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError("synthetic transient failure; do not expose driver details")
        original(current_service, rows)

    monkeypatch.setattr(ingest, "_insert_batch", fail_after_committed_batch)
    result = worker.run_once("worker-a")
    assert result == {"status": "retry_after_lease", "error": {"type": "RuntimeError"}}
    assert table_count(service, s.observations) == 500
    assert table_count(service, s.datasets) == 0
    with service.engine.connect() as db:
        job = db.execute(select(s.jobs).where(s.jobs.c.job_id == jid)).mappings().one()
    assert job["status"] == "running" and job["attempts"] == 1
    assert worker.run_once("worker-a") is None
    with service.engine.begin() as db:
        db.execute(update(s.jobs).where(s.jobs.c.job_id == jid).values(lease_until="2000-01-01T00:00:00.000000Z"))
    monkeypatch.setattr(ingest, "_insert_batch", original)
    assert worker.run_once("worker-b")["status"] == "imported"
    assert table_count(service, s.observations) == 1001


def test_initial_import_lock_prevents_job_claim(worker_env):
    tmp, service, worker = worker_env
    sid, manifest_sha = publish_source(tmp, service)
    enqueue_month(tmp, service, sid, manifest_sha)
    with project_lock(worker.data_dir, "investment") as locked:
        assert locked
        assert worker.run_once("worker-a") is None
    with service.engine.connect() as db:
        assert db.scalar(select(s.jobs.c.status)) == "queued"


def expire_job(service, jid):
    with service.engine.begin() as db:
        db.execute(update(s.jobs).where(s.jobs.c.job_id == jid).values(lease_until="2000-01-01T00:00:00.000000Z"))


def test_opt_in_cleanup_resumes_after_publication_without_reimporting_current_rows(worker_env, monkeypatch):
    tmp, service, worker = worker_env
    sid, manifest = publish_source(tmp, service)
    _, old_payload = enqueue_month(tmp, service, sid, manifest, rows=7)
    worker.run_once("worker-a")
    old = dataset_version(service, old_payload["dataset"])
    sid, manifest = publish_source(tmp, service)
    jid, payload = enqueue_month(tmp, service, sid, manifest, rows=8)
    before_files = table_count(service, s.files)
    worker.archive_obsolete = True
    worker.retention_batch_size, worker.retention_max_batches, worker.retention_passes = 2, 1, 1
    result = worker.run_once("worker-a")
    assert result["status"] == "retry_after_lease" and result["phase"] == "retention" and result["published"]
    assert result["cleanup"] == {"deleted_rows": 2, "passes": 1, "complete": False}
    assert len(ingest.query_rows(service, payload["dataset"])["items"]) == 8
    assert dataset_version(service, payload["dataset"], old["version_id"])["status"] == "archived"
    assert table_count(service, s.observations) == 13
    expire_job(service, jid)
    worker.retention_passes = 5

    def no_reimport(*args):
        raise AssertionError("An unchanged-head cleanup retry must not download or import")

    monkeypatch.setattr(worker, "_download_validate", no_reimport)
    result = worker.run_once("worker-b")
    assert result["status"] == "unchanged" and result["cleanup"]["complete"]
    assert result["cleanup"]["deleted_rows"] == 5
    assert table_count(service, s.observations) == 8 and table_count(service, s.files) == before_files
    retained = b"".join(service.objects.chunks("investment", old_payload["sha256"]))
    assert hashlib.sha256(retained).hexdigest() == old_payload["sha256"]
    with service.engine.connect() as db:
        assert db.scalar(select(s.jobs.c.status).where(s.jobs.c.job_id == jid)) == "completed"


def test_cleanup_failure_retains_published_head_and_retry_can_finish(worker_env, monkeypatch):
    tmp, service, worker = worker_env
    sid, manifest = publish_source(tmp, service)
    enqueue_month(tmp, service, sid, manifest, rows=1)
    worker.run_once("worker-a")
    sid, manifest = publish_source(tmp, service)
    jid, payload = enqueue_month(tmp, service, sid, manifest, rows=2)
    worker.archive_obsolete = True
    original = row_worker.prune_obsolete_rows

    def unavailable_source(*args, **kwargs):
        raise RuntimeError("Synthetic private driver detail that must not be logged")

    monkeypatch.setattr(row_worker, "prune_obsolete_rows", unavailable_source)
    result = worker.run_once("worker-a")
    assert result["status"] == "retry_after_lease" and result["published"]
    assert result["error"] == {"type": "RuntimeError"}
    assert len(ingest.query_rows(service, payload["dataset"])["items"]) == 2
    assert table_count(service, s.observations) == 3
    expire_job(service, jid)
    monkeypatch.setattr(row_worker, "prune_obsolete_rows", original)
    result = worker.run_once("worker-b")
    assert result["status"] == "unchanged" and result["cleanup"]["complete"]
    assert table_count(service, s.observations) == 2


@pytest.mark.parametrize("blocked", ["capacity", "superseded"])
def test_retention_never_runs_before_successful_publication(worker_env, monkeypatch, blocked):
    tmp, service, worker = worker_env
    sid, manifest = publish_source(tmp, service)
    _, payload = enqueue_month(tmp, service, sid, manifest, rows=1)
    worker.run_once("worker-a")
    old = dataset_version(service, payload["dataset"])["version_id"]
    sid, manifest = publish_source(tmp, service)
    enqueue_month(tmp, service, sid, manifest, rows=2)
    worker.archive_obsolete = True

    def no_cleanup(*args, **kwargs):
        raise AssertionError("Cleanup must not run after unsuccessful publication")

    monkeypatch.setattr(row_worker, "prune_obsolete_rows", no_cleanup)
    if blocked == "superseded":
        publish_source(tmp, service)
    else:
        def no_capacity(*args):
            raise ValueError("Synthetic quota limit")
        monkeypatch.setattr(worker, "_check_capacity", no_capacity)
    result = worker.run_once("worker-a")
    assert result["status"] == ("failed" if blocked == "capacity" else "skipped_superseded_source")
    assert dataset_version(service, payload["dataset"])["version_id"] == old
    assert table_count(service, s.observations) == 1


def test_retention_is_disabled_by_default_even_after_a_new_head_is_published(worker_env):
    tmp, service, worker = worker_env
    sid, manifest = publish_source(tmp, service)
    _, payload = enqueue_month(tmp, service, sid, manifest, rows=1)
    worker.run_once("worker-a")
    old = dataset_version(service, payload["dataset"])["version_id"]
    sid, manifest = publish_source(tmp, service)
    enqueue_month(tmp, service, sid, manifest, rows=2)
    result = worker.run_once("worker-a")
    assert result["status"] == "imported" and "cleanup" not in result
    assert dataset_version(service, payload["dataset"], old)["status"] == "complete"
    assert table_count(service, s.observations) == 3
