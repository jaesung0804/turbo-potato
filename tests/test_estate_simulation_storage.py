"""Storage adapter tests use an in-memory service, never production access."""
from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path

import pytest

from research_backend_client import BackendError
from simulation.engine import ReplayError, canonical, replay
from simulation.fixtures import fixture
from simulation.storage import BackendSession, read_gzip_json


class FakeClient:
    project = "estate"

    def __init__(self, identity):
        self.record = {"version": 1, "payload": {"status": "prepared", "identity": identity,
                                               "source_snapshot_id": "test-snapshot"}}
        self.files = {}
        self.puts = 0
        self.conflict = False

    def json(self, method, path, body=None):
        if method == "GET":
            return deepcopy(self.record)
        assert method == "PUT"
        if self.conflict or body["expected_version"] != self.record["version"]:
            raise BackendError(409, "test conflict")
        self.puts += 1
        self.record = {"version": self.record["version"] + 1, "payload": deepcopy(body["payload"])}
        return {"version": self.record["version"]}

    def upload(self, path):
        raw = Path(path).read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        self.files[sha] = raw
        return {"sha256": sha, "byte_size": len(raw)}

    def download(self, sha, path, expected_size):
        raw = self.files[sha]
        assert len(raw) == expected_size and hashlib.sha256(raw).hexdigest() == sha
        Path(path).write_bytes(raw)


def setup_session(tmp_path):
    data, policy, costs = fixture()
    result = replay(data, policy, costs, stop_after_days=7)
    identity = result["identity"]
    (tmp_path / "panel.json.gz").write_bytes(gzip.compress(canonical(data), mtime=0))
    client = FakeClient(identity)
    session = BackendSession(state_dir=tmp_path, snapshot_name="test-snapshot", input_path="panel.json.gz",
                             checkpoint_key="test-run", expected_checkpoint_version=1,
                             required_growth_bytes=5_000_000, client=client)
    return session, client, result


def permit_test_storage(monkeypatch):
    # Explicit test monkeypatch: never a production readiness receipt.
    monkeypatch.setattr("estate_research_storage.run_preflight", lambda **kwargs:
                        {"ready": True, "snapshot_id": "test-snapshot"})


def test_failed_live_gate_performs_no_checkpoint_or_file_writes(tmp_path, monkeypatch):
    session, client, result = setup_session(tmp_path)
    monkeypatch.setattr("estate_research_storage.run_preflight", lambda **kwargs:
                        {"ready": False, "blockers": [{"code": "capacity_unverified"}]})
    with pytest.raises(ReplayError, match="capacity_unverified"):
        session.authorize(result["identity"])
    assert client.puts == 0 and not client.files


def test_checkpoint_is_compressed_verified_and_restored_with_original_identity(tmp_path, monkeypatch):
    session, client, result = setup_session(tmp_path)
    permit_test_storage(monkeypatch)
    session.authorize(result["identity"])
    assert session.load_checkpoint(result["identity"]["run_id"]) == {"version": 1, "state": None}
    version = session.save_checkpoint(result["identity"]["run_id"], result["checkpoint"], 1)
    assert version == 2 and client.puts == 1
    payload = client.record["payload"]
    assert payload["artifact_sha256"] in client.files
    assert "ledger" not in payload and "state" not in payload
    loaded = session.load_checkpoint(result["identity"]["run_id"])
    assert loaded["state"] == json.loads(canonical(result["checkpoint"]))
    # Saving identical state does not create another artifact or DB version.
    assert session.save_checkpoint(result["identity"]["run_id"], result["checkpoint"], 2) == 2
    assert client.puts == 1


def test_checkpoint_conflict_is_not_retried_against_a_new_version(tmp_path, monkeypatch):
    session, client, result = setup_session(tmp_path)
    permit_test_storage(monkeypatch)
    session.authorize(result["identity"])
    session.load_checkpoint(result["identity"]["run_id"])
    client.conflict = True
    with pytest.raises(BackendError) as caught:
        session.save_checkpoint(result["identity"]["run_id"], result["checkpoint"], 1)
    assert caught.value.status == 409 and client.puts == 0
    assert session.expected_version == 1


def test_restored_panel_must_match_the_exact_canonical_input(tmp_path, monkeypatch):
    session, client, result = setup_session(tmp_path)
    permit_test_storage(monkeypatch)
    (tmp_path / "panel.json.gz").write_bytes(gzip.compress(b'{"different":true}', mtime=0))
    with pytest.raises(ReplayError, match="canonical panel SHA"):
        session.authorize(result["identity"])
    assert client.puts == 0


def test_gzip_expansion_is_bounded(tmp_path):
    path = tmp_path / "expanded.json.gz"
    path.write_bytes(gzip.compress(b'"' + b"x" * 5000 + b'"', mtime=0))
    with pytest.raises(ReplayError, match="Expanded"):
        read_gzip_json(path, 1000)
