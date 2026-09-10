import json

import pytest
from sqlalchemy import func, select

from research_backend import ingest, schema as s
from research_backend.config import Settings
from research_backend.database import Databases
from research_backend.objects import LocalObjects
from research_backend.service import Service


@pytest.fixture
def service(tmp_path):
    settings = Settings(tmp_path / "runtime")
    databases = Databases(settings, projects=("investment",))
    databases.initialize()
    yield Service(databases.engine("investment"), LocalObjects(settings.data_dir / "objects"), "investment")
    databases.close()


def test_resume_keeps_committed_prefix_when_batch_size_changes(service, tmp_path, monkeypatch):
    source = tmp_path / "month.jsonl"
    source.write_text("".join(json.dumps({"ticker": "000020", "date": "2026-08-03", "close": n}) + "\n"
        for n in range(173)), encoding="utf-8")
    original = ingest._insert_batch
    attempted = []

    def fail_third_batch(current, rows):
        attempted.append((rows[0]["row_no"], rows[-1]["row_no"]))
        if len(attempted) == 3:
            raise RuntimeError("synthetic transient failure")
        original(current, rows)

    monkeypatch.setattr(ingest, "_insert_batch", fail_third_batch)
    with pytest.raises(RuntimeError):
        ingest.import_rows(service, "ohlcv-kr/2026-08", source, "jsonl", batch_size=50)
    assert attempted == [(1, 50), (51, 100), (101, 150)]
    with service.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(s.observations)) == 100
        assert db.scalar(select(func.count()).select_from(s.datasets)) == 0
    resumed = []

    def save_batch(current, rows):
        resumed.append((rows[0]["row_no"], rows[-1]["row_no"]))
        original(current, rows)

    monkeypatch.setattr(ingest, "_insert_batch", save_batch)
    result = ingest.import_rows(service, "ohlcv-kr/2026-08", source, "jsonl", batch_size=25)
    assert resumed == [(101, 125), (126, 150), (151, 173)]
    assert result["rows"] == 173 and result["changed"]
    with service.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(s.observations)) == 173
        assert db.scalar(select(s.datasets.c.row_count)) == 173


@pytest.mark.parametrize("batch_size", [0, 501, True, 1.5, "50"])
def test_invalid_batch_size_fails_before_opening_source(service, tmp_path, batch_size):
    with pytest.raises(ValueError, match="batch_size"):
        ingest.import_rows(service, "ohlcv-kr/2026-08", tmp_path / "not-opened.csv", batch_size=batch_size)
    with service.engine.connect() as db:
        assert db.scalar(select(func.count()).select_from(s.dataset_versions)) == 0
