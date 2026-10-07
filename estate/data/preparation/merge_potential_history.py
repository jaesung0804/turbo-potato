"""Verify and concatenate the two durable, disjoint historical sales snapshots."""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path


def digest(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(block)
    return value.hexdigest()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--older-dir", default=".work/older-history-state")
    p.add_argument("--history-dir", default=".work/history-state")
    p.add_argument("--output", default=".work/history/transactions_2006_2020.csv")
    p.add_argument("--provenance", default="reports/estate_potential_five_year_sources.json")
    args = p.parse_args()
    sources = [(Path(args.older_dir), "research-older-history"),
               (Path(args.history_dir), "research-history")]
    inputs = [(directory / f"{name}.csv.gz", json.loads((directory / f"{name}.json").read_text()))
              for directory, name in sources]
    if inputs[0][1]["end"] >= inputs[1][1]["start"]:
        raise ValueError("Historical source contract periods overlap")
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(".tmp")
    provenance, header = [], None
    try:
        with temporary.open("wb") as destination:
            for path, metadata in inputs:
                compressed_hash = digest(path)
                if metadata.get("gzip_sha256", compressed_hash) != compressed_hash:
                    raise ValueError(f"Compressed source hash mismatch: {path.name}")
                uncompressed = hashlib.sha256()
                with gzip.open(path, "rb") as source:
                    first = source.readline()
                    uncompressed.update(first)
                    if header is None:
                        header = first
                        destination.write(first)
                    elif first != header:
                        raise ValueError("Historical CSV headers do not match")
                    for block in iter(lambda: source.read(1024 * 1024), b""):
                        uncompressed.update(block)
                        destination.write(block)
                if uncompressed.hexdigest() != metadata["sha256"]:
                    raise ValueError(f"Uncompressed source hash mismatch: {path.name}")
                provenance.append({"path": str(path), "raw_sha256": uncompressed.hexdigest(),
                                   "gzip_sha256": compressed_hash, "rows": metadata["rows"],
                                   "start": metadata["start"], "end": metadata["end"]})
        temporary.replace(output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    report = {"sources": provenance, "sha256": digest(output),
              "raw_rows": sum(item["rows"] for item in provenance), "path": str(output)}
    Path(args.provenance).write_text(json.dumps(report, ensure_ascii=False, indent=2))
    print(json.dumps(report))


if __name__ == "__main__":
    main()
