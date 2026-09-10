"""Prepare complete monthly row bundles, then apply them explicitly on the VM.

prepare is local/file-only. apply is the only command that opens a configured DB.
The source files and the published bundle are immutable inputs; rerun apply with
the same bundle after interruption to resume its committed 500-row batches.
"""
from __future__ import annotations

import argparse
import calendar
from collections import Counter
from contextlib import ExitStack
from datetime import date, datetime, timezone
import gzip
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sys
import time
from uuid import uuid4

ROOT = Path(__file__).resolve().parent.parent
if (ROOT / "outputs/research-backend/src").is_dir():
    sys.path.insert(0, str(ROOT / "outputs/research-backend/src"))
logging.disable(logging.CRITICAL)

from research_backend.ingest import NORMALIZER, import_rows, iter_rows, normalized, query_rows
from research_backend.util import digest, json_text

SOURCES = {
    "ohlcv-kr": {"project": "investment", "dataset": "ohlcv-kr", "month": "2026-08",
        "path": "investment-migration-source/data/raw/krx_ohlcv_kospi_kosdaq_state.csv"},
    "ohlcv-us": {"project": "investment", "dataset": "ohlcv-us", "month": "2026-08",
        "path": "investment-migration-source/data/raw/us_ohlcv_nasdaq_nyse_yfinfo_state.csv"},
    "estate-current": {"project": "estate", "dataset": "apt-trades", "month": "2026-08", "expected_rows": 1146204,
        "path": "estate-source-validation/raw-restored/data/capital_area_apt_trade_transactions.csv"},
    "estate-2016-2020": {"project": "estate", "dataset": "apt-trades", "month": "2020-12", "expected_rows": 477522,
        "path": "estate-migration-source/estate-history-state/research-history.csv.gz"},
    "estate-2006-2015": {"project": "estate", "dataset": "apt-trades", "month": "2015-12", "expected_rows": 740326,
        "path": "estate-migration-source/estate-history-extended-state/research-older-history.csv.gz"},
}


class PreparationError(Exception):
    pass


def require(condition, label):
    if not condition:
        raise PreparationError(label)


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.replace(temp, path)


def safe_error(error):
    result = {"type": type(error).__name__}
    if isinstance(error, PreparationError):
        result["check"] = error.args[0]
    for value in (error, getattr(error, "orig", None)):
        if value is None:
            continue
        for item in (value, *getattr(value, "args", ())):
            code = getattr(item, "full_code", None)
            if isinstance(code, str) and re.fullmatch(r"(?:ORA|DPY|DPI)-\d{4,5}", code):
                result["database_code"] = code
    return result


def source_snapshot_guard(project, name, snapshot_id):
    require(bool(name) == bool(snapshot_id), 'source_snapshot_options_required_together')
    if project == 'investment':
        require(name == 'pipeline-state' and isinstance(snapshot_id, str)
                and re.fullmatch(r'[a-f0-9]{32}', snapshot_id) is not None,
                'investment_requires_pinned_pipeline_snapshot')
    elif not name:
        return None
    else:
        require(False, 'source_snapshot_guard_is_investment_only')
    from sqlalchemy import update
    from research_backend import schema as s
    from research_backend.util import Conflict

    def guard(db):
        # The source row stays locked until the dataset publication commits.
        # A preflight read alone cannot prevent a newer snapshot racing us.
        locked = db.execute(update(s.heads).where(s.heads.c.name == name,
            s.heads.c.snapshot_id == snapshot_id).values(snapshot_id=s.heads.c.snapshot_id))
        if locked.rowcount != 1:
            raise Conflict('Pinned source snapshot was superseded; preserve current dataset heads')
    return guard


def import_with_retry(service, item, path, retries=3, batch_size=500, publication_guard=None):
    retryable = {'ORA-30036', 'ORA-03113', 'ORA-03114', 'DPY-4024', 'DPY-4011', 'DPY-1001'}
    for attempt in range(retries + 1):
        try:
            return import_rows(service, item['dataset'], path, 'jsonl', encoding='utf-8', batch_size=batch_size,
                               publication_guard=publication_guard)
        except Exception as error:
            safe = safe_error(error)
            if safe.get('database_code') not in retryable or attempt >= retries:
                raise
            service.engine.dispose()
            print(json.dumps({'dataset': item['dataset'], 'retry': attempt + 1,
                              'wait_seconds': 30, 'error': safe}), flush=True)
            time.sleep(30)


def row_day(row):
    value = str(row.get("date") or row.get("CTRT_DAY") or "")[:10]
    if len(value) == 8 and value.isdigit():
        value = value[:4] + "-" + value[4:6] + "-" + value[6:]
    return date.fromisoformat(value).isoformat()


def prepare_source(name, spec, source, output, month):
    """Scan the complete source; select the complete month without row limits."""
    require(re.fullmatch(r"\d{4}-\d{2}", month) is not None, "valid_month")
    date.fromisoformat(month + "-01")
    source, output = Path(source), Path(output)
    before = source.stat()
    original_sha = sha256(source)
    destination = output / f"{name}-{month}.jsonl.gz"
    require(not destination.exists(), "bundle_files_are_immutable_use_new_output_directory")
    temporary = destination.with_name(destination.name + "." + uuid4().hex + ".partial")
    output.mkdir(parents=True, exist_ok=True)
    months, entities, regions = Counter(), Counter(), Counter()
    duplicate_entity_days, unique_entity_days = 0, set()
    samples, sample_bytes, sample_max, selected_bytes, selected_max = 0, 0, 0, 0, 0
    selected_rows, total, start, end, columns, probe = 0, 0, None, None, None, None
    with temporary.open("xb") as raw, gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6) as compressed:
        for total, row in enumerate(iter_rows(source, "csv"), 1):
            if columns is None:
                columns = list(row)
            day = row_day(row)
            current_month = day[:7]
            months[current_month] += 1
            start = min(start, day) if start else day
            end = max(end, day) if end else day
            body = None
            if total <= 1000 or total % 997 == 0:
                body = json_text(row).encode("utf-8")
                samples += 1
                sample_bytes += len(body)
                sample_max = max(sample_max, len(body))
            if current_month != month:
                continue
            body = body if body is not None else json_text(row).encode("utf-8")
            keys = normalized(row)
            require(keys["observed_day"] == day, "normalizer_day_matches_partition")
            entities[keys["entity_key"]] += 1
            regions[keys["region_code"]] += 1
            if spec["project"] == "investment":
                pair = (keys["entity_key"], day)
                duplicate_entity_days += pair in unique_entity_days
                unique_entity_days.add(pair)
            if probe is None:
                probe = {"entity": keys["entity_key"], "region": keys["region_code"], "day": day}
            compressed.write(body + b"\n")
            selected_rows += 1
            selected_bytes += len(body)
            selected_max = max(selected_max, len(body))
    after = source.stat()
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), "source_unchanged_during_scan")
    require(total > 0 and selected_rows > 0, "source_and_selected_month_are_nonempty")
    if spec.get("expected_rows") is not None:
        require(total == spec["expected_rows"], "source_count_matches_verified_inventory")
    os.replace(temporary, destination)
    average = sample_bytes / samples
    probe["entity_rows"] = entities[probe["entity"]]
    probe["region_rows"] = regions[probe["region"]]
    return {
        "source_id": name, "project": spec["project"], "dataset": spec["dataset"] + "/" + month,
        "month": month, "file": destination.name, "sha256": sha256(destination),
        "compressed_bytes": destination.stat().st_size, "rows": selected_rows,
        "json_bytes": selected_bytes, "max_json_bytes": selected_max,
        "duplicate_entity_days": duplicate_entity_days if spec["project"] == "investment" else None,
        "probe": probe,
        "source": {"path": str(source.resolve()), "sha256": original_sha, "bytes": before.st_size,
            "rows": total, "columns": columns, "date_start": start, "date_end": end,
            "monthly_counts": dict(sorted(months.items())), "sample_rows": samples,
            "mean_json_bytes": round(average, 2), "max_sample_json_bytes": sample_max,
            "payload_only_estimate_bytes": round(total * average),
            "allocated_estimate_low_bytes": round(total * (average + 350) * 1.25),
            "allocated_estimate_high_bytes": round(total * (average + 750) * 1.7)},
    }


def prepare_all_source(name, spec, source, output):
    """One source scan for every month; at most 240 small gzip writers stay open.

    Only counters and the first query probe are kept per month. No list/set of
    millions of rows, entity/day pairs, or apartment identities is retained.
    """
    source, output = Path(source), Path(output)
    before, original_sha = source.stat(), sha256(source)
    output.mkdir(parents=True, exist_ok=True)
    states = {}
    total, total_json, max_json, start, end, columns = 0, 0, 0, None, None, None
    with ExitStack() as stack:
        for total, row in enumerate(iter_rows(source, "csv"), 1):
            day = row_day(row)
            month = day[:7]
            if columns is None:
                columns = list(row)
            start, end = min(start, day) if start else day, max(end, day) if end else day
            body = json_text(row).encode("utf-8")
            keys = normalized(row)
            require(keys["observed_day"] == day, "normalizer_day_matches_partition")
            total_json += len(body)
            max_json = max(max_json, len(body))
            if month not in states:
                require(len(states) < 240, "source_at_most_240_months_per_bundle")
                destination = output / f"{name}-{month}.jsonl.gz"
                require(not destination.exists(), "bundle_files_are_immutable_use_new_output_directory")
                temporary = destination.with_name(destination.name + "." + uuid4().hex + ".partial")
                raw = stack.enter_context(temporary.open("xb"))
                compressed = stack.enter_context(gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6))
                states[month] = {"destination": destination, "temporary": temporary, "writer": compressed,
                    "rows": 0, "json_bytes": 0, "max_json_bytes": 0,
                    "probe": {"entity": keys["entity_key"], "region": keys["region_code"], "day": day,
                              "entity_rows": 0, "region_rows": 0}}
            state = states[month]
            state["writer"].write(body + b"\n")
            state["rows"] += 1
            state["json_bytes"] += len(body)
            state["max_json_bytes"] = max(state["max_json_bytes"], len(body))
            state["probe"]["entity_rows"] += keys["entity_key"] == state["probe"]["entity"]
            state["probe"]["region_rows"] += keys["region_code"] == state["probe"]["region"]
    after = source.stat()
    require(total > 0, "source_is_nonempty")
    require((before.st_size, before.st_mtime_ns) == (after.st_size, after.st_mtime_ns), "source_unchanged_during_scan")
    if spec.get("expected_rows") is not None:
        require(total == spec["expected_rows"], "source_count_matches_verified_inventory")
    average = total_json / total
    source_info = {"path": str(source.resolve()), "sha256": original_sha, "bytes": before.st_size,
        "rows": total, "columns": columns, "date_start": start, "date_end": end,
        "monthly_counts": {month: state["rows"] for month, state in sorted(states.items())},
        "sample_rows": total, "mean_json_bytes": round(average, 2), "max_sample_json_bytes": max_json,
        "payload_only_estimate_bytes": total_json,
        "allocated_estimate_low_bytes": round(total * (average + 350) * 1.25),
        "allocated_estimate_high_bytes": round(total * (average + 750) * 1.7)}
    entries = []
    for month, state in sorted(states.items()):
        os.replace(state["temporary"], state["destination"])
        entries.append({"source_id": name, "project": spec["project"], "dataset": spec["dataset"] + "/" + month,
            "month": month, "file": state["destination"].name, "sha256": sha256(state["destination"]),
            "compressed_bytes": state["destination"].stat().st_size, "rows": state["rows"],
            "json_bytes": state["json_bytes"], "max_json_bytes": state["max_json_bytes"],
            "duplicate_entity_days": None, "duplicate_scan": "not_performed_full_bundle_preserves_every_source_row",
            "probe": state["probe"], "source": source_info})
    return entries


def prepare(args):
    output = args.output.resolve()
    require(not (output / "manifest.json").exists(), "published_bundle_is_immutable_use_new_output_directory")
    overrides = dict(item.split("=", 1) for item in args.source)
    months = dict(item.split("=", 1) for item in args.month)
    names = args.include or list(SOURCES)
    require(not (args.all_months and months), "all_months_cannot_mix_with_month_overrides")
    require(set(overrides) <= set(SOURCES) and set(months) <= set(SOURCES), "known_source_override")
    entries = []
    for name in names:
        spec = SOURCES[name]
        source = Path(overrides.get(name, args.source_root / spec["path"]))
        prepared = prepare_all_source(name, spec, source, output) if args.all_months else [
            prepare_source(name, spec, source, output, months.get(name, spec["month"]))]
        entries.extend(prepared)
        print(json.dumps({"source": name, "source_rows": prepared[0]["source"]["rows"], "prepared_months": len(prepared),
                          "prepared_rows": sum(item["rows"] for item in prepared),
                          "compressed_bytes": sum(item["compressed_bytes"] for item in prepared)}), flush=True)
    require(len({(item["project"], item["dataset"]) for item in entries}) == len(entries), "no_overlapping_source_months")
    if {"ohlcv-kr", "ohlcv-us"} <= set(names):
        per_source = {item["source_id"]: item["source"]["rows"] for item in entries if item["project"] == "investment"}
        require(sum(per_source.values()) == 9058324,
                "investment_rows_match_verified_inventory")
    manifest = {"format": "live-row-bundle-v1", "prepared_at": datetime.now(timezone.utc).isoformat(),
        "normalizer": NORMALIZER, "uploaded_to_database": False, "entries": entries,
        "selection": "all_available_months" if args.all_months else "representative_months",
        "month_scope": "Every selected partition contains all rows present in its verified source for that month. The latest ongoing calendar month may still receive future observations.",
        "capacity_estimate_note": "Heuristic only: sampled JSON plus 350..750 bytes of table/LOB/index metadata per row, then 1.25..1.7 allocation factors. Measure USER_SEGMENTS on the VM pilot before full ingestion. Existing versions and the duplicate investment region index also consume quota.",
        "oracle_lob_reference": "https://docs.oracle.com/en/database/oracle/oracle-database/19/adlob/LOB-storage-with-applications.html",
        "query_contract": "Use the complete monthly dataset key and pin the returned version while paging; entity is the original ticker for OHLCV or normalized apartment identity hash for trades. Trade duplicates are retained. Cross-month callers query each month explicitly."}
    write_json(output / "manifest.json", manifest)
    return manifest


def validate_bundle(manifest_path, project=None):
    manifest_path = Path(manifest_path).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    require(manifest["format"] == "live-row-bundle-v1" and manifest["normalizer"] == NORMALIZER, "compatible_bundle")
    entries = [item for item in manifest["entries"] if project is None or item["project"] == project]
    require(entries and len({(item["project"], item["dataset"]) for item in entries}) == len(entries), "nonempty_unique_datasets")
    for item in entries:
        path = (manifest_path.parent / item["file"]).resolve()
        require(path.is_relative_to(manifest_path.parent) and path.is_file(), "bundle_file_inside_manifest_directory")
        require(sha256(path) == item["sha256"], "bundle_sha256_verified")
        count = 0
        for count, row in enumerate(iter_rows(path, "jsonl", encoding="utf-8"), 1):
            require(row_day(row)[:7] == item["month"], "bundle_contains_only_complete_declared_month")
        require(count == item["rows"], "bundle_row_count_verified")
    return manifest, entries


def oracle_storage(engine):
    if engine.dialect.name != "oracle":
        return None
    from sqlalchemy import text
    with engine.connect() as db:
        allocated = int(db.scalar(text("SELECT COALESCE(SUM(bytes),0) FROM user_segments")))
        lob = db.execute(text("SELECT in_row FROM user_lobs WHERE table_name='OBSERVATIONS' AND column_name='PAYLOAD'")).scalar()
        require(lob == "YES", "oracle_payload_lob_must_allow_inline_storage")
        quotas = [int(value) for value in db.execute(text("SELECT max_bytes FROM user_ts_quotas WHERE tablespace_name='DATA'")).scalars() if value is not None and value > 0]
    return {"allocated_bytes": allocated, "quota_bytes": min(quotas) if quotas else None, "payload_lob_in_row": True}


def verify_queries(service, item, version):
    from sqlalchemy import func, select
    from research_backend.schema import observations
    probe = item["probe"]
    common = [observations.c.dataset_key == item["dataset"], observations.c.version_id == version]
    with service.engine.connect() as db:
        count = db.scalar(select(func.count()).select_from(observations).where(*common))
        entity_count = db.scalar(select(func.count()).select_from(observations).where(*common, observations.c.entity_key == probe["entity"]))
    require(count == item["rows"] and entity_count == probe["entity_rows"], "complete_month_and_entity_counts")
    page = query_rows(service, item["dataset"], version=version, entity=probe["entity"],
                      start=item["month"] + "-01",
                      end=item["month"] + f"-{calendar.monthrange(*map(int, item['month'].split('-')))[1]:02d}", limit=2)
    require(page["items"] and all(row["entity_key"] == probe["entity"] for row in page["items"]), "entity_month_query")
    if page["next_cursor"]:
        following = query_rows(service, item["dataset"], version=version, entity=probe["entity"],
                               after=page["next_cursor"], limit=2)
        require(all(row["row_no"] > page["next_cursor"] for row in following["items"]), "pinned_cursor_progresses")
    if probe["region"]:
        region_page = query_rows(service, item["dataset"], version=version, region=probe["region"], limit=2)
        require(region_page["items"] and all(row["region_code"] == probe["region"] for row in region_page["items"]), "region_month_query")


def apply(args):
    # Validate every file before loading credentials or touching a database.
    manifest, entries = validate_bundle(args.manifest, args.project)
    require(sum(item["rows"] for item in entries) <= args.max_rows, "explicit_row_budget")
    snapshot_name = getattr(args, 'source_snapshot_name', None)
    snapshot_id = getattr(args, 'source_snapshot_id', None)
    publication_guard = source_snapshot_guard(args.project, snapshot_name, snapshot_id)
    require(args.env_file.is_file(), "explicit_environment_file_exists")
    from dotenv import load_dotenv
    from research_backend.config import Settings
    from research_backend.database import Databases
    from research_backend.objects import LocalObjects, OCIObjects
    from research_backend.service import Service
    from sqlalchemy import select
    from research_backend import schema as s
    load_dotenv(args.env_file, override=True)
    settings = Settings.from_env()
    if settings.database == "oracle":
        require(os.getenv(args.project.upper() + "_ORACLE_USER") == args.project.upper() + "_APP", "app_schema_only")
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    lock = (settings.data_dir / ("row-import-" + args.project + ".lock")).open("a+b")
    if os.name == "nt":
        import msvcrt
        lock.seek(0)
        if os.fstat(lock.fileno()).st_size == 0:
            lock.write(b"0"); lock.flush()
        lock.seek(0)
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    else:
        import fcntl
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    databases = Databases(settings, projects=(args.project,))
    attempts = []
    if args.report.is_file():
        previous_report = json.loads(args.report.read_text())
        require(previous_report.get('project') == args.project, 'report_project_matches')
        attempts = previous_report.get('previous_attempts', []) + [{
            'status': previous_report.get('status'), 'completed_months': len(previous_report.get('partitions', [])),
            'error': previous_report.get('error')}]
    report = {"project": args.project, "database": settings.database, "status": "running", "partitions": [],
              'previous_attempts': attempts, 'source_snapshot_name': snapshot_name, 'source_snapshot_id': snapshot_id}
    write_json(args.report, report)
    try:
        objects = LocalObjects(settings.data_dir / "objects") if settings.blob_store == "local" else OCIObjects()
        service = Service(databases.engine(args.project), objects, args.project)
        initial = oracle_storage(service.engine)
        report["initial_storage"] = initial
        for item in entries:
            if publication_guard is not None:
                from research_backend.util import Conflict
                if service.snapshot_head(snapshot_name)['snapshot_id'] != snapshot_id:
                    raise Conflict('Pinned source snapshot was superseded; preserve current dataset heads')
            storage = oracle_storage(service.engine)
            expected_version = digest(f"{item['sha256']}:jsonl:item:utf-8:{NORMALIZER}".encode())
            with service.engine.connect() as db:
                previous = db.scalar(select(s.datasets.c.version_id).where(s.datasets.c.dataset_key == item["dataset"]))
            if storage and previous != expected_version:
                quota = storage["quota_bytes"]
                ceiling = min(args.max_allocated_gib * 1024**3, quota * 0.9 if quota else float("inf"))
                conservative_next = item["rows"] * (item["json_bytes"] / item["rows"] + 750) * 1.7
                require(storage["allocated_bytes"] + conservative_next < ceiling, "oracle_capacity_headroom_before_next_month")
            started = time.monotonic()
            result = import_with_retry(service, item, args.manifest.resolve().parent / item["file"],
                                       getattr(args, 'retries', 3), getattr(args, 'batch_size', 500),
                                       publication_guard=publication_guard)
            require(result["rows"] == item["rows"] and result["version"] == expected_version, "import_manifest_count_and_version")
            verify_queries(service, item, result["version"])
            final = oracle_storage(service.engine)
            completed = {"dataset": item["dataset"], "rows": result["rows"], "changed": result["changed"],
                         "seconds": round(time.monotonic() - started, 3), "queries_verified": True, "storage": final}
            if storage and final and result["changed"]:
                completed["allocated_bytes_per_pilot_row"] = round(max(0, final["allocated_bytes"] - storage["allocated_bytes"]) / result["rows"], 2)
            report["partitions"].append(completed)
            write_json(args.report, report)
            print(json.dumps(completed), flush=True)
            if result['changed']:
                time.sleep(getattr(args, 'month_pause_seconds', 0))
        report["status"] = "complete"
        write_json(args.report, report)
        return report
    except Exception as error:
        report.update(status="failed_resumable", error=safe_error(error))
        write_json(args.report, report)
        raise
    finally:
        databases.close()
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prep = commands.add_parser("prepare", help="File-only scan and complete-month gzip bundle")
    prep.add_argument("--source-root", type=Path, default=ROOT / "work")
    prep.add_argument("--source", action="append", default=[], metavar="SOURCE_ID=PATH")
    prep.add_argument("--month", action="append", default=[], metavar="SOURCE_ID=YYYY-MM")
    prep.add_argument("--include", nargs="+", choices=SOURCES)
    prep.add_argument("--all-months", action="store_true", help="Prepare all available months in one scan per source")
    prep.add_argument("--output", type=Path, default=ROOT / "work/live-row-pilot")
    verify = commands.add_parser("verify", help="Verify bundle hashes, counts and month boundaries without DB access")
    verify.add_argument("--manifest", type=Path, required=True)
    run = commands.add_parser("apply", help="Explicitly import an already prepared bundle; rerun to resume")
    run.add_argument("--manifest", type=Path, required=True)
    run.add_argument("--env-file", type=Path, required=True)
    run.add_argument("--project", choices=("estate", "investment"), required=True)
    run.add_argument("--max-rows", type=int, default=250000)
    run.add_argument("--max-allocated-gib", type=float, default=12.0)
    run.add_argument("--report", type=Path, required=True)
    run.add_argument('--retries', type=int, choices=range(6), default=3)
    run.add_argument('--batch-size', type=int, default=500)
    run.add_argument('--source-snapshot-name', help='Investment requires pipeline-state plus its pinned snapshot id')
    run.add_argument('--source-snapshot-id', help='Immutable source snapshot used to prepare the investment bundle')
    run.add_argument('--month-pause-seconds', type=float, default=1.0)
    args = parser.parse_args()
    if args.command == 'apply' and not 0 <= args.month_pause_seconds <= 60:
        parser.error('--month-pause-seconds must be between 0 and 60')
    if args.command == 'apply' and not 1 <= args.batch_size <= 500:
        parser.error('--batch-size must be between 1 and 500')
    try:
        if args.command == "prepare":
            result = prepare(args)
            print(json.dumps({"status": "prepared", "partitions": len(result["entries"]), "database_writes": 0}))
        elif args.command == "verify":
            _, entries = validate_bundle(args.manifest)
            print(json.dumps({"status": "verified", "partitions": len(entries), "rows": sum(item["rows"] for item in entries), "database_writes": 0}))
        else:
            apply(args)
    except Exception as error:
        print(json.dumps({"status": "failed", "error": safe_error(error)}), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
