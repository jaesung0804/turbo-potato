"""Explicitly queue changed complete monthly CSV partitions after snapshot push.

Uses only the standard library and the vendored client. No collection is started.
The source CSV is checked against a manifest in the just-published snapshot.
"""
from __future__ import annotations

import argparse
from contextlib import ExitStack
import csv
from datetime import date
import gzip
import hashlib
import json
import os
from pathlib import Path
import tempfile
import urllib.parse

from research_backend_client import Client, BackendError

PROJECT = "estate"
NORMALIZER = "research-rows-v1"
JOB_TYPE = "monthly-row-import-v1"


def sha_file(path, decompressed=False):
    opener = gzip.open if decompressed and str(path).endswith(".gz") else open
    with opener(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True).encode("utf-8")


def monthly_files(source, directory, *, max_months=240, max_rows=250000):
    """Deterministic gzip matches the initial SQL bundle, including row order."""
    source, directory = Path(source), Path(directory)
    before = source.stat()
    opener = gzip.open if source.name.endswith(".gz") else open
    states = {}
    with ExitStack() as stack:
        stream = stack.enter_context(opener(source, "rt", encoding="utf-8-sig", newline=""))
        reader = csv.DictReader(stream)
        if not reader.fieldnames or len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV must have distinct headers")
        for row in reader:
            if None in row or any(value is None for value in row.values()):
                raise ValueError("Malformed CSV row")
            day = str(row.get("date") or row.get("CTRT_DAY") or "")[:10]
            if len(day) == 8 and day.isdigit():
                day = day[:4] + "-" + day[4:6] + "-" + day[6:]
            month = date.fromisoformat(day).isoformat()[:7]
            if month not in states:
                if len(states) >= max_months:
                    raise ValueError("Source has too many months for one explicit synchronization")
                path = directory / (month + ".jsonl.gz")
                raw = stack.enter_context(path.open("xb"))
                writer = stack.enter_context(gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0, compresslevel=6))
                states[month] = {"file": path, "rows": 0, "writer": writer}
            state = states[month]
            state["rows"] += 1
            if state["rows"] > max_rows:
                raise ValueError("Monthly row budget exceeded")
            body = canonical(row)
            if len(body) > 131072:
                raise ValueError("One source row exceeds the record budget")
            state["writer"].write(body + b"\n")
    after = source.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ValueError("CSV changed while preparing monthly artifacts")
    if not states:
        raise ValueError("Empty CSV refused; existing months remain intact")
    for month, state in sorted(states.items()):
        yield {"month": month, "file": state["file"], "rows": state["rows"], "sha256": sha_file(state["file"])}


def dataset_heads(client):
    heads, cursor = {}, ""
    while True:
        page = client.json("GET", "/datasets?" + urllib.parse.urlencode({"after": cursor, "limit": 200}))
        for item in page["items"]:
            heads[item["dataset_key"]] = item["version_id"]
        following = page["next_cursor"]
        if following is None:
            return heads
        if following == cursor:
            raise ValueError("Dataset cursor did not advance")
        cursor = following


def verify_source(client, source, state_dir, snapshot_name, dataset):
    if PROJECT == "investment":
        source_names = {"ohlcv-kr": "data/raw/krx_ohlcv_kospi_kosdaq_state.csv",
                        "ohlcv-us": "data/raw/us_ohlcv_nasdaq_nyse_yfinfo_state.csv"}
        if snapshot_name != "pipeline-state" or dataset not in source_names:
            raise ValueError("Unsupported investment source binding")
        manifest_name = "state-manifest.json"
    else:
        manifests = {"estate-raw-state": "raw_manifest.json", "estate-history-state": "research-history.json",
                     "estate-history-extended-state": "research-older-history.json"}
        if snapshot_name not in manifests or dataset != "apt-trades":
            raise ValueError("Unsupported estate source binding")
        manifest_name = manifests[snapshot_name]
    receipt = state_dir / ".research-backend" / (hashlib.sha256(snapshot_name.encode()).hexdigest() + ".json")
    sid = json.loads(receipt.read_text(encoding="utf-8"))["snapshot_id"]
    if client.json("GET", "/snapshot-heads/" + snapshot_name)["snapshot_id"] != sid:
        raise ValueError("Saved source receipt differs from the current snapshot head")
    manifest_path = state_dir / manifest_name
    if manifest_path.stat().st_size > 32 * 1024**2:
        raise ValueError("Source manifest exceeds its bounded metadata budget")
    manifest_sha = sha_file(manifest_path)
    remote = [item for item in client.snapshot_entries(sid) if item["relative_path"] == manifest_name]
    if len(remote) != 1 or remote[0]["sha256"] != manifest_sha:
        raise ValueError("Local source manifest differs from the published snapshot")
    metadata = json.loads(manifest_path.read_text(encoding="utf-8"))
    if PROJECT == "investment":
        expected_sha = metadata[source_names[dataset]]["sha256"]
        expected_rows = None
    else:
        metadata = metadata["collection"] if snapshot_name == "estate-raw-state" else metadata
        if metadata.get("complete") is not True or metadata.get("shard_complete") is not True:
            raise ValueError("Only a complete source export may update monthly observations")
        expected_sha, expected_rows = metadata["sha256"], metadata["rows"]
    if sha_file(source, decompressed=PROJECT == "estate") != expected_sha:
        raise ValueError("CSV differs from its published source manifest")
    return sid, manifest_sha, expected_rows


def queue_months(client, entries, *, dataset, snapshot_name, snapshot_id, manifest_sha256):
    heads = dataset_heads(client)
    if client.json("GET", "/snapshot-heads/" + snapshot_name)["snapshot_id"] != snapshot_id:
        raise ValueError("Source snapshot changed during CSV preparation")
    counts = {"months": 0, "unchanged_months": 0, "uploaded_months": 0, "queued_months": 0}
    for entry in entries:
        counts["months"] += 1
        key = dataset + "/" + entry["month"]
        version = hashlib.sha256(f"{entry['sha256']}:jsonl:item:utf-8:{NORMALIZER}".encode()).hexdigest()
        if heads.get(key) == version:
            counts["unchanged_months"] += 1
            continue
        info = client.upload(entry["file"])
        if info["sha256"] != entry["sha256"]:
            raise ValueError("Uploaded month differs from prepared bytes")
        counts["uploaded_months"] += bool(info.get("changed"))
        payload = {"schema": 1, "normalizer": NORMALIZER, "dataset": key, "sha256": entry["sha256"], "rows": entry["rows"],
                   "snapshot_name": snapshot_name, "snapshot_id": snapshot_id, "manifest_sha256": manifest_sha256}
        job_id = hashlib.sha256(canonical(payload)).hexdigest()
        client.json("POST", "/jobs", {"job_id": job_id, "job_type": JOB_TYPE, "payload": payload, "max_attempts": 3})
        if client.json("GET", "/jobs/" + job_id).get("status") == "failed":
            raise ValueError("This monthly job exhausted retries; inspect its worker result before retrying")
        counts["queued_months"] += 1
    if client.json("GET", "/snapshot-heads/" + snapshot_name)["snapshot_id"] != snapshot_id:
        raise ValueError("Source was superseded; obsolete jobs will be skipped by the worker")
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, required=True)
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--snapshot-name", default="pipeline-state" if PROJECT == "investment" else "estate-raw-state")
    parser.add_argument("--state-dir", type=Path, default=Path(".dashboard-state" if PROJECT == "investment" else ".work/raw-state"))
    args = parser.parse_args()
    if os.getenv("RESEARCH_STORAGE", "git") != "backend":
        print(json.dumps({"status": "legacy_storage_no_backend_jobs"}))
        return 0
    try:
        client = Client(project=PROJECT)
        sid, manifest_sha, expected_rows = verify_source(client, args.csv, args.state_dir, args.snapshot_name, args.dataset)
        with tempfile.TemporaryDirectory(prefix="monthly-observations-") as temporary:
            entries = list(monthly_files(args.csv, Path(temporary)))
            if expected_rows is not None and sum(item["rows"] for item in entries) != expected_rows:
                raise ValueError("CSV row count differs from its complete published source")
            result = queue_months(client, entries, dataset=args.dataset, snapshot_name=args.snapshot_name,
                                  snapshot_id=sid, manifest_sha256=manifest_sha)
        print(json.dumps({"status": "queued", "project": PROJECT, **result}))
        return 0
    except Exception as error:
        safe = {"status": "failed", "type": type(error).__name__}
        if isinstance(error, BackendError):
            safe["http_status"] = error.status
        print(json.dumps(safe))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
