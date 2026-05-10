# Map Build Run:
#   cd C:\code
#   python convert_bnd_adm_dong_map.py
#
# This converts data\BND_ADM_DONG_PG\BND_ADM_DONG_PG.shp into a lightweight
# Seoul/Gyeonggi/Incheon 시군구 GeoJSON at web\data\capital_area_adm_sgg.geojson.

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

import shapefile
from pyproj import CRS, Transformer


# 이 파일의 의도:
# - 행정동 경계 SHP를 웹 지도에서 읽기 쉬운 GeoJSON으로 변환합니다.
# - 수도권 행정동/시군구만 남겨 지도 로딩량을 줄입니다.
# - 대시보드의 실거래 요약 JSON과 행정동 코드로 매칭되는 지도 레이어를 만듭니다.
DEFAULT_INPUT = "data/BND_ADM_DONG_PG/BND_ADM_DONG_PG.shp"
DEFAULT_OUTPUT = "web/data/capital_area_adm_sgg.geojson"
CAPITAL_AREA_PREFIXES = {"11": "서울특별시", "23": "인천광역시", "31": "경기도"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="행정동 Shapefile을 수도권 시군구 GeoJSON으로 변환합니다.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help="BND_ADM_DONG_PG.shp 경로")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help="저장할 GeoJSON 경로")
    parser.add_argument("--encoding", default="cp949", help="DBF 인코딩")
    parser.add_argument("--keep-dong", action="store_true", help="시군구 묶음 대신 행정동 단위로 저장합니다.")
    return parser.parse_args()


def shape_parts_to_rings(shape: shapefile.Shape, transformer: Transformer) -> list[list[list[float]]]:
    points = shape.points
    part_starts = list(shape.parts) + [len(points)]
    rings = []
    for start, end in zip(part_starts, part_starts[1:]):
        ring = []
        for x, y in points[start:end]:
            lon, lat = transformer.transform(x, y)
            ring.append([round(lon, 6), round(lat, 6)])
        if ring and ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) >= 4:
            rings.append(ring)
    return rings


def feature_for_dong(record: dict[str, Any], rings: list[list[list[float]]]) -> dict[str, Any]:
    return {
        "type": "Feature",
        "properties": {
            "ADM_CD": record["ADM_CD"],
            "ADM_NM": record["ADM_NM"],
            "ADM_SGG_CD": record["ADM_CD"][:5],
            "SIDO_NM": CAPITAL_AREA_PREFIXES.get(record["ADM_CD"][:2], ""),
            "BASE_DATE": record["BASE_DATE"],
        },
        "geometry": {"type": "MultiPolygon", "coordinates": [[ring] for ring in rings]},
    }


def feature_for_sgg(stat_sgg_code: str, records: list[tuple[dict[str, Any], list[list[list[float]]]]]) -> dict[str, Any]:
    first = records[0][0]
    coordinates = []
    for _, rings in records:
        coordinates.extend([[ring] for ring in rings])
    return {
        "type": "Feature",
        "properties": {
            "ADM_SGG_CD": stat_sgg_code,
            "SIDO_NM": CAPITAL_AREA_PREFIXES.get(stat_sgg_code[:2], ""),
            "BASE_DATE": first["BASE_DATE"],
            "DONG_COUNT": len(records),
        },
        "geometry": {"type": "MultiPolygon", "coordinates": coordinates},
    }


def convert(input_path: Path, output_path: Path, encoding: str, keep_dong: bool) -> None:
    source_crs = CRS.from_wkt(input_path.with_suffix(".prj").read_text(encoding="utf-8"))
    transformer = Transformer.from_crs(source_crs, CRS.from_epsg(4326), always_xy=True)
    reader = shapefile.Reader(str(input_path), encoding=encoding)

    dong_features = []
    grouped: dict[str, list[tuple[dict[str, Any], list[list[list[float]]]]]] = defaultdict(list)
    for shape_record in reader.iterShapeRecords():
        record = shape_record.record.as_dict()
        sido_prefix = record["ADM_CD"][:2]
        if sido_prefix not in CAPITAL_AREA_PREFIXES:
            continue
        rings = shape_parts_to_rings(shape_record.shape, transformer)
        if not rings:
            continue
        if keep_dong:
            dong_features.append(feature_for_dong(record, rings))
        else:
            grouped[record["ADM_CD"][:5]].append((record, rings))

    features = dong_features if keep_dong else [feature_for_sgg(code, records) for code, records in sorted(grouped.items())]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(
        json.dumps({"type": "FeatureCollection", "features": features}, ensure_ascii=False, separators=(",", ":")),
        encoding="utf-8",
    )
    print(f"saved={output_path.resolve()} features={len(features):,}")


def main() -> None:
    args = parse_args()
    convert(Path(args.input), Path(args.output), args.encoding, args.keep_dong)


if __name__ == "__main__":
    main()
