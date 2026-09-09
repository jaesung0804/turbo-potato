"""Offline tests for the official CSV collector's source-preservation contract."""

import csv
from datetime import date
import gzip
import hashlib
import io
import json
from pathlib import Path
import socket

import pytest

import collect_molit_capital_csv as collector


CUTOFF = date(2026, 9, 9)
SIDO_CODES = {"seoul": "11000", "gyeonggi": "41000", "incheon": "28000"}
SALE_COLUMNS = ["시군구", "단지명", "전용면적(㎡)", "계약년월", "계약일",
                "거래금액(만원)", "해제사유발생일", "해제여부"]
RENT_COLUMNS = ["시군구", "단지명", "전용면적(㎡)", "계약년월", "계약일",
                "보증금(만원)", "월세금(만원)", "종전계약 보증금(만원)",
                "종전계약 월세(만원)"]


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Collector tests must not open network connections")

    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)


def request(region="gyeonggi", kind="sale", year=2020):
    return collector.ExportRequest(region, kind, date(year, 1, 1), date(year, 12, 31))


def transaction(req=None, **changes):
    req = req or request()
    row = {"시군구": collector.REGIONS[req.region] + " 시험구 시험동",
           "단지명": "시험,아파트", "전용면적(㎡)": "59.9700",
           "계약년월": f"{req.start.year}06", "계약일": "07",
           "거래금액(만원)": "12,345", "해제사유발생일": "-", "해제여부": "N",
           "보증금(만원)": "20,000", "월세금(만원)": "0",
           "종전계약 보증금(만원)": "-", "종전계약 월세(만원)": "--"}
    row.update(changes)
    return row


def csv_bytes(rows=None, *, req=None, encoding="cp949", columns=None):
    req = req or request()
    rows = [transaction(req)] if rows is None else rows
    columns = columns or (SALE_COLUMNS if req.kind == "sale" else RENT_COLUMNS)
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\r\n")
    for index in range(15):
        writer.writerow([f"자료 안내 {index + 1}", "원자료의 취소 및 반복 거래를 보존합니다"])
    writer.writerow(columns)
    for row in rows:
        writer.writerow([row.get(column, "") for column in columns])
    return stream.getvalue().encode(encoding)


class FakeClient:
    def __init__(self, exports, *, download_error=None, count_override=None):
        self.exports = exports
        self.download_error = download_error or {}
        self.count_override = count_override or {}
        self.sido_codes = dict(SIDO_CODES)
        self.calls = []

    @staticmethod
    def key(fields):
        region = next(region for region, code in SIDO_CODES.items()
                      if code == fields["srhSidoCd"])
        kind = "sale" if fields["srhDelngSecd"] == "1" else "rent"
        return collector.ExportRequest(region, kind, date.fromisoformat(fields["srhFromDt"]),
                                       date.fromisoformat(fields["srhToDt"])).key

    def initialize(self):
        self.calls.append(("initialize", None))

    def count(self, fields):
        key = self.key(fields)
        self.calls.append(("count", key))
        if key in self.count_override:
            return self.count_override[key]
        return len(list(collector.iter_csv_records(self.exports[key])))

    def download(self, fields):
        key = self.key(fields)
        self.calls.append(("download", key))
        if key in self.download_error:
            raise self.download_error[key]
        return self.exports[key], {"content-type": "application/octet-stream"}


def read_json(path):
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize("encoding", ["cp949", "utf-8-sig"])
def test_metadata_encoding_repeated_and_cancelled_rows_are_preserved(encoding):
    row = transaction()
    cancelled = transaction(**{"해제사유발생일": "20200701", "해제여부": "Y"})
    raw = csv_bytes([row, row, cancelled], encoding=encoding)

    result = collector.inspect_csv(raw, request(), 3, "application/octet-stream")
    records = list(collector.iter_csv_records(raw))

    assert result["encoding"] == encoding
    assert result["header_row"] == 16
    assert len(result["metadata_rows"]) == 15
    assert result["metadata_rows"][0][0] == "자료 안내 1"
    assert result["columns"] == SALE_COLUMNS
    assert result["row_count"] == 3
    assert result["cancellation_marked_rows"] == 1
    assert result["deduplication_applied"] is False
    assert result["raw_sha256"] == hashlib.sha256(raw).hexdigest()
    assert result["raw_bytes"] == len(raw)
    assert result["minimum_contract_date"] == "2020-06-07"
    assert result["maximum_contract_date"] == "2020-06-07"
    assert result["contract_month_counts"] == {"202006": 3}
    assert records[0] == records[1]
    assert records[0]["단지명"] == "시험,아파트"
    assert records[0]["거래금액(만원)"] == "12,345"
    assert records[2]["해제사유발생일"] == "20200701"


def test_month_counts_use_contract_month_and_include_cancelled_repeated_rows():
    june = transaction()
    december = transaction(**{"계약년월": "202012", "계약일": "31"})
    cancelled = transaction(**{"계약년월": "202001", "계약일": "01",
                               "해제사유발생일": "20200701", "해제여부": "Y"})
    result = collector.inspect_csv(csv_bytes([december, june, cancelled, june]), request(), 4)
    assert result["contract_month_counts"] == {"202001": 1, "202006": 2, "202012": 1}
    assert sum(result["contract_month_counts"].values()) == result["row_count"] == 4
    assert result["minimum_contract_date"] == "2020-01-01"
    assert result["maximum_contract_date"] == "2020-12-31"


def test_expansion_plan_has_documented_coverage_and_opt_in_current_sales():
    plan = collector.expansion_requests(CUTOFF)
    with_current = collector.expansion_requests(CUTOFF, include_current_sales=True)
    assert len(plan) == len({item.key for item in plan}) == 78
    assert len(with_current) == len({item.key for item in with_current}) == 81
    assert sum(item.kind == "sale" for item in plan) == 30
    assert {(item.region, item.start.year) for item in plan if item.kind == "sale"} == {
        (region, year) for region in ("gyeonggi", "incheon") for year in range(2006, 2021)}
    assert {(item.region, item.start.year) for item in plan if item.kind == "rent"} == {
        (region, year) for region in collector.REGIONS for year in range(2011, 2027)}
    extras = set(with_current) - set(plan)
    assert extras == {collector.ExportRequest(region, "sale", date(2026, 1, 1), CUTOFF)
                      for region in collector.REGIONS}
    assert all(item.end <= CUTOFF for item in with_current)
    with pytest.raises(ValueError):
        collector.expansion_requests(date(2019, 12, 31))


@pytest.mark.parametrize("changes, message", [
    ({"계약년월": "201912"}, "outside the requested contract dates"),
    ({"계약년월": "202102"}, "outside the requested contract dates"),
    ({"계약년월": "202002", "계약일": "30"}, "Invalid calendar"),
    ({"계약년월": "20206"}, "Malformed contract date"),
    ({"시군구": "서울특별시 시험구 시험동"}, "outside the requested province"),
    ({"시군구": "경기도외 시험구"}, "outside the requested province"),
    ({"전용면적(㎡)": "0"}, "numeric value"),
    ({"전용면적(㎡)": "NaN"}, "numeric value"),
    ({"거래금액(만원)": "NaN"}, "numeric value"),
    ({"거래금액(만원)": "Infinity"}, "numeric value"),
    ({"거래금액(만원)": "-Infinity"}, "numeric value"),
    ({"거래금액(만원)": "-1"}, "numeric value"),
    ({"거래금액(만원)": "0"}, "numeric value"),
])
def test_invalid_transaction_scope_and_numbers_are_rejected(changes, message):
    with pytest.raises(collector.CollectionError, match=message):
        collector.inspect_csv(csv_bytes([transaction(**changes)]), request(), 1)


@pytest.mark.parametrize("column", ["보증금(만원)", "월세금(만원)",
                                     "종전계약 보증금(만원)", "종전계약 월세(만원)"])
def test_nonfinite_rent_amounts_are_rejected(column):
    req = request(kind="rent")
    with pytest.raises(collector.CollectionError, match="numeric value"):
        collector.inspect_csv(csv_bytes([transaction(req, **{column: "NaN"})], req=req), req, 1)


def test_rent_zero_amounts_and_prior_contract_placeholders_are_valid():
    req = request(kind="rent")
    row = transaction(req, **{"보증금(만원)": "0", "월세금(만원)": "0"})
    assert collector.inspect_csv(csv_bytes([row], req=req), req, 1)["row_count"] == 1


@pytest.mark.parametrize("expected_count", [0, 2, -1, True, "1"])
def test_count_mismatch_and_invalid_count_are_rejected(expected_count):
    with pytest.raises(collector.CollectionError, match="count"):
        collector.inspect_csv(csv_bytes(), request(), expected_count)


@pytest.mark.parametrize("raw", [b"", b"<html>error</html>", b"  {\"error\": true}",
                                  b"[1, 2]", b"\x00bad", b"\xff"])
def test_error_or_invalid_download_body_is_rejected(raw):
    with pytest.raises(collector.CollectionError):
        collector.inspect_csv(raw, request(), 1)


@pytest.mark.parametrize("mime", ["text/html; charset=UTF-8", "application/json",
                                   "application/xml"])
def test_error_mime_is_rejected_even_if_body_looks_like_csv(mime):
    with pytest.raises(collector.CollectionError, match="MIME type"):
        collector.inspect_csv(csv_bytes(), request(), 1, mime)


def test_csv_header_and_row_structure_are_validated():
    with pytest.raises(collector.CollectionError, match="Duplicate CSV column"):
        collector.inspect_csv(csv_bytes(columns=SALE_COLUMNS + ["계약일"]), request(), 1)
    with pytest.raises(collector.CollectionError, match="lacks columns"):
        collector.inspect_csv(csv_bytes(columns=[col for col in SALE_COLUMNS
                                                if col != "거래금액(만원)"]), request(), 1)
    with pytest.raises(collector.CollectionError, match="width"):
        collector.inspect_csv(csv_bytes() + b"extra,row\r\n", request(), 2)


def test_complete_collection_publishes_verified_gzip_before_complete_manifest(tmp_path, monkeypatch):
    req = request()
    raw = csv_bytes([transaction(), transaction()])
    client = FakeClient({req.key: raw})
    observed_completed = []
    real_replace = collector.os.replace

    def inspect_publication(source, destination):
        source, destination = Path(source), Path(destination)
        assert source.suffix == ".tmp"
        assert source.parent == destination.parent
        if destination.name == "manifest.json":
            manifest = read_json(source)
            entry = manifest["entries"].get(req.key, {})
            if entry.get("status") == "complete":
                assert gzip.decompress((tmp_path / req.relative_path).read_bytes()) == raw
                observed_completed.append(manifest["status"])
        return real_replace(source, destination)

    monkeypatch.setattr(collector.os, "replace", inspect_publication)
    result = collector.collect([req], tmp_path, CUTOFF, client)
    compressed = (tmp_path / req.relative_path).read_bytes()

    assert result["status"] == "complete"
    assert read_json(tmp_path / "manifest.json") == result
    assert result["completed_count"] == 1
    assert result["data_cutoff"] == CUTOFF.isoformat()
    assert result["entries"][req.key]["row_count"] == 2
    assert result["entries"][req.key]["gzip_sha256"] == hashlib.sha256(compressed).hexdigest()
    assert compressed == gzip.compress(raw, mtime=0)
    assert observed_completed[-1] == "complete"
    assert not list(tmp_path.rglob("*.tmp"))

    cached_client = FakeClient({})
    original_stat = (tmp_path / req.relative_path).stat().st_mtime_ns
    resumed = collector.collect([req], tmp_path, CUTOFF, cached_client)
    assert resumed["status"] == "complete"
    assert cached_client.calls == []
    assert (tmp_path / req.relative_path).stat().st_mtime_ns == original_stat
    assert read_json(tmp_path / "download-ledger.json")["days"][collector.korea_day()]["attempts"] == 1


def test_zero_count_completes_without_download_and_reuses_offline(tmp_path):
    req = request()
    client = FakeClient({}, count_override={req.key: 0})
    result = collector.collect([req], tmp_path, CUTOFF, client)
    entry = result["entries"][req.key]
    assert entry["row_count"] == 0
    assert entry["file"] is None
    assert entry["empty_confirmed_by_count_endpoint"] is True
    assert not (tmp_path / req.relative_path).exists()
    assert client.calls == [("initialize", None), ("count", req.key)]
    cached_client = FakeClient({})
    collector.collect([req], tmp_path, CUTOFF, cached_client)
    assert cached_client.calls == []


def test_server_error_stops_preserves_completed_partition_and_resumes(tmp_path):
    requests = [request(year=year) for year in (2018, 2019, 2020)]
    exports = {req.key: csv_bytes(req=req) for req in requests}
    error = collector.CollectionError("Official service returned an error or quota restriction; stopped")
    client = FakeClient(exports, download_error={requests[1].key: error})

    with pytest.raises(collector.CollectionError, match="quota restriction"):
        collector.collect(requests, tmp_path, CUTOFF, client)

    first_path = tmp_path / requests[0].relative_path
    first_bytes = first_path.read_bytes()
    first_mtime = first_path.stat().st_mtime_ns
    failed = read_json(tmp_path / "manifest.json")
    assert failed["status"] == "failed"
    assert failed["completed_count"] == 1
    assert failed["failed_key"] == requests[1].key
    assert failed["error"] == str(error)
    assert failed["entries"][requests[0].key]["status"] == "complete"
    assert failed["entries"][requests[1].key]["error"] == str(error)
    assert requests[2].key not in failed["entries"]
    assert not (tmp_path / requests[1].relative_path).exists()
    assert all(key != requests[2].key for _, key in client.calls)
    assert read_json(tmp_path / "download-ledger.json")["days"][collector.korea_day()]["attempts"] == 2

    resumed_client = FakeClient({req.key: exports[req.key] for req in requests[1:]})
    resumed = collector.collect(requests, tmp_path, CUTOFF, resumed_client)
    assert resumed["status"] == "complete"
    assert resumed["completed_count"] == 3
    assert "error" not in resumed
    assert all(key != requests[0].key for _, key in resumed_client.calls)
    assert [key for action, key in resumed_client.calls if action == "download"] == [
        req.key for req in requests[1:]]
    assert first_path.read_bytes() == first_bytes
    assert first_path.stat().st_mtime_ns == first_mtime
    assert read_json(tmp_path / "download-ledger.json")["days"][collector.korea_day()]["attempts"] == 4


def test_invalid_csv_is_quarantined_and_history_survives_successful_resume(tmp_path):
    req = request()
    raw = csv_bytes()
    client = FakeClient({req.key: raw}, count_override={req.key: 2})
    with pytest.raises(collector.CollectionError, match="row-count mismatch"):
        collector.collect([req], tmp_path, CUTOFF, client)
    manifest = read_json(tmp_path / "manifest.json")
    assert manifest["status"] == "failed"
    assert manifest["completed_count"] == 0
    assert manifest["entries"][req.key]["status"] == "failed"
    assert not (tmp_path / req.relative_path).exists()
    assert not list(tmp_path.rglob("*.tmp"))
    failures = manifest["entries"][req.key]["validation_failures"]
    assert len(failures) == 1
    failure = failures[0]
    raw_hash = hashlib.sha256(raw).hexdigest()
    assert failure["file"] == ("quarantine/gyeonggi/sale/2020/"
                               f"2020-01-01_2020-12-31.{raw_hash[:16]}.csv.gz")
    quarantined_path = tmp_path / failure["file"]
    quarantined_bytes = quarantined_path.read_bytes()
    assert gzip.decompress(quarantined_bytes) == raw
    assert failure["raw_sha256"] == raw_hash
    assert failure["gzip_sha256"] == hashlib.sha256(quarantined_bytes).hexdigest()
    assert "row-count mismatch" in failure["validation_error"]
    assert failure["expected_count"] == 2
    resumed = collector.collect([req], tmp_path, CUTOFF, FakeClient({req.key: raw}))
    assert resumed["status"] == "complete"
    assert resumed["entries"][req.key]["validation_failures"] == failures
    assert quarantined_path.read_bytes() == quarantined_bytes
    assert gzip.decompress((tmp_path / req.relative_path).read_bytes()) == raw


@pytest.mark.parametrize("body, mime", [
    (b"<html>access challenge with sensitive token</html>", "text/html"),
    (b'{"error": "quota reached", "session": "sensitive token"}', "application/json"),
    (b"<html>unexpected body</html>", "application/octet-stream"),
    (csv_bytes(), "application/json"),
])
def test_error_body_or_mime_is_never_quarantined(tmp_path, body, mime, monkeypatch):
    req = request()
    client = FakeClient({}, count_override={req.key: 1})
    monkeypatch.setattr(client, "download", lambda fields: (body, {"content-type": mime}))
    with pytest.raises(collector.CollectionError):
        collector.collect([req], tmp_path, CUTOFF, client)
    manifest = read_json(tmp_path / "manifest.json")
    assert manifest["status"] == "failed"
    assert "validation_failures" not in manifest["entries"][req.key]
    assert not (tmp_path / "quarantine").exists()
    assert not list(tmp_path.rglob("*.csv.gz"))
    assert "sensitive token" not in (tmp_path / "manifest.json").read_text(encoding="utf-8")


def test_damaged_cache_stops_without_network(tmp_path):
    req = request()
    collector.collect([req], tmp_path, CUTOFF, FakeClient({req.key: csv_bytes()}))
    (tmp_path / req.relative_path).write_bytes(gzip.compress(b"changed data", mtime=0))
    client = FakeClient({})
    with pytest.raises(collector.CollectionError, match="hash mismatch"):
        collector.collect([req], tmp_path, CUTOFF, client)
    assert client.calls == []
    assert read_json(tmp_path / "manifest.json")["status"] == "failed"


def test_atomic_write_failure_keeps_existing_file_and_removes_temporary(tmp_path, monkeypatch):
    destination = tmp_path / "source.csv.gz"
    destination.write_bytes(b"existing complete bytes")

    def fail_replace(*args):
        raise OSError("simulated storage failure")

    monkeypatch.setattr(collector.os, "replace", fail_replace)
    with pytest.raises(OSError, match="storage failure"):
        collector.atomic_bytes(destination, b"new bytes")
    assert destination.read_bytes() == b"existing complete bytes"
    assert sorted(path.name for path in tmp_path.iterdir()) == [destination.name]


def test_download_budget_persists_prior_usage_and_stops_at_one_hundred(tmp_path, monkeypatch):
    monkeypatch.setattr(collector, "korea_day", lambda: "2026-09-09")
    path = tmp_path / "download-ledger.json"
    ledger = collector.DownloadLedger(path, prior_downloads=99)
    ledger.reserve("last permitted partition")
    assert read_json(path)["days"]["2026-09-09"]["attempts"] == 100
    ledger = collector.DownloadLedger(path, prior_downloads=1)
    before = path.read_bytes()
    with pytest.raises(collector.CollectionError, match="budget exhausted"):
        ledger.reserve("must not download")
    assert path.read_bytes() == before
    monkeypatch.setattr(collector, "korea_day", lambda: "2026-09-10")
    collector.DownloadLedger(path).reserve("next day partition")
    days = read_json(path)["days"]
    assert days["2026-09-09"]["attempts"] == 100
    assert days["2026-09-10"]["attempts"] == 1


def test_exhausted_budget_prevents_download_and_records_failure(tmp_path):
    req = request()
    client = FakeClient({req.key: csv_bytes()})
    with pytest.raises(collector.CollectionError, match="budget exhausted"):
        collector.collect([req], tmp_path, CUTOFF, client, daily_limit=1, prior_downloads=1)
    assert client.calls == [("initialize", None), ("count", req.key)]
    assert read_json(tmp_path / "manifest.json")["entries"][req.key]["status"] == "failed"
    assert not (tmp_path / req.relative_path).exists()


@pytest.mark.parametrize("body", [b'{"error": "quota reached"}', b"<html>server failure</html>"])
def test_count_endpoint_errors_stop_without_retry(body, monkeypatch):
    client = collector.PublicCSVClient()
    calls = []

    def response(path, fields):
        calls.append(path)
        return body, {}

    monkeypatch.setattr(client, "_request", response)
    with pytest.raises(collector.CollectionError):
        client.count({})
    assert calls == [collector.COUNT_PATH]


@pytest.mark.parametrize("count", [True, -1, "1.0", None, 1.5])
def test_count_endpoint_rejects_ambiguous_or_invalid_count(count, monkeypatch):
    client = collector.PublicCSVClient()
    monkeypatch.setattr(client, "_json", lambda path, fields: {"cnt": count})
    with pytest.raises(collector.CollectionError, match="nonnegative integer"):
        client.count({})
