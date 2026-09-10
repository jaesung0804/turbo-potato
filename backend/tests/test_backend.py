from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, update, func
from sqlalchemy.dialects.oracle import dialect
from sqlalchemy.schema import CreateTable, CreateIndex

from research_backend.api import create_app
from research_backend.config import Settings
from research_backend.database import Databases
from research_backend.objects import LocalObjects
from research_backend.service import Service
from research_backend.ingest import import_rows, import_monthly_csv, query_rows
from research_backend import schema as s
from research_backend.util import Conflict, Missing, safe_relative


@pytest.fixture
def environment(tmp_path):
    settings = Settings(tmp_path / "runtime")
    db = Databases(settings)
    db.initialize()
    objects = LocalObjects(settings.data_dir / "objects")
    service = Service(db.engine("estate"), objects, "estate")
    yield settings, db, objects, service
    db.close()


@pytest.fixture
def api(environment):
    settings, db, objects, _ = environment
    tokens = {"estate": "e" * 48, "investment": "i" * 48}
    with TestClient(create_app(settings, db, objects, tokens)) as client:
        yield client


def auth(project="estate"):
    return {"Authorization": "Bearer " + ("e" if project == "estate" else "i") * 48}


def test_auth_project_isolation_and_reads_do_not_create_data(api, environment):
    assert api.get("/v1/estate/records/agents").status_code == 401
    assert api.get("/v1/investment/records/agents", headers=auth()).status_code == 401
    response = api.get("/v1/estate/records/agents", headers=auth())
    assert response.json() == {"items": [], "next_cursor": None}
    assert response.headers["cache-control"] == "no-store"
    with environment[1].engine("estate").connect() as db:
        assert db.scalar(select(func.count()).select_from(s.jobs)) == 0


def test_record_cas_and_revision_retention(environment):
    service = environment[3]
    assert service.put_record("research", "apt-1", {"score": 1}, 0)["version"] == 1
    assert service.put_record("research", "apt-1", {"score": 1}, 0)["changed"] is False
    assert service.put_record("research", "apt-1", {"score": 2}, 1)["version"] == 2
    with pytest.raises(Conflict):
        service.put_record("research", "apt-1", {"score": 3}, 1)
    assert service.get_record("research", "apt-1")["payload"] == {"score": 2}
    with service.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(s.revisions)) == 2


def test_empty_source_uri_retries_as_null(environment):
    service = environment[3]
    service.put_record("research", "nullable-source", {"score": 1}, 0, source_uri="")
    assert service.get_record("research", "nullable-source")["source_uri"] is None
    assert service.put_record("research", "nullable-source", {"score": 1}, 0, source_uri=None)["changed"] is False


def test_record_api_projection_limits_and_sql_injection(api):
    for i in range(3):
        response = api.put(f"/v1/estate/records/research/r{i}", headers=auth(),
            json={"payload": {"private_detail": "x" * 10000}, "expected_version": 0, "summary": "요약"})
        assert response.status_code == 200, response.text
    response = api.get("/v1/estate/records/research?limit=2", headers=auth())
    assert len(response.json()["items"]) == 2
    assert response.json()["next_cursor"] == "r1"
    assert "private_detail" not in response.text
    assert api.get("/v1/estate/records/research?limit=10000", headers=auth()).status_code == 422
    assert api.get("/v1/estate/records/research?after=' OR 1=1 --", headers=auth()).status_code == 200
    assert api.put("/v1/estate/records/research/r0", headers=auth(),
        json={"payload": {"x": 1}, "expected_version": 0}).status_code == 409


def test_file_upload_integrity_and_project_isolation(api):
    body = b"\x89PNG\r\n\x1a\n" + b"binary-photo" * 10000
    sha = hashlib.sha256(body).hexdigest()
    response = api.put(f"/v1/estate/files/{sha}", content=body, headers=auth())
    assert response.status_code == 200, response.text
    assert response.json()["changed"] is True
    assert api.put(f"/v1/estate/files/{sha}", content=body, headers=auth()).json()["changed"] is False
    download = api.get(f"/v1/estate/files/{sha}", headers=auth())
    assert download.content == body
    assert download.headers["content-disposition"].startswith("attachment;")
    assert api.get(f"/v1/investment/files/{sha}", headers=auth("investment")).status_code == 404
    assert api.put(f"/v1/estate/files/{sha}", content=b"corrupt", headers=auth()).status_code == 422


def test_json_body_limits_do_not_trust_content_length(api):
    path = '/v1/estate/records/research/large'
    assert api.put(path, content=b'x' * (1024 * 1024 + 1),
        headers=auth() | {'Content-Length': '1'}).status_code == 413
    assert api.put(path, content=b'{}',
        headers=auth() | {'Content-Length': '1'}).status_code == 400


def register(service, tmp_path, data=b"original"):
    path = tmp_path / "source.bin"
    path.write_bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    service.register_file(sha, path)
    return {"relative_path": "data/raw/source.bin", "sha256": sha, "byte_size": len(data)}


def test_atomic_snapshot_publication_preserves_previous(environment, tmp_path):
    service = environment[3]
    entry = register(service, tmp_path)
    first = service.begin_snapshot("pipeline-state", None)["snapshot_id"]
    service.add_snapshot_files(first, [entry])
    with pytest.raises(ValueError):
        service.commit_snapshot(first, 2)
    assert service.snapshot_head("pipeline-state")["snapshot_id"] is None
    service.commit_snapshot(first, 1)
    a = service.begin_snapshot("pipeline-state", first)["snapshot_id"]
    b = service.begin_snapshot("pipeline-state", first)["snapshot_id"]
    service.add_snapshot_files(a, [entry]); service.add_snapshot_files(b, [entry])
    service.commit_snapshot(a, 1)
    with pytest.raises(Conflict):
        service.commit_snapshot(b, 1)
    assert service.snapshot_head("pipeline-state")["snapshot_id"] == a
    assert service.snapshot_entries(first)["items"][0]["sha256"] == entry["sha256"]
    with pytest.raises(Conflict):
        service.add_snapshot_files(a, [entry])


def test_snapshot_batch_is_atomic_for_missing_or_conflicting_files(environment, tmp_path):
    service = environment[3]
    entry = register(service, tmp_path)
    sid = service.begin_snapshot("batch-verification", None)["snapshot_id"]
    valid = [dict(entry, relative_path=f"rows/file-{i}.bin") for i in range(200)]
    with pytest.raises(ValueError):
        service.add_snapshot_files(sid, valid + [dict(entry, relative_path="missing.bin", sha256="0" * 64)])
    with service.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(s.snapshot_files).where(s.snapshot_files.c.snapshot_id == sid)) == 0
    with pytest.raises(Conflict):
        service.add_snapshot_files(sid, [entry, dict(entry, sha256="0" * 64)])
    service.add_snapshot_files(sid, valid + [valid[0]])
    service.add_snapshot_files(sid, valid)
    service.commit_snapshot(sid, 200)
    assert len(service.snapshot_entries(sid)["items"]) == 200


@pytest.mark.parametrize("path", ["../secret", "C:/secret", "/etc/passwd", ".git/config", "data/.env", "data\\x", "data/./x"])
def test_reject_unsafe_snapshot_paths(path):
    with pytest.raises(ValueError):
        safe_relative(path)


def test_import_keeps_duplicate_transactions_and_is_idempotent(environment, tmp_path):
    service = environment[3]
    path = tmp_path / "transactions.csv"
    path.write_text("CTRT_DAY,CGG_CD,BLDG_NM,THING_AMT\n20260101,11110,단지,50000\n20260101,11110,단지,50000\n", encoding="utf-8")
    result = import_rows(service, "trades/2026-01", path)
    assert result["rows"] == 2
    assert import_rows(service, "trades/2026-01", path)["changed"] is False
    rows = query_rows(service, "trades/2026-01", region="11110")["items"]
    assert len(rows) == 2 and rows[0]["payload"] == rows[1]["payload"]


def test_failed_import_not_visible_and_old_version_page_is_stable(environment, tmp_path):
    service = environment[3]
    path = tmp_path / "prices.csv"
    path.write_text("date,ticker,close\n2026-01-01,A,10\n2026-01-02,A,11\n", encoding="utf-8")
    old = import_rows(service, "prices/us", path)["version"]
    path.write_text("date,ticker,close\n2026-01-01,A,12\nbad-date,A,13\n", encoding="utf-8")
    with pytest.raises(ValueError):
        import_rows(service, "prices/us", path)
    assert query_rows(service, "prices/us")["version"] == old
    path.write_text("date,ticker,close\n2026-01-01,A,12\n2026-01-02,A,13\n", encoding="utf-8")
    new = import_rows(service, "prices/us", path)["version"]
    assert new != old
    assert query_rows(service, "prices/us", version=old, after=1)["items"][0]["payload"]["close"] == "11"


def test_monthly_import_only_changes_changed_month(environment, tmp_path):
    service = environment[3]
    path = tmp_path / "prices.csv"
    path.write_text("date,ticker,close\n2026-01-01,A,10\n2026-02-01,A,11\n", encoding="utf-8")
    assert all(x["changed"] for x in import_monthly_csv(service, "prices/us", path))
    path.write_text("date,ticker,close\n2026-01-01,A,10\n2026-02-01,A,12\n", encoding="utf-8")
    result = list(import_monthly_csv(service, "prices/us", path))
    assert [x["changed"] for x in result] == [False, True]


def test_concurrent_job_claim_and_fencing(environment):
    service = environment[3]
    service.enqueue("collect-1", "collect", {"month": "2026-09"})
    with ThreadPoolExecutor(max_workers=4) as workers:
        results = list(workers.map(lambda n: service.claim_job("collect", f"w{n}"), range(4)))
    claimed = [r for r in results if r]
    assert len(claimed) == 1
    original = claimed[0]
    with service.engine.begin() as db:
        db.execute(update(s.jobs).where(s.jobs.c.job_id == "collect-1").values(lease_until="2000-01-01T00:00:00.000000Z"))
    replacement = service.claim_job("collect", "recovery")
    assert replacement["attempts"] == 2
    with pytest.raises(Conflict):
        service.finish_job("collect-1", original["lease_token"], {"bad": True})
    service.renew_job("collect-1", replacement["lease_token"])
    service.finish_job("collect-1", replacement["lease_token"], {"count": 20})
    assert service.claim_job("collect", "again") is None


def test_exhausted_job_and_idempotency(environment):
    service = environment[3]
    service.enqueue("job-1", "collect", {"a": 1}, max_attempts=1)
    service.enqueue("job-1", "collect", {"a": 1}, max_attempts=1)
    with pytest.raises(Conflict):
        service.enqueue("job-1", "collect", {"a": 2})
    service.claim_job("collect", "worker")
    with service.engine.begin() as db:
        db.execute(update(s.jobs).values(lease_until="2000-01-01T00:00:00.000000Z"))
    assert service.claim_job("collect", "worker2") is None
    with service.engine.connect() as db:
        assert db.scalar(select(s.jobs.c.status)) == "failed"


def test_oracle_schema_compiles_without_sqlite_features():
    # This verifies generated SQL only. It is not claimed as a live Oracle integration test.
    statements = []
    for table in s.metadata.sorted_tables:
        statements.append(str(CreateTable(table).compile(dialect=dialect())))
        statements.extend(str(CreateIndex(index).compile(dialect=dialect())) for index in table.indexes)
    ddl = "\n".join(statements)
    assert "CLOB" in ddl
    assert "AUTOINCREMENT" not in ddl and "JSONB" not in ddl
