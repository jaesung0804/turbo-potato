"""Bounded historical research export; never replaces the live sales dataset."""
from pathlib import Path
import gzip
import json
import collect_estate_transactions as collector


def main():
    collector.REGIONS = {code: region for code, region in collector.REGIONS.items()
                         if code.startswith('11') or code == '41210'}
    out = Path('.work/history')
    out.mkdir(parents=True, exist_ok=True)
    manifest = collector.collect(collector.existing_key(), '201601', '202012',
        out / 'cache', out / 'transactions.csv', refresh_months=0, workers=4)
    (out / 'research-history.csv.gz').write_bytes(
        gzip.compress((out / 'transactions.csv').read_bytes(), mtime=0))
    (out / 'research-history.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
