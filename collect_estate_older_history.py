"""Complete 2006–2015 research sales; never replace live or 2016+ inputs."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil

import collect_estate_transactions as collector


def sha256_file(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            result.update(block)
    return result.hexdigest()


def collect_history(start: str, end: str, out: Path) -> dict:
    if not ('200601' <= start <= end <= '201512'):
        raise ValueError('Research contract-month range must stay within 2006–2015')
    months = collector.month_range(start, end)
    original_regions = collector.REGIONS
    regions = {code: region for code, region in original_regions.items()
               if code.startswith('11') or code == '41210'}
    if len(regions) != 26 or sum(code.startswith('11') for code in regions) != 25:
        raise RuntimeError('Expected the stable Seoul 25 districts and Gwangmyeong')
    out.mkdir(parents=True, exist_ok=True)
    key = collector.existing_key()
    if key and os.getenv('GITHUB_ACTIONS'):
        print('::add-mask::' + key, flush=True)
    collector.REGIONS = regions
    try:
        # Two complete, disjoint shards avoid the collector's unrelated audit
        # of reorganized Incheon/Bucheon/Hwaseong codes. Our 26 codes are fixed.
        # Shards run sequentially: at most two simultaneous API workers.
        shards = [collector.collect(key, start, end, out / 'cache',
                  out / f'shard-{index}.csv', refresh_months=0, workers=2,
                  shard_index=index, shard_count=2) for index in range(2)]
    finally:
        collector.REGIONS = original_regions
    expected = len(months) * len(regions)
    if (not all(item['shard_complete'] for item in shards)
            or sum(item['partition_count'] for item in shards) != expected):
        raise RuntimeError('Incomplete older-history shard coverage')

    output = out / 'research-older-history.csv'
    temporary = output.with_suffix('.csv.tmp')
    partitions, total = [], 0
    with temporary.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=collector.DASHBOARD_FIELDNAMES)
        writer.writeheader()
        for month in months:
            for code in sorted(regions):
                data = collector.read_partition(out / 'cache' / f'{month}-{code}.json.gz', month, code)
                writer.writerows(data['rows'])
                total += data['count']
                partitions.append({key: data[key] for key in
                                   ('month', 'code', 'count', 'rows_sha256', 'fetched_at')})
    if not total or total != sum(item['rows'] for item in shards):
        temporary.unlink(missing_ok=True)
        raise RuntimeError('Older-history row count does not match complete shards')
    temporary.replace(output)
    compressed = output.with_suffix('.csv.gz')
    compressed_temporary = compressed.with_suffix('.gz.tmp')
    with output.open('rb') as source, compressed_temporary.open('wb') as target:
        with gzip.GzipFile(filename='', mode='wb', fileobj=target, mtime=0) as zipped:
            shutil.copyfileobj(source, zipped)
    compressed_temporary.replace(compressed)
    manifest = {
        'schema_version': 1, 'normalizer_version': collector.NORMALIZER_VERSION,
        'complete': True, 'start': start, 'end': end, 'rows': total,
        'partition_count': expected, 'region_count': len(regions),
        'region_codes': sorted(regions), 'sha256': sha256_file(output),
        'gzip_sha256': sha256_file(compressed), 'fetched_at': collector.utc_now(),
        'source': collector.URL, 'historical_publication_vintages': False,
        'note': 'All pages and cancellations retained; fetched current historical records, not archived publication-time records. Separate from live sales and 2016–2020 history.',
        'partitions': partitions,
    }
    metadata = out / 'research-older-history.json'
    metadata.with_suffix('.json.tmp').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    metadata.with_suffix('.json.tmp').replace(metadata)
    print(json.dumps({key: value for key, value in manifest.items() if key != 'partitions'}, ensure_ascii=False), flush=True)
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', default='200601')
    parser.add_argument('--end', default='201512')
    parser.add_argument('--out', type=Path, default=Path('.work/five-year-history'))
    args = parser.parse_args()
    collect_history(args.start, args.end, args.out)
