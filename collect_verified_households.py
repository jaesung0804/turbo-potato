"""Preserve Seoul's public complex household register, without guessing joins.

The public Sheet JSON download needs no API key. This collector validates the
provider's whole-complex fields; it does NOT verify a match to transaction keys.
Current observations must not be backfilled into historical model features.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import hashlib
import json
from pathlib import Path
import urllib.parse
import urllib.request

DATASET_URL = "https://data.seoul.go.kr/dataList/OA-15818/S/1/datasetView.do"
SCHEMA_URL = "https://data.seoul.go.kr/dataList/sheetView.do?infId=OA-15818&srvType=S"
DOWNLOAD_URL = "https://datafile.seoul.go.kr/bigfile/iot/sheet/json/download.do"
# Public download form fields, observed in SCHEMA_URL on 2026-09-08 UTC.
DOWNLOAD_FORM = {
    "srvType": "S", "infId": "OA-15818", "serviceKind": "0",
    "pageNo": "1", "gridTotalCnt": "", "ssUserId": "SAMPLE_VIEW",
    "strWhere": "", "strOrderby": "SN ASC", "filterCol": "", "txtFilter": "",
}


def positive_integer(value):
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
        return int(number) if number > 0 and number.is_integer() else None
    except (ValueError, TypeError, OverflowError):
        return None


def timestamp(value):
    """The downloaded register represents source dates as Unix milliseconds."""
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return dt.datetime.fromtimestamp(value / 1000, dt.timezone.utc).isoformat()
    except (ValueError, OverflowError, OSError):
        return None


def normalize(raw: bytes, observed_at: str):
    observation = dt.datetime.fromisoformat(observed_at)
    if observation.tzinfo is None:
        raise ValueError("observed_at must include a timezone")
    source = json.loads(raw)
    fields = source.get("DESCRIPTION", {})
    if fields.get("TNOHSH") != "k-전체세대수" or fields.get("WHOL_DONG_CNT") != "k-전체동수":
        raise ValueError("Provider whole-complex field definitions changed")
    rows = source.get("DATA")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Expected a nonempty public DATA register")
    records = []
    seen = set()
    for row in rows:
        code = row.get("apt_cd")
        if not isinstance(code, str) or not code.strip() or code in seen:
            raise ValueError("Missing or duplicate official apartment code")
        seen.add(code)
        households = positive_integer(row.get("tnohsh"))
        buildings = positive_integer(row.get("whol_dong_cnt"))
        road_address = row.get("apt_rdn_addr") or None
        modified_at = timestamp(row.get("mdfcn_ymd"))
        records.append({
            "official_complex_id": code,
            "official_complex_name": row.get("apt_nm"),
            "complex_type": row.get("cmpx_clsf"),
            "whole_complex_households": households,
            "whole_complex_buildings": buildings,
            "road_address": road_address,
            "province": row.get("ctpv_addr"),
            "district": row.get("sgg_addr"),
            "neighborhood": row.get("emd_addr"),
            "unverified_other_address": row.get("daddr"),
            "source_modified_at": modified_at,
            "source_modified_raw_ms": row.get("mdfcn_ymd"),
            "source_registered_at": timestamp(row.get("reg_ymd")),
            "use_approved_at": timestamp(row.get("use_aprv_ymd")),
            "observed_at": observed_at,
            "effective_from": None,
            "source_scope": "official_whole_complex",
            "denominator_fields_complete": bool(households and buildings and road_address and modified_at),
            "transaction_scope_verified": False,
            "parcel_ids": [],
            "building_management_ids": [],
            "use_for_turnover": False,
            "use_for_historical_model": False,
        })
    by_id = {r["official_complex_id"]: r for r in records}
    approved_matches = []
    # A reviewed crosswalk backed by two official sources, never name matching.
    # The groundwater register proves the address identity, not full parcel scope.
    mapo = by_id.get("A10025003")
    if mapo and mapo["road_address"] == "서울특별시 마포구 대흥로 175" and mapo["whole_complex_households"]:
        approved_matches.append({
            "official_complex_id": "A10025003",
            "transaction_region_code": "11440",
            "transaction_neighborhood": "대흥동",
            "transaction_lot_number": "806",
            "transaction_complex_name": "마포그랑자이",
            "transaction_complex_key": "마포구 대흥동 806 마포그랑자이",
            "road_address": mapo["road_address"],
            "match_scope": "current_identity_display_only",
            "approved_for_household_display": True,
            "approved_for_turnover": False,
            "approved_for_historical_model": False,
            "identity_evidence_url": "https://swo.seoul.go.kr/ugrwtr/retrieveUgrwtrFcltyList.do?pageIndex=340",
            "identity_evidence_record_ids": ["1202000001", "1202000002", "1202000003", "1202000004"],
            "identity_evidence": "서울시 지하수시설 원장에 대흥동 806번지 마포그랑자이와 대흥로 175가 함께 기재됨",
            "household_evidence_url": "https://openapt.seoul.go.kr/openApt/index.do?aptCode=A10025003",
            "reviewed_at": "2026-09-08T22:58:53+00:00",
            "full_parcel_membership_verified": False,
        })
    return {
        "schema_version": 1,
        "provider": "서울특별시 공동주택과 / 서울 열린데이터광장",
        "dataset_id": "OA-15818",
        "dataset_url": DATASET_URL,
        "schema_url": SCHEMA_URL,
        "download_url": DOWNLOAD_URL,
        "download_method": "POST",
        "download_form": DOWNLOAD_FORM,
        "license": "공공누리 제1유형: 출처표시",
        "observed_at": observed_at,
        "raw_sha256": hashlib.sha256(raw).hexdigest(),
        "raw_bytes": len(raw),
        "record_count": len(records),
        "positive_household_count": sum(r["whole_complex_households"] is not None for r in records),
        "denominator_fields_complete_count": sum(r["denominator_fields_complete"] for r in records),
        "transaction_scope_verified_count": 0,
        "approved_matches": approved_matches,
        "limitations": [
            "The provider omits inaccurate kapt legal-address data; free-text other addresses are not parcel identifiers.",
            "A current source modification timestamp is not proof of a historic effective date or publication date.",
            "The register lacks the full parcel/building membership required for matching whole-complex transaction volume.",
            "Official complex household counts must not be joined using names alone or treated as verified turnover denominators.",
        ],
        "records": sorted(records, key=lambda r: r["official_complex_id"]),
    }


def write_gzip(path: Path, payload: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(gzip.compress(payload, mtime=0))


def read_json(path: Path):
    raw = path.read_bytes()
    return json.loads(gzip.decompress(raw) if path.suffix == ".gz" else raw)


def apply_verified_households(
    summary,
    register_path=Path("metadata/verified_households_seoul_20260908.json.gz"),
    matches_path=None,
):
    """Attach reviewed current display identities to all/year summary buckets.

    No match is inferred. Exact gu code, neighborhood, complex key, building name,
    official ID and reviewed road address must agree. Historical price features
    and whole-complex turnover remain disabled even in historical display buckets.
    """
    register_path = Path(register_path)
    if not register_path.exists():
        return {"matched_types": 0, "matched_bucket_rows": 0, "status": "source_missing"}
    register = read_json(register_path)
    if register.get("schema_version") != 1:
        raise ValueError("Unsupported household register schema")
    records = register.get("records", [])
    by_id = {r["official_complex_id"]: r for r in records}
    if len(by_id) != len(records):
        raise ValueError("Duplicate official complex IDs in household register")
    matches = read_json(Path(matches_path)) if matches_path else register.get("approved_matches", [])
    if isinstance(matches, dict):
        matches = matches.get("approved_matches", [])
    approved = {}
    for match in matches:
        if (not match.get("approved_for_household_display")
                or match.get("match_scope") != "current_identity_display_only"
                or not match.get("identity_evidence_url")
                or not match.get("household_evidence_url")):
            continue
        row = by_id.get(match.get("official_complex_id"))
        if (not row or not row.get("whole_complex_households")
                or row.get("source_scope") != "official_whole_complex"
                or row.get("road_address") != match.get("road_address")):
            continue
        key = (match.get("transaction_region_code"), match.get("transaction_neighborhood"),
               match.get("transaction_complex_key"), match.get("transaction_complex_name"))
        if not all(key):
            continue
        if key in approved:
            raise ValueError("Ambiguous reviewed household address match")
        approved[key] = (row, match)
    all_types = set()
    bucket_rows = 0
    for region in summary["regions"]:
        for bucket in [region["all"], *region["years"].values()]:
            for building in bucket["addresses"]:
                key = (str(region["gu_code"]), region["dong_name"],
                       building.get("complex_key"), building.get("building_name"))
                if key not in approved:
                    continue
                row, match = approved[key]
                building.setdefault("legacy_households", building.get("households"))
                building.update({
                    "households": row["whole_complex_households"],
                    "households_verified": True,
                    "household_scope": "official_whole_complex_address_verified",
                    "household_use_for_model": False,
                    "household_use_for_turnover": False,
                    "household_observed_at": row["observed_at"],
                    "household_source_modified_at": row.get("source_modified_at"),
                    "household_source_url": match["household_evidence_url"],
                    "household_identity_evidence_url": match["identity_evidence_url"],
                    "household_is_current_observation": True,
                    "official_complex_id": row["official_complex_id"],
                })
                all_types.add((region["code"], building["key"]))
                bucket_rows += 1
    return {"matched_types": len(all_types), "matched_bucket_rows": bucket_rows,
            "status": "available", "observed_at": register["observed_at"],
            "household_use_for_model": False, "household_use_for_turnover": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Previously downloaded official JSON, for deterministic normalization")
    parser.add_argument("--observed-at", help="Required for --input; actual timezone-aware first retrieval timestamp")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-output", type=Path, required=True, help="Exact official response preserved as gzip")
    args = parser.parse_args()
    if args.input:
        if not args.observed_at:
            parser.error("--input requires --observed-at")
        raw = args.input.read_bytes()
        observed_at = args.observed_at
    else:
        request = urllib.request.Request(DOWNLOAD_URL, data=urllib.parse.urlencode(DOWNLOAD_FORM).encode())
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read()
        observed_at = dt.datetime.now(dt.timezone.utc).isoformat()
    document = normalize(raw, observed_at)
    write_gzip(args.raw_output, raw)
    write_gzip(args.output, json.dumps(document, ensure_ascii=False, separators=(",", ":")).encode())
    print(json.dumps({k: document[k] for k in (
        "record_count", "positive_household_count", "denominator_fields_complete_count",
        "transaction_scope_verified_count", "observed_at", "raw_sha256",
    )}, ensure_ascii=False))


if __name__ == "__main__":
    main()
