"""Explicit post-collection monthly imports; API reads never enqueue work."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import signal
import tempfile
import threading

from sqlalchemy import func, select, text, update
from . import schema as s
from .config import Settings
from .database import Databases
from .ingest import NORMALIZER, import_rows, normalized
from .objects import LocalObjects, OCIObjects
from .retention import prune_obsolete_rows
from .service import Service
from .util import Conflict, digest, now, sha_value

JOB_TYPE = "monthly-row-import-v1"
ALLOWED = {
    "investment": {"ohlcv-kr": {"pipeline-state"}, "ohlcv-us": {"pipeline-state"}},
    "estate": {"apt-trades": {"estate-raw-state", "estate-history-state", "estate-history-extended-state"}},
}


class StaleSource(Conflict):
    pass


class LostLease(Conflict):
    pass


def safe_error(error):
    result = {"type": type(error).__name__}
    for outer in (error, getattr(error, "orig", None)):
        for value in (outer, *getattr(outer, "args", ())):
            code = getattr(value, "full_code", None)
            if isinstance(code, str) and re.fullmatch(r"(?:ORA|DPY|DPI)-\d{4,5}", code):
                result["database_code"] = code
    return result


def validate_payload(project, payload, max_rows):
    expected = {"schema", "normalizer", "dataset", "sha256", "rows", "snapshot_name", "snapshot_id", "manifest_sha256"}
    if not isinstance(payload, dict) or set(payload) != expected or payload["schema"] != 1 or payload["normalizer"] != NORMALIZER:
        raise ValueError("Unsupported monthly import job")
    dataset = payload["dataset"]
    if not isinstance(dataset, str) or "/" not in dataset:
        raise ValueError("Invalid monthly dataset")
    base, month = dataset.rsplit("/", 1)
    if (base not in ALLOWED[project] or payload["snapshot_name"] not in ALLOWED[project][base]
            or re.fullmatch(r"\d{4}-\d{2}", month) is None):
        raise ValueError("Dataset/source snapshot is not permitted")
    date.fromisoformat(month + "-01")
    if project == "estate":
        first, last = {"estate-raw-state": ("2021-01", "9999-12"),
            "estate-history-state": ("2016-01", "2020-12"),
            "estate-history-extended-state": ("2006-01", "2015-12")}[payload["snapshot_name"]]
        if not first <= month <= last:
            raise ValueError("Month is outside its authoritative estate snapshot range")
    if type(payload["rows"]) is not int or not 1 <= payload["rows"] <= max_rows:
        raise ValueError("Monthly row budget exceeded or empty month refused")
    if not isinstance(payload["snapshot_id"], str) or re.fullmatch(r"[a-f0-9]{32}", payload["snapshot_id"]) is None:
        raise ValueError("Invalid source snapshot id")
    sha_value(payload["sha256"])
    sha_value(payload["manifest_sha256"])
    return month


@contextmanager
def project_lock(data_dir, project):
    """Share the initial-import lock: one publication worker per project."""
    data_dir.mkdir(parents=True, exist_ok=True)
    handle = (data_dir / f"row-import-{project}.lock").open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0"); handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
        yield acquired
    finally:
        handle.close()


class LeaseHeartbeat:
    def __init__(self, service, job, lease_seconds=300, interval=30):
        self.service, self.job, self.lease_seconds = service, job, lease_seconds
        self.interval = interval
        self.stop_event, self.lost = threading.Event(), threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop_event.wait(self.interval):
            try:
                self.service.renew_job(self.job["job_id"], self.job["lease_token"], self.lease_seconds)
            except Exception:
                self.lost.set()
                return

    def __enter__(self):
        self.thread.start()
        return self

    def check(self):
        if self.lost.is_set():
            raise LostLease("Monthly import lease could not be renewed")

    def __exit__(self, *args):
        self.stop_event.set()
        self.thread.join(timeout=35)


class RowWorker:
    def __init__(self, service, data_dir, *, max_rows=250000, max_compressed_bytes=128 * 1024**2,
                 max_decoded_bytes=512 * 1024**2, max_allocated_bytes=12 * 1024**3,
                 lease_seconds=300, heartbeat_interval=30, archive_obsolete=False,
                 retention_batch_size=500, retention_max_batches=100, retention_passes=5):
        self.service, self.data_dir = service, Path(data_dir)
        self.max_rows, self.max_compressed_bytes = max_rows, max_compressed_bytes
        self.max_decoded_bytes, self.max_allocated_bytes = max_decoded_bytes, max_allocated_bytes
        self.lease_seconds, self.heartbeat_interval = lease_seconds, heartbeat_interval
        if (type(archive_obsolete) is not bool or type(retention_batch_size) is not int or not 1 <= retention_batch_size <= 500
                or type(retention_max_batches) is not int or not 1 <= retention_max_batches <= 100
                or type(retention_passes) is not int or not 1 <= retention_passes <= 5):
            raise ValueError("Invalid bounded retention configuration")
        self.archive_obsolete = archive_obsolete
        self.retention_batch_size, self.retention_max_batches = retention_batch_size, retention_max_batches
        self.retention_passes = retention_passes

    def _source_current(self, payload):
        if self.service.snapshot_head(payload["snapshot_name"])["snapshot_id"] != payload["snapshot_id"]:
            raise StaleSource("Source snapshot has been superseded")
        with self.service.engine.connect() as db:
            evidence = db.scalar(select(func.count()).select_from(s.snapshot_files).where(
                s.snapshot_files.c.snapshot_id == payload["snapshot_id"],
                s.snapshot_files.c.sha256 == payload["manifest_sha256"]))
        if not evidence:
            raise ValueError("Source manifest is not part of the published snapshot")

    def publication_guard(self, job, heartbeat):
        payload = job["payload"]

        def guard(db):
            heartbeat.check()
            # UPDATE is a portable row lock, including on SQLite. Snapshot
            # publishers cannot move this head until our publication commits.
            locked = db.execute(update(s.heads).where(s.heads.c.name == payload["snapshot_name"],
                s.heads.c.snapshot_id == payload["snapshot_id"]).values(snapshot_id=payload["snapshot_id"]))
            if locked.rowcount != 1:
                raise StaleSource("Source changed before monthly publication")
            at = now()
            until = (datetime.now(timezone.utc) + timedelta(seconds=self.lease_seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")
            fenced = db.execute(update(s.jobs).where(s.jobs.c.job_id == job["job_id"], s.jobs.c.status == "running",
                s.jobs.c.lease_token == job["lease_token"], s.jobs.c.lease_until >= at)
                .values(lease_until=until, updated_at=at))
            if fenced.rowcount != 1:
                raise LostLease("Job was expired or claimed by another worker")
            heartbeat.check()
        return guard

    def _download_validate(self, payload, path, heartbeat):
        info = self.service.file_info(payload["sha256"])
        if not 0 < info["byte_size"] <= self.max_compressed_bytes:
            raise ValueError("Compressed monthly artifact exceeds budget")
        actual, size = hashlib.sha256(), 0
        with path.open("xb") as out:
            for chunk in self.service.objects.chunks(self.service.project, payload["sha256"]):
                heartbeat.check()
                size += len(chunk)
                if size > info["byte_size"]:
                    raise ValueError("Artifact exceeds registered size")
                actual.update(chunk)
                out.write(chunk)
        if size != info["byte_size"] or actual.hexdigest() != payload["sha256"]:
            raise ValueError("Monthly artifact integrity failure")
        rows, decoded, json_bytes = 0, 0, 0
        month = payload["dataset"].rsplit("/", 1)[1]
        with gzip.open(path, "rb") as source:
            while line := source.readline(131074):
                heartbeat.check()
                decoded += len(line)
                rows += 1
                if len(line) > 131073 or decoded > self.max_decoded_bytes or rows > payload["rows"]:
                    raise ValueError("Expanded monthly artifact exceeds declared budget")
                value = json.loads(line)
                if not isinstance(value, dict) or (normalized(value)["observed_day"] or "")[:7] != month:
                    raise ValueError("Artifact contains a row outside the declared month")
                json_bytes += len(line.rstrip(b"\r\n"))
        if rows != payload["rows"] or rows == 0:
            raise ValueError("Artifact row count differs from its job")
        return json_bytes

    def _check_capacity(self, rows, json_bytes):
        if self.service.engine.dialect.name != "oracle":
            return
        with self.service.engine.connect() as db:
            used = int(db.scalar(text("SELECT COALESCE(SUM(bytes),0) FROM user_segments")))
            inline = db.scalar(text("SELECT in_row FROM user_lobs WHERE table_name='OBSERVATIONS' AND column_name='PAYLOAD'"))
        if inline != "YES" or used + (json_bytes + rows * 750) * 1.7 >= self.max_allocated_bytes:
            raise ValueError("Oracle storage headroom is insufficient for another month version")

    def process(self, job):
        payload = job["payload"]
        try:
            validate_payload(self.service.project, payload, self.max_rows)
            self._source_current(payload)
            version = digest(f"{payload['sha256']}:jsonl:item:utf-8:{NORMALIZER}".encode())
            with self.service.engine.connect() as db:
                current = db.scalar(select(s.datasets.c.version_id).where(s.datasets.c.dataset_key == payload["dataset"]))
            with LeaseHeartbeat(self.service, job, self.lease_seconds, self.heartbeat_interval) as heartbeat:
                if current == version:
                    with self.service.engine.begin() as db:
                        self.publication_guard(job, heartbeat)(db)
                    result = {"status": "unchanged", "dataset": payload["dataset"], "rows": payload["rows"]}
                else:
                    with tempfile.TemporaryDirectory(prefix="monthly-worker-", dir=self.data_dir) as temporary:
                        path = Path(temporary) / "month.jsonl.gz"
                        json_bytes = self._download_validate(payload, path, heartbeat)
                        self._source_current(payload)
                        self._check_capacity(payload["rows"], json_bytes)
                        imported = import_rows(self.service, payload["dataset"], path, "jsonl", encoding="utf-8",
                            publication_guard=self.publication_guard(job, heartbeat))
                        result = {"status": "imported", "dataset": payload["dataset"], "rows": imported["rows"], "changed": imported["changed"]}
                if self.archive_obsolete:
                    cleanup = {"deleted_rows": 0, "passes": 0, "complete": False}
                    try:
                        for _ in range(self.retention_passes):
                            heartbeat.check()
                            batch = prune_obsolete_rows(self.service, payload["dataset"],
                                batch_size=self.retention_batch_size, max_batches=self.retention_max_batches)
                            cleanup["deleted_rows"] += batch["deleted_rows"]
                            cleanup["passes"] += 1
                            if batch["cleanup_complete"]:
                                cleanup["complete"] = True
                                break
                        heartbeat.check()
                    except LostLease:
                        raise
                    except Exception as error:
                        # Publication already succeeded. Keep the leased job so
                        # an unchanged-head retry can resume bounded cleanup.
                        return {"status": "retry_after_lease", "phase": "retention", "published": True,
                                "cleanup": cleanup, "error": safe_error(error)}
                    if not cleanup["complete"]:
                        return {"status": "retry_after_lease", "phase": "retention", "published": True, "cleanup": cleanup}
                    result["cleanup"] = cleanup
                self.service.finish_job(job["job_id"], job["lease_token"], result)
            return result
        except StaleSource:
            result = {"status": "skipped_superseded_source"}
            try:
                self.service.finish_job(job["job_id"], job["lease_token"], result)
            except Conflict:
                return {"status": "lease_lost"}
            return result
        except LostLease:
            return {"status": "lease_lost"}
        except Conflict as error:
            return {"status": "retry_after_lease", "error": safe_error(error)}
        except (ValueError, EOFError, gzip.BadGzipFile) as error:
            result = {"status": "failed", "error": safe_error(error)}
            try:
                self.service.finish_job(job["job_id"], job["lease_token"], result, success=False)
            except Conflict:
                return {"status": "lease_lost"}
            return result
        except Exception as error:
            # Leave transient failures leased. claim_job will retry on expiry,
            # up to max_attempts; partial committed batches remain resumable.
            return {"status": "retry_after_lease", "error": safe_error(error)}

    def run_once(self, worker_id):
        with project_lock(self.data_dir, self.service.project) as acquired:
            if not acquired:
                return None
            job = self.service.claim_job(JOB_TYPE, worker_id, self.lease_seconds)
            return self.process(job) if job else None


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--project", choices=ALLOWED, required=True)
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--poll-seconds", type=int, default=30)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    if args.env_file:
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    settings = Settings.from_env()
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    databases = None
    try:
        if settings.database == "oracle" and os.getenv(args.project.upper() + "_ORACLE_USER") != args.project.upper() + "_APP":
            raise ValueError("Monthly workers require the project application account")
        databases = Databases(settings, projects=(args.project,))
        objects = LocalObjects(settings.data_dir / "objects") if settings.blob_store == "local" else OCIObjects()
        service = Service(databases.engine(args.project), objects, args.project)
        retention = os.getenv("RESEARCH_ROW_RETENTION", "false").lower()
        if retention not in {"true", "false"}:
            raise ValueError("RESEARCH_ROW_RETENTION must be true or false")
        worker = RowWorker(service, settings.data_dir, archive_obsolete=retention == "true")
        while not stop.is_set():
            result = worker.run_once("monthly-worker-" + args.project)
            if result:
                print(json.dumps({"project": args.project, **result}), flush=True)
            if args.once:
                return
            if result is None or result.get("status") in {"retry_after_lease", "lease_lost"}:
                stop.wait(max(30, args.poll_seconds))
    except Exception as error:
        print(json.dumps({"project": args.project, "status": "worker_failed", "error": safe_error(error)}), flush=True)
        raise SystemExit(1) from None
    finally:
        if databases is not None:
            databases.close()


if __name__ == "__main__":
    main()
