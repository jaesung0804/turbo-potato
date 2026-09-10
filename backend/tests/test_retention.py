import hashlib

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import delete, func, select, update

from research_backend import retention, schema as s
from research_backend.api import create_app
from research_backend.config import Settings
from research_backend.database import Databases
from research_backend.ingest import import_rows, query_rows
from research_backend.objects import LocalObjects
from research_backend.service import Service
from research_backend.util import Conflict, Missing

DATASET = "ohlcv-us/2026-09"


@pytest.fixture
def system(tmp_path):
    settings = Settings(tmp_path / "runtime")
    databases = Databases(settings)
    databases.initialize()
    objects = LocalObjects(settings.data_dir / "objects")
    service = Service(databases.engine("investment"), objects, "investment")
    source = tmp_path / "before.csv"
    source.write_text("ticker,date,close\n" + "".join(f"SYN,2026-09-{day:02},10\n" for day in range(1, 8)))
    old = import_rows(service, DATASET, source)
    current_source = tmp_path / "current.csv"
    current_source.write_text(source.read_text() + "SYN,2026-09-08,11\n")
    current = import_rows(service, DATASET, current_source)
    yield settings, databases, objects, service, source, old["version"], current["version"]
    databases.close()


def counts(service):
    with service.engine.connect() as db:
        return {table.name: db.scalar(select(func.count()).select_from(table)) for table in
                (s.files, s.records, s.revisions, s.jobs, s.dataset_versions)}


def rows(service, version):
    with service.engine.connect() as db:
        return db.scalars(select(s.observations.c.row_no).where(s.observations.c.dataset_key == DATASET,
            s.observations.c.version_id == version).order_by(s.observations.c.row_no)).all()


def test_only_obsolete_query_rows_are_removed_in_bounded_resumable_batches(system):
    _, _, objects, service, source, old, current = system
    before = counts(service)
    result = retention.archive_obsolete_version(service, DATASET, old, batch_size=2, max_batches=1)
    assert result["deleted_rows"] == 2 and result["batches"] == 1 and not result["cleanup_complete"]
    assert rows(service, old) == [3, 4, 5, 6, 7]
    assert rows(service, current) == list(range(1, 9))
    version = retention.dataset_version(service, DATASET, old)
    assert version["status"] == "archived" and version["row_count"] == 7 and not version["is_current"]
    with pytest.raises(Missing):
        query_rows(service, DATASET, version=old)
    assert len(query_rows(service, DATASET)["items"]) == 8
    result = retention.prune_obsolete_rows(service, DATASET, batch_size=2, max_batches=10)
    assert result["deleted_rows"] == 5 and not rows(service, old)
    assert retention.prune_obsolete_rows(service, DATASET)["deleted_rows"] == 0
    assert counts(service) == before
    retained = objects.path("investment", version["source_sha256"])
    assert retained.read_bytes() == source.read_bytes()
    assert hashlib.sha256(retained.read_bytes()).hexdigest() == version["source_sha256"]


def test_current_head_and_incomplete_versions_are_never_cleanup_targets(system):
    _, _, _, service, _, old, current = system
    with pytest.raises(Conflict, match="Current dataset head"):
        retention.archive_obsolete_version(service, DATASET, current)
    with service.engine.begin() as db:
        db.execute(update(s.dataset_versions).where(s.dataset_versions.c.version_id == old).values(status="staging"))
    with pytest.raises(Conflict, match="previously published"):
        retention.archive_obsolete_version(service, DATASET, old)
    assert rows(service, old) == list(range(1, 8)) and rows(service, current) == list(range(1, 9))


@pytest.mark.parametrize("problem", ["missing_object", "changed_object", "missing_metadata"])
def test_unverifiable_source_preserves_all_query_copies(system, problem):
    _, _, objects, service, _, old, current = system
    metadata = retention.dataset_version(service, DATASET, old)
    source = objects.path("investment", metadata["source_sha256"])
    if problem == "missing_object":
        source.unlink()
    elif problem == "changed_object":
        source.write_bytes(b"invalid replacement")
    else:
        with service.engine.begin() as db:
            db.execute(delete(s.files).where(s.files.c.sha256 == metadata["source_sha256"]))
    with pytest.raises((Missing, FileNotFoundError, ValueError)):
        retention.archive_obsolete_version(service, DATASET, old)
    assert retention.dataset_version(service, DATASET, old)["status"] == "complete"
    assert rows(service, old) == list(range(1, 8)) and rows(service, current) == list(range(1, 9))


def test_head_is_rechecked_after_source_verification_before_any_delete(system, monkeypatch):
    _, _, objects, service, _, old, current = system
    original = objects.chunks

    def head_changed(project, sha):
        yield from original(project, sha)
        with service.engine.begin() as db:
            db.execute(update(s.datasets).where(s.datasets.c.dataset_key == DATASET).values(version_id=old, row_count=7))

    monkeypatch.setattr(objects, "chunks", head_changed)
    with pytest.raises(Conflict, match="became current"):
        retention.archive_obsolete_version(service, DATASET, old)
    assert rows(service, old) == list(range(1, 8)) and rows(service, current) == list(range(1, 9))


def test_interrupted_cleanup_retains_source_and_resumes_without_touching_head(system, monkeypatch):
    _, _, _, service, _, old, current = system
    original = retention._delete_observations
    calls = [0]

    def interrupted(*args):
        calls[0] += 1
        if calls[0] == 2:
            raise RuntimeError("Synthetic interrupted batch")
        return original(*args)

    monkeypatch.setattr(retention, "_delete_observations", interrupted)
    with pytest.raises(RuntimeError):
        retention.archive_obsolete_version(service, DATASET, old, batch_size=2)
    assert rows(service, old) == [3, 4, 5, 6, 7]
    monkeypatch.setattr(retention, "_delete_observations", original)
    assert retention.archive_obsolete_version(service, DATASET, old)["deleted_rows"] == 5
    assert rows(service, current) == list(range(1, 9))


def test_archived_metadata_get_is_bounded_and_does_not_restore_or_enqueue(system):
    settings, databases, objects, service, _, old, current = system
    retention.archive_obsolete_version(service, DATASET, old)
    before = counts(service)
    with TestClient(create_app(settings, databases, objects, {"estate": "e" * 48, "investment": "i" * 48})) as api:
        headers = {"Authorization": "Bearer " + "i" * 48}
        response = api.get("/v1/investment/dataset-version", params={"dataset": DATASET, "version": old}, headers=headers)
        assert response.status_code == 200 and len(response.content) < 2048
        assert response.json()["status"] == "archived" and response.json()["source_sha256"]
        assert api.get("/v1/investment/observations", params={"dataset": DATASET, "version": old}, headers=headers).status_code == 404
        assert api.get("/v1/investment/dataset-version", params={"dataset": DATASET, "version": "f" * 64}, headers=headers).status_code == 404
        assert counts(service) == before and not rows(service, old)
        assert retention.dataset_version(service, DATASET)["version_id"] == current


def test_archived_version_cannot_be_implicitly_rebuilt_or_published(system):
    _, _, _, service, source, old, current = system
    retention.archive_obsolete_version(service, DATASET, old, batch_size=2, max_batches=1)
    with pytest.raises(Conflict, match="explicit reconstruction"):
        import_rows(service, DATASET, source)
    assert retention.dataset_version(service, DATASET)["version_id"] == current
    assert rows(service, old) == [3, 4, 5, 6, 7]


def test_cleanup_racing_an_old_version_republish_cannot_publish_partial_rows(system, monkeypatch):
    _, _, _, service, source, old, current = system
    original = service.register_file

    def archive_before_publication(*args):
        result = original(*args)
        retention.archive_obsolete_version(service, DATASET, old, batch_size=2, max_batches=1)
        return result

    monkeypatch.setattr(service, "register_file", archive_before_publication)
    with pytest.raises(Conflict, match="archived during import"):
        import_rows(service, DATASET, source)
    assert retention.dataset_version(service, DATASET)["version_id"] == current
    assert rows(service, current) == list(range(1, 9))
