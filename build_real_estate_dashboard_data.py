# Data Build Only:
#   cd C:\code
#   python build_real_estate_dashboard_data.py
# This only rebuilds web\data\seoul_real_estate_summary.json.

from __future__ import annotations

import argparse
import csv
import json
import math
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any


# 이 파일의 의도:
# - 국토부 실거래 CSV를 웹에서 바로 읽을 수 있는 요약 JSON으로 변환합니다.
# - 실거래 원천 행을 연도/시도/시군구/읍면동/건물/평형 단위로 묶습니다.
# - 직거래와 취소거래를 제외해 왜곡된 가격이 대시보드에 들어오지 않게 합니다.
# - 세대수, 연식, 초품아, 역세권 같은 보조 정보를 매칭해 매물 비교에 필요한 지표를 만듭니다.
# - 결과물은 web/data/seoul_real_estate_summary.json이며 GitHub Pages에서 정적으로 배포됩니다.


SQM_PER_PYEONG = 3.3058
DEFAULT_PROPERTY_TYPES = {"아파트"}
METRIC_KEYS = [
    "price_billion",
    "area_pyeong",
    "price_per_pyeong",
]

SGG_STAT_CODE_BY_LAWD = {
    "11110": "11010", "11140": "11020", "11170": "11030", "11200": "11040", "11215": "11050",
    "11230": "11060", "11260": "11070", "11290": "11080", "11305": "11090", "11320": "11100",
    "11350": "11110", "11380": "11120", "11410": "11130", "11440": "11140", "11470": "11150",
    "11500": "11160", "11530": "11170", "11545": "11180", "11560": "11190", "11590": "11200",
    "11620": "11210", "11650": "11220", "11680": "11230", "11710": "11240", "11740": "11250",
    "28110": "23010", "28140": "23020", "28177": "23040", "28185": "23050", "28200": "23060",
    "28237": "23070", "28245": "23080", "28260": "23090", "28710": "23510", "28720": "23520",
    "41111": "31011", "41113": "31012", "41115": "31013", "41117": "31014", "41131": "31021",
    "41133": "31022", "41135": "31023", "41150": "31030", "41171": "31041", "41173": "31042",
    "41192": "31051", "41194": "31052", "41196": "31053", "41210": "31060", "41220": "31070",
    "41250": "31080", "41271": "31091", "41273": "31092", "41281": "31101", "41285": "31103",
    "41287": "31104", "41290": "31110", "41310": "31120", "41360": "31130", "41370": "31140",
    "41390": "31150", "41410": "31160", "41430": "31170", "41450": "31180", "41461": "31191",
    "41463": "31192", "41465": "31193", "41480": "31200", "41500": "31210", "41550": "31220",
    "41570": "31230", "41590": "31240", "41610": "31250", "41630": "31260", "41650": "31270",
    "41670": "31280", "41800": "31550", "41820": "31570", "41830": "31580",
}

LEGAL_ADMIN_MAP_PATH = Path("data/국가데이터처_법정동 연계정보_20250602.csv")
COMPLEX_INFO_PATH = Path("data/한국부동산원_공동주택 단지 식별정보_기본정보_20250918.csv")
APT_COORD_PATH = Path("data/apt_mst_info_202410.csv")
SCHOOL_COORD_PATH = Path("data/전국초중등학교위치표준데이터.csv")
APT_DETAIL_PATH = Path("data/apt_basis_detail_info.csv")
EARTH_RADIUS_M = 6371000
SCHOOL_DISTANCE_M = 500
SCHOOL_GRID_SIZE = 0.01


def parse_float(value: Any) -> float | None:
    value = "" if value is None else str(value).strip().replace(",", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def parse_int(value: Any) -> int | None:
    parsed = parse_float(value)
    return int(parsed) if parsed is not None else None


def parse_walk_distance_m(value: Any) -> int | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    numbers = []
    for token in text.replace("~", " ").replace("-", " ").split():
        parsed = parse_float(token)
        if parsed is not None:
            numbers.append(parsed)
    if not numbers:
        parsed = parse_float(text)
        numbers = [parsed] if parsed is not None else []
    if not numbers:
        return None
    value = max(numbers)
    if "분" in text or "遺" in text:
        return int(value * 80)
    return int(value)


def count_facility_items(value: Any) -> int | None:
    text = "" if value is None else str(value).strip()
    if not text:
        return None
    parts = [
        item.strip()
        for item in text.replace("|", ",").replace("/", ",").replace(";", ",").split(",")
        if item.strip()
    ]
    return len(parts) if parts else 1


def normalize_name(value: str) -> str:
    return "".join(ch for ch in value.lower() if ch.isalnum())


def normalize_sgg_name(value: str) -> str:
    return value.split()[-1] if " " in value else value


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    lat1_rad = math.pi * lat1 / 180
    lon1_rad = math.pi * lon1 / 180
    lat2_rad = math.pi * lat2 / 180
    lon2_rad = math.pi * lon2 / 180
    dlat = lat2_rad - lat1_rad
    dlon = lon2_rad - lon1_rad
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return EARTH_RADIUS_M * c


def school_grid_key(lat: float, lon: float) -> tuple[int, int]:
    return (int(lat / SCHOOL_GRID_SIZE), int(lon / SCHOOL_GRID_SIZE))


def sido_name_for_code(code: str) -> str:
    code = (code or "").strip()
    if code.startswith("11"):
        return "서울특별시"
    if code.startswith(("28", "23")):
        return "인천광역시"
    if code.startswith(("41", "31")):
        return "경기도"
    return "기타"


def legal_dong_code(row: dict[str, str]) -> str:
    gu_code = row.get("CGG_CD", "").strip()
    dong_code = row.get("STDG_CD", "").strip()
    if dong_code:
        return f"{gu_code}{dong_code[:3]}"
    dong_name = row.get("STDG_NM", "").strip()
    return f"{gu_code}:{dong_name}" if dong_name else gu_code


def legal_dong_full_code(row: dict[str, str]) -> str:
    gu_code = row.get("CGG_CD", "").strip()
    dong_code = row.get("STDG_CD", "").strip()
    return f"{gu_code}{dong_code[:5]}" if gu_code and dong_code else ""


def load_legal_admin_mapping(path: Path = LEGAL_ADMIN_MAP_PATH) -> tuple[dict[str, list[str]], dict[tuple[str, str], list[str]]]:
    by_legal_code: dict[str, set[str]] = {}
    by_name: dict[tuple[str, str], set[str]] = {}
    if not path.exists():
        return {}, {}

    with path.open("r", encoding="cp949", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            legal_code = row.get("법정동코드", "").strip()
            admin_code = row.get("행정구역코드", "").strip()
            if len(admin_code) == 7:
                admin_code = f"{admin_code}0"
            sgg_name = row.get("시군구명", "").strip()
            legal_name = row.get("법정동명", "").strip()
            if not admin_code:
                continue
            if legal_code:
                by_legal_code.setdefault(legal_code, set()).add(admin_code)
            if sgg_name and legal_name:
                by_name.setdefault((sgg_name, legal_name), set()).add(admin_code)

    return (
        {key: sorted(values) for key, values in by_legal_code.items()},
        {key: sorted(values) for key, values in by_name.items()},
    )


def load_complex_info(path: Path = COMPLEX_INFO_PATH) -> dict[tuple[str, str, str], dict[str, int | None]]:
    mapping: dict[tuple[str, str, str], dict[str, int | None]] = {}
    if not path.exists():
        return mapping

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            address = row.get("주소", "").strip()
            parts = address.split()
            if len(parts) < 3:
                continue
            sgg_name = normalize_sgg_name(parts[1])
            dong_name = parts[2]
            households = parse_int(row.get("세대수"))
            approved_year = parse_int((row.get("사용승인일") or "")[:4])
            names = {
                row.get("단지명_공시가격", "").strip(),
                row.get("단지명_건축물대장", "").strip(),
                row.get("단지명_도로명주소", "").strip(),
            }
            for name in names:
                normalized = normalize_name(name)
                if not normalized:
                    continue
                mapping[(sgg_name, dong_name, normalized)] = {
                    "households": households,
                    "built_year": approved_year,
                }
    return mapping


def complex_info_for(row: dict[str, str], complex_info: dict[tuple[str, str, str], dict[str, int | None]]) -> dict[str, int | None]:
    key = (
        normalize_sgg_name(row.get("CGG_NM", "").strip()),
        row.get("STDG_NM", "").strip(),
        normalize_name(row.get("BLDG_NM", "").strip()),
    )
    return complex_info.get(key, {})


def load_elementary_school_grid(path: Path = SCHOOL_COORD_PATH) -> dict[tuple[int, int], list[dict[str, Any]]]:
    grid: dict[tuple[int, int], list[dict[str, Any]]] = {}
    if not path.exists():
        return grid

    with path.open("r", encoding="cp949", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            if row.get("학교급구분", "").strip() != "초등학교":
                continue
            if row.get("운영상태", "").strip() != "운영":
                continue
            address = f"{row.get('소재지지번주소', '')} {row.get('소재지도로명주소', '')}"
            if not address.startswith(("서울", "경기", "인천")) and " 서울" not in address and " 경기" not in address and " 인천" not in address:
                continue
            lat = parse_float(row.get("위도"))
            lon = parse_float(row.get("경도"))
            if lat is None or lon is None:
                continue
            school = {
                "name": row.get("학교명", "").strip(),
                "lat": lat,
                "lon": lon,
            }
            grid.setdefault(school_grid_key(lat, lon), []).append(school)
    return grid


def nearest_elementary(lat: float, lon: float, school_grid: dict[tuple[int, int], list[dict[str, Any]]]) -> dict[str, Any] | None:
    if not school_grid:
        return None
    base_lat, base_lon = school_grid_key(lat, lon)
    best: dict[str, Any] | None = None
    for dlat in range(-2, 3):
        for dlon in range(-2, 3):
            for school in school_grid.get((base_lat + dlat, base_lon + dlon), []):
                distance = haversine_m(lat, lon, school["lat"], school["lon"])
                if best is None or distance < best["distance_m"]:
                    best = {"name": school["name"], "distance_m": distance}
    return best


def merge_school_match(current: dict[str, Any] | None, candidate: dict[str, Any] | None) -> dict[str, Any] | None:
    if candidate is None:
        return current
    if current is None or candidate["distance_m"] < current["distance_m"]:
        return candidate
    return current


def normalize_subway_line(value: str) -> str:
    value = (value or "").strip()
    if not value:
        return ""
    value = value.replace(" ", "")
    value = value.replace("호선", "")
    return f"{value}호선" if value and not value.endswith("호선") else value


def load_apt_detail_info(path: Path = APT_DETAIL_PATH) -> dict[tuple[str, str], dict[str, Any]]:
    mapping: dict[tuple[str, str], dict[str, Any]] = {}
    if not path.exists():
        return mapping

    with path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            raw = {}
            if row.get("raw_json"):
                try:
                    raw = json.loads(row["raw_json"])
                except json.JSONDecodeError:
                    raw = {}
            cgg_code = (row.get("bjd_code") or "")[:5]
            apt_name = normalize_name(row.get("apt_nm", ""))
            if not cgg_code or not apt_name:
                continue
            subway_line = row.get("subway_line") or raw.get("subwayLine") or ""
            subway_station = row.get("subway_station") or raw.get("subwayStation") or ""
            subway_distance = parse_float(row.get("subway_distance_m")) or parse_walk_distance_m(raw.get("kaptdWtimesub"))
            bus_distance = parse_float(row.get("bus_stop_distance_m")) or parse_walk_distance_m(raw.get("kaptdWtimebus"))
            education_facilities = row.get("education_facilities") or raw.get("educationFacility") or ""
            lines = [
                normalize_subway_line(item)
                for item in subway_line.replace("/", ",").replace("|", ",").split(",")
                if normalize_subway_line(item)
            ]
            mapping[(cgg_code, apt_name)] = {
                "subway_lines": sorted(set(lines)),
                "subway_station": str(subway_station).strip() or None,
                "subway_distance_m": subway_distance,
                "bus_stop_distance_m": bus_distance,
                "education_facilities": str(education_facilities).strip() or None,
                "education_facility_count": count_facility_items(education_facilities),
            }
    return mapping


def load_apt_school_info(
    apt_path: Path = APT_COORD_PATH,
    school_path: Path = SCHOOL_COORD_PATH,
) -> dict[tuple[str, str], dict[str, Any]]:
    mapping: dict[tuple[str, str], dict[str, Any]] = {}
    if not apt_path.exists() or not school_path.exists():
        return mapping

    school_grid = load_elementary_school_grid(school_path)
    apt_detail_info = load_apt_detail_info()
    with apt_path.open("r", encoding="cp949", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            apt_cd = row.get("apt_cd", "").strip()
            cgg_code = (row.get("legaldong_cd", "") or "")[:5]
            name = normalize_name(row.get("apt_nm", "").strip())
            lat = parse_float(row.get("la"))
            lon = parse_float(row.get("lo"))
            if not cgg_code or not name or lat is None or lon is None:
                continue
            nearest = nearest_elementary(lat, lon, school_grid)
            key = (cgg_code, name)
            info = mapping.setdefault(key, {})
            school_info = merge_school_match(
                {
                    "name": info["nearest_elementary_name"],
                    "distance_m": info["nearest_elementary_m"],
                } if info.get("nearest_elementary_m") is not None else None,
                nearest,
            )
            if school_info:
                info["nearest_elementary_name"] = school_info["name"]
                info["nearest_elementary_m"] = school_info["distance_m"]
            detail = apt_detail_info.get(key, {})
            if detail:
                info["subway_lines"] = detail.get("subway_lines", [])
                info["subway_station"] = detail.get("subway_station")
                info["subway_distance_m"] = detail.get("subway_distance_m")
                info["bus_stop_distance_m"] = detail.get("bus_stop_distance_m")
                info["education_facilities"] = detail.get("education_facilities")
                info["education_facility_count"] = detail.get("education_facility_count")
    return mapping


def apt_school_info_for(row: dict[str, str], apt_school_info: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any] | None:
    key = (
        row.get("CGG_CD", "").strip(),
        normalize_name(row.get("BLDG_NM", "").strip()),
    )
    return apt_school_info.get(key)


def map_region_codes(
    row: dict[str, str],
    by_legal_code: dict[str, list[str]],
    by_name: dict[tuple[str, str], list[str]],
) -> list[str]:
    full_code = legal_dong_full_code(row)
    if full_code and full_code in by_legal_code:
        return by_legal_code[full_code]

    name_key = (row.get("CGG_NM", "").strip(), row.get("STDG_NM", "").strip())
    if name_key in by_name:
        return by_name[name_key]

    gu_code = row.get("CGG_CD", "").strip()
    fallback = SGG_STAT_CODE_BY_LAWD.get(gu_code, legal_dong_code(row))
    return [fallback] if fallback else []


def address_key(row: dict[str, str]) -> str:
    gu = row.get("CGG_NM", "").strip()
    dong = row.get("STDG_NM", "").strip()
    main_no = row.get("MNO", "").strip().lstrip("0") or "0"
    sub_no = row.get("SNO", "").strip().lstrip("0")
    lot = main_no if not sub_no or sub_no == "0" else f"{main_no}-{sub_no}"
    building = row.get("BLDG_NM", "").strip()
    return f"{gu} {dong} {lot} {building}".strip()


def area_type_label(metrics: dict[str, float]) -> str:
    area = metrics.get("area_pyeong")
    return f"전용 {int(area)}평" if area is not None else "전용면적 미상"


def typed_address_key(row: dict[str, str], metrics: dict[str, float]) -> str:
    gu = row.get("CGG_NM", "").strip()
    dong = row.get("STDG_NM", "").strip()
    building = row.get("BLDG_NM", "").strip() or "(건물명 없음)"
    return f"{gu} {dong} {building} | {area_type_label(metrics)}".strip()


def contract_year(row: dict[str, str]) -> str:
    day = row.get("CTRT_DAY", "").strip()
    if len(day) >= 4 and day[:4].isdigit():
        return day[:4]
    return row.get("RCPT_YR", "").strip()


def calculate_metrics(row: dict[str, str]) -> dict[str, float] | None:
    price_10k = parse_float(row.get("THING_AMT"))
    area_sqm = parse_float(row.get("ARCH_AREA"))
    land_sqm = parse_float(row.get("LAND_AREA"))

    if not price_10k or not area_sqm or area_sqm <= 0:
        return None

    price_billion = price_10k / 10000
    area_pyeong = area_sqm / SQM_PER_PYEONG
    land_pyeong = land_sqm / SQM_PER_PYEONG if land_sqm else None
    price_per_pyeong = price_10k / area_pyeong if area_pyeong else None
    land_ratio = land_sqm / area_sqm if land_sqm else None
    land_efficiency = land_pyeong / price_billion if land_pyeong and price_billion else None

    metrics = {
        "price_billion": price_billion,
        "area_pyeong": area_pyeong,
        "price_per_pyeong": price_per_pyeong,
    }
    if land_pyeong is not None:
        metrics["land_pyeong"] = land_pyeong
    if land_ratio is not None:
        metrics["land_ratio"] = land_ratio
    if land_efficiency is not None:
        metrics["land_efficiency"] = land_efficiency
    return metrics


def summarize(values: list[float]) -> dict[str, float | None]:
    if not values:
        return {"avg": None, "median": None, "min": None, "max": None}
    return {
        "avg": round(mean(values), 3),
        "median": round(median(values), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
    }


def blank_metric_bucket() -> dict[str, list[float]]:
    return {key: [] for key in METRIC_KEYS}


def round_metric(value: float | None, digits: int = 2) -> float | None:
    return round(value, digits) if value is not None else None


def update_metric_bucket(bucket: dict[str, list[float]], metrics: dict[str, float]) -> None:
    for key in METRIC_KEYS:
        value = metrics.get(key)
        if value is not None:
            bucket[key].append(value)


def build_dashboard_data(
    input_path: Path,
    output_path: Path,
    property_types: set[str],
    address_limit_per_region_year: int,
    recent_limit_per_region_year: int,
    include_direct_trades: bool,
) -> None:
    regions: dict[str, dict[str, Any]] = {}
    years_seen: set[str] = set()
    total_rows = 0
    used_rows = 0
    excluded_direct_rows = 0
    admin_by_legal_code, admin_by_name = load_legal_admin_mapping()
    complex_info = load_complex_info()
    apt_school_info = load_apt_school_info()

    with input_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            total_rows += 1
            if row.get("RTRCN_DAY", "").strip():
                continue
            if property_types and row.get("BLDG_USG", "").strip() not in property_types:
                continue
            if not include_direct_trades and row.get("DCLR_SE", "").strip() == "직거래":
                excluded_direct_rows += 1
                continue

            metrics = calculate_metrics(row)
            code = legal_dong_code(row)
            year = contract_year(row)
            if not metrics or not code or not year:
                continue

            used_rows += 1
            years_seen.add(year)
            map_codes = map_region_codes(row, admin_by_legal_code, admin_by_name)
            complex_match = complex_info_for(row, complex_info)
            school_match = apt_school_info_for(row, apt_school_info)
            row_households = parse_int(row.get("HHLD_CNT")) or complex_match.get("households")
            row_built_year = parse_int(row.get("ARCH_YR")) or complex_match.get("built_year")
            row_nearest_school_name = school_match.get("nearest_elementary_name") if school_match else None
            row_nearest_school_distance = school_match.get("nearest_elementary_m") if school_match else None
            row_elementary_500m = row_nearest_school_distance is not None and row_nearest_school_distance <= SCHOOL_DISTANCE_M
            row_subway_lines = school_match.get("subway_lines", []) if school_match else []
            row_subway_station = school_match.get("subway_station") if school_match else None
            row_subway_distance = school_match.get("subway_distance_m") if school_match else None
            row_bus_stop_distance = school_match.get("bus_stop_distance_m") if school_match else None
            row_education_facilities = school_match.get("education_facilities") if school_match else None
            row_education_count = school_match.get("education_facility_count") if school_match else None
            region = regions.setdefault(
                code,
                {
                    "code": code,
                    "map_codes": set(),
                    "sido_name": sido_name_for_code(row.get("CGG_CD", "")),
                    "gu_code": row.get("CGG_CD", "").strip(),
                    "gu_name": row.get("CGG_NM", "").strip(),
                    "dong_code": row.get("STDG_CD", "").strip(),
                    "dong_name": row.get("STDG_NM", "").strip(),
                    "all": {"metrics": blank_metric_bucket(), "count": 0, "addresses": {}, "recent": []},
                    "years": {},
                },
            )
            region["map_codes"].update(map_codes)
            year_bucket = region["years"].setdefault(
                year,
                {"metrics": blank_metric_bucket(), "count": 0, "addresses": {}, "recent": []},
            )

            for bucket in (region["all"], year_bucket):
                bucket["count"] += 1
                update_metric_bucket(bucket["metrics"], metrics)

                addr = typed_address_key(row, metrics)
                addr_bucket = bucket["addresses"].setdefault(
                    addr,
                    {
                        "key": addr,
                        "address": addr,
                        "building_name": row.get("BLDG_NM", "").strip() or "(건물명 없음)",
                        "area_type": area_type_label(metrics),
                        "count": 0,
                        "households": row_households,
                        "built_year": row_built_year,
                        "elementary_500m": row_elementary_500m,
                        "nearest_elementary_name": row_nearest_school_name,
                        "nearest_elementary_m": row_nearest_school_distance,
                        "subway_lines": row_subway_lines,
                        "subway_station": row_subway_station,
                        "subway_distance_m": row_subway_distance,
                        "bus_stop_distance_m": row_bus_stop_distance,
                        "education_facilities": row_education_facilities,
                        "education_facility_count": row_education_count,
                        "metrics": blank_metric_bucket(),
                    },
                )
                addr_bucket["count"] += 1
                update_metric_bucket(addr_bucket["metrics"], metrics)
                if addr_bucket["households"] is None:
                    addr_bucket["households"] = row_households
                if addr_bucket["built_year"] is None:
                    addr_bucket["built_year"] = row_built_year
                if row_elementary_500m:
                    addr_bucket["elementary_500m"] = True
                if row_nearest_school_distance is not None and (
                    addr_bucket.get("nearest_elementary_m") is None
                    or row_nearest_school_distance < addr_bucket["nearest_elementary_m"]
                ):
                    addr_bucket["nearest_elementary_name"] = row_nearest_school_name
                    addr_bucket["nearest_elementary_m"] = row_nearest_school_distance
                if row_subway_lines and not addr_bucket.get("subway_lines"):
                    addr_bucket["subway_lines"] = row_subway_lines
                if row_subway_station and not addr_bucket.get("subway_station"):
                    addr_bucket["subway_station"] = row_subway_station
                if row_subway_distance is not None and (
                    addr_bucket.get("subway_distance_m") is None
                    or row_subway_distance < addr_bucket["subway_distance_m"]
                ):
                    addr_bucket["subway_distance_m"] = row_subway_distance
                if row_bus_stop_distance is not None and (
                    addr_bucket.get("bus_stop_distance_m") is None
                    or row_bus_stop_distance < addr_bucket["bus_stop_distance_m"]
                ):
                    addr_bucket["bus_stop_distance_m"] = row_bus_stop_distance
                if row_education_facilities and not addr_bucket.get("education_facilities"):
                    addr_bucket["education_facilities"] = row_education_facilities
                if row_education_count is not None and (
                    addr_bucket.get("education_facility_count") is None
                    or row_education_count > addr_bucket["education_facility_count"]
                ):
                    addr_bucket["education_facility_count"] = row_education_count

                bucket["recent"].append(
                    {
                        "contract_day": row.get("CTRT_DAY", ""),
                        "address": addr,
                        "building_name": row.get("BLDG_NM", "").strip() or "(건물명 없음)",
                        "area_type": area_type_label(metrics),
                        "floor": round_metric(parse_float(row.get("FLR")), 0),
                        "built_year": row_built_year,
                        "price_billion": round_metric(metrics.get("price_billion"), 2),
                        "area_pyeong": round_metric(metrics.get("area_pyeong"), 1),
                        "land_pyeong": round_metric(metrics.get("land_pyeong"), 1),
                        "price_per_pyeong": round_metric(metrics.get("price_per_pyeong"), 0),
                        "land_efficiency": round_metric(metrics.get("land_efficiency"), 2),
                    }
                )

    output_regions = []
    for region in regions.values():
        output_region = {
            "code": region["code"],
            "map_codes": sorted(region["map_codes"]),
            "sido_name": region["sido_name"],
            "gu_code": region["gu_code"],
            "gu_name": region["gu_name"],
            "dong_code": region["dong_code"],
            "dong_name": region["dong_name"],
            "all": finalize_bucket(region["all"], address_limit_per_region_year, recent_limit_per_region_year),
            "years": {},
        }
        for year, bucket in sorted(region["years"].items()):
            output_region["years"][year] = finalize_bucket(
                bucket,
                address_limit_per_region_year,
                recent_limit_per_region_year,
            )
        output_regions.append(output_region)

    output_regions.sort(key=lambda item: item["all"]["metrics"]["price_billion"]["avg"] or 0, reverse=True)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(
            {
                "generated_at": date.today().isoformat(),
                "source": str(input_path),
                "filters": {
                    "property_types": sorted(property_types),
                    "include_direct_trades": include_direct_trades,
                    "excluded_direct_rows": excluded_direct_rows,
                },
                "total_rows": total_rows,
                "used_rows": used_rows,
                "years": sorted(years_seen, reverse=True),
                "metrics": METRIC_KEYS,
                "region_key": "legal dong or CGG_CD + legal dong name",
                "map_key": "행정구역코드 from 법정동 연계정보",
                "regions": output_regions,
            },
            file,
            ensure_ascii=False,
            separators=(",", ":"),
        )


def finalize_bucket(
    bucket: dict[str, Any],
    address_limit: int,
    recent_limit: int,
) -> dict[str, Any]:
    fill_missing_built_years(bucket["addresses"])
    addresses = []
    for address in bucket["addresses"].values():
        addresses.append(
            {
                "key": address["key"],
                "building_name": address["building_name"],
                "area_type": address["area_type"],
                "count": address["count"],
                "households": address.get("households"),
                "built_year": address.get("built_year"),
                "elementary_500m": bool(address.get("elementary_500m")),
                "nearest_elementary_name": address.get("nearest_elementary_name"),
                "nearest_elementary_m": round_metric(address.get("nearest_elementary_m"), 0),
                "subway_lines": address.get("subway_lines") or [],
                "subway_station": address.get("subway_station"),
                "subway_distance_m": round_metric(address.get("subway_distance_m"), 0),
                "bus_stop_distance_m": round_metric(address.get("bus_stop_distance_m"), 0),
                "education_facilities": address.get("education_facilities"),
                "education_facility_count": address.get("education_facility_count"),
                "metrics": {key: summarize(address["metrics"][key]) for key in METRIC_KEYS},
            }
        )
    addresses.sort(key=lambda item: (item["count"], item["metrics"]["price_billion"]["avg"] or 0), reverse=True)

    recent = sorted(bucket["recent"], key=lambda item: item.get("contract_day") or "", reverse=True)
    return {
        "count": bucket["count"],
        "metrics": {key: summarize(bucket["metrics"][key]) for key in METRIC_KEYS},
        "addresses": addresses[:address_limit],
        "recent": recent[:recent_limit],
    }


def fill_missing_built_years(addresses: dict[str, Any]) -> None:
    year_by_building: dict[str, int] = {}
    school_by_building: dict[str, dict[str, Any]] = {}
    subway_by_building: dict[str, dict[str, Any]] = {}
    bus_by_building: dict[str, float] = {}
    education_by_building: dict[str, dict[str, Any]] = {}
    for address in addresses.values():
        building_name = address.get("building_name", "")
        built_year = address.get("built_year")
        if building_name and built_year:
            year_by_building.setdefault(building_name, built_year)
        if building_name and address.get("nearest_elementary_m") is not None:
            current = school_by_building.get(building_name)
            if current is None or address["nearest_elementary_m"] < current["nearest_elementary_m"]:
                school_by_building[building_name] = {
                    "nearest_elementary_name": address.get("nearest_elementary_name"),
                    "nearest_elementary_m": address.get("nearest_elementary_m"),
                    "elementary_500m": bool(address.get("elementary_500m")),
                }
        if building_name and address.get("subway_distance_m") is not None:
            current_subway = subway_by_building.get(building_name)
            if current_subway is None or address["subway_distance_m"] < current_subway["subway_distance_m"]:
                subway_by_building[building_name] = {
                    "subway_lines": address.get("subway_lines") or [],
                    "subway_station": address.get("subway_station"),
                    "subway_distance_m": address.get("subway_distance_m"),
                }
        if building_name and address.get("bus_stop_distance_m") is not None:
            current_bus = bus_by_building.get(building_name)
            if current_bus is None or address["bus_stop_distance_m"] < current_bus:
                bus_by_building[building_name] = address["bus_stop_distance_m"]
        if building_name and address.get("education_facility_count") is not None:
            current_education = education_by_building.get(building_name)
            if current_education is None or address["education_facility_count"] > current_education["education_facility_count"]:
                education_by_building[building_name] = {
                    "education_facilities": address.get("education_facilities"),
                    "education_facility_count": address.get("education_facility_count"),
                }

    for address in addresses.values():
        if address.get("built_year") is None:
            address["built_year"] = year_by_building.get(address.get("building_name", ""))
        school_info = school_by_building.get(address.get("building_name", ""))
        if school_info and address.get("nearest_elementary_m") is None:
            address.update(school_info)
        subway_info = subway_by_building.get(address.get("building_name", ""))
        if subway_info and address.get("subway_distance_m") is None:
            address.update(subway_info)
        bus_distance = bus_by_building.get(address.get("building_name", ""))
        if bus_distance is not None and address.get("bus_stop_distance_m") is None:
            address["bus_stop_distance_m"] = bus_distance
        education_info = education_by_building.get(address.get("building_name", ""))
        if education_info and address.get("education_facility_count") is None:
            address.update(education_info)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="서울 부동산 CSV를 지도 대시보드용 JSON으로 변환합니다.")
    parser.add_argument("--input", default="data/capital_area_apt_trade_transactions.csv")
    parser.add_argument("--output", default="web/data/seoul_real_estate_summary.json")
    parser.add_argument("--apt-detail", default=str(APT_DETAIL_PATH), help="중간 수집된 K-APT 역세권 상세 CSV 경로")
    parser.add_argument("--property-types", nargs="*", default=sorted(DEFAULT_PROPERTY_TYPES))
    parser.add_argument("--address-limit-per-region-year", type=int, default=70)
    parser.add_argument("--recent-limit-per-region-year", type=int, default=0)
    parser.add_argument(
        "--include-direct-trades",
        action="store_true",
        help="직거래도 포함합니다. 기본값은 직거래 제외입니다.",
    )
    return parser.parse_args()


def main() -> None:
    global APT_DETAIL_PATH
    args = parse_args()
    APT_DETAIL_PATH = Path(args.apt_detail)
    build_dashboard_data(
        Path(args.input),
        Path(args.output),
        set(args.property_types),
        args.address_limit_per_region_year,
        args.recent_limit_per_region_year,
        args.include_direct_trades,
    )


if __name__ == "__main__":
    main()
