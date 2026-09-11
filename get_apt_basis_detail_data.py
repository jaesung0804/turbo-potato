# Data Collection:
#   cd C:\code
#   $env:MOLIT_APT_BASIS_KEY="YOUR_DECODING_SERVICE_KEY"
#   python get_apt_basis_detail_data.py --limit 1000
# Resume/full run:
#   python get_apt_basis_detail_data.py
# Output:
#   data\apt_basis_detail_info.csv

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import urlopen


# 이 파일의 의도:
# - K-APT 단지 목록 API로 단지코드를 확보합니다.
# - 단지 상세 API로 지하철 호선, 역명, 역 거리 같은 입지 정보를 수집합니다.
# - 수집 결과는 대시보드 데이터 생성 단계에서 실거래 건물명과 매칭해 사용합니다.
ROOT_DIR = Path(__file__).resolve().parent
APT_MASTER_PATH = ROOT_DIR / "data" / "apt_mst_info_202410.csv"
KAPT_LIST_PATH = ROOT_DIR / "data" / "kapt_code_list.csv"
OUTPUT_PATH = ROOT_DIR / "data" / "apt_basis_detail_info.csv"
ENDPOINT = "https://apis.data.go.kr/1613000/AptBasisInfoServiceV4/getAphusDtlInfoV4"
LIST_ENDPOINT = "https://apis.data.go.kr/1613000/AptListService3/getTotalAptList3"
# Credentials are supplied only by the private runtime environment.
FIELDNAMES = [
    "apt_cd",
    "apt_nm",
    "bjd_code",
    "subway_line",
    "subway_station",
    "subway_distance_m",
    "bus_stop_distance_m",
    "education_facilities",
    "raw_json",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="공동주택 상세 API에서 지하철/교육시설 정보를 수집합니다.")
    parser.add_argument("--input", default=str(APT_MASTER_PATH))
    parser.add_argument("--kapt-list", default=str(KAPT_LIST_PATH))
    parser.add_argument("--output", default=str(OUTPUT_PATH))
    parser.add_argument("--service-key", default=os.environ.get("MOLIT_APT_BASIS_KEY"))
    parser.add_argument("--limit", type=int, help="테스트용 최대 호출 수입니다.")
    parser.add_argument("--sleep", type=float, default=0.08)
    parser.add_argument("--reset", action="store_true", help="기존 상세 수집 CSV를 백업하고 처음부터 다시 수집합니다.")
    args = parser.parse_args()
    if not args.service_key:
        parser.error("MOLIT_APT_BASIS_KEY 환경 변수 또는 --service-key가 필요합니다.")
    return args


def parse_float(value: Any) -> float | None:
    value = "" if value is None else str(value).strip().replace(",", "")
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit() or ch == ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def parse_subway_distance_m(value: Any) -> int | None:
    text = "" if value is None else str(value).strip()
    numbers = [float(item) for item in re.findall(r"\d+(?:\.\d+)?", text)]
    if not numbers:
        return None
    parsed = max(numbers)
    if "분" in text:
        return int(parsed * 80)
    return int(parsed)


def first_value(item: dict[str, Any], names: list[str]) -> str:
    lower = {str(key).lower(): value for key, value in item.items()}
    for name in names:
        if name in item and item[name] not in (None, ""):
            return str(item[name]).strip()
        value = lower.get(name.lower())
        if value not in (None, ""):
            return str(value).strip()
    return ""


def normalize_item(payload: dict[str, Any]) -> dict[str, Any]:
    response = payload.get("response", payload)
    body = response.get("body", response)
    if isinstance(body.get("item"), dict):
        return body["item"]
    items = body.get("items", body.get("item", body))
    if isinstance(items, dict) and "item" in items:
        items = items["item"]
    if isinstance(items, list):
        return items[0] if items else {}
    return items if isinstance(items, dict) else {}


def fetch_detail(apt_cd: str, service_key: str) -> dict[str, Any]:
    params = {
        "serviceKey": service_key,
        "kaptCode": apt_cd,
        "_type": "json",
    }
    url = f"{ENDPOINT}?{urlencode(params)}"
    with urlopen(url, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def extract_row(apt_cd: str, apt_nm: str, bjd_code: str, item: dict[str, Any]) -> dict[str, str]:
    subway_distance = first_value(item, ["kaptdWtimesub", "subwayDistance", "subwaydist", "subway_distance", "지하철역거리", "지하철역 거리"])
    parsed_subway_distance = parse_subway_distance_m(subway_distance)
    return {
        "apt_cd": apt_cd,
        "apt_nm": apt_nm,
        "bjd_code": first_value(item, ["bjdCode", "bjd_code", "법정동코드"]) or bjd_code,
        "subway_line": first_value(item, ["subwayLine", "subwayHo", "subway_line", "지하철호선", "지하철 호선"]),
        "subway_station": first_value(item, ["subwayStation", "subwayNm", "subway_station", "지하철역명", "지하철역명"]),
        "subway_distance_m": "" if parsed_subway_distance is None else str(parsed_subway_distance),
        "bus_stop_distance_m": first_value(item, ["busStopDistance", "bus_stop_distance", "버스정류장거리", "버스정류장 거리"]),
        "education_facilities": first_value(item, ["educationFacilities", "education", "교육시설"]),
        "raw_json": json.dumps(item, ensure_ascii=False, separators=(",", ":")),
    }


def read_done(path: Path) -> set[str]:
    if not path.exists():
        return set()
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return {row.get("apt_cd", "") for row in csv.DictReader(file) if row.get("apt_cd")}


def ensure_output_schema(path: Path) -> None:
    if not path.exists():
        return
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.reader(file)
        header = next(reader, [])
    if header != FIELDNAMES:
        backup = path.with_suffix(".legacy.csv")
        path.replace(backup)
        print(f"기존 출력 파일 스키마가 달라 {backup}로 이동했습니다.")


def fetch_kapt_list_page(service_key: str, page_no: int, num_rows: int = 1000) -> tuple[list[dict[str, str]], int]:
    params = {
        "serviceKey": service_key,
        "pageNo": page_no,
        "numOfRows": num_rows,
        "_type": "json",
    }
    url = f"{LIST_ENDPOINT}?{urlencode(params)}"
    with urlopen(url, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    body = payload.get("response", {}).get("body", {})
    items = body.get("items", [])
    if isinstance(items, dict):
        items = items.get("item", [])
    if isinstance(items, dict):
        items = [items]
    return items or [], int(body.get("totalCount") or 0)


def fetch_kapt_list(service_key: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    page_no = 1
    num_rows = 1000
    total_count = None
    while True:
        page_rows, total = fetch_kapt_list_page(service_key, page_no, num_rows)
        if total_count is None:
            total_count = total
        rows.extend(page_rows)
        print(f"KAPT list page {page_no}: {len(rows)}/{total_count or '?'}")
        if not page_rows or (total_count and len(rows) >= total_count):
            break
        page_no += 1
    return rows


def ensure_kapt_list(path: Path, service_key: str) -> None:
    if path.exists():
        return
    rows = fetch_kapt_list(service_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        fieldnames = ["kaptCode", "kaptName", "as1", "as2", "as3", "as4", "bjdCode"]
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key, "") for key in fieldnames})


def iter_apt_codes(path: Path, service_key: str) -> list[tuple[str, str, str]]:
    ensure_kapt_list(path, service_key)
    rows: list[tuple[str, str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        for row in csv.DictReader(file):
            apt_cd = row.get("kaptCode", "").strip()
            apt_nm = row.get("kaptName", "").strip()
            bjd_code = row.get("bjdCode", "").strip()
            sido = row.get("as1", "").strip()
            if apt_cd and sido in {"서울특별시", "경기도", "인천광역시"}:
                rows.append((apt_cd, apt_nm, bjd_code))
    return rows


def main() -> None:
    args = parse_args()
    input_path = Path(args.kapt_list)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.reset and output_path.exists():
        backup = output_path.with_suffix(".reset-backup.csv")
        output_path.replace(backup)
        print(f"기존 출력 파일을 {backup}로 이동했습니다.")
    ensure_output_schema(output_path)
    done = read_done(output_path)
    mode = "a" if output_path.exists() else "w"
    rows = [(code, name, bjd_code) for code, name, bjd_code in iter_apt_codes(input_path, args.service_key) if code not in done]
    if args.limit:
        rows = rows[: args.limit]

    with output_path.open(mode, encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=FIELDNAMES)
        if mode == "w":
            writer.writeheader()
        for index, (apt_cd, apt_nm, bjd_code) in enumerate(rows, 1):
            try:
                payload = fetch_detail(apt_cd, args.service_key)
                writer.writerow(extract_row(apt_cd, apt_nm, bjd_code, normalize_item(payload)))
            except Exception as exc:
                writer.writerow({"apt_cd": apt_cd, "apt_nm": apt_nm, "raw_json": json.dumps({"error": str(exc)}, ensure_ascii=False)})
            if index % 100 == 0:
                print(f"{index}/{len(rows)} collected")
            time.sleep(args.sleep)


if __name__ == "__main__":
    main()
