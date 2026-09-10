"""Explicit cleanup of obsolete SQL query copies; immutable source bytes survive.

No GET handler calls cleanup. Archive metadata retains the original row count
and source SHA. Each deletion transaction locks the current head and target
version again, and removes at most 500 generated observation rows.
"""
from __future__ import annotations

import hashlib
from sqlalchemy import and_, delete, exists, or_, select, update

from . import schema as s
from .util import Conflict, Missing, identifier, sha_value


def dataset_version(service, dataset_key, version=None):
    identifier(dataset_key, 150)
    if version is not None:
        sha_value(version)
    with service.engine.connect() as db:
        head = db.scalar(select(s.datasets.c.version_id).where(s.datasets.c.dataset_key == dataset_key))
        version = version or head
        row = db.execute(select(s.dataset_versions).where(s.dataset_versions.c.dataset_key == dataset_key,
            s.dataset_versions.c.version_id == version)).mappings().first() if version else None
    if not row:
        raise Missing("Dataset version not found")
    return dict(row) | {"is_current": version == head}


def _verified_source(service, metadata):
    sha = metadata["source_sha256"]
    info = service.file_info(sha)
    digest, size = hashlib.sha256(), 0
    # Verify actual immutable source bytes before deleting any reproducible SQL
    # copies, rather than trusting a metadata row for a missing object.
    for chunk in service.objects.chunks(service.project, sha):
        size += len(chunk)
        if size > info["byte_size"]:
            raise ValueError("Archive source exceeds its registered size")
        digest.update(chunk)
    if size != info["byte_size"] or digest.hexdigest() != sha:
        raise ValueError("Archive source failed integrity verification")


def _delete_observations(db, dataset_key, version, row_numbers):
    return db.execute(delete(s.observations).where(s.observations.c.dataset_key == dataset_key,
        s.observations.c.version_id == version, s.observations.c.row_no.in_(row_numbers))).rowcount


def archive_obsolete_version(service, dataset_key, version, *, batch_size=500, max_batches=20):
    """Archive one obsolete copy after publication, preserving its verified source.

    An interrupted cleanup is resumable. An archived version is never restored
    by a read or silently republished by the importer. Explicit reconstruction
    can use its retained source SHA and format metadata.
    """
    if type(batch_size) is not int or not 1 <= batch_size <= 500 or type(max_batches) is not int or not 1 <= max_batches <= 100:
        raise ValueError("Cleanup requires 1..500 rows per batch and 1..100 batches")
    metadata = dataset_version(service, dataset_key, version)
    if metadata["is_current"]:
        raise Conflict("Current dataset head cannot be archived")
    if metadata["status"] not in {"complete", "archived"}:
        raise Conflict("Only a previously published dataset version can be archived")
    _verified_source(service, metadata)
    removed, batches = 0, 0
    where = and_(s.dataset_versions.c.dataset_key == dataset_key, s.dataset_versions.c.version_id == version)
    done = False
    for _ in range(max_batches):
        with service.engine.begin() as db:
            # A no-op UPDATE also obtains a writer lock in SQLite; Oracle locks
            # the row. Import publication uses this same head -> version order.
            locked = db.execute(update(s.datasets).where(s.datasets.c.dataset_key == dataset_key)
                .values(updated_at=s.datasets.c.updated_at))
            if locked.rowcount != 1:
                raise Conflict("Cleanup requires a published dataset head")
            head = db.scalar(select(s.datasets.c.version_id).where(s.datasets.c.dataset_key == dataset_key))
            if head == version:
                raise Conflict("Dataset version became current; cleanup refused")
            locked_version = db.execute(update(s.dataset_versions).where(where,
                s.dataset_versions.c.status.in_(["complete", "archived"]))
                .values(status=s.dataset_versions.c.status))
            if locked_version.rowcount != 1:
                raise Conflict("Dataset version changed during cleanup")
            current = db.execute(select(s.dataset_versions).where(where)).mappings().one()
            if current["source_sha256"] != metadata["source_sha256"]:
                raise Conflict("Dataset source changed during cleanup")
            info = db.execute(select(s.files).where(s.files.c.sha256 == current["source_sha256"])).mappings().first()
            if not info or info["store_name"] != service.objects.name:
                raise Conflict("Verified source metadata disappeared; cleanup refused")
            row_numbers = db.scalars(select(s.observations.c.row_no).where(
                s.observations.c.dataset_key == dataset_key, s.observations.c.version_id == version)
                .order_by(s.observations.c.row_no).limit(batch_size)).all()
            db.execute(update(s.dataset_versions).where(where).values(status="archived"))
            if row_numbers:
                actual = _delete_observations(db, dataset_key, version, row_numbers)
                if actual != len(row_numbers):
                    raise Conflict("Observation copy changed during cleanup")
                removed += actual
                batches += 1
            remaining = db.scalar(select(s.observations.c.row_no).where(s.observations.c.dataset_key == dataset_key,
                s.observations.c.version_id == version).limit(1))
            done = remaining is None
        if done:
            break
    return {"dataset": dataset_key, "version": version, "status": "archived", "deleted_rows": removed,
            "batches": batches, "cleanup_complete": done, "source_sha256": metadata["source_sha256"],
            "original_row_count": metadata["row_count"]}


def prune_obsolete_rows(service, dataset_key, *, batch_size=500, max_batches=20):
    """Bounded per-dataset maintenance, intended only after successful publication."""
    identifier(dataset_key, 150)
    if type(max_batches) is not int or not 1 <= max_batches <= 100 or type(batch_size) is not int or not 1 <= batch_size <= 500:
        raise ValueError("Invalid cleanup budget")
    with service.engine.connect() as db:
        head = db.scalar(select(s.datasets.c.version_id).where(s.datasets.c.dataset_key == dataset_key))
        if not head:
            raise Missing("Published dataset not found")
        has_rows = exists(select(s.observations.c.row_no).where(s.observations.c.dataset_key == dataset_key,
            s.observations.c.version_id == s.dataset_versions.c.version_id))
        versions = db.scalars(select(s.dataset_versions.c.version_id).where(
            s.dataset_versions.c.dataset_key == dataset_key, s.dataset_versions.c.version_id != head,
            or_(s.dataset_versions.c.status == "complete", and_(s.dataset_versions.c.status == "archived", has_rows)))
            .order_by(s.dataset_versions.c.created_at, s.dataset_versions.c.version_id).limit(max_batches)).all()
    results, spent = [], 0
    for version in versions:
        result = archive_obsolete_version(service, dataset_key, version, batch_size=batch_size, max_batches=max_batches - spent)
        results.append(result)
        spent += max(result["batches"], 1)
        if spent >= max_batches:
            break
    return {"dataset": dataset_key, "deleted_rows": sum(result["deleted_rows"] for result in results),
            "versions": results, "batch_budget": max_batches,
            "cleanup_complete": len(versions) < max_batches and len(results) == len(versions)
                and all(result["cleanup_complete"] for result in results)}
