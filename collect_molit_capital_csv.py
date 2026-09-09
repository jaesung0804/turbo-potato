"""Collect the public MOLIT apartment CSV exports using the published UI contract.

Only the documented capital-area expansion is planned by default: Gyeonggi and
Incheon sales in 2006–2020, and all three capital-area leases in 2011–cutoff.
No API key, authenticated account, or session rotation is used. Optional bounded
retries apply only to transport failures and retain the same anonymous session.
Original CP949/UTF-8 bytes, including notices, cancellations and repeated rows,
are retained in deterministic gzip files. The manifest is the completion gate.
"""

from __future__ import annotations

import argparse
import calendar
import csv
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import fcntl
import gzip
import hashlib
import http.client
import http.cookiejar
import io
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import tempfile
import time
from typing import Any, Iterable
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo


ORIGIN = "https://rt.molit.go.kr"
PAGE_PATH = "/pt/xls/xls.do?mobileAt="
CSV_PATH = "/pt/xls/ptXlsCSVDown.do"
COUNT_PATH = "/pt/xls/ptXlsDownDataCheck.do"
SIDO_PATH = "/data/sido.do"
REGIONS = {"seoul": "서울특별시", "gyeonggi": "경기도", "incheon": "인천광역시"}
DAILY_LIMIT = 100
MAX_RESPONSE_BYTES = 512 * 1024 * 1024
FORMAT_VERSION = 1


class CollectionError(RuntimeError):
    """Stop this collection; do not retry or bypass the server's response."""


class TransportError(CollectionError):
    """A network transport failure eligible for an explicitly bounded retry."""


def transport_diagnostic(exc: Exception) -> dict[str, Any]:
    """Keep useful connection evidence without logging URLs, cookies or error bodies."""
    reason = getattr(exc, "reason", exc)
    category = "connection_error"
    if isinstance(reason, ssl.SSLCertVerificationError):
        category = "certificate_verification_failed"
    elif isinstance(reason, ssl.SSLError):
        category = "tls_error"
    elif isinstance(reason, socket.gaierror):
        category = "dns_error"
    elif isinstance(reason, TimeoutError):
        category = "timeout"
    elif isinstance(reason, http.client.RemoteDisconnected):
        category = "remote_disconnected"
    return {"category": category, "exception_type": type(exc).__name__,
            "reason_type": type(reason).__name__,
            "errno": getattr(reason, "errno", None),
            "certificate_verify_code": getattr(reason, "verify_code", None)}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def korea_day() -> str:
    return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()


@dataclass(frozen=True)
class ExportRequest:
    region: str
    kind: str
    start: date
    end: date

    def __post_init__(self) -> None:
        if self.region not in REGIONS or self.kind not in {"sale", "rent"}:
            raise ValueError("Only capital-area apartment sale/rent exports are supported")
        if self.start > self.end or self.start.year != self.end.year:
            raise ValueError("Each export must cover a nonempty range within one calendar year")
        if self.start.year < (2006 if self.kind == "sale" else 2011):
            raise ValueError("Date precedes the public transaction coverage")

    @property
    def key(self) -> str:
        return f"{self.region}/{self.kind}/{self.start.year}/{self.start}_{self.end}"

    @property
    def relative_path(self) -> str:
        return f"{self.region}/{self.kind}/{self.start.year}/{self.start}_{self.end}.csv.gz"

    def as_dict(self) -> dict[str, str]:
        return {"region": self.region, "kind": self.kind,
                "start": self.start.isoformat(), "end": self.end.isoformat()}

    def form(self, sido_codes: dict[str, str]) -> dict[str, str]:
        if self.region not in sido_codes:
            raise CollectionError("Requested region is absent from the live official sido list")
        fields = dict.fromkeys(("mobileAt", "srhSggCd", "srhEmdCd", "srhRoadNm",
                                "srhLoadCd", "srhHsmpCd", "srhArea", "srhLrArea",
                                "srhFromAmount", "srhToAmount"), "")
        fields.update({"srhThingNo": "A", "srhDelngSecd": "1" if self.kind == "sale" else "2",
                       "srhAddrGbn": "1", "srhLfstsSecd": "1", "sidoNm": REGIONS[self.region],
                       "sggNm": "전체", "emdNm": "전체", "loadNm": "전체", "areaNm": "전체",
                       "hsmpNm": "전체", "srhFromDt": self.start.isoformat(),
                       "srhToDt": self.end.isoformat(), "srhSidoCd": sido_codes[self.region]})
        return fields


def expansion_requests(cutoff: date, include_current_sales: bool = False) -> list[ExportRequest]:
    if cutoff < date(2020, 12, 31):
        raise ValueError("Expansion cutoff must be 2020-12-31 or later")
    sales = [ExportRequest(region, "sale", date(year, 1, 1), date(year, 12, 31))
             for region in ("gyeonggi", "incheon") for year in range(2006, 2021)]
    leases = [ExportRequest(region, "rent", date(year, 1, 1), min(date(year, 12, 31), cutoff))
              for region in REGIONS for year in range(2011, cutoff.year + 1)]
    current_sales = [ExportRequest(region, "sale", date(cutoff.year, 1, 1), cutoff)
                     for region in REGIONS] if include_current_sales else []
    if include_current_sales and cutoff.year <= 2020:
        raise ValueError("Current sales addition requires a year after the historical sales plan")
    return sales + leases + current_sales


def decode_csv(raw: bytes) -> tuple[str, str]:
    if not raw:
        raise CollectionError("Empty response; expected a CSV download")
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise CollectionError("Response is neither strict UTF-8 nor CP949")
    if text.lstrip().startswith(("<", "{", "[")) or "\x00" in text:
        raise CollectionError("Non-CSV response; possible server error or access challenge")
    return text, encoding


def csv_document(raw: bytes) -> tuple[str, list[list[str]], list[str], Any]:
    """Return encoding, original metadata rows, columns and an iterator of raw rows."""
    text, encoding = decode_csv(raw)
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    metadata: list[list[str]] = []
    try:
        for row in reader:
            if "계약년월" in row and "계약일" in row and "시군구" in row:
                if len(set(row)) != len(row):
                    raise CollectionError("Duplicate CSV column names")
                return encoding, metadata, row, reader
            metadata.append(row)
            if len(metadata) > 100:
                break
    except csv.Error as exc:
        raise CollectionError("Malformed CSV before the transaction header") from exc
    raise CollectionError("Required transaction CSV header was not found")


def iter_csv_records(raw: bytes) -> Iterable[dict[str, str]]:
    """Yield every source transaction, without deduplication or normalization."""
    _, _, fields, rows = csv_document(raw)
    for row in rows:
        if not any(cell.strip() for cell in row):
            continue
        if len(row) != len(fields):
            raise CollectionError("CSV transaction width differs from its header")
        yield dict(zip(fields, row))


def _number(value: str, column: str, positive: bool = False) -> Decimal:
    cleaned = value.replace(",", "").strip()
    if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)", cleaned):
        raise CollectionError(f"Invalid numeric value in {column}")
    try:
        number = Decimal(cleaned)
    except InvalidOperation as exc:
        raise CollectionError(f"Invalid numeric value in {column}") from exc
    if not number.is_finite() or number < 0 or (positive and number == 0):
        raise CollectionError(f"Non-finite or out-of-range numeric value in {column}")
    return number


def inspect_csv(raw: bytes, request: ExportRequest, expected_count: int,
                content_type: str = "") -> dict[str, Any]:
    """Validate scope and count without deleting repeated, revised or cancelled rows."""
    if isinstance(expected_count, bool) or not isinstance(expected_count, int) or expected_count < 0:
        raise CollectionError("Expected row count must be a nonnegative integer")
    if any(mime in content_type.lower() for mime in ("html", "json", "xml")):
        raise CollectionError("Server returned an error/document MIME type instead of CSV")
    encoding, metadata, fields, rows = csv_document(raw)
    required = {"시군구", "계약년월", "계약일", "전용면적(㎡)"}
    amounts = ["거래금액(만원)"] if request.kind == "sale" else ["보증금(만원)", "월세금(만원)"]
    required.update(amounts)
    if not required.issubset(fields):
        raise CollectionError("CSV lacks columns required for the requested transaction type")
    count = 0
    cancelled = 0
    month_counts: dict[str, int] = {}
    first_date: date | None = None
    last_date: date | None = None
    try:
        for row in rows:
            if not any(value.strip() for value in row):
                continue
            if len(row) != len(fields):
                raise CollectionError("CSV transaction width differs from its header")
            record = dict(zip(fields, row))
            if record["시군구"].strip().split(" ", 1)[0] != REGIONS[request.region]:
                raise CollectionError("CSV transaction is outside the requested province")
            ym, day = record["계약년월"].strip(), record["계약일"].strip()
            if not re.fullmatch(r"\d{6}", ym) or not re.fullmatch(r"\d{1,2}", day):
                raise CollectionError("Malformed contract date")
            try:
                contract_date = date(int(ym[:4]), int(ym[4:]), int(day))
            except ValueError as exc:
                raise CollectionError("Invalid calendar contract date") from exc
            if not request.start <= contract_date <= request.end:
                raise CollectionError("CSV transaction is outside the requested contract dates")
            _number(record["전용면적(㎡)"], "전용면적(㎡)", positive=True)
            for column in amounts:
                _number(record[column], column, positive=request.kind == "sale")
            for column in ("종전계약 보증금(만원)", "종전계약 월세(만원)"):
                if record.get(column, "").strip() not in {"", "-", "--"}:
                    _number(record[column], column)
            if any(record.get(column, "").strip() not in {"", "-", "--", "N"}
                   for column in ("해제사유발생일", "해제여부")):
                cancelled += 1
            count += 1
            month_counts[ym] = month_counts.get(ym, 0) + 1
            first_date = min(first_date or contract_date, contract_date)
            last_date = max(last_date or contract_date, contract_date)
    except csv.Error as exc:
        raise CollectionError("Malformed CSV transaction rows") from exc
    if count != expected_count:
        raise CollectionError(f"CSV row-count mismatch: expected {expected_count}, received {count}")
    return {"encoding": encoding, "columns": fields, "header_row": len(metadata) + 1,
            "metadata_rows": metadata, "row_count": count,
            "minimum_contract_date": first_date.isoformat() if first_date else None,
            "maximum_contract_date": last_date.isoformat() if last_date else None,
            "cancellation_marked_rows": cancelled, "deduplication_applied": False,
            "contract_month_counts": dict(sorted(month_counts.items())),
            "raw_bytes": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest()}


class _SameOriginRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.netloc != "rt.molit.go.kr" or parsed.scheme != "https":
            raise CollectionError("Unexpected cross-origin redirect; collection stopped")
        if any(word in newurl.lower() for word in ("login", "captcha", "auth")):
            raise CollectionError("Authentication/access challenge; collection stopped")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class PublicCSVClient:
    """One anonymous cookie session, kept in memory for the entire run."""

    def __init__(self, timeout: float = 180, pause: float = 1.0,
                 metadata_timeout: float | None = None, csv_timeout: float | None = None):
        self.timeout = timeout
        self.metadata_timeout = timeout if metadata_timeout is None else metadata_timeout
        self.csv_timeout = timeout if csv_timeout is None else csv_timeout
        self.pause = pause
        self.opener = urllib.request.build_opener(
            urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()), _SameOriginRedirect())
        self.sido_codes: dict[str, str] = {}
        self._last_request: float | None = None
        self.last_request: dict[str, Any] = {}

    def _request(self, path: str, fields: dict[str, str] | None = None) -> tuple[bytes, dict[str, str]]:
        if path not in {PAGE_PATH, CSV_PATH, COUNT_PATH, SIDO_PATH}:
            raise CollectionError("Endpoint is outside the confirmed public UI contract")
        if self._last_request is not None:
            remaining = self.pause - (time.monotonic() - self._last_request)
            if remaining > 0:
                time.sleep(remaining)
        headers = {"Referer": ORIGIN + PAGE_PATH}
        data = None
        if fields is not None:
            data = urllib.parse.urlencode(fields).encode("utf-8")
            headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        req = urllib.request.Request(ORIGIN + path, data=data, headers=headers)
        self._last_request = time.monotonic()
        self.last_request = {"endpoint": path, "method": req.get_method(),
                             "started_at": utc_now()}
        request_timeout = self.csv_timeout if path == CSV_PATH else self.metadata_timeout
        print(f"request start endpoint={path} timeout={request_timeout:g}s", flush=True)
        try:
            with self.opener.open(req, timeout=request_timeout) as response:
                self.last_request["headers_seconds"] = round(time.monotonic() - self._last_request, 3)
                if response.status != 200:
                    raise CollectionError(f"Server HTTP {response.status}; collection stopped")
                raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise CollectionError("Response exceeds the explicit CSV size bound")
                safe_headers = {key.lower(): value for key, value in response.headers.items()
                                if key.lower() in {"content-type", "date", "last-modified"}}
                leading = raw.lstrip()[:1]
                self.last_request.update({"http_status": response.status,
                    "content_type": safe_headers.get("content-type", ""),
                    "response_bytes": len(raw), "response_sha256": hashlib.sha256(raw).hexdigest(),
                    "body_shape": "markup" if leading == b"<" else
                                  "json_like" if leading in (b"{", b"[") else "other"})
                return raw, safe_headers
        except urllib.error.HTTPError as exc:
            self.last_request.update({"http_status": exc.code, "category": "http_error"})
            raise CollectionError(f"Server HTTP {exc.code}; collection stopped without retry") from None
        except (urllib.error.URLError, TimeoutError, http.client.RemoteDisconnected, OSError) as exc:
            diagnostic = transport_diagnostic(exc)
            self.last_request.update(diagnostic)
            message = f"Transport request failed ({type(exc).__name__}; {diagnostic['category']}; errno={diagnostic['errno']})"
            if diagnostic["category"] == "certificate_verification_failed":
                raise CollectionError(message) from None
            raise TransportError(message) from None
        finally:
            self.last_request["elapsed_seconds"] = round(time.monotonic() - self._last_request, 3)
            print("request end " + json.dumps(self.last_request), flush=True)

    def _json(self, path: str, fields: dict[str, str]) -> Any:
        raw, _ = self._request(path, fields)
        try:
            result = json.loads(raw.decode("utf-8-sig"))
        except (UnicodeDecodeError, ValueError):
            raise CollectionError("Expected JSON; possible server error or access challenge") from None
        if isinstance(result, dict) and result.get("error"):
            # Never print arbitrary server bodies or URL-rewritten session identifiers.
            raise CollectionError("Official service returned an error or quota restriction; stopped")
        return result

    def initialize(self) -> None:
        raw, _ = self._request(PAGE_PATH)
        if any(path.encode() not in raw for path in (CSV_PATH, COUNT_PATH, SIDO_PATH)):
            raise CollectionError("Confirmed export endpoints are absent from the live public page")
        rows = self._json(SIDO_PATH, {})
        if not isinstance(rows, list):
            raise CollectionError("Unexpected official sido-list schema")
        for region, name in REGIONS.items():
            matches = [str(row.get("signguCode", "")) for row in rows
                       if isinstance(row, dict) and row.get("ctprvnNm") == name]
            if len(matches) != 1 or not re.fullmatch(r"\d{5}", matches[0]):
                raise CollectionError("Missing/ambiguous capital-area code in the official sido list")
            self.sido_codes[region] = matches[0]

    def count(self, fields: dict[str, str]) -> int:
        result = self._json(COUNT_PATH, fields)
        count = result.get("cnt") if isinstance(result, dict) else None
        if isinstance(count, bool) or not isinstance(count, (str, int)) or not re.fullmatch(r"\d+", str(count)):
            raise CollectionError("Official count response is not a nonnegative integer")
        return int(count)

    def download(self, fields: dict[str, str]) -> tuple[bytes, dict[str, str]]:
        return self._request(CSV_PATH, fields)


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def atomic_json(path: Path, value: Any) -> None:
    atomic_bytes(path, (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))


class DownloadLedger:
    """Conservatively count attempted downloads before sending their HTTP request."""

    def __init__(self, path: Path, limit: int = DAILY_LIMIT, prior_downloads: int = 0):
        if not 1 <= limit <= DAILY_LIMIT or not 0 <= prior_downloads <= DAILY_LIMIT:
            raise ValueError("Daily limit must be 1–100; prior downloads must be 0–100")
        self.path, self.limit = path, limit
        self.data = json.loads(path.read_text("utf-8")) if path.exists() else {"version": 1, "days": {}}
        if prior_downloads:
            day = self.data["days"].setdefault(korea_day(), {"attempts": 0, "requests": []})
            day["attempts"] = max(day["attempts"], prior_downloads)
            atomic_json(self.path, self.data)

    def reserve(self, key: str) -> None:
        day = self.data["days"].setdefault(korea_day(), {"attempts": 0, "requests": []})
        if day["attempts"] >= self.limit:
            raise CollectionError("Local daily download budget exhausted; resume on a later day")
        day["attempts"] += 1
        day["requests"].append({"key": key, "attempted_at": utc_now()})
        atomic_json(self.path, self.data)


def _verify_cached(output: Path, request: ExportRequest, entry: dict[str, Any], verify_rows: bool = True) -> None:
    if entry.get("query") != request.as_dict():
        raise CollectionError("Cached completion belongs to a different query")
    if entry.get("expected_count") == 0 and entry.get("row_count") == 0 and entry.get("file") is None:
        return
    if entry.get("file") != request.relative_path:
        raise CollectionError("Cached path does not match the requested export")
    path = output / request.relative_path
    try:
        compressed = path.read_bytes()
        raw = gzip.decompress(compressed)
    except (OSError, EOFError) as exc:
        raise CollectionError("Completed source file is missing or damaged; restore it before resuming") from exc
    if (hashlib.sha256(raw).hexdigest() != entry.get("raw_sha256") or
            hashlib.sha256(compressed).hexdigest() != entry.get("gzip_sha256")):
        raise CollectionError("Completed source hash mismatch; restore it before resuming")
    if verify_rows:
        inspect_csv(raw, request, entry["expected_count"])


def _checked_download(client: PublicCSVClient, fields: dict[str, str], request: ExportRequest,
                      ledger: DownloadLedger, entry: dict[str, Any], manifest: dict[str, Any],
                      manifest_path: Path, transport_retries: int,
                      retry_delay: float) -> tuple[int, bytes | None, dict[str, str]]:
    """Repeat count+download only for transport failures, never server/CSV errors."""
    for attempt in range(transport_retries + 1):
        phase = "count"
        entry["status"] = "checking"
        try:
            expected_count = client.count(fields)
            entry.update({"expected_count": expected_count, "count_checked_at": utc_now()})
            if expected_count == 0:
                return expected_count, None, {}
            # Every attempted CSV request consumes quota, even when transport fails.
            ledger.reserve(request.key)
            phase = "download"
            entry["status"] = "downloading"
            atomic_json(manifest_path, manifest)
            raw, headers = client.download(fields)
            return expected_count, raw, headers
        except TransportError as exc:
            retry = attempt < transport_retries
            entry.setdefault("transport_failures", []).append({
                "failed_at": utc_now(), "phase": phase, "attempt": attempt + 1,
                "error": str(exc), "retry_scheduled": retry,
            })
            entry["status"] = "retry_wait" if retry else "failed"
            manifest["updated_at"] = utc_now()
            atomic_json(manifest_path, manifest)
            if not retry:
                raise
            print(f"transport retry {request.key} retry={attempt + 1}/{transport_retries} "
                  f"phase={phase} delay={retry_delay:g}s", flush=True)
            time.sleep(retry_delay)
    raise AssertionError("Bounded transport retry loop ended without a result")


def collect(requests: list[ExportRequest], output: Path, cutoff: date,
            client: PublicCSVClient | None = None, daily_limit: int = DAILY_LIMIT,
            prior_downloads: int = 0, transport_retries: int = 0,
            retry_delay: float = 10, max_new_partitions: int | None = None,
            verify_cached_rows: bool = True) -> dict[str, Any]:
    """Resume a plan; optionally retry transport only and retain completed files."""
    if len({request.key for request in requests}) != len(requests):
        raise ValueError("Collection plan contains duplicate requests")
    if max_new_partitions is not None and (isinstance(max_new_partitions, bool)
            or not isinstance(max_new_partitions, int) or max_new_partitions < 1):
        raise ValueError("max_new_partitions must be a positive integer")
    if any(request.end > cutoff for request in requests):
        raise ValueError("Collection plan extends beyond the explicit data cutoff")
    if not 1 <= daily_limit <= DAILY_LIMIT or not 0 <= prior_downloads <= DAILY_LIMIT:
        raise ValueError("Daily limit must be 1–100; prior downloads must be 0–100")
    if isinstance(transport_retries, bool) or not isinstance(transport_retries, int) or not 0 <= transport_retries <= 2:
        raise ValueError("Transport retries must be an integer from 0 to 2")
    if not 0 <= retry_delay <= 60:
        raise ValueError("Retry delay must be between 0 and 60 seconds")
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    with (output / ".collection.lock").open("a+") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise CollectionError("Another collector is using this output; run only one session") from None
        manifest_path = output / "manifest.json"
        manifest = json.loads(manifest_path.read_text("utf-8")) if manifest_path.exists() else {
            "format_version": FORMAT_VERSION, "source": ORIGIN + PAGE_PATH,
            "download_endpoint": ORIGIN + CSV_PATH, "created_at": utc_now(), "entries": {},
            "limitations": ["Contract-date public microdata; not report-date official statistics",
                            "Source records may later change or be cancelled",
                            "All original rows preserved; no deduplication or cancellation filtering"],
        }
        if manifest.get("format_version") != FORMAT_VERSION:
            raise CollectionError("Unsupported manifest format")
        manifest.update({"status": "running", "data_cutoff": cutoff.isoformat(),
                         "planned_keys": [request.key for request in requests], "updated_at": utc_now()})
        manifest["transport_retry_policy"] = {"max_retries_per_partition": transport_retries,
                                               "delay_seconds": retry_delay,
                                               "same_anonymous_session": True,
                                               "http_and_validation_errors_retried": False}
        manifest["coverage_bounds"] = {
            "sale_source_start": "2006-01-01", "rent_source_start": "2011-01-01",
            "unsupported_rent_interval": {"start": "2006-01-01", "end": "2010-12-31"},
            "data_cutoff": cutoff.isoformat(),
            "cutoff_month_partial": cutoff.day != calendar.monthrange(cutoff.year, cutoff.month)[1],
            "planned_partitions": len(requests),
        }
        manifest.pop("error", None)
        manifest.pop("failed_key", None)
        manifest.pop("completed_at", None)
        manifest.pop("failure_diagnostic", None)
        manifest.pop("pause_reason", None)
        manifest.pop("next_key", None)
        atomic_json(manifest_path, manifest)
        ledger = DownloadLedger(output / "download-ledger.json", daily_limit, prior_downloads)
        active_key: str | None = None
        initialized = False
        new_partitions = 0
        phase = "cache_verification"
        try:
            for request in requests:
                active_key = request.key
                phase = "cache_verification"
                cached = manifest["entries"].get(request.key)
                if cached and cached.get("status") == "complete":
                    _verify_cached(output, request, cached, verify_cached_rows)
                    print(f"reuse {request.key} rows={cached['row_count']}", flush=True)
                    continue
                if max_new_partitions is not None and new_partitions >= max_new_partitions:
                    manifest.update({"status": "paused", "pause_reason": "batch_limit",
                        "next_key": request.key, "updated_at": utc_now(),
                        "completed_count": sum(manifest["entries"].get(r.key, {}).get("status") == "complete"
                                               for r in requests)})
                    atomic_json(manifest_path, manifest)
                    print(f"PAUSED: batch saved; next={request.key}", flush=True)
                    return manifest
                if not initialized:
                    client = client or PublicCSVClient()
                    phase = "initialize"
                    for attempt in range(transport_retries + 1):
                        print(f"initialize attempt={attempt + 1}/{transport_retries + 1}", flush=True)
                        try:
                            client.initialize()
                            break
                        except TransportError:
                            retry = attempt < transport_retries
                            manifest.setdefault("initialization_failures", []).append({
                                "failed_at": utc_now(), "attempt": attempt + 1,
                                "retry_scheduled": retry,
                                "request": dict(getattr(client, "last_request", {}))})
                            atomic_json(manifest_path, manifest)
                            if not retry:
                                raise
                            time.sleep(retry_delay)
                    initialized = True
                    manifest["sido_codes"] = dict(client.sido_codes)
                phase = "count_download_validate"
                fields = request.form(client.sido_codes)
                entry: dict[str, Any] = {"query": request.as_dict(), "request_fields": fields,
                                         "status": "checking", "started_at": utc_now(),
                                         "data_cutoff": cutoff.isoformat()}
                for history in ("validation_failures", "transport_failures"):
                    if cached and cached.get(history):
                        entry[history] = cached[history]
                manifest["entries"][request.key] = entry
                atomic_json(manifest_path, manifest)
                expected_count, raw, headers = _checked_download(
                    client, fields, request, ledger, entry, manifest, manifest_path,
                    transport_retries, retry_delay)
                if expected_count == 0:
                    entry.update({"status": "complete", "row_count": 0, "file": None,
                                  "completed_at": utc_now(), "empty_confirmed_by_count_endpoint": True})
                else:
                    downloaded_at = utc_now()
                    try:
                        checked = inspect_csv(raw, request, expected_count, headers.get("content-type", ""))
                    except CollectionError as exc:
                        # Preserve a recognisable source CSV for review, never HTML/auth/error bodies.
                        preservable = not any(mime in headers.get("content-type", "").lower()
                                              for mime in ("html", "json", "xml"))
                        try:
                            csv_document(raw)
                        except CollectionError:
                            preservable = False
                        if preservable:
                            raw_hash = hashlib.sha256(raw).hexdigest()
                            quarantine_path = (f"quarantine/{request.region}/{request.kind}/{request.start.year}/"
                                               f"{request.start}_{request.end}.{raw_hash[:16]}.csv.gz")
                            quarantined = gzip.compress(raw, mtime=0)
                            atomic_bytes(output / quarantine_path, quarantined)
                            entry.setdefault("validation_failures", []).append({
                                "file": quarantine_path, "raw_sha256": raw_hash,
                                "gzip_sha256": hashlib.sha256(quarantined).hexdigest(),
                                "raw_bytes": len(raw), "downloaded_at": downloaded_at,
                                "validation_error": str(exc), "expected_count": expected_count,
                            })
                        raise
                    compressed = gzip.compress(raw, mtime=0)
                    atomic_bytes(output / request.relative_path, compressed)
                    entry.update(checked)
                    entry.update({"status": "complete", "file": request.relative_path,
                                  "gzip_sha256": hashlib.sha256(compressed).hexdigest(),
                                  "gzip_bytes": len(compressed), "downloaded_at": downloaded_at,
                                  "response_headers": headers, "completed_at": utc_now()})
                manifest["updated_at"] = utc_now()
                atomic_json(manifest_path, manifest)
                new_partitions += 1
                print(f"complete {request.key} rows={entry['row_count']}", flush=True)
            manifest.update({"status": "complete", "completed_at": utc_now(),
                             "completed_count": len(requests), "updated_at": utc_now()})
            atomic_json(manifest_path, manifest)
            return manifest
        except (Exception, KeyboardInterrupt) as exc:
            message = str(exc) if isinstance(exc, CollectionError) else type(exc).__name__
            manifest.update({"status": "interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                             "failed_key": active_key, "error": message, "updated_at": utc_now(),
                             "failure_diagnostic": {"phase": phase,
                                 "request": dict(getattr(client, "last_request", {}))},
                             "completed_count": sum(manifest["entries"].get(r.key, {}).get("status") == "complete"
                                                    for r in requests)})
            if active_key in manifest["entries"] and manifest["entries"][active_key].get("status") != "complete":
                manifest["entries"][active_key].update({"status": "failed", "error": message})
            atomic_json(manifest_path, manifest)
            raise


def prioritize_incheon_sales(requests: list[ExportRequest]) -> list[ExportRequest]:
    """Keep the full coverage plan; visit Incheon historical sales newest first."""
    priority = [r for r in requests if r.region == "incheon" and r.kind == "sale"
                and r.start.year <= 2020]
    rest = [r for r in requests if r not in priority]
    return sorted(priority, key=lambda r: r.start, reverse=True) + rest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/raw/molit-capital-csv"))
    parser.add_argument("--cutoff", type=date.fromisoformat, default=date(2026, 9, 9))
    parser.add_argument("--regions", nargs="+", choices=tuple(REGIONS), default=list(REGIONS))
    parser.add_argument("--kinds", nargs="+", choices=("sale", "rent"), default=["sale", "rent"])
    parser.add_argument("--years", nargs="+", type=int, help="Restrict the default expansion plan")
    parser.add_argument("--incheon-sales-descending", action="store_true",
                        help="Prioritize historical Incheon sales from 2020 downward without dropping coverage")
    parser.add_argument("--daily-limit", type=int, default=DAILY_LIMIT)
    parser.add_argument("--include-current-sales", action="store_true",
                        help="Append three capital-area sale exports in the cutoff year")
    parser.add_argument("--prior-downloads-today", type=int, default=0,
                        help="Conservative floor for already-used daily quota, including manual probes")
    parser.add_argument("--timeout", type=float, default=180)
    parser.add_argument("--metadata-timeout", type=float, help="Socket timeout for page, region list and count")
    parser.add_argument("--csv-timeout", type=float, help="Socket timeout for CSV response; use an outer process deadline")
    parser.add_argument("--pause", type=float, default=1.0)
    parser.add_argument("--transport-retries", type=int, choices=(0, 1, 2), default=0,
                        help="Retry transport failures only, retaining one anonymous session")
    parser.add_argument("--retry-delay", type=float, default=10,
                        help="Seconds before an allowed transport retry (0–60)")
    parser.add_argument("--plan-only", action="store_true", help="Print the plan without network or file writes")
    parser.add_argument("--max-new-partitions", type=int,
                        help="Stop after this many newly completed exports, preserving the full plan")
    parser.add_argument("--cache-hashes-only", action="store_true",
                        help="Verify both hashes of previously validated cached files without reparsing every row")
    args = parser.parse_args(argv)
    if args.pause < 0 or args.pause > 60 or args.timeout <= 0:
        parser.error("pause must be 0–60 seconds and timeout must be positive")
    if any(value is not None and (not math.isfinite(value) or value <= 0)
           for value in (args.metadata_timeout, args.csv_timeout)):
        parser.error("phase timeouts must be finite and positive")
    if not 0 <= args.retry_delay <= 60:
        parser.error("retry-delay must be between 0 and 60 seconds")
    requests = [request for request in expansion_requests(args.cutoff, args.include_current_sales)
                if request.region in args.regions and request.kind in args.kinds
                and (args.years is None or request.start.year in args.years)]
    if args.incheon_sales_descending:
        requests = prioritize_incheon_sales(requests)
    if not requests:
        parser.error("No requested partitions belong to the default expansion plan")
    if args.plan_only:
        print(json.dumps({"data_cutoff": args.cutoff.isoformat(), "count": len(requests),
                          "requests": [request.as_dict() for request in requests]}, indent=2))
        return 0
    try:
        collect(requests, args.output, args.cutoff, PublicCSVClient(args.timeout, args.pause, args.metadata_timeout, args.csv_timeout),
                args.daily_limit, args.prior_downloads_today,
                args.transport_retries, args.retry_delay, args.max_new_partitions,
                not args.cache_hashes_only)
    except (CollectionError, ValueError) as exc:
        print(f"STOP: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("STOP: interrupted; completed partitions are retained for resume", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
