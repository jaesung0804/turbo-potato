from copy import deepcopy
import io
import json
import urllib.error
import urllib.parse

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import func, select

from research_backend.api import create_app
from research_backend.client import BackendError, Client
from research_backend.config import Settings
from research_backend.database import Databases
from research_backend.objects import LocalObjects
from research_backend import schema as s
from research_backend.util import Conflict, digest

ORG = "data/reference/investment_organization.json"
QUEUE = "data/reference/investment_operations_queue.json"
TASK = "investment_operations_queue:tasks:task-1"
INITIAL = {ORG: {"companies": {"c": {"teams": {"t": {"employees": [{"id": "agent-1", "name": "Synthetic", "status": "ready"}]}}}}},
           QUEUE: {"tasks": [{"id": "task-1", "status": "ready"}]}}


@pytest.fixture
def system(tmp_path):
    settings = Settings(tmp_path / "runtime")
    databases = Databases(settings)
    databases.initialize()
    app = create_app(settings, databases, LocalObjects(settings.data_dir / "objects"),
                     {"estate": "e" * 48, "investment": "i" * 48})
    with TestClient(app) as api:
        class Opener:
            def open(self, request, timeout):
                assert timeout == 120
                body = request.data.read() if hasattr(request.data, "read") else request.data
                response = api.request(request.get_method(), urllib.parse.urlsplit(request.full_url).path,
                                       headers=request.headers, content=body)
                if response.status_code >= 400:
                    raise urllib.error.HTTPError(request.full_url, response.status_code, "Test response", {}, io.BytesIO(response.content))
                return io.BytesIO(response.content)

        def make_client():
            client = Client("http://127.0.0.1:8787", "i" * 48, "investment")
            client.opener = Opener()
            return client

        initial = make_client()
        for path in INITIAL:
            initial.versions[path] = 0
            initial.reference_bases[path] = None
        initial.write_references(deepcopy(INITIAL))
        yield app.state.services["investment"], make_client, api


def read_all(client):
    return {path: client.read_json(path) for path in INITIAL}


def changed(client):
    values = read_all(client)
    values[ORG]["companies"]["c"]["teams"]["t"]["employees"][0]["status"] = "reviewed"
    values[QUEUE]["tasks"][0]["status"] = "reviewed"
    return values


def revisions(service):
    with service.engine.connect() as db:
        return db.scalar(select(func.count()).select_from(s.revisions))


def test_reference_pointer_and_projection_commit_together_and_retry_is_idempotent(system, monkeypatch):
    service, make_client, _ = system
    client = make_client()
    values = changed(client)
    original = client.json
    lost = [False]

    def lose_response(method, path, body=None):
        result = original(method, path, body)
        if method == "POST" and path == "/reference-transactions" and not lost[0]:
            lost[0] = True
            raise BackendError(503, "Synthetic lost response")
        return result

    monkeypatch.setattr(client, "json", lose_response)
    with pytest.raises(BackendError):
        client.write_references(values)
    assert service.get_record("agents", "agent-1")["payload"]["status"] == "reviewed"
    assert service.get_record("tasks", TASK)["payload"]["status"] == "reviewed"
    before = revisions(service)
    assert client.write_references(values)["changed"] is False
    assert revisions(service) == before
    assert read_all(make_client()) == values
    assert all(client.versions[path] == 2 for path in INITIAL)


@pytest.mark.parametrize("stale", ["document", "record"])
def test_any_stale_document_or_record_rejects_all_other_changes(system, stale):
    service, make_client, _ = system
    client = make_client()
    values = changed(client)
    if stale == "document":
        other = make_client()
        queue = other.read_json(QUEUE)
        queue["tasks"][0]["status"] = "concurrent"
        other.write_references({QUEUE: queue})
    else:
        service.put_record("tasks", TASK, {"id": "task-1", "status": "concurrent"}, 1)
    before = revisions(service)
    with pytest.raises(BackendError) as error:
        client.write_references(values)
    assert error.value.status == 409
    assert service.get_record("agents", "agent-1")["payload"]["status"] == "ready"
    assert service.get_record("documents", digest(ORG.encode()))["version"] == 1
    assert service.get_record("tasks", TASK)["payload"]["status"] == "concurrent"
    assert revisions(service) == before


def test_a_late_cas_failure_rolls_back_an_already_written_record_and_revision(system, monkeypatch):
    service, make_client, _ = system
    client = make_client()
    values = changed(client)
    original = service._put_record_in
    writes = [0]

    def fail_second(*args, **kwargs):
        writes[0] += 1
        if writes[0] == 2:
            raise Conflict("Synthetic stale second row")
        return original(*args, **kwargs)

    before = revisions(service)
    monkeypatch.setattr(service, "_put_record_in", fail_second)
    with pytest.raises(BackendError) as error:
        client.write_references(values)
    assert error.value.status == 409 and writes[0] == 2
    assert read_all(make_client()) == INITIAL
    assert service.get_record("agents", "agent-1")["payload"]["status"] == "ready"
    assert revisions(service) == before


def test_unverified_projection_and_oversized_records_cannot_be_published(system, monkeypatch):
    service, make_client, _ = system
    client = make_client()
    values = changed(client)
    original = client.json

    def tamper(method, path, body=None):
        if method == "POST" and path == "/reference-transactions":
            body = deepcopy(body)
            body["records"][0]["payload"]["status"] = "does not match uploaded artifact"
        return original(method, path, body)

    monkeypatch.setattr(client, "json", tamper)
    before = revisions(service)
    with pytest.raises(BackendError) as error:
        client.write_references(values)
    assert error.value.status == 422 and revisions(service) == before
    values[QUEUE]["tasks"][0]["notes"] = "x" * 131072
    with pytest.raises(ValueError, match="record too large"):
        client.write_references(values)
    assert read_all(make_client()) == INITIAL


def test_reference_reads_create_no_records_revisions_or_jobs(system):
    service, make_client, _ = system
    before = revisions(service)
    values = read_all(make_client())
    assert values == INITIAL and revisions(service) == before
    with service.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(s.jobs)) == 0


def test_source_artifact_and_http_request_limits_are_enforced(system, tmp_path):
    service, make_client, api = system
    client = make_client()
    before = revisions(service)
    values = read_all(client)
    values[QUEUE]["large_document_note"] = "x" * (1024 * 1024)
    with pytest.raises(ValueError, match="1 MiB"):
        client.write_references(values)
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b" " * (1024 * 1024 + 1))
    info = service.register_file(digest(oversized.read_bytes()), oversized)
    with pytest.raises(ValueError, match="1 MiB"):
        service.sync_references([{"relative_path": QUEUE, "artifact_sha256": info["sha256"],
                                  "base_artifact_sha256": None, "expected_version": 0}], [])
    response = api.post("/v1/investment/reference-transactions", content=b" " * (1024 * 1024 + 1),
                        headers={"Authorization": "Bearer " + "i" * 48, "Content-Type": "application/json"})
    assert response.status_code == 413
    with pytest.raises(ValueError, match="Invalid investment reference structure"):
        client.write_references({QUEUE: {"tasks": [None]}})
    assert revisions(service) == before and read_all(make_client()) == INITIAL


def test_same_payload_with_an_unrelated_version_is_not_an_idempotent_retry(system, monkeypatch):
    service, make_client, _ = system
    client = make_client()
    values = read_all(client)
    original = client.json

    def incorrect_version(method, path, body=None):
        if method == "POST" and path == "/reference-transactions":
            body = deepcopy(body)
            body["documents"][0]["expected_version"] += 9
        return original(method, path, body)

    monkeypatch.setattr(client, "json", incorrect_version)
    before = revisions(service)
    with pytest.raises(BackendError) as error:
        client.write_references(values)
    assert error.value.status == 409
    assert revisions(service) == before and read_all(make_client()) == INITIAL
