from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from sqlalchemy import and_, or_, select, update, func
from sqlalchemy.exc import IntegrityError
from . import schema as s
from .util import Conflict, Missing, digest, identifier, json_text, now, safe_relative, sha_value, stamp

KINDS = {"agents", "tasks", "decisions", "meetings", "research", "rounds", "policies",
         "complexes", "visits", "rankings", "experiments", "sources", "checkpoints", "documents"}


class Service:
    def __init__(self, engine, objects, project, max_response_bytes=262144):
        self.engine, self.objects, self.project = engine, objects, project
        self.max_response_bytes = max_response_bytes

    def _record_where(self, kind, key=None):
        if kind not in KINDS:
            raise ValueError("Unknown record kind")
        where = s.records.c.kind == kind
        if key is not None:
            identifier(key, 200)
            where = and_(where, s.records.c.record_key == key)
        return where

    def get_record(self, kind, key):
        with self.engine.connect() as db:
            row = db.execute(select(s.records).where(self._record_where(kind, key))).mappings().first()
        if not row:
            raise Missing("Record not found")
        return dict(row) | {"payload": json.loads(row["payload"])}

    def put_record(self, kind, key, payload, expected_version, summary="", source_uri=None, observed_at=None):
        try:
            with self.engine.begin() as db:
                return self._put_record_in(db, kind, key, payload, expected_version, summary, source_uri, observed_at)
        except IntegrityError:
            raise Conflict("Concurrent record write; reread and retry") from None

    def _put_record_in(self, db, kind, key, payload, expected_version, summary="", source_uri=None, observed_at=None):
        where = self._record_where(kind, key)
        if expected_version < 0 or len(summary.encode()) > 1800:
            raise ValueError("Invalid version or summary exceeds 1800 bytes")
        body = json_text(payload)
        at, observed = now(), stamp(observed_at) if observed_at else now()
        summary = summary or key
        # Oracle stores empty strings as NULL; retries must compare the same value.
        source_uri = source_uri or None
        version = expected_version + 1
        values = dict(kind=kind, record_key=key, payload=body, sha256=digest(body.encode()), version=version,
                      summary=summary, source_uri=source_uri, observed_at=observed, updated_at=at)
        existing = db.execute(select(s.records).where(where)).mappings().first()
        if existing and existing["sha256"] == values["sha256"] and existing["summary"] == summary and existing["source_uri"] == source_uri:
            return {"key": key, "version": existing["version"], "changed": False}
        if existing:
            result = db.execute(update(s.records).where(where, s.records.c.version == expected_version).values(**values))
            if result.rowcount != 1:
                raise Conflict("Record changed; reread its version before writing")
        elif expected_version == 0:
            db.execute(s.records.insert().values(**values, created_at=at))
        else:
            raise Conflict("Expected record does not exist")
        db.execute(s.revisions.insert().values(kind=kind, record_key=key, version=version,
            payload=body, sha256=values["sha256"], observed_at=observed, created_at=at))
        return {"key": key, "version": version, "changed": True}

    def _reference_artifact(self, sha):
        from .client import REFERENCE_MAX_BYTES
        info = self.file_info(sha)
        if info["byte_size"] > REFERENCE_MAX_BYTES:
            raise ValueError("Reference artifact exceeds 1 MiB")
        body = bytearray()
        for chunk in self.objects.chunks(self.project, sha):
            body.extend(chunk)
            if len(body) > REFERENCE_MAX_BYTES:
                raise ValueError("Reference artifact exceeds 1 MiB")
        if len(body) != info["byte_size"] or digest(body) != sha:
            raise ValueError("Reference artifact failed integrity check")
        try:
            value = json.loads(body)
        except (ValueError, UnicodeError):
            raise ValueError("Reference artifact is not valid JSON") from None
        if not isinstance(value, dict):
            raise ValueError("Reference document must be an object")
        return value

    def sync_references(self, documents, records):
        """Atomically publish immutable JSON pointers and their bounded projections."""
        from .client import canonical_json, reference_delta, reference_projection
        if self.project != "investment" or not 1 <= len(documents) <= 10 or len(records) > 200:
            raise ValueError("Invalid investment reference transaction")
        json_text({"documents": documents, "records": records}, 1024 * 1024)
        operations, guards, doc_keys, expected_records = {}, {}, {}, []
        for document in documents:
            relative = document["relative_path"]
            key = ("documents", digest(relative.encode()))
            if relative in doc_keys or document["expected_version"] < 0:
                raise ValueError("Duplicate document or invalid expected version")
            sha = sha_value(document["artifact_sha256"])
            base = document["base_artifact_sha256"]
            if (document["expected_version"] == 0) != (base is None):
                raise ValueError("Reference base must match its expected version")
            previous = self._reference_artifact(sha_value(base)) if base else None
            current = self._reference_artifact(sha)
            old = reference_projection(relative, previous) if previous is not None else {}
            new = reference_projection(relative, current)
            expected_records.extend(reference_delta(relative, previous, current))
            doc_keys[relative] = key
            operations[key] = {"payload": {"artifact_sha256": sha, "format": "json", "relative_path": relative},
                               "summary": relative, "source_uri": None}
            for record_key, entry in new.items():
                pair = (entry["kind"], record_key)
                self._record_where(*pair)
                if pair in guards:
                    raise ValueError("Reference documents have overlapping records")
                guards[pair] = (digest(canonical_json(old[record_key]["payload"])) if record_key in old else None,
                                digest(canonical_json(entry["payload"])))
                if len(guards) > 200:
                    raise ValueError("Reference transaction exceeds 200 projected records; split the source first")
        sort_key = lambda record: (record["relative_path"], record["kind"], record["key"])
        if canonical_json(sorted(records, key=sort_key)) != canonical_json(sorted(expected_records, key=sort_key)):
            raise ValueError("Reference changes do not match the verified source artifacts")
        by_path = {document["relative_path"]: document for document in documents}
        for record in records:
            pair = (record["kind"], record["key"])
            json_text(record["payload"])
            relative = record["relative_path"]
            operations[pair] = {"payload": record["payload"], "summary": record["summary"],
                               "source_uri": f"github:jaesung0804/st_dashboard/{relative}#{by_path[relative]['artifact_sha256']}"}
        pairs = sorted(set(operations) | set(guards))
        try:
            with self.engine.begin() as db:
                locked = db.execute(select(s.records).where(or_(*(self._record_where(*pair) for pair in pairs)))
                                    .order_by(s.records.c.kind, s.records.c.record_key).with_for_update()).mappings()
                existing = {(row["kind"], row["record_key"]): row for row in locked}
                matches = lambda pair, value: (pair in existing and existing[pair]["sha256"] == digest(json_text(value["payload"]).encode())
                    and existing[pair]["summary"] == (value["summary"] or pair[1])
                    and existing[pair]["source_uri"] == value["source_uri"])
                # A lost successful response can be retried with the original
                # versions only when every requested new state still matches.
                if (all(matches(pair, value) for pair, value in operations.items())
                        and all(pair in existing and existing[pair]["sha256"] == hashes[1] for pair, hashes in guards.items())):
                    for document in documents:
                        key = doc_keys[document["relative_path"]]
                        row = existing[key]
                        expected = document["expected_version"]
                        if row["version"] == expected:
                            base_sha = json.loads(row["payload"]).get("artifact_sha256")
                        elif row["version"] == expected + 1:
                            previous_payload = db.scalar(select(s.revisions.c.payload).where(
                                s.revisions.c.kind == key[0], s.revisions.c.record_key == key[1],
                                s.revisions.c.version == expected)) if expected else None
                            if expected and previous_payload is None:
                                raise Conflict("Reference retry base revision is unavailable; reread before retrying")
                            base_sha = json.loads(previous_payload).get("artifact_sha256") if previous_payload else None
                        else:
                            raise Conflict("Reference retry version does not match; reread before retrying")
                        if base_sha != document["base_artifact_sha256"]:
                            raise Conflict("Reference retry base changed; reread before retrying")
                    return {"documents": [{"relative_path": path, "version": existing[key]["version"]}
                                          for path, key in doc_keys.items()], "records": len(records), "changed": False}
                for document in documents:
                    row = existing.get(doc_keys[document["relative_path"]])
                    if ((row["version"] if row else 0) != document["expected_version"]
                            or (json.loads(row["payload"]).get("artifact_sha256") if row else None) != document["base_artifact_sha256"]):
                        raise Conflict("Reference document changed; reread all documents before retrying")
                for pair, (before, _) in guards.items():
                    row = existing.get(pair)
                    if (row["sha256"] if row else None) != before:
                        raise Conflict("Reference record changed independently; reread and merge before retrying")
                results = {}
                for pair, value in sorted(operations.items()):
                    row = existing.get(pair)
                    results[pair] = self._put_record_in(db, *pair, expected_version=row["version"] if row else 0, **value)
                return {"documents": [{"relative_path": path, "version": results[key]["version"]}
                                      for path, key in doc_keys.items()], "records": len(records),
                        "changed": any(result["changed"] for result in results.values())}
        except IntegrityError:
            raise Conflict("Concurrent reference transaction; reread all documents before retrying") from None

    def list_records(self, kind, after="", limit=50):
        where = self._record_where(kind)
        cols = [s.records.c[k] for k in ("record_key", "version", "summary", "observed_at", "updated_at")]
        if after:
            where = and_(where, s.records.c.record_key > after)
        with self.engine.connect() as db:
            rows = db.execute(select(*cols).where(where)
                              .order_by(s.records.c.record_key).limit(limit + 1)).mappings().all()
        return self._page(rows, limit, "record_key")

    def _page(self, rows, limit, key, decode=False):
        result, size, more = [], 0, False
        for row in rows:
            item = dict(row)
            if decode:
                item["payload"] = json.loads(item["payload"])
            used = len(json_text(item, self.max_response_bytes).encode())
            if not result and used > self.max_response_bytes - 1024:
                raise ValueError("One record exceeds the configured response budget")
            if len(result) >= limit or size + used > self.max_response_bytes - 1024:
                more = True
                break
            result.append(item)
            size += used
        return {"items": result, "next_cursor": result[-1][key] if more and result else None}

    def register_file(self, sha, path):
        sha_value(sha)
        path = Path(path)
        with path.open("rb") as f:
            actual = hashlib.file_digest(f, "sha256").hexdigest()
        if actual != sha:
            raise ValueError("Artifact SHA-256 mismatch")
        size = path.stat().st_size
        with self.engine.connect() as db:
            existing = db.execute(select(s.files).where(s.files.c.sha256 == sha)).mappings().first()
        if existing:
            if existing["byte_size"] != size or existing["store_name"] != self.objects.name:
                raise Conflict("Artifact metadata/storage mismatch")
            return {"sha256": sha, "byte_size": size, "changed": False}
        self.objects.put(self.project, sha, path)
        try:
            with self.engine.begin() as db:
                db.execute(s.files.insert().values(sha256=sha, byte_size=size, store_name=self.objects.name, created_at=now()))
        except IntegrityError:
            # Concurrent identical uploads converge to the same immutable object.
            pass
        return {"sha256": sha, "byte_size": size, "changed": True}

    def file_info(self, sha):
        sha_value(sha)
        with self.engine.connect() as db:
            row = db.execute(select(s.files).where(s.files.c.sha256 == sha)).mappings().first()
        if not row:
            raise Missing("Artifact not found")
        if row["store_name"] != self.objects.name:
            raise Conflict("Configure the original artifact store before reading")
        return dict(row)

    def snapshot_head(self, name):
        identifier(name, 100)
        with self.engine.connect() as db:
            row = db.execute(select(s.heads).where(s.heads.c.name == name)).mappings().first()
        return dict(row) if row else {"name": name, "snapshot_id": None}

    def begin_snapshot(self, name, previous_id):
        identifier(name, 100)
        if self.snapshot_head(name)["snapshot_id"] != previous_id:
            raise Conflict("Snapshot head changed")
        sid = uuid4().hex
        with self.engine.begin() as db:
            db.execute(s.snapshots.insert().values(snapshot_id=sid, name=name, previous_id=previous_id,
                status="staging", file_count=0, created_at=now()))
        return {"snapshot_id": sid}

    def add_snapshot_files(self, sid, entries):
        if not 1 <= len(entries) <= 500:
            raise ValueError("Snapshot batch must contain 1..500 entries")
        normalized = {}
        for entry in entries:
            path = str(safe_relative(entry["relative_path"]))
            sha = sha_value(entry["sha256"])
            item = dict(snapshot_id=sid, entry_id=digest(path.encode()),
                        relative_path=path, sha256=sha, byte_size=int(entry["byte_size"]))
            if item["entry_id"] in normalized and normalized[item["entry_id"]] != item:
                raise Conflict("A snapshot cannot contain conflicting file versions")
            normalized[item["entry_id"]] = item
        with self.engine.begin() as db:
            # This update takes a row lock on Oracle and SQLite and serializes commit versus upload.
            locked = db.execute(update(s.snapshots).where(s.snapshots.c.snapshot_id == sid,
                s.snapshots.c.status == "staging").values(status="staging"))
            if locked.rowcount != 1:
                raise Conflict("Snapshot is not open for writes")
            # Three bounded batch queries instead of three WAN round trips per file.
            sizes = dict(db.execute(select(s.files.c.sha256, s.files.c.byte_size).where(
                s.files.c.sha256.in_({item["sha256"] for item in normalized.values()}))).all())
            existing = {row["entry_id"]: row for row in db.execute(select(s.snapshot_files).where(
                s.snapshot_files.c.snapshot_id == sid,
                s.snapshot_files.c.entry_id.in_(normalized))).mappings()}
            pending = []
            for eid, item in normalized.items():
                if item["sha256"] not in sizes or sizes[item["sha256"]] != item["byte_size"]:
                    raise ValueError("Upload and verify every artifact before referencing it")
                old = existing.get(eid)
                if old:
                    if old["sha256"] != item["sha256"] or old["relative_path"] != item["relative_path"]:
                        raise Conflict("A snapshot cannot contain conflicting file versions")
                    continue
                pending.append(item)
            if pending:
                db.execute(s.snapshot_files.insert(), pending)
        return {"accepted": len(entries)}

    def commit_snapshot(self, sid, expected_count):
        try:
            with self.engine.begin() as db:
                row = db.execute(select(s.snapshots).where(s.snapshots.c.snapshot_id == sid)).mappings().first()
                if not row:
                    raise Missing("Snapshot not found")
                if row["status"] == "complete":
                    if row["file_count"] != expected_count:
                        raise Conflict("Committed snapshot has a different file count")
                    return {"snapshot_id": sid, "file_count": expected_count}
                lock = db.execute(update(s.snapshots).where(s.snapshots.c.snapshot_id == sid,
                    s.snapshots.c.status == "staging").values(status="committing"))
                if lock.rowcount != 1:
                    raise Conflict("Snapshot state changed")
                count = db.scalar(select(func.count()).select_from(s.snapshot_files).where(s.snapshot_files.c.snapshot_id == sid))
                if count != expected_count or count == 0:
                    raise ValueError("Incomplete or empty snapshot; previous state preserved")
                if row["previous_id"]:
                    moved = db.execute(update(s.heads).where(s.heads.c.name == row["name"],
                        s.heads.c.snapshot_id == row["previous_id"]).values(snapshot_id=sid, updated_at=now()))
                    if moved.rowcount != 1:
                        raise Conflict("Another writer published first; previous state preserved")
                else:
                    db.execute(s.heads.insert().values(name=row["name"], snapshot_id=sid, updated_at=now()))
                db.execute(update(s.snapshots).where(s.snapshots.c.snapshot_id == sid).values(status="complete", file_count=count))
        except IntegrityError:
            raise Conflict("Another writer published first") from None
        return {"snapshot_id": sid, "file_count": expected_count}

    def snapshot_entries(self, sid, after="", limit=200):
        with self.engine.connect() as db:
            status = db.scalar(select(s.snapshots.c.status).where(s.snapshots.c.snapshot_id == sid))
            if status != "complete":
                raise Missing("Complete snapshot not found")
            query = select(s.snapshot_files).where(s.snapshot_files.c.snapshot_id == sid)
            if after:
                query = query.where(s.snapshot_files.c.entry_id > after)
            rows = db.execute(query.order_by(s.snapshot_files.c.entry_id).limit(limit + 1)).mappings().all()
        return self._page(rows, limit, "entry_id")

    def enqueue(self, key, job_type, payload, max_attempts=3):
        identifier(key, 64); identifier(job_type, 64)
        if not 1 <= max_attempts <= 10:
            raise ValueError("Invalid max_attempts")
        body, at = json_text(payload, 65536), now()
        try:
            with self.engine.begin() as db:
                db.execute(s.jobs.insert().values(job_id=key, job_type=job_type, status="queued", payload=body,
                    attempts=0, max_attempts=max_attempts, created_at=at, updated_at=at))
        except IntegrityError:
            with self.engine.connect() as db:
                old = db.execute(select(s.jobs).where(s.jobs.c.job_id == key)).mappings().one()
                if old["job_type"] != job_type or old["payload"] != body or old["max_attempts"] != max_attempts:
                    raise Conflict("Job idempotency key reused with different work") from None
        return {"job_id": key}

    def claim_job(self, job_type, worker_id, lease_seconds=300):
        identifier(job_type, 64); identifier(worker_id, 100)
        if not 10 <= lease_seconds <= 3600:
            raise ValueError("Lease must be 10..3600 seconds")
        at = now()
        expired = and_(s.jobs.c.status == "running", s.jobs.c.lease_until < at)
        available = and_(s.jobs.c.job_type == job_type, or_(s.jobs.c.status == "queued", expired),
                         s.jobs.c.attempts < s.jobs.c.max_attempts)
        with self.engine.begin() as db:
            db.execute(update(s.jobs).where(expired, s.jobs.c.attempts >= s.jobs.c.max_attempts)
                       .values(status="failed", updated_at=at, lease_token=None, lease_until=None))
            ids = db.execute(select(s.jobs.c.job_id).where(available).order_by(s.jobs.c.created_at).limit(10)).scalars().all()
            for jid in ids:
                token = uuid4().hex
                until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")
                changed = db.execute(update(s.jobs).where(available, s.jobs.c.job_id == jid).values(
                    status="running", worker_id=worker_id, lease_token=token, lease_until=until,
                    attempts=s.jobs.c.attempts + 1, updated_at=at))
                if changed.rowcount == 1:
                    row = dict(db.execute(select(s.jobs).where(s.jobs.c.job_id == jid)).mappings().one())
                    row["payload"] = json.loads(row["payload"])
                    return row
        return None

    def finish_job(self, jid, token, result, success=True):
        at = now()
        with self.engine.begin() as db:
            changed = db.execute(update(s.jobs).where(s.jobs.c.job_id == jid, s.jobs.c.status == "running",
                s.jobs.c.lease_token == token, s.jobs.c.lease_until >= at).values(
                status="completed" if success else "failed", result_payload=json_text(result, 65536),
                lease_token=None, lease_until=None, updated_at=at))
            if changed.rowcount != 1:
                raise Conflict("Job lease expired or belongs to another worker")
        return {"job_id": jid, "status": "completed" if success else "failed"}

    def renew_job(self, jid, token, lease_seconds=300):
        if not 10 <= lease_seconds <= 3600:
            raise ValueError("Lease must be 10..3600 seconds")
        at = now()
        until = (datetime.now(timezone.utc) + timedelta(seconds=lease_seconds)).isoformat(timespec="microseconds").replace("+00:00", "Z")
        with self.engine.begin() as db:
            changed = db.execute(update(s.jobs).where(s.jobs.c.job_id == jid, s.jobs.c.status == "running",
                s.jobs.c.lease_token == token, s.jobs.c.lease_until >= at).values(lease_until=until, updated_at=at))
            if changed.rowcount != 1:
                raise Conflict("Job lease expired or belongs to another worker")
        return {"lease_until": until}
