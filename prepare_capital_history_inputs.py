"""Restore and verify the existing inputs reused by the capital CSV expansion."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
from pathlib import Path
import subprocess

from get_molit_apt_trade_data import DASHBOARD_FIELDNAMES
from raw_estate_state import restore


def sha(path, compressed=False):
    digest = hashlib.sha256()
    opener = gzip.open if compressed else open
    with opener(path, 'rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def prepare(older_state, history_state, raw_state, output):
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Choose an empty research input directory')
    output.mkdir(parents=True, exist_ok=True)
    restored = restore(Path(raw_state), output)
    source_specs = [
        (Path(older_state), 'research-older-history', '200601', '201512'),
        (Path(history_state), 'research-history', '201601', '202012'),
    ]
    sources, total = [], 0
    combined = output / 'transactions_2006_2020.csv'
    with combined.open('w', encoding='utf-8-sig', newline='') as target:
        writer = csv.DictWriter(target, fieldnames=DASHBOARD_FIELDNAMES)
        writer.writeheader()
        for state, name, start, end in source_specs:
            manifest = json.loads((state / (name + '.json')).read_text())
            path = state / (name + '.csv.gz')
            if (manifest.get('complete') is not True or manifest.get('normalizer_version') != 2
                    or (manifest['start'], manifest['end']) != (start, end)
                    or sha(path, compressed=True) != manifest['sha256']):
                raise ValueError(f'Historical source failed integrity or coverage verification: {name}')
            count = 0
            with gzip.open(path, 'rt', encoding='utf-8-sig', newline='') as stream:
                reader = csv.DictReader(stream)
                if reader.fieldnames != DASHBOARD_FIELDNAMES:
                    raise ValueError('Historical source schema changed')
                for row in reader:
                    if not start <= row['CTRT_DAY'][:6] <= end:
                        raise ValueError('Historical row is outside its source period')
                    writer.writerow(row)
                    count += 1
            if count != manifest['rows']:
                raise ValueError('Historical row count differs from its verified manifest')
            total += count
            sources.append({'name': name, 'source_commit': subprocess.check_output(
                ['git', '-C', str(state), 'rev-parse', 'HEAD'], text=True).strip(),
                'rows': count, 'start': start, 'end': end,
                'raw_sha256': manifest['sha256'], 'gzip_sha256': sha(path),
                'fetched_at': manifest['fetched_at']})
    history_manifest = {'complete': True, 'rows': total, 'sha256': sha(combined),
                        'start': '200601', 'end': '202012', 'sources': sources,
                        'scope': 'Seoul and Gwangmyeong; downstream ownership keeps Seoul only',
                        'historical_publication_vintages': False}
    combined.with_suffix('.manifest.json').write_text(json.dumps(history_manifest, indent=2) + '\n')
    result = {'history': history_manifest, 'recent': restored['collection'],
              'recent_source_commit': subprocess.check_output(
                  ['git', '-C', str(raw_state), 'rev-parse', 'HEAD'], text=True).strip()}
    (output / 'inputs_manifest.json').write_text(json.dumps(result, indent=2) + '\n')
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('older-state', 'history-state', 'raw-state', 'output'):
        parser.add_argument('--' + name, type=Path, required=True)
    args = parser.parse_args()
    result = prepare(args.older_state, args.history_state, args.raw_state, args.output)
    print(json.dumps({'verified_historical_rows': result['history']['rows'],
                      'verified_recent_rows': result['recent']['rows']}))
