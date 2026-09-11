"""Read-only, fail-closed storage checks before estate historical experiments.

The existing backend proves connectivity, immutable bytes and record versions.
It does not currently expose live capacity/billing telemetry. Therefore the
production default always blocks historical work until that evidence exists.
No success certificate, credentials, uploads or restore receipts are created.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Protocol
from urllib.parse import quote

from research_backend_client import BackendError, Client, safe_path

MAX_VERIFY_BYTES = 64 * 1024 * 1024
MAX_EXPANDED_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class LiveCapacityEvidence:
    """Return type for a future independently implemented provider adapter.

    Each capacity is provider-wide (including the other project, revisions,
    staging objects and backups), not just the selected snapshot's file sizes.
    This module deliberately has no JSON/CLI route for supplying this evidence.
    """
    measured_at: datetime
    database_used_bytes: int
    database_limit_bytes: int
    object_used_bytes: int
    object_limit_bytes: int
    paid_resources_disabled: bool
    free_resource_configuration_verified: bool
    protected_snapshot_retention_verified: bool
    checkpoint_write_and_conflict_probe_verified: bool
    source: str


class LiveCapacityAdapter(Protocol):
    def inspect(self, client: Client) -> LiveCapacityEvidence:
        """Measure live provider configuration; never accept a self-report file."""


def _hash_file(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _failure_code(error):
    # Backend response text, URLs and raw transport exceptions can contain data.
    return "http_" + str(error.status) if isinstance(error, BackendError) else type(error).__name__


def configured_client():
    if os.environ.get("RESEARCH_STORAGE") != "backend":
        raise ValueError("backend_mode_required")
    if os.environ.get("RESEARCH_PROJECT", "estate") != "estate":
        raise ValueError("estate_project_required")
    if not all(os.environ.get(name) for name in ("RESEARCH_BACKEND_URL", "RESEARCH_BACKEND_TOKEN")):
        raise ValueError("private_backend_configuration_missing")
    return Client(project="estate")


def run_preflight(*, state_dir, snapshot_name, required_files, checkpoint_key,
                  expected_checkpoint_version, required_growth_bytes, identity=None,
                  client=None, live_capacity_adapter=None):
    """Check actual backend state without collecting, training, writing or deleting.

    required_files are compressed files already restored by backend_state.py.
    identity.input_sha256, when present, is the hash of one decompressed input.
    A positive result belongs to this head/version only; callers must retain
    those original versions for CAS writes and stop when either changes.
    """
    result = {"schema_version": 1, "project": "estate", "ready": False,
              "checks": {}, "blockers": [], "snapshot_id": None, "checkpoint_version": None}

    def block(code, detail):
        result["blockers"].append({"code": code, "detail": detail})

    if live_capacity_adapter is None:
        block("live_capacity_and_free_tier_unverifiable",
              "Current backend API has no live provider-wide capacity, free-resource, protected-retention or checkpoint write/conflict probe telemetry.")
    if (not isinstance(required_growth_bytes, int) or isinstance(required_growth_bytes, bool)
            or required_growth_bytes <= 0):
        block("growth_budget_required", "Declare a positive worst-case storage growth budget including checkpoints and revisions.")
    if (not isinstance(required_files, (list, tuple)) or not 1 <= len(required_files) <= 64
            or not all(isinstance(path, str) for path in required_files)
            or len(set(required_files)) != len(required_files)):
        block("compressed_inputs_required", "Declare 1..64 distinct saved compressed input paths.")
        return result
    if (not isinstance(checkpoint_key, str)
            or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.:/-]{0,199}", checkpoint_key)
            or not isinstance(expected_checkpoint_version, int)
            or isinstance(expected_checkpoint_version, bool) or expected_checkpoint_version < 1):
        block("checkpoint_version_required", "Read an existing bounded checkpoint and provide its positive version.")
        return result
    if not isinstance(snapshot_name, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,149}", snapshot_name):
        block("invalid_snapshot_name", "Use the exact existing estate snapshot name.")
        return result
    if identity is not None:
        if (not isinstance(identity, dict) or not isinstance(identity.get("run_id"), str)
                or not all(isinstance(identity.get(key), str) and re.fullmatch(r"[a-f0-9]{64}", identity[key])
                           for key in ("input_sha256", "policy_sha256", "engine_sha256"))):
            block("invalid_experiment_identity", "Bind the run to input, policy and engine SHA-256 values.")
            return result
    try:
        if os.environ.get("RESEARCH_STORAGE") != "backend":
            block("backend_mode_required", "RESEARCH_STORAGE must be configured as backend in the private runtime.")
            return result
        if os.environ.get("RESEARCH_PROJECT", "estate") != "estate":
            block("estate_project_required", "The private execution project must be estate.")
            return result
        client = client or configured_client()
        if getattr(client, "project", None) != "estate":
            block("estate_project_required", "The authenticated client must address the estate project.")
            return result
    except (ValueError, KeyError):
        block("private_backend_configuration_missing", "A private backend URL and project token are not configured.")
        return result

    try:
        ready = client.json("GET", "/ready")
        if ready.get("status") != "ready" or ready.get("database") != "oracle" or ready.get("blob_store") != "oci":
            block("production_backend_not_ready", "Require a ready Oracle/OCI backend; local test storage cannot authorize historical work.")
            return result
        result["checks"]["connectivity"] = "verified"
        head_path = "/snapshot-heads/" + quote(snapshot_name, safe="")
        sid = client.json("GET", head_path)["snapshot_id"]
        if not isinstance(sid, str) or not re.fullmatch(r"[a-f0-9]{32}", sid):
            block("snapshot_missing", "An existing complete source snapshot is required; do not initialize empty state.")
            return result
        result["snapshot_id"] = sid
        root = Path(state_dir).resolve()
        receipt_path = safe_path(root, ".research-backend/" + hashlib.sha256(snapshot_name.encode()).hexdigest() + ".json")
        if not receipt_path.is_file() or receipt_path.stat().st_size > 4096:
            block("restore_receipt_missing", "Restore matching state with backend_state.py pull before an experiment.")
            return result
        if json.loads(receipt_path.read_text(encoding="utf-8"))["snapshot_id"] != sid:
            block("restored_snapshot_conflict", "The original restore receipt and current head differ; recompute from the new source.")
            return result
        selected, seen = {}, set()
        for index, entry in enumerate(client.snapshot_entries(sid)):
            if index >= 10000:
                block("snapshot_manifest_limit", "Plan a smaller saved input snapshot before verifying this experiment.")
                return result
            relative = entry["relative_path"]
            if relative in seen:
                raise ValueError("duplicate_snapshot_path")
            seen.add(relative)
            if relative in required_files:
                selected[relative] = entry
        if set(selected) != set(required_files):
            block("source_coverage_missing", "At least one required compressed input is absent from the committed snapshot.")
            return result
        total = sum(entry["byte_size"] for entry in selected.values())
        if not 0 < total <= MAX_VERIFY_BYTES:
            block("verification_byte_limit", "Use a bounded compressed panel of at most 64 MiB; this preflight never restores a full corpus.")
            return result
        expanded_total, verified_inputs = 0, []
        with tempfile.TemporaryDirectory(prefix="estate-storage-check-") as tmp:
            for index, (relative, entry) in enumerate(sorted(selected.items())):
                local = safe_path(root, relative)
                sha, size = entry["sha256"], entry["byte_size"]
                if (not relative.endswith(".gz") or not re.fullmatch(r"[a-f0-9]{64}", sha)
                        or not isinstance(size, int) or isinstance(size, bool) or size <= 0):
                    raise ValueError("invalid_compressed_input")
                if not local.is_file() or local.stat().st_size != size or _hash_file(local) != sha:
                    block("restored_file_integrity_failed", "Saved local bytes do not match the immutable source manifest.")
                    return result
                info = client.json("GET", "/files/" + sha + "/info")
                if info.get("sha256") != sha or info.get("byte_size") != size:
                    raise ValueError("backend_file_metadata_mismatch")
                restored = Path(tmp) / str(index)
                client.download(sha, restored, size)
                # Recheck even a custom client must not substitute unchecked bytes.
                if restored.stat().st_size != size or _hash_file(restored) != sha:
                    raise ValueError("backend_download_integrity_failed")
                digest = hashlib.sha256()
                with gzip.open(restored, "rb") as stream:
                    while chunk := stream.read(1024 * 1024):
                        expanded_total += len(chunk)
                        if expanded_total > MAX_EXPANDED_BYTES:
                            raise ValueError("compressed_expansion_budget_exceeded")
                        digest.update(chunk)
                verified_inputs.append({"relative_path": relative, "compressed_sha256": sha,
                                        "input_sha256": digest.hexdigest(), "compressed_bytes": size})
        if identity is not None and identity["input_sha256"] not in {v["input_sha256"] for v in verified_inputs}:
            block("input_identity_mismatch", "The experiment input hash is not the restored compressed panel's payload hash.")
            return result
        result["checks"]["compressed_restore"] = verified_inputs
        # Follow the existing bounded agent memory list-then-get protocol.
        client.json("GET", "/records/checkpoints?limit=20")
        record_path = "/records/checkpoints/" + quote(checkpoint_key, safe="")
        checkpoint = client.json("GET", record_path)
        version = checkpoint["version"]
        result["checkpoint_version"] = version
        if version != expected_checkpoint_version:
            block("checkpoint_conflict", "The checkpoint changed since the caller read it; stop and reconcile.")
            return result
        payload = checkpoint["payload"]
        if payload.get("source_snapshot_id") != sid or (identity is not None and payload.get("identity") != identity):
            block("checkpoint_source_mismatch", "Checkpoint identity must retain the original source snapshot and input/policy/engine hashes.")
            return result
        result["checks"]["checkpoint"] = {"key": checkpoint_key, "version": version}
        if client.json("GET", head_path)["snapshot_id"] != sid:
            block("source_changed_during_verification", "The source head changed during verification; recompute instead of replacing its receipt.")
        reread = client.json("GET", record_path)
        if reread["version"] != version or reread["payload"] != payload:
            block("checkpoint_changed_during_verification", "The checkpoint changed during verification; stop and reconcile.")
    except Exception as error:
        code = "checkpoint_or_source_missing" if isinstance(error, BackendError) and error.status == 404 else "backend_verification_failed"
        block(code, "Read-only verification failed (" + _failure_code(error) + "); no collection or write was attempted.")
        return result

    if live_capacity_adapter is not None:
        try:
            evidence = live_capacity_adapter.inspect(client)
            if not isinstance(evidence, LiveCapacityEvidence):
                raise ValueError("typed_live_provider_evidence_required")
            age = (datetime.now(timezone.utc) - evidence.measured_at).total_seconds()
            sizes = (evidence.database_used_bytes, evidence.database_limit_bytes,
                     evidence.object_used_bytes, evidence.object_limit_bytes)
            if (not 0 <= age <= 900 or not evidence.source
                    or not all(type(n) is int and n >= 0 for n in sizes)
                    or not all(flag is True for flag in
                               (evidence.paid_resources_disabled, evidence.free_resource_configuration_verified,
                                evidence.protected_snapshot_retention_verified,
                                evidence.checkpoint_write_and_conflict_probe_verified))):
                raise ValueError("incomplete_or_stale_provider_measurement")
            if (evidence.database_used_bytes + required_growth_bytes > evidence.database_limit_bytes * 0.8
                    or evidence.object_used_bytes + required_growth_bytes > evidence.object_limit_bytes * 0.8):
                block("capacity_headroom_insufficient", "The declared worst-case growth would exceed 80% of a verified provider-wide limit.")
            else:
                result["checks"]["live_capacity"] = {"measured_at": evidence.measured_at.isoformat(),
                                                     "growth_budget_bytes": required_growth_bytes}
        except Exception as error:
            block("live_capacity_verification_failed", "Live provider evidence could not be verified (" + _failure_code(error) + ").")
    result["ready"] = not result["blockers"]
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-dir", type=Path, default=Path(".work/estate-simulation-panel"))
    parser.add_argument("--snapshot-name", default="estate-simulation-panel")
    parser.add_argument("--required-file", action="append", default=[])
    parser.add_argument("--checkpoint-key", default="simulation.readiness")
    parser.add_argument("--expected-checkpoint-version", type=int, default=0)
    parser.add_argument("--required-growth-bytes", type=int, default=0)
    args = parser.parse_args()
    result = run_preflight(state_dir=args.state_dir, snapshot_name=args.snapshot_name,
                           required_files=args.required_file, checkpoint_key=args.checkpoint_key,
                           expected_checkpoint_version=args.expected_checkpoint_version,
                           required_growth_bytes=args.required_growth_bytes)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))
    return 0 if result["ready"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
