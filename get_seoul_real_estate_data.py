from __future__ import annotations

# %%
import argparse
import csv
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path
from typing import Any

# 사용방법
# $env:SEOUL_API_KEY='발급받은키'
# python main.py --output data/seoul_real_estate_transactions.csv

# 테스트(일부 저장)
# python main.py --output data/sample.csv --limit 1000



SERVICE_NAME = "tbLnOpendataRtmsV"
BASE_URL = "http://openapi.seoul.go.kr:8088"
MAX_PAGE_SIZE = 1000


def build_url(
    api_key: str,
    start: int,
    end: int,
    *,
    service_name: str = SERVICE_NAME,
    filters: list[str] | None = None,
) -> str:
    """Build a Seoul OpenAPI URL.

    Seoul OpenAPI passes parameters as path segments, so Korean filter values
    must be URL-encoded segment by segment.
    """
    parts = [
        BASE_URL,
        urllib.parse.quote(api_key, safe=""),
        "json",
        service_name,
        str(start),
        str(end),
    ]
    if filters:
        parts.extend(urllib.parse.quote(str(value), safe="") for value in filters)
    return "/".join(parts)


def request_json(url: str, *, timeout: int = 30, retries: int = 3) -> dict[str, Any]:
    last_error: Exception | None = None

    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                encoding = response.headers.get_content_charset() or "utf-8"
                return json.loads(response.read().decode(encoding))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(1.5 * attempt)

    raise RuntimeError(f"API request failed after {retries} attempts: {last_error}")


def extract_payload(data: dict[str, Any], service_name: str) -> tuple[int, list[dict[str, Any]]]:
    if service_name not in data:
        result = data.get("RESULT") or data.get("result") or data
        raise RuntimeError(f"Unexpected API response: {result}")

    payload = data[service_name]
    total_count = int(payload.get("list_total_count", 0))
    rows = payload.get("row") or []
    return total_count, rows


def fetch_all_rows(
    api_key: str,
    *,
    service_name: str = SERVICE_NAME,
    page_size: int = MAX_PAGE_SIZE,
    filters: list[str] | None = None,
    sleep_seconds: float = 0.1,
) -> list[dict[str, Any]]:
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    first_url = build_url(api_key, 1, page_size, service_name=service_name, filters=filters)
    first_data = request_json(first_url)
    total_count, rows = extract_payload(first_data, service_name)

    print(f"total_count={total_count:,}, fetched={len(rows):,} (1-{page_size})")

    for start in range(page_size + 1, total_count + 1, page_size):
        end = min(start + page_size - 1, total_count)
        url = build_url(api_key, start, end, service_name=service_name, filters=filters)
        data = request_json(url)
        _, page_rows = extract_payload(data, service_name)
        rows.extend(page_rows)
        print(f"fetched={len(rows):,}/{total_count:,} ({start}-{end})")

        if sleep_seconds:
            time.sleep(sleep_seconds)

    return rows


def fetch_pages(
    api_key: str,
    *,
    service_name: str = SERVICE_NAME,
    page_size: int = MAX_PAGE_SIZE,
    filters: list[str] | None = None,
    sleep_seconds: float = 0.1,
    limit: int | None = None,
):
    if page_size < 1 or page_size > MAX_PAGE_SIZE:
        raise ValueError(f"page_size must be between 1 and {MAX_PAGE_SIZE}")

    first_url = build_url(api_key, 1, page_size, service_name=service_name, filters=filters)
    first_data = request_json(first_url)
    total_count, rows = extract_payload(first_data, service_name)
    target_count = min(total_count, limit) if limit else total_count

    rows = rows[:target_count]
    print(
        f"total_count={total_count:,}, target_count={target_count:,}, "
        f"fetched={len(rows):,}",
        flush=True,
    )
    yield total_count, target_count, 1, min(page_size, target_count), rows

    for start in range(page_size + 1, target_count + 1, page_size):
        end = min(start + page_size - 1, target_count)
        url = build_url(api_key, start, end, service_name=service_name, filters=filters)
        data = request_json(url)
        _, page_rows = extract_payload(data, service_name)
        print(f"fetched={end:,}/{target_count:,} ({start}-{end})", flush=True)
        yield total_count, target_count, start, end, page_rows

        if sleep_seconds:
            time.sleep(sleep_seconds)


def write_csv(rows: list[dict[str, Any]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    fieldnames: list[str] = []
    seen: set[str] = set()
    for row in rows:
        for key in row:
            if key not in seen:
                seen.add(key)
                fieldnames.append(key)

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def write_csv_stream(pages, output_path: Path) -> int:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    writer: csv.DictWriter | None = None
    fieldnames: list[str] = []
    seen: set[str] = set()
    written = 0

    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        for _, _, _, _, rows in pages:
            if not rows:
                continue

            if writer is None:
                for row in rows:
                    for key in row:
                        if key not in seen:
                            seen.add(key)
                            fieldnames.append(key)
                writer = csv.DictWriter(file, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()

            writer.writerows(rows)
            written += len(rows)
            file.flush()

    return written


def add_today_suffix(output_path: Path) -> Path:
    today = date.today().strftime("%Y%m%d")
    return output_path.with_name(f"{output_path.stem}_{today}{output_path.suffix}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="서울시 부동산 실거래가 정보를 1,000건 단위로 전체 수집합니다."
    )
    parser.add_argument(
        "--api-key",
        default=os.getenv("SEOUL_API_KEY"),
        help="서울 열린데이터광장 인증키. 미지정 시 SEOUL_API_KEY 환경변수를 사용합니다.",
    )
    parser.add_argument(
        "--output",
        default="data/seoul_real_estate_transactions.csv",
        help="저장할 CSV 경로",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=MAX_PAGE_SIZE,
        help="호출당 건수. 서울 OpenAPI는 최대 1000건입니다.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.1,
        help="페이지 호출 사이 대기 시간(초)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="테스트용 최대 수집 건수. 미지정 시 전체를 수집합니다.",
    )
    parser.add_argument(
        "--filters",
        nargs="*",
        help=(
            "API path 뒤에 붙일 선택 필터. 예: 2025 11680 강남구. "
            "필터 순서는 서울시 API 명세의 요청인자 순서를 따릅니다."
        ),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()

    if not args.api_key:
        raise SystemExit(
            "서울 열린데이터광장 인증키가 필요합니다. "
            "PowerShell 예: $env:SEOUL_API_KEY='발급받은키'"
        )

    pages = fetch_pages(
        args.api_key,
        page_size=args.page_size,
        filters=args.filters,
        sleep_seconds=args.sleep,
        limit=args.limit,
    )
    output_path = add_today_suffix(Path(args.output))
    written = write_csv_stream(pages, output_path)
    print(f"saved={output_path.resolve()} rows={written:,}")


if __name__ == "__main__":
    main()
