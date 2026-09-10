"""Stream source rows into immutable versions; only complete versions become visible."""
from __future__ import annotations

import csv
import gzip
import hashlib
import json
import tempfile
from collections import OrderedDict
from pathlib import Path
from sqlalchemy import and_, select, update, func
from sqlalchemy.exc import IntegrityError
from . import schema as s
from .util import Conflict, Missing, digest, identifier, json_text, now

NORMALIZER = "research-rows-v1"


def normalized(row):
    ticker = str(row.get("ticker") or row.get("symbol") or "")
    region = str(row.get("CGG_CD") or row.get("region_code") or row.get("code") or "")
    day = str(row.get("date") or row.get("CTRT_DAY") or row.get("observed_day") or "")[:10]
    if len(day) == 8 and day.isdigit():
        day = f"{day[:4]}-{day[4:6]}-{day[6:8]}"
    if day:
        from datetime import date
        day = date.fromisoformat(day).isoformat()
    if ticker:
        entity = ticker
    elif row.get("BLDG_NM"):
        # Apartment names alone are not unique. Preserve lot/address and district in identity.
        entity = digest(json_text([row.get(k, "") for k in
            ("CGG_CD", "STDG_CD", "STDG_NM", "MNO", "SNO", "BLDG_NM")]).encode())
    else:
        entity = str(row.get("complex_id") or row.get("entity_key") or "")
    if len(entity.encode()) > 200 or len(region.encode()) > 20:
        raise ValueError("Entity/region key too long")
    return {"entity_key": entity or None, "observed_day": day or None, "region_code": region or None}


def iter_rows(path, format_name, prefix="item", encoding="utf-8-sig"):
    opener = gzip.open if str(path).endswith(".gz") else open
    if format_name == "csv":
        with opener(path, "rt", encoding=encoding, newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
                raise ValueError("CSV requires distinct headers")
            for row in reader:
                if None in row or any(v is None for v in row.values()):
                    raise ValueError("Malformed CSV row")
                yield row
    elif format_name == "jsonl":
        with opener(path, "rt", encoding=encoding) as stream:
            for line in stream:
                if line.strip():
                    value = json.loads(line)
                    if not isinstance(value, dict):
                        raise ValueError("Rows must be objects")
                    yield value
    elif format_name == "json":
        import ijson
        with opener(path, "rb") as stream:
            for row in ijson.items(stream, prefix, use_float=True):
                if not isinstance(row, dict):
                    raise ValueError("Rows must be objects")
                yield row
    else:
        raise ValueError("Unsupported input format")


def import_rows(service, dataset_key, path, format_name="csv", prefix="item", encoding="utf-8-sig", allow_empty=False,
                *, publication_guard=None, batch_size=500):
    if type(batch_size) is not int or not 1 <= batch_size <= 500:
        raise ValueError("Import batch_size must be an integer from 1 to 500")
    identifier(dataset_key, 150)
    path = Path(path)
    # The exact bytes read by the importer are immutable even if a collector replaces the source.
    with tempfile.TemporaryDirectory(prefix="research-import-") as tmp:
        staged = Path(tmp) / path.name
        import shutil
        with path.open("rb") as source, staged.open("wb") as target:
            shutil.copyfileobj(source, target, 1024 * 1024)
        with staged.open("rb") as source:
            sha = hashlib.file_digest(source, "sha256").hexdigest()
        version = digest(f"{sha}:{format_name}:{prefix}:{encoding}:{NORMALIZER}".encode())
        where = and_(s.dataset_versions.c.dataset_key == dataset_key, s.dataset_versions.c.version_id == version)
        with service.engine.connect() as db:
            head = db.execute(select(s.datasets).where(s.datasets.c.dataset_key == dataset_key)).mappings().first()
            if head and head["version_id"] == version:
                return {"dataset": dataset_key, "version": version, "rows": head["row_count"], "changed": False}
            existing = db.execute(select(s.dataset_versions).where(where)).mappings().first()
        if existing and existing["status"] == "archived":
            raise Conflict("Archived SQL copies require an explicit reconstruction; use the retained source artifact")
        previous = head["version_id"] if head else None
        if not existing:
            try:
                with service.engine.begin() as db:
                    db.execute(s.dataset_versions.insert().values(dataset_key=dataset_key, version_id=version,
                        source_sha256=sha, status="staging", row_count=0, format_name=format_name,
                        created_at=now()))
            except IntegrityError:
                raise Conflict("Another importer is initializing this version; retry") from None
        # Incomplete versions are resumable. Each committed batch owns its exact row range.
        with service.engine.connect() as db:
            saved = db.scalar(select(func.count()).select_from(s.observations).where(
                s.observations.c.dataset_key == dataset_key, s.observations.c.version_id == version))
        count, batch = 0, []
        for count, row in enumerate(iter_rows(staged, format_name, prefix, encoding), 1):
            if count <= saved:
                continue
            if not isinstance(row, dict):
                raise ValueError("Dataset rows must be objects")
            batch.append(dict(dataset_key=dataset_key, version_id=version, row_no=count,
                              payload=json_text(row), **normalized(row)))
            if len(batch) >= batch_size:
                _insert_batch(service, batch)
                batch = []
        if batch:
            _insert_batch(service, batch)
        if count == 0 and not allow_empty:
            raise ValueError("Empty import refused; use explicit allow_empty for a verified empty partition")
        service.register_file(sha, staged)
        try:
            with service.engine.begin() as db:
                # Optional worker fencing runs in the same transaction as the
                # completed-version/head publication. Existing CLI imports have
                # no guard and preserve their original behavior.
                if publication_guard is not None:
                    publication_guard(db)
                # Serialize against explicit retention before checking row counts.
                # Current head is always preserved if an archived copy is chosen.
                db.execute(update(s.datasets).where(s.datasets.c.dataset_key == dataset_key)
                    .values(updated_at=s.datasets.c.updated_at))
                locked = db.execute(update(s.dataset_versions).where(where, s.dataset_versions.c.status != "archived")
                    .values(status=s.dataset_versions.c.status))
                if locked.rowcount != 1:
                    raise Conflict("Dataset version was archived during import; explicit reconstruction is required")
                actual = db.scalar(select(func.count()).select_from(s.observations).where(
                    s.observations.c.dataset_key == dataset_key, s.observations.c.version_id == version))
                if count != actual:
                    raise ValueError("Imported row count differs from source")
                db.execute(update(s.dataset_versions).where(where).values(status="complete", row_count=count))
                if previous:
                    changed = db.execute(update(s.datasets).where(s.datasets.c.dataset_key == dataset_key,
                        s.datasets.c.version_id == previous).values(version_id=version, row_count=count, updated_at=now()))
                    if changed.rowcount != 1:
                        raise Conflict("Dataset head changed during import; do not overwrite newer data")
                else:
                    db.execute(s.datasets.insert().values(dataset_key=dataset_key, version_id=version,
                        row_count=count, updated_at=now()))
        except IntegrityError:
            raise Conflict("Dataset was concurrently published; reread before retrying") from None
        return {"dataset": dataset_key, "version": version, "rows": count, "changed": True}


def _insert_batch(service, batch):
    try:
        with service.engine.begin() as db:
            first = batch[0]
            locked = db.execute(update(s.dataset_versions).where(
                s.dataset_versions.c.dataset_key == first["dataset_key"], s.dataset_versions.c.version_id == first["version_id"],
                s.dataset_versions.c.status != "archived").values(status=s.dataset_versions.c.status))
            if locked.rowcount != 1:
                raise Conflict("Dataset version was archived during import; preserve the current head")
            db.execute(s.observations.insert(), batch)
    except IntegrityError:
        # No silent conflict suppression: caller retries from the last complete batch.
        raise Conflict("Concurrent import; retry the same source to resume safely") from None


def import_monthly_csv(service, dataset_key, path, encoding="utf-8-sig"):
    """Partition by month. Unchanged history converges to identical hashes on every run."""
    identifier(dataset_key, 140)
    with tempfile.TemporaryDirectory(prefix="research-months-") as temp:
        handles, paths = OrderedDict(), {}
        try:
            for row in iter_rows(path, "csv", encoding=encoding):
                day = normalized(row)["observed_day"]
                if not day:
                    raise ValueError("Monthly import requires a date on every row")
                month = day[:7]
                if month not in handles:
                    if len(handles) >= 32:
                        _, handle = handles.popitem(last=False)
                        handle.close()
                    paths[month] = Path(temp) / (month + ".jsonl")
                    handles[month] = paths[month].open("a", encoding="utf-8", newline="\n")
                handles.move_to_end(month)
                handles[month].write(json_text(row) + "\n")
        finally:
            for handle in handles.values():
                handle.close()
        if not paths:
            raise ValueError("Empty monthly source; existing history preserved")
        # Missing months are retained. Dropping history requires a separate reviewed migration.
        for month, staged in sorted(paths.items()):
            yield import_rows(service, dataset_key + "/" + month, staged, "jsonl", encoding="utf-8")


def query_rows(service, dataset_key, *, version=None, entity=None, region=None, start=None, end=None, after=0, limit=50):
    identifier(dataset_key, 150)
    with service.engine.connect() as db:
        if version is None:
            version = db.scalar(select(s.datasets.c.version_id).where(s.datasets.c.dataset_key == dataset_key))
        if not version:
            raise Missing("Dataset not found")
        status = db.scalar(select(s.dataset_versions.c.status).where(s.dataset_versions.c.dataset_key == dataset_key,
            s.dataset_versions.c.version_id == version))
        if status != "complete":
            raise Missing("Complete dataset version not found")
        q = select(s.observations).where(s.observations.c.dataset_key == dataset_key,
            s.observations.c.version_id == version, s.observations.c.row_no > after)
        for column, value in ((s.observations.c.entity_key, entity), (s.observations.c.region_code, region)):
            if value is not None:
                q = q.where(column == value)
        if start:
            q = q.where(s.observations.c.observed_day >= start)
        if end:
            q = q.where(s.observations.c.observed_day <= end)
        rows = db.execute(q.order_by(s.observations.c.row_no).limit(limit + 1)).mappings().all()
    # Pin this version while paging so a concurrent collection cannot mix versions.
    return {"version": version, **service._page(rows, limit, "row_no", decode=True)}
