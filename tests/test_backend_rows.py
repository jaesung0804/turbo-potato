import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


def load_producer():
    root = Path(__file__).resolve().parents[1]
    directory = root if (root / "backend_rows.py").is_file() else root / "scripts"
    sys.path.insert(0, str(directory))
    try:
        spec = importlib.util.spec_from_file_location("rows_producer_under_test", directory / "backend_rows.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module
    finally:
        sys.path.pop(0)


def test_identical_dataset_skips_upload_and_job():
    producer = load_producer()
    sha = "a" * 64
    version = hashlib.sha256(f"{sha}:jsonl:item:utf-8:{producer.NORMALIZER}".encode()).hexdigest()

    class NoWritesClient:
        def json(self, method, path, body=None):
            assert method == "GET"
            if path.startswith("/datasets?"):
                return {"items": [{"dataset_key": "ohlcv-kr/2026-08", "version_id": version}], "next_cursor": None}
            return {"snapshot_id": "b" * 32}

        def upload(self, source):
            pytest.fail("An unchanged monthly version must not be uploaded")

    result = producer.queue_months(NoWritesClient(), [{"month": "2026-08", "sha256": sha, "rows": 10}],
        dataset="ohlcv-kr", snapshot_name="pipeline-state", snapshot_id="b" * 32, manifest_sha256="c" * 64)
    assert result["unchanged_months"] == 1 and result["uploaded_months"] == result["queued_months"] == 0


def test_job_identity_is_stable_and_enqueue_failure_propagates(tmp_path):
    producer = load_producer()

    class FakeClient:
        failed = False

        def __init__(self):
            self.jobs = []

        def json(self, method, path, body=None):
            if method == "POST":
                if self.failed:
                    raise RuntimeError("synthetic enqueue failure")
                self.jobs.append(body)
                return {"job_id": body["job_id"]}
            if path.startswith("/datasets?"):
                return {"items": [], "next_cursor": None}
            if path.startswith("/jobs/"):
                return {"status": "queued"}
            return {"snapshot_id": "b" * 32}

        def upload(self, path):
            return {"sha256": "a" * 64, "changed": False}

    fake = FakeClient()
    entry = {"month": "2026-08", "sha256": "a" * 64, "rows": 10, "file": tmp_path / "unused"}
    kwargs = dict(dataset="ohlcv-kr", snapshot_name="pipeline-state", snapshot_id="b" * 32, manifest_sha256="c" * 64)
    producer.queue_months(fake, [entry], **kwargs)
    producer.queue_months(fake, [entry], **kwargs)
    assert fake.jobs[0] == fake.jobs[1]
    fake.failed = True
    with pytest.raises(RuntimeError):
        producer.queue_months(fake, [entry], **kwargs)


def test_gzip_matches_initial_partition_format(tmp_path):
    producer = load_producer()
    csv = tmp_path / "prices.csv"
    csv.write_text("date,ticker,close\n2026-08-03,000020,7\n2026-07-03,000020,8\n2026-08-04,000020,9\n", encoding="utf-8")
    entries = list(producer.monthly_files(csv, tmp_path))
    entry = next(item for item in entries if item["month"] == "2026-08")
    expected = tmp_path / "expected.gz"
    with expected.open("wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0, compresslevel=6) as out:
        for row in ({"date": "2026-08-03", "ticker": "000020", "close": "7"}, {"date": "2026-08-04", "ticker": "000020", "close": "9"}):
            out.write(producer.canonical(row) + b"\n")
    assert entry["rows"] == 2
    assert entry["sha256"] == hashlib.sha256(expected.read_bytes()).hexdigest()


def test_empty_csv_cannot_replace_an_existing_month(tmp_path):
    producer = load_producer()
    path = tmp_path / "empty.csv"
    path.write_text("date,ticker,close\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Empty CSV"):
        list(producer.monthly_files(path, tmp_path))


def test_source_csv_must_match_manifest_in_current_snapshot(tmp_path):
    producer = load_producer()
    source = tmp_path / "source.csv"
    source.write_text("date,ticker,close\n2026-08-03,000020,7\n", encoding="utf-8")
    state = tmp_path / "state"
    state.mkdir()
    if producer.PROJECT == "investment":
        snapshot_name, dataset, manifest_name = "pipeline-state", "ohlcv-kr", "state-manifest.json"
        metadata = {"data/raw/krx_ohlcv_kospi_kosdaq_state.csv": {"sha256": producer.sha_file(source)}}
    else:
        snapshot_name, dataset, manifest_name = "estate-raw-state", "apt-trades", "raw_manifest.json"
        metadata = {"collection": {"complete": True, "shard_complete": True, "rows": 1, "sha256": producer.sha_file(source)}}
    manifest = state / manifest_name
    manifest.write_text(json.dumps(metadata), encoding="utf-8")
    receipt = state / ".research-backend" / (hashlib.sha256(snapshot_name.encode()).hexdigest() + ".json")
    receipt.parent.mkdir()
    receipt.write_text(json.dumps({"snapshot_id": "b" * 32}), encoding="utf-8")

    class FakeClient:
        def json(self, *args):
            return {"snapshot_id": "b" * 32}

        def snapshot_entries(self, sid):
            return [{"relative_path": manifest_name, "sha256": producer.sha_file(manifest)}]

    assert producer.verify_source(FakeClient(), source, state, snapshot_name, dataset)[0] == "b" * 32
    source.write_text("date,ticker,close\n2026-08-03,000020,99\n", encoding="utf-8")
    with pytest.raises(ValueError, match="CSV differs"):
        producer.verify_source(FakeClient(), source, state, snapshot_name, dataset)
