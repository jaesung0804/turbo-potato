# Data Fetch Run - MOLIT apartment trade API:
#   cd C:\code
#   $env:MOLIT_API_KEY='YOUR_DATA_GO_KR_SERVICE_KEY'
#   python get_molit_apt_trade_data.py --start 202401 --end 202605
#
# This downloads Seoul/Gyeonggi/Incheon apartment trade rows from the MOLIT API
# and writes data\capital_area_apt_trade_transactions.csv in the dashboard schema.
#
# Test run:
#   python get_molit_apt_trade_data.py --start 202605 --end 202605 --limit-codes 2

from __future__ import annotations

import argparse
import csv
import os
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any


# 이 파일의 의도:
# - 국토부 아파트 매매 실거래 API를 호출해 수도권 실거래 CSV를 수집합니다.
# - 연월/지역 단위로 반복 호출해 장기간 데이터를 누적할 수 있게 합니다.
# - 파일이 열려 저장 실패가 나도 백업 파일명으로 최대한 수집 결과를 보존합니다.
BASE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTrade/getRTMSDataSvcAptTrade"
DEV_BASE_URL = "https://apis.data.go.kr/1613000/RTMSDataSvcAptTradeDev/getRTMSDataSvcAptTradeDev"
DEFAULT_OUTPUT = "data/capital_area_apt_trade_transactions.csv"
DEFAULT_NUM_ROWS = 1000


@dataclass(frozen=True)
class LawdCode:
    code: str
    sido: str
    sgg: str


CAPITAL_AREA_LAWD_CODES = [
    # Seoul
    LawdCode("11110", "서울특별시", "종로구"),
    LawdCode("11140", "서울특별시", "중구"),
    LawdCode("11170", "서울특별시", "용산구"),
    LawdCode("11200", "서울특별시", "성동구"),
    LawdCode("11215", "서울특별시", "광진구"),
    LawdCode("11230", "서울특별시", "동대문구"),
    LawdCode("11260", "서울특별시", "중랑구"),
    LawdCode("11290", "서울특별시", "성북구"),
    LawdCode("11305", "서울특별시", "강북구"),
    LawdCode("11320", "서울특별시", "도봉구"),
    LawdCode("11350", "서울특별시", "노원구"),
    LawdCode("11380", "서울특별시", "은평구"),
    LawdCode("11410", "서울특별시", "서대문구"),
    LawdCode("11440", "서울특별시", "마포구"),
    LawdCode("11470", "서울특별시", "양천구"),
    LawdCode("11500", "서울특별시", "강서구"),
    LawdCode("11530", "서울특별시", "구로구"),
    LawdCode("11545", "서울특별시", "금천구"),
    LawdCode("11560", "서울특별시", "영등포구"),
    LawdCode("11590", "서울특별시", "동작구"),
    LawdCode("11620", "서울특별시", "관악구"),
    LawdCode("11650", "서울특별시", "서초구"),
    LawdCode("11680", "서울특별시", "강남구"),
    LawdCode("11710", "서울특별시", "송파구"),
    LawdCode("11740", "서울특별시", "강동구"),
    # Incheon
    LawdCode("28110", "인천광역시", "중구"),
    LawdCode("28140", "인천광역시", "동구"),
    LawdCode("28177", "인천광역시", "미추홀구"),
    LawdCode("28185", "인천광역시", "연수구"),
    LawdCode("28200", "인천광역시", "남동구"),
    LawdCode("28237", "인천광역시", "부평구"),
    LawdCode("28245", "인천광역시", "계양구"),
    LawdCode("28260", "인천광역시", "서구"),
    LawdCode("28710", "인천광역시", "강화군"),
    LawdCode("28720", "인천광역시", "옹진군"),
    # Gyeonggi
    LawdCode("41111", "경기도", "수원시 장안구"),
    LawdCode("41113", "경기도", "수원시 권선구"),
    LawdCode("41115", "경기도", "수원시 팔달구"),
    LawdCode("41117", "경기도", "수원시 영통구"),
    LawdCode("41131", "경기도", "성남시 수정구"),
    LawdCode("41133", "경기도", "성남시 중원구"),
    LawdCode("41135", "경기도", "성남시 분당구"),
    LawdCode("41150", "경기도", "의정부시"),
    LawdCode("41171", "경기도", "안양시 만안구"),
    LawdCode("41173", "경기도", "안양시 동안구"),
    LawdCode("41192", "경기도", "부천시 원미구"),
    LawdCode("41194", "경기도", "부천시 소사구"),
    LawdCode("41196", "경기도", "부천시 오정구"),
    LawdCode("41210", "경기도", "광명시"),
    LawdCode("41220", "경기도", "평택시"),
    LawdCode("41250", "경기도", "동두천시"),
    LawdCode("41271", "경기도", "안산시 상록구"),
    LawdCode("41273", "경기도", "안산시 단원구"),
    LawdCode("41281", "경기도", "고양시 덕양구"),
    LawdCode("41285", "경기도", "고양시 일산동구"),
    LawdCode("41287", "경기도", "고양시 일산서구"),
    LawdCode("41290", "경기도", "과천시"),
    LawdCode("41310", "경기도", "구리시"),
    LawdCode("41360", "경기도", "남양주시"),
    LawdCode("41370", "경기도", "오산시"),
    LawdCode("41390", "경기도", "시흥시"),
    LawdCode("41410", "경기도", "군포시"),
    LawdCode("41430", "경기도", "의왕시"),
    LawdCode("41450", "경기도", "하남시"),
    LawdCode("41461", "경기도", "용인시 처인구"),
    LawdCode("41463", "경기도", "용인시 기흥구"),
    LawdCode("41465", "경기도", "용인시 수지구"),
    LawdCode("41480", "경기도", "파주시"),
    LawdCode("41500", "경기도", "이천시"),
    LawdCode("41550", "경기도", "안성시"),
    LawdCode("41570", "경기도", "김포시"),
    LawdCode("41590", "경기도", "화성시"),
    LawdCode("41610", "경기도", "광주시"),
    LawdCode("41630", "경기도", "양주시"),
    LawdCode("41650", "경기도", "포천시"),
    LawdCode("41670", "경기도", "여주시"),
    LawdCode("41800", "경기도", "연천군"),
    LawdCode("41820", "경기도", "가평군"),
    LawdCode("41830", "경기도", "양평군"),
]


DASHBOARD_FIELDNAMES = [
    "RCPT_YR",
    "CGG_CD",
    "CGG_NM",
    "STDG_CD",
    "STDG_NM",
    "LOTNO_SE",
    "LOTNO_SE_NM",
    "MNO",
    "SNO",
    "BLDG_NM",
    "CTRT_DAY",
    "THING_AMT",
    "ARCH_AREA",
    "LAND_AREA",
    "FLR",
    "RGHT_SE",
    "RTRCN_DAY",
    "ARCH_YR",
    "BLDG_USG",
    "DCLR_SE",
    "OPBIZ_RESTAGNT_SGG_NM",
]


def month_range(start: str, end: str) -> list[str]:
    start_year, start_month = int(start[:4]), int(start[4:])
    end_year, end_month = int(end[:4]), int(end[4:])
    months = []
    year, month = start_year, start_month
    while (year, month) <= (end_year, end_month):
        months.append(f"{year}{month:02d}")
        month += 1
        if month == 13:
            year += 1
            month = 1
    return months


def text_of(item: ET.Element, *names: str) -> str:
    for name in names:
        value = item.findtext(name)
        if value is not None:
            return value.strip()
    return ""


def build_query_url(base_url: str, service_key: str, lawd_cd: str, deal_ymd: str, page_no: int, num_rows: int) -> str:
    query = urllib.parse.urlencode(
        {
            "serviceKey": service_key,
            "LAWD_CD": lawd_cd,
            "DEAL_YMD": deal_ymd,
            "pageNo": page_no,
            "numOfRows": num_rows,
        },
        safe="%",
    )
    return f"{base_url}?{query}"


def request_xml(url: str, retries: int = 3, timeout: int = 30) -> ET.Element:
    last_error: Exception | None = None
    for attempt in range(1, retries + 1):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                body = response.read()
            return ET.fromstring(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            last_error = RuntimeError(f"HTTP {exc.code}: {body[:500]}")
            if attempt == retries:
                break
            time.sleep(1.5 * attempt)
        except (urllib.error.URLError, TimeoutError, ET.ParseError) as exc:
            last_error = exc
            if attempt == retries:
                break
            time.sleep(1.5 * attempt)
    raise RuntimeError(f"API request failed after {retries} attempts: {last_error}")


def response_total_count(root: ET.Element) -> int:
    total = root.findtext(".//totalCount") or "0"
    return int(total)


def response_items(root: ET.Element) -> list[ET.Element]:
    return list(root.findall(".//item"))


def normalize_row(item: ET.Element, lawd: LawdCode) -> dict[str, str]:
    deal_year = text_of(item, "dealYear")
    deal_month = text_of(item, "dealMonth").zfill(2)
    deal_day = text_of(item, "dealDay").zfill(2)
    jibun = text_of(item, "jibun")
    main_no, sub_no = split_jibun(jibun)
    cancel_day = next((value for name in ['cdealDay', 'cancelDealDay', 'cancelDealDate']
                       if (value := text_of(item, name)) not in {'', '-', '--'}), '')
    cancel_type = next((value for name in ['cdealType', 'cancelDealType']
                        if (value := text_of(item, name).upper()) not in {'', '-', '--'}), '')
    if cancel_type in {'Y', 'O', '1', '해제'} and not cancel_day:
        cancel_day = 'cancelled'
    legal_dong = text_of(item, "umdNm", "umdNmKor")

    return {
        "RCPT_YR": deal_year,
        "CGG_CD": lawd.code,
        "CGG_NM": lawd.sgg,
        "STDG_CD": "",
        "STDG_NM": legal_dong,
        "LOTNO_SE": "",
        "LOTNO_SE_NM": "",
        "MNO": main_no,
        "SNO": sub_no,
        "BLDG_NM": text_of(item, "aptNm", "aptName"),
        "CTRT_DAY": f"{deal_year}{deal_month}{deal_day}",
        "THING_AMT": text_of(item, "dealAmount").replace(",", ""),
        "ARCH_AREA": text_of(item, "excluUseAr", "excluUseArea"),
        "LAND_AREA": "",
        "FLR": text_of(item, "floor"),
        "RGHT_SE": "",
        "RTRCN_DAY": cancel_day,
        "ARCH_YR": text_of(item, "buildYear"),
        "BLDG_USG": "아파트",
        "DCLR_SE": text_of(item, "dealingGbn"),
        "OPBIZ_RESTAGNT_SGG_NM": text_of(item, "estateAgentSggNm"),
    }


def split_jibun(jibun: str) -> tuple[str, str]:
    if "-" not in jibun:
        return jibun, ""
    main_no, sub_no = jibun.split("-", 1)
    return main_no, sub_no


def fetch_month_code(
    base_url: str,
    service_key: str,
    lawd: LawdCode,
    deal_ymd: str,
    num_rows: int,
    sleep: float,
) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    page_no = 1
    total_count: int | None = None
    while True:
        url = build_query_url(base_url, service_key, lawd.code, deal_ymd, page_no, num_rows)
        root = request_xml(url)
        if total_count is None:
            total_count = response_total_count(root)
        items = response_items(root)
        rows.extend(normalize_row(item, lawd) for item in items)
        if page_no * num_rows >= total_count:
            break
        page_no += 1
        if sleep:
            time.sleep(sleep)
    print(f"{deal_ymd} {lawd.sido} {lawd.sgg}: {len(rows):,} rows")
    return rows


def write_csv(rows: list[dict[str, str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        write_csv_to_path(rows, output_path)
    except PermissionError:
        fallback = output_path.with_name(f"{output_path.stem}_{time.strftime('%Y%m%d_%H%M%S')}{output_path.suffix}")
        print(f"target file is locked, saving to fallback={fallback}")
        write_csv_to_path(rows, fallback)


def write_csv_to_path(rows: list[dict[str, str]], output_path: Path) -> None:
    with output_path.open("w", newline="", encoding="utf-8-sig") as file:
        writer = csv.DictWriter(file, fieldnames=DASHBOARD_FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="국토교통부 아파트 매매 실거래가를 수도권 대시보드용 CSV로 저장합니다.")
    parser.add_argument("--service-key", default=os.getenv("MOLIT_API_KEY"), help="data.go.kr 일반 인증키")
    parser.add_argument(
        "--endpoint",
        choices=["prod", "dev"],
        default="prod",
        help="API 엔드포인트입니다. 개발계정 키가 prod에서 401이면 dev를 사용해 보세요.",
    )
    parser.add_argument("--start", required=True, help="시작 월 YYYYMM")
    parser.add_argument("--end", required=True, help="종료 월 YYYYMM")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="저장할 CSV 경로")
    parser.add_argument("--num-rows", type=int, default=DEFAULT_NUM_ROWS, help="API 페이지 크기")
    parser.add_argument("--sleep", type=float, default=0.05, help="호출 사이 대기 시간(초)")
    parser.add_argument("--limit-codes", type=int, help="테스트용으로 앞 N개 시군구만 수집")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.service_key:
        raise SystemExit("MOLIT_API_KEY 환경변수 또는 --service-key가 필요합니다.")

    lawd_codes = CAPITAL_AREA_LAWD_CODES[: args.limit_codes] if args.limit_codes else CAPITAL_AREA_LAWD_CODES
    base_url = DEV_BASE_URL if args.endpoint == "dev" else BASE_URL
    rows: list[dict[str, str]] = []
    for deal_ymd in month_range(args.start, args.end):
        for lawd in lawd_codes:
            rows.extend(fetch_month_code(base_url, args.service_key, lawd, deal_ymd, args.num_rows, args.sleep))

    output_path = Path(args.output)
    write_csv(rows, output_path)
    print(f"saved={output_path.resolve()} rows={len(rows):,}")


if __name__ == "__main__":
    main()
