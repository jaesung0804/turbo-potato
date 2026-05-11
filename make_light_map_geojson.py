from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def round_coords(value: Any, digits: int) -> Any:
    if isinstance(value, list):
        if len(value) == 2 and all(isinstance(item, (int, float)) for item in value):
            return [round(value[0], digits), round(value[1], digits)]
        return [round_coords(item, digits) for item in value]
    return value


def main() -> None:
    parser = argparse.ArgumentParser(description="Make a smaller GeoJSON for browser rendering.")
    parser.add_argument("--input", default="web/data/capital_area_adm_dong.geojson")
    parser.add_argument("--output", default="web/data/capital_area_adm_dong_light.geojson")
    parser.add_argument("--digits", type=int, default=5)
    args = parser.parse_args()

    data = json.load(open(args.input, encoding="utf-8"))
    features = []
    for feature in data.get("features", []):
        props = feature.get("properties", {})
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "ADM_CD": props.get("ADM_CD"),
                    "ADM_SGG_CD": props.get("ADM_SGG_CD"),
                },
                "geometry": {
                    **feature.get("geometry", {}),
                    "coordinates": round_coords(feature.get("geometry", {}).get("coordinates"), args.digits),
                },
            }
        )

    output = {"type": "FeatureCollection", "features": features}
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as file:
        json.dump(output, file, ensure_ascii=False, separators=(",", ":"))
    print(f"Wrote {output_path}")


if __name__ == "__main__":
    main()
