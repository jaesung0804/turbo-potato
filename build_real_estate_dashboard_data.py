from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path
from statistics import mean, median
from typing import Any


SQM_PER_PYEONG = 3.3058
DEFAULT_PROPERTY_TYPES = {"아파트"}
METRIC_KEYS = [
    "price_billion",
    "area_pyeong",
    "land_pyeong",
    "price_per_pyeong",
    "land_ratio",
    "land_efficiency",
]


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


def legal_dong_code(row: dict[str, str]) -> str:
    gu_code = row.get("CGG_CD", "").strip()
    dong_code = row.get("STDG_CD", "").strip()
    return f"{gu_code}{dong_code[:3]}"


def address_key(row: dict[str, str]) -> str:
    gu = row.get("CGG_NM", "").strip()
    dong = row.get("STDG_NM", "").strip()
    main_no = row.get("MNO", "").strip().lstrip("0") or "0"
    sub_no = row.get("SNO", "").strip().lstrip("0")
    lot = main_no if not sub_no or sub_no == "0" else f"{main_no}-{sub_no}"
    building = row.get("BLDG_NM", "").strip()
    return f"{gu} {dong} {lot} {building}".strip()


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
) -> None:
    regions: dict[str, dict[str, Any]] = {}
    years_seen: set[str] = set()
    total_rows = 0
    used_rows = 0

    with input_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)
        for row in reader:
            total_rows += 1
            if row.get("RTRCN_DAY", "").strip():
                continue
            if property_types and row.get("BLDG_USG", "").strip() not in property_types:
                continue

            metrics = calculate_metrics(row)
            code = legal_dong_code(row)
            year = contract_year(row)
            if not metrics or not code or not year:
                continue

            used_rows += 1
            years_seen.add(year)
            region = regions.setdefault(
                code,
                {
                    "code": code,
                    "gu_code": row.get("CGG_CD", ""),
                    "gu_name": row.get("CGG_NM", ""),
                    "dong_code": row.get("STDG_CD", ""),
                    "dong_name": row.get("STDG_NM", ""),
                    "all": {"metrics": blank_metric_bucket(), "count": 0, "addresses": {}, "recent": []},
                    "years": {},
                },
            )
            year_bucket = region["years"].setdefault(
                year,
                {"metrics": blank_metric_bucket(), "count": 0, "addresses": {}, "recent": []},
            )

            for bucket in (region["all"], year_bucket):
                bucket["count"] += 1
                update_metric_bucket(bucket["metrics"], metrics)

                addr = address_key(row)
                addr_bucket = bucket["addresses"].setdefault(
                    addr,
                    {
                        "address": addr,
                        "building_name": row.get("BLDG_NM", "").strip(),
                        "count": 0,
                        "metrics": blank_metric_bucket(),
                    },
                )
                addr_bucket["count"] += 1
                update_metric_bucket(addr_bucket["metrics"], metrics)

                bucket["recent"].append(
                    {
                        "contract_day": row.get("CTRT_DAY", ""),
                        "address": addr,
                        "building_name": row.get("BLDG_NM", "").strip(),
                        "floor": round_metric(parse_float(row.get("FLR")), 0),
                        "built_year": parse_int(row.get("ARCH_YR")),
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
                "filters": {"property_types": sorted(property_types)},
                "total_rows": total_rows,
                "used_rows": used_rows,
                "years": sorted(years_seen, reverse=True),
                "metrics": METRIC_KEYS,
                "region_key": "CGG_CD + first 3 digits of STDG_CD",
                "regions": output_regions,
            },
            file,
            ensure_ascii=False,
        )


def finalize_bucket(
    bucket: dict[str, Any],
    address_limit: int,
    recent_limit: int,
) -> dict[str, Any]:
    addresses = []
    for address in bucket["addresses"].values():
        addresses.append(
            {
                "address": address["address"],
                "building_name": address["building_name"],
                "count": address["count"],
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="실거래가 CSV를 지도 웹앱용 JSON으로 변환합니다.")
    parser.add_argument("--input", default="data/seoul_real_estate_transactions.csv")
    parser.add_argument("--output", default="web/data/seoul_real_estate_summary.json")
    parser.add_argument("--property-types", nargs="*", default=sorted(DEFAULT_PROPERTY_TYPES))
    parser.add_argument("--address-limit-per-region-year", type=int, default=120)
    parser.add_argument("--recent-limit-per-region-year", type=int, default=80)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    build_dashboard_data(
        Path(args.input),
        Path(args.output),
        set(args.property_types),
        args.address_limit_per_region_year,
        args.recent_limit_per_region_year,
    )


if __name__ == "__main__":
    main()
