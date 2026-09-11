"""Plan or explicitly apply the 25 bounded round-three handoff records.

Default is offline dry-run. The source stays outside Git. Applying uses live
reads and CAS version 0 for confirmed-new keys only; differing records stop
the run. The resumable report contains hashes/keys/versions, never payloads.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import quote

from estate_research_storage import configured_client, _failure_code
from research_backend_client import BackendError, canonical_json

SCHEMA = "fieldwork_round3_handoff_v1"
MAX_INPUT_BYTES = 1024 * 1024
MAX_RECORD_BYTES = 128 * 1024
MAX_RECORDS = 50
KINDS = {"research", "tasks", "sources", "checkpoints"}


def fingerprint(value):
    return hashlib.sha256(canonical_json(value)).hexdigest()


def validate_handoff(document):
    if not isinstance(document, dict) or document.get("schema_version") != SCHEMA or document.get("target_project") != "estate":
        raise ValueError("Expected the estate fieldwork_round3_handoff_v1 document")
    records = document.get("records")
    if not isinstance(records, list) or not 1 <= len(records) <= MAX_RECORDS or document.get("record_count") != len(records):
        raise ValueError("Record count is missing, inconsistent or exceeds 50")
    if len(canonical_json(document)) > MAX_INPUT_BYTES:
        raise ValueError("Handoff exceeds 1 MiB; split bounded records first")
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Each handoff record must be an object")
        kind, key, data = record.get("kind"), record.get("key"), record.get("data")
        if kind not in KINDS or not isinstance(key, str) or not re.fullmatch(r"fieldwork\.r3\.20260911\.[a-zA-Z0-9_.:/-]{1,160}", key):
            raise ValueError("Unexpected round-three record kind or key")
        if (kind, key) in seen:
            raise ValueError("Duplicate handoff key; review before writing")
        seen.add((kind, key))
        if not isinstance(data, dict) or data.get("model_eligible") is not False or len(canonical_json(data)) > MAX_RECORD_BYTES:
            raise ValueError("Preserve model_eligible=false and keep every payload within 128 KiB")
        if record.get("expected_version") is not None:
            raise ValueError("Source versions must be unset; always read actual backend versions")
        if not isinstance(record.get("summary"), str) or len(record["summary"].encode()) > 1800:
            raise ValueError("Invalid or oversized record summary")
    return records


def read_handoff(path):
    path = Path(path)
    if not path.is_file() or path.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError("Handoff must be an existing file no larger than 1 MiB")
    # Strict parse prevents duplicate JSON keys being silently replaced.
    def unique(pairs):
        out = {}
        for key, value in pairs:
            if key in out:
                raise ValueError("Duplicate JSON object key")
            out[key] = value
        return out
    document = json.loads(path.read_text(encoding="utf-8-sig"), object_pairs_hook=unique)
    validate_handoff(document)
    return document


def save_report(path, report):
    if path is None:
        return
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        raise ValueError("Report path must not be a symlink")
    fd, temporary = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def sync_records(document, *, client=None, apply=False, report_path=None):
    records = validate_handoff(document)
    if report_path is not None and Path(report_path).exists():
        previous_path = Path(report_path)
        if previous_path.stat().st_size > MAX_INPUT_BYTES:
            raise ValueError("Existing report is oversized; preserve it and select a new report path")
        previous = json.loads(previous_path.read_text(encoding="utf-8"))
        if previous.get("source_canonical_sha256") != fingerprint(document):
            raise ValueError("Existing report belongs to a different input; preserve it and select a new report path")
    report = {"schema_version": 1, "project": "estate", "source_schema": SCHEMA,
              "source_canonical_sha256": fingerprint(document), "record_count": len(records),
              "counts_by_kind": dict(Counter(v["kind"] for v in records)), "mode": "apply" if apply else "dry_run",
              "status": "planned", "backend_write_attempted": False, "write_attempts": 0,
              "confirmed_written": 0, "already_present": 0, "next_key": records[0]["key"],
              "error_code": None, "items": [
                  {"kind": r["kind"], "key": r["key"], "payload_sha256": fingerprint(r["data"]),
                   "status": "not_checked", "before_version": None, "confirmed_version": None}
                  for r in records],
              "resume_instruction": "Rerun the same input; reread every selected key, reuse identical payloads, stop on differences. Never skip based only on this report."}
    save_report(report_path, report)
    if client is None:
        if apply:
            report.update(status="blocked", error_code="private_backend_configuration_missing")
        save_report(report_path, report)
        return report
    if getattr(client, "project", None) != "estate":
        report.update(status="blocked", error_code="estate_project_required")
        save_report(report_path, report)
        return report

    def read_selected(path):
        try:
            return client.json("GET", path)
        except BackendError as error:
            if error.status == 404:
                return None
            raise

    def same_payload(remote, item):
        return remote is not None and fingerprint(remote["payload"]) == item["payload_sha256"]

    def confirmed(remote, item, status):
        version = remote["version"]
        if type(version) is not int or version < 1:
            raise ValueError("invalid_live_record_version")
        item.update(status=status, confirmed_version=version)

    try:
        ready = client.json("GET", "/ready")
        if ready.get("status") != "ready":
            raise ValueError("backend_not_ready")
        for kind in sorted({r["kind"] for r in records}):
            client.json("GET", "/records/" + kind + "?limit=20")
        for record, item in zip(records, report["items"]):
            report["next_key"] = record["key"]
            path = "/records/" + record["kind"] + "/" + quote(record["key"], safe="")
            item["status"] = "read_pending"
            save_report(report_path, report)
            current = read_selected(path)
            item["before_version"] = current["version"] if current else 0
            if same_payload(current, item):
                confirmed(current, item, "already_present")
                report["already_present"] += 1
            elif current is not None:
                confirmed(current, item, "conflict")
                item["server_payload_sha256"] = fingerprint(current["payload"])
                report.update(status="conflict", error_code="existing_payload_differs")
                save_report(report_path, report)
                return report
            elif not apply:
                item["status"] = "would_create"
            else:
                item["status"] = "write_pending"
                report["backend_write_attempted"] = True
                report["write_attempts"] += 1
                save_report(report_path, report)
                body = {"payload": record["data"], "summary": record["summary"], "expected_version": 0}
                # All source dates/provenance remain untouched in payload. Do
                # not invent a UTC timestamp for a date-only original source.
                try:
                    client.json("PUT", path, body)
                except BackendError as error:
                    if error.status != 409:
                        raise
                    raced = read_selected(path)
                    if same_payload(raced, item):
                        confirmed(raced, item, "already_present_after_conflict")
                        report["already_present"] += 1
                    else:
                        item["status"] = "conflict"
                        if raced is not None:
                            confirmed(raced, item, "conflict")
                            item["server_payload_sha256"] = fingerprint(raced["payload"])
                        report.update(status="conflict", error_code="concurrent_payload_differs")
                        save_report(report_path, report)
                        return report
                else:
                    # A successful PUT response is insufficient if another
                    # writer replaced the record immediately afterwards.
                    verified = read_selected(path)
                    if not same_payload(verified, item):
                        item["status"] = "write_unconfirmed"
                        if verified is not None:
                            item["confirmed_version"] = verified["version"]
                        report.update(status="conflict", error_code="post_write_payload_changed")
                        save_report(report_path, report)
                        return report
                    confirmed(verified, item, "written")
                    report["confirmed_written"] += 1
            save_report(report_path, report)
        report.update(status="complete" if apply else "verified_dry_run", next_key=None)
    except Exception as error:
        report.update(status="failed", error_code=_failure_code(error))
        # write_pending marks a possible committed write after a timeout. A
        # rerun rereads the key instead of trusting this inconclusive attempt.
    save_report(report_path, report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--apply", action="store_true", help="Explicit live CAS writes; default is offline dry-run")
    parser.add_argument("--check-backend", action="store_true", help="Read-only live existence/conflict check")
    parser.add_argument("--report", type=Path, default=Path(".work/estate-round3-sync-report.json"))
    args = parser.parse_args()
    try:
        if args.input.resolve() == args.report.resolve():
            raise ValueError("Source and report paths must differ")
        document = read_handoff(args.input)
        client, configuration_error = None, None
        if args.apply or args.check_backend:
            try:
                client = configured_client()
            except (KeyError, ValueError) as error:
                configuration_error = str(error) if str(error) in {
                    "backend_mode_required", "estate_project_required", "private_backend_configuration_missing"
                } else "private_backend_configuration_invalid"
        result = sync_records(document, client=client, apply=args.apply, report_path=args.report)
        if configuration_error:
            result.update(status="blocked", error_code=configuration_error)
            save_report(args.report, result)
        print(json.dumps({k: v for k, v in result.items() if k != "items"}, ensure_ascii=False, indent=2))
        return 0 if result["status"] in {"planned", "verified_dry_run", "complete"} else 2
    except Exception as error:
        print(json.dumps({"status": "blocked", "error_code": _failure_code(error),
                          "detail": "Operation stopped. Preserve the last report and reread live records before retrying an apply."}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
