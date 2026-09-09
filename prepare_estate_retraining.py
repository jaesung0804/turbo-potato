"""Prepare one immutable, source-owned sales dataset for the 2026-09 audit."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path
import shutil
import subprocess

from get_molit_apt_trade_data import DASHBOARD_FIELDNAMES
from normalize_molit_capital_history import normalize_sources, file_sha256, CompleteWriter


def prepare(collection, existing, output, uploads=None):
    collection, existing, output = map(Path, (collection, existing, output))
    if output.exists() and any(output.iterdir()):
        raise FileExistsError('Choose an empty retraining preparation directory')
    output.mkdir(parents=True, exist_ok=True)
    original_path = collection / 'manifest.json'
    original = json.loads(original_path.read_text())
    expected = {f'{region}/sale/{year}/{year}-01-01_{year}-12-31'
                for region in ('gyeonggi', 'incheon') for year in range(2006, 2021)}
    entries = {key: original['entries'][key] for key in sorted(expected)}
    if any(e.get('status') != 'complete' for e in entries.values()):
        raise ValueError('A required sales year has not been completed')
    stage = output / 'sources'
    stage.mkdir()
    for entry in entries.values():
        src = (collection / entry['file']).resolve()
        if not src.is_relative_to(collection.resolve()):
            raise ValueError('Source path escapes collection')
        if file_sha256(src) != entry['gzip_sha256']:
            raise ValueError('Archived source checksum mismatch')
        dest = stage / entry['file']
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
    source_commit = subprocess.check_output(
        ['git', '-C', str(collection), 'rev-parse', 'HEAD'], text=True).strip()
    subset = {'status': 'complete', 'complete_scope': 'Gyeonggi and Incheon sales, 2006-2020 only',
              'original_collection_status': original['status'],
              'original_collection_sha256': file_sha256(original_path),
              'source_commit': source_commit, 'entries': entries}
    subset_path = stage / 'manifest.json'
    subset_path.write_text(json.dumps(subset, ensure_ascii=False, indent=2) + '\n')
    matches = []
    if uploads:
        for path in sorted(Path(uploads).glob('아파트(매매)_실거래가_20260909*.csv')):
            digest = file_sha256(path)
            keys = [key for key, entry in entries.items() if entry['raw_sha256'] == digest]
            if len(keys) != 1:
                raise ValueError('Uploaded source does not match exactly one archived year')
            matches.append({'filename': path.name, 'sha256': digest, 'partition': keys[0]})
    manifest = normalize_sources([subset_path], output / 'normalized', as_of='2026-09-09',
        existing_history=existing / 'transactions_2006_2020.csv',
        existing_recent=existing / 'data/capital_area_apt_trade_transactions.csv')
    target = output / 'transactions_extended.csv'
    counts = {}
    with io.TextIOWrapper(CompleteWriter(target.open('wb', buffering=0)), encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=DASHBOARD_FIELDNAMES)
        writer.writeheader()
        for name, info in sorted(manifest['outputs'].items()):
            if not name.startswith('trade/'):
                continue
            path = output / 'normalized' / name
            if file_sha256(path) != info['sha256']:
                raise ValueError('Normalized partition checksum mismatch')
            n = 0
            with gzip.open(path, 'rt', encoding='utf-8-sig', newline='') as part:
                reader = csv.DictReader(part)
                if reader.fieldnames != DASHBOARD_FIELDNAMES:
                    raise ValueError('Normalized partition schema mismatch')
                for row in reader:
                    writer.writerow(row)
                    group = row['CGG_CD'][:2] + ':' + row['CTRT_DAY'][:4]
                    counts[group] = counts.get(group, 0) + 1
                    n += 1
            if n != info['rows']:
                raise ValueError('Normalized partition row count mismatch')
    if sum(counts.values()) != manifest['counts']['dashboard_rows']:
        raise ValueError('Combined dataset count mismatch')
    report = {'schema_version': 1, 'status': 'prepared', 'source_commit': source_commit,
              'source_subset_sha256': file_sha256(subset_path),
              'normalization_manifest_sha256': file_sha256(output / 'normalized/normalization_manifest.json'),
              'normalizer_sha256': manifest['normalizer_sha256'],
              'data_sha256': file_sha256(target), 'rows': sum(counts.values()),
              'province_year_raw_rows': counts, 'uploaded_source_matches': matches,
              'new_csv_rows': sum(e['row_count'] for e in entries.values()),
              'sources': [{'partition': key, 'rows': e['row_count'], 'sha256': e['raw_sha256'],
                           'observation_time': e.get('downloaded_at') or e.get('imported_at'),
                           'observation_basis': 'downloaded_at' if e.get('downloaded_at') else 'imported_at',
                           'independent_server_count_checked': e.get('independent_server_count_checked', e.get('count_checked_at') is not None)}
                          for key, e in entries.items()],
              'normalization_sources': [{'source_id': s['source_id'], 'counts': s['counts'],
                                        'quality_flags': s['quality_flags'],
                                        'dashboard_exclusions': s['dashboard_exclusions'],
                                        'unresolved_address_examples': s['unresolved_address_examples']}
                                       for s in manifest['sources']],
              'existing_inputs': manifest['existing_inputs'],
              'ownership': manifest['ownership'], 'historical_publication_vintages': False,
              'multiplicity': manifest['multiplicity']}
    (output / 'preparation.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({k: report[k] for k in ('status', 'rows', 'new_csv_rows', 'data_sha256')}, ensure_ascii=False), flush=True)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--collection', default='.work/capital-source/collection')
    p.add_argument('--existing', default='.work/retrain-inputs')
    p.add_argument('--output', default='.work/retraining')
    p.add_argument('--uploads', default='../inputs')
    a = p.parse_args()
    prepare(a.collection, a.existing, a.output, a.uploads)
