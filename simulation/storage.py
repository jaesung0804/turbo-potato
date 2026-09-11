"""Authenticated, fail-closed storage for a bounded historical replay.

No local receipt, command-line flag, or user-authored readiness JSON can replace
the live capacity adapter in estate_research_storage.run_preflight.
"""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path
import tempfile
from urllib.parse import quote

from .engine import MAX_INPUT_BYTES, ReplayError, canonical

MAX_CHECKPOINT_BYTES = 5_000_000


def read_gzip_json(path: Path, limit: int) -> tuple[dict, bytes]:
    if path.stat().st_size > limit:
        raise ReplayError("Compressed input exceeds its bounded size limit")
    with gzip.open(path, "rb") as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ReplayError("Expanded input exceeds its bounded size limit")
    return json.loads(raw), raw


class BackendSession:
    def __init__(self, *, state_dir: Path, snapshot_name: str, input_path: str,
                 checkpoint_key: str, expected_checkpoint_version: int,
                 required_growth_bytes: int, client=None):
        from research_backend_client import Client, safe_path
        self.client = client or Client(project="estate")
        if self.client.project != "estate":
            raise ReplayError("A simulation cannot use another project's data")
        self.state_dir = Path(state_dir)
        self.snapshot_name = snapshot_name
        self.input_path = input_path
        self.path = safe_path(self.state_dir, input_path)
        if not input_path.endswith(".json.gz"):
            raise ReplayError("Historical inputs must be a compressed restored JSON feature panel")
        self.checkpoint_key = checkpoint_key
        self.expected_version = expected_checkpoint_version
        self.required_growth_bytes = required_growth_bytes
        self.identity = None
        self.last_artifact = None
        self.last_state_hash = None
        self.preflight_result = None

    @property
    def record_path(self):
        return "/records/checkpoints/" + quote(self.checkpoint_key, safe="")

    def _preflight(self, identity):
        from estate_research_storage import run_preflight
        result = run_preflight(state_dir=self.state_dir, snapshot_name=self.snapshot_name,
                               required_files=[self.input_path], checkpoint_key=self.checkpoint_key,
                               expected_checkpoint_version=self.expected_version,
                               required_growth_bytes=self.required_growth_bytes,
                               identity=identity, client=self.client)
        self.preflight_result = result
        if result.get("ready") is not True:
            reasons = ", ".join(str(item.get("code", "unknown")) for item in result.get("blockers", []))
            raise ReplayError("Historical replay blocked by live storage checks: " + (reasons or "not_ready"))
        return result

    def authorize(self, identity: dict) -> dict:
        self._preflight(identity)
        _, raw = read_gzip_json(self.path, MAX_INPUT_BYTES)
        if hashlib.sha256(raw).hexdigest() != identity["input_sha256"]:
            raise ReplayError("Restored input must match the canonical panel SHA")
        self.identity = dict(identity)
        return {"ready": True, "identity": self.identity}

    def load_checkpoint(self, run_id: str) -> dict:
        if self.identity is None or self.identity["run_id"] != run_id:
            raise ReplayError("Authorize exact inputs before restoring a checkpoint")
        record = self.client.json("GET", self.record_path)
        if record["version"] != self.expected_version or record["payload"].get("identity") != self.identity:
            raise ReplayError("Checkpoint changed after verification; reread and review the run")
        payload = record["payload"]
        if payload.get("status") == "prepared" and "artifact_sha256" not in payload:
            # Explicit prior initialization by the record workflow, never missing→empty fallback.
            return {"version": record["version"], "state": None}
        sha, size = payload["artifact_sha256"], payload["artifact_bytes"]
        if not 0 < size <= MAX_CHECKPOINT_BYTES:
            raise ReplayError("Checkpoint artifact size is invalid")
        with tempfile.TemporaryDirectory(prefix="estate-replay-restore-") as tmp:
            path = Path(tmp) / "checkpoint.json.gz"
            self.client.download(sha, path, expected_size=size)
            checkpoint, raw = read_gzip_json(path, MAX_CHECKPOINT_BYTES)
        if hashlib.sha256(raw).hexdigest() != payload["uncompressed_sha256"]:
            raise ReplayError("Expanded checkpoint checksum mismatch")
        if checkpoint.get("identity") != self.identity:
            raise ReplayError("Checkpoint artifact identity mismatch")
        self.last_artifact, self.last_state_hash = sha, checkpoint["state_sha256"]
        return {"version": record["version"], "state": checkpoint}

    def save_checkpoint(self, run_id: str, state: dict, expected_version: int) -> int:
        if self.identity is None or self.identity != state.get("identity") or run_id != self.identity["run_id"]:
            raise ReplayError("Cannot persist an unverified or different run")
        if expected_version != self.expected_version:
            raise ReplayError("Local checkpoint version changed")
        if state["state_sha256"] == self.last_state_hash:
            return expected_version
        raw = canonical(state)
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise ReplayError("Checkpoint exceeds 5 MB; reduce panel/decision frequency before another run")
        self._preflight(self.identity)
        compressed = gzip.compress(raw, mtime=0)
        if len(compressed) > self.required_growth_bytes:
            raise ReplayError("Checkpoint growth exceeds the live-checked allocation")
        with tempfile.TemporaryDirectory(prefix="estate-replay-save-") as tmp:
            path = Path(tmp) / "checkpoint.json.gz"
            path.write_bytes(compressed)
            info = self.client.upload(path)
            sha = hashlib.sha256(compressed).hexdigest()
            if info.get("sha256") != sha or info.get("byte_size") != len(compressed):
                raise ReplayError("Uploaded checkpoint metadata mismatch")
            restored = Path(tmp) / "verified.json.gz"
            self.client.download(sha, restored, expected_size=len(compressed))
            if restored.read_bytes() != compressed:
                raise ReplayError("Checkpoint read-after-write failed")
        payload = {"identity": self.identity, "status": "checkpointed", "artifact_sha256": sha,
                   "artifact_bytes": len(compressed), "uncompressed_sha256": hashlib.sha256(raw).hexdigest(),
                   "previous_artifact_sha256": self.last_artifact,
                   "source_snapshot_id": self.preflight_result["snapshot_id"],
                   "next_date": state["state"]["next_date"], "state_sha256": state["state_sha256"]}
        result = self.client.json("PUT", self.record_path, {"expected_version": expected_version,
                                 "payload": payload, "summary": "SIMULATION: " + run_id})
        # A 409 propagates without rereading merely to force the stale output through.
        if result.get("version") != expected_version + 1:
            raise ReplayError("Unexpected checkpoint version after compare-and-swap")
        self.expected_version = result["version"]
        self.last_artifact, self.last_state_hash = sha, state["state_sha256"]
        return self.expected_version
