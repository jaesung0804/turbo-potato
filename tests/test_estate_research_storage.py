"""Bounded synthetic tests; none of these fixtures are provider evidence."""
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from estate_research_storage import LiveCapacityEvidence, run_preflight


class MemoryBackend:
    project = "estate"
    sid = "a" * 32

    def __init__(self, root, content=b'{"synthetic":true}'):
        self.raw = content
        self.compressed = gzip.compress(content, mtime=0)
        self.sha = hashlib.sha256(self.compressed).hexdigest()
        self.calls = []
        self.head_reads = 0
        self.move_head = False
        self.identity = {"run_id": "synthetic-test", "input_sha256": hashlib.sha256(content).hexdigest(),
                         "policy_sha256": "b" * 64, "engine_sha256": "c" * 64}
        self.checkpoint = {"version": 3, "payload": {"source_snapshot_id": self.sid, "identity": self.identity}}
        (root / "input.json.gz").write_bytes(self.compressed)
        receipt = root / ".research-backend" / (hashlib.sha256(b"estate-synthetic-panel").hexdigest() + ".json")
        receipt.parent.mkdir()
        receipt.write_text(json.dumps({"snapshot_id": self.sid}))

    def json(self, method, path, body=None):
        self.calls.append((method, path))
        assert method == "GET", "Preflight must never mutate even synthetic fixtures"
        if path == "/ready":
            return {"status": "ready", "database": "oracle", "blob_store": "oci"}
        if path.startswith("/snapshot-heads/"):
            self.head_reads += 1
            return {"snapshot_id": "d" * 32 if self.move_head and self.head_reads > 1 else self.sid}
        if path.endswith("/info"):
            return {"sha256": self.sha, "byte_size": len(self.compressed)}
        if path == "/records/checkpoints?limit=20":
            return {"items": [], "next_cursor": None}
        if path == "/records/checkpoints/simulation.test":
            return deepcopy(self.checkpoint)
        raise AssertionError(path)

    def snapshot_entries(self, sid):
        assert sid == self.sid
        yield {"relative_path": "input.json.gz", "sha256": self.sha, "byte_size": len(self.compressed)}

    def download(self, sha, target, size):
        assert sha == self.sha and size == len(self.compressed)
        Path(target).write_bytes(self.compressed)


class SyntheticCapacityAdapter:
    """Tests only; no implementation of this adapter ships to production."""
    def inspect(self, client):
        return LiveCapacityEvidence(datetime.now(timezone.utc), 10, 10000, 10, 10000,
                                    True, True, True, True, "synthetic-unit-test")


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("RESEARCH_STORAGE", "backend")
    monkeypatch.setenv("RESEARCH_PROJECT", "estate")
    backend = MemoryBackend(tmp_path)
    args = dict(state_dir=tmp_path, snapshot_name="estate-synthetic-panel", required_files=["input.json.gz"],
                checkpoint_key="simulation.test", expected_checkpoint_version=3, required_growth_bytes=100,
                identity=backend.identity, client=backend)
    return backend, args


def codes(result):
    return {v["code"] for v in result["blockers"]}


def test_unconfigured_storage_does_not_attempt_network(setup, monkeypatch):
    backend, args = setup
    monkeypatch.delenv("RESEARCH_STORAGE")
    result = run_preflight(**args)
    assert not result["ready"] and "backend_mode_required" in codes(result)
    assert backend.calls == []


def test_real_default_stays_blocked_despite_connected_verified_bytes(setup):
    backend, args = setup
    result = run_preflight(**args)
    assert result["checks"]["compressed_restore"][0]["input_sha256"] == backend.identity["input_sha256"]
    assert result["checkpoint_version"] == 3
    assert not result["ready"] and codes(result) == {"live_capacity_and_free_tier_unverifiable"}


def test_stale_restore_receipt_blocks_before_download(setup):
    backend, args = setup
    path = next((args["state_dir"] / ".research-backend").iterdir())
    path.write_text(json.dumps({"snapshot_id": "e" * 32}))
    result = run_preflight(**args)
    assert "restored_snapshot_conflict" in codes(result)
    assert not any("/info" in path for _, path in backend.calls)


def test_corrupt_restored_bytes_cannot_pass_using_saved_success_flag(setup):
    backend, args = setup
    (args["state_dir"] / "input.json.gz").write_bytes(b"corrupt")
    result = run_preflight(**args, live_capacity_adapter=SyntheticCapacityAdapter())
    assert not result["ready"] and "restored_file_integrity_failed" in codes(result)


def test_invalid_gzip_crc_is_rejected_even_if_compressed_sha_matches(setup):
    backend, args = setup
    backend.compressed = backend.compressed[:-8] + b"\x00" * 8
    backend.sha = hashlib.sha256(backend.compressed).hexdigest()
    (args["state_dir"] / "input.json.gz").write_bytes(backend.compressed)
    result = run_preflight(**args, live_capacity_adapter=SyntheticCapacityAdapter())
    assert not result["ready"] and "backend_verification_failed" in codes(result)


def test_source_head_change_is_not_fixed_by_refreshing_receipt(setup):
    backend, args = setup
    backend.move_head = True
    receipt = next((args["state_dir"] / ".research-backend").iterdir())
    before = receipt.read_bytes()
    result = run_preflight(**args, live_capacity_adapter=SyntheticCapacityAdapter())
    assert not result["ready"] and "source_changed_during_verification" in codes(result)
    assert receipt.read_bytes() == before


def test_checkpoint_identity_and_version_must_match(setup):
    backend, args = setup
    backend.checkpoint["version"] = 4
    assert "checkpoint_conflict" in codes(run_preflight(**args))
    backend.checkpoint["version"] = 3
    backend.checkpoint["payload"]["source_snapshot_id"] = "d" * 32
    assert "checkpoint_source_mismatch" in codes(run_preflight(**args))


def test_caller_json_cannot_supply_readiness_evidence(setup):
    _, args = setup
    result = run_preflight(**args, live_capacity_adapter={"ready": True, "free": True})
    assert not result["ready"] and "live_capacity_verification_failed" in codes(result)


def test_synthetic_adapter_demonstrates_future_contract_only(setup):
    _, args = setup
    result = run_preflight(**args, live_capacity_adapter=SyntheticCapacityAdapter())
    assert result["ready"] and result["blockers"] == []
    args["required_growth_bytes"] = 9000
    result = run_preflight(**args, live_capacity_adapter=SyntheticCapacityAdapter())
    assert not result["ready"] and "capacity_headroom_insufficient" in codes(result)


def test_input_hash_is_bound_to_restored_payload(setup):
    _, args = setup
    args["identity"] = args["identity"] | {"input_sha256": "f" * 64}
    result = run_preflight(**args)
    assert not result["ready"] and "input_identity_mismatch" in codes(result)


def test_malformed_identity_is_blocked_without_network(setup):
    backend, args = setup
    args["identity"] = args["identity"] | {"engine_sha256": 123}
    result = run_preflight(**args)
    assert not result["ready"] and "invalid_experiment_identity" in codes(result)
    assert backend.calls == []
