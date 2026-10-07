"""Refresh the existing complete apartment state from official public CSV exports.

Fetch only the same recent three months and one historical rotation as the API
job. Restore raw_estate_state first. Source rows, multiplicity, cancellations,
download bytes and observation history are preserved; no API key is needed.
"""
from __future__ import annotations

import argparse
import calendar
import csv
from datetime import date
import gzip
import json
from pathlib import Path
import re
import shutil
import tempfile

from estate.data.collection.transactions import (
    REGIONS, NORMALIZER_VERSION, digest, month_range, read_partition, rows_bytes, utc_now,
)
from estate.data.collection.molit_csv import (
    ExportRequest, PublicCSVClient, REGIONS as PROVINCES, ORIGIN, PAGE_PATH,
    collect, iter_csv_records,
)
from estate.core.calendar import today
from estate.core.io import write_binary, write_json
from estate.data.storage.vintages import observe_partition
from estate.data.collection.molit_api import DASHBOARD_FIELDNAMES, split_jibun
from estate.data.preparation.normalize_capital_history import AddressRegistry, normalize_row, dashboard_row


def normalize_export_row(raw, info, number, registry):
    record = normalize_row(raw, info, number, registry)
    parcel = raw['번지'].strip()
    mountain = re.fullmatch(r'산(\d+)(?:-(\d+))?', parcel)
    main, sub = raw['본번'].strip(), raw['부번'].strip()
    # The public export splits a mountain parcel into numeric fields and puts
    # the '산' qualifier only in 번지. The API keeps that qualifier in jibun.
    mountain_matches = (mountain and main.isdigit() and sub.isdigit()
                        and int(main) == int(mountain[1])
                        and int(sub) == int(mountain[2] or 0))
    # Published provisional parcels also occur in API jibun. Preserve their
    # label rather than merging them into an invented numeric parcel '0'.
    labelled_number = re.fullmatch(r'가-(\d+)', parcel)
    provisional = (main.isdigit() and sub.isdigit() and int(sub) == 0 and (
        (parcel in {'가-', 'BL-', '지구BL'} and int(main) == 0)
        or (labelled_number and int(main) == int(labelled_number[1]))))
    if mountain_matches or provisional:
        record['quality_flags'] = [flag for flag in record['quality_flags'] if flag != 'lot_source_disagrees']
    row, reason = dashboard_row(record)
    if reason:
        return row, reason
    if mountain_matches:
        row['MNO'] = '산' + str(int(mountain[1]))
        row['SNO'] = str(int(mountain[2])) if int(mountain[2] or 0) else ''
    elif provisional:
        row['MNO'], row['SNO'] = split_jibun(parcel)
    else:
        row['MNO'] = str(int(row['MNO']))
        row['SNO'] = str(int(row['SNO'])) if int(row['SNO']) else ''
    return row, None


def refresh_plan(start: str, cutoff: date):
    months = month_range(start, cutoff.strftime('%Y%m'))
    recent = months[-3:]
    old = months[:-3]
    audit = old[cutoff.toordinal() % len(old)] if old else None
    selected = sorted(set(recent + ([audit] if audit else [])))
    # Adjacent months in one year fit one official province export.
    spans = []
    for month in selected:
        first = date(int(month[:4]), int(month[4:]), 1)
        last = min(cutoff, date(first.year, first.month, calendar.monthrange(first.year, first.month)[1]))
        if spans and spans[-1][1].toordinal() + 1 == first.toordinal() and spans[-1][0].year == first.year:
            spans[-1] = (spans[-1][0], last)
        else:
            spans.append((first, last))
    requests = [ExportRequest(region, 'sale', first, last)
                for first, last in spans for region in PROVINCES]
    return months, selected, audit, requests


def verify_baseline(root: Path):
    source = root / 'data/capital_area_apt_trade_transactions.csv'
    meta = json.loads(source.with_suffix('.manifest.json').read_text('utf-8'))
    if (meta.get('complete') is not True or meta.get('normalizer_version') != NORMALIZER_VERSION
            or digest(source.read_bytes()) != meta['sha256']):
        raise ValueError('Restore a complete checksum-verified raw state before refreshing')
    months = month_range(meta['start'], meta['end'])
    if meta['partition_count'] != len(months) * len(REGIONS) or meta['region_count'] != len(REGIONS):
        raise ValueError('Restored state does not cover the current complete district registry')
    total = 0
    cache = root / 'data/molit_cache_v3'
    for month in months:
        for code in sorted(REGIONS):
            data = read_partition(cache / f'{month}-{code}.json.gz', month, code)
            if any(row['CGG_CD'] != code or row['CTRT_DAY'][:6] != month for row in data['rows']):
                raise ValueError('Restored partition contains out-of-scope rows')
            total += data['count']
    if total != meta['rows']:
        raise ValueError('Restored partition counts differ from the complete source')
    return meta


def publish_to_local_cache(root: Path, exports: Path, baseline: dict, cutoff: date, manifest: dict):
    months, selected, audit, requests = refresh_plan(baseline['start'], cutoff)
    keys = {r.key for r in requests}
    if (manifest.get('status') != 'complete' or manifest.get('data_cutoff') != cutoff.isoformat()
            or set(manifest.get('planned_keys', [])) != keys
            or any(manifest.get('entries', {}).get(key, {}).get('status') != 'complete' for key in keys)):
        raise ValueError('Only the complete requested public CSV collection can be applied')
    cache = root / 'data/molit_cache_v3'
    source = root / 'data/capital_area_apt_trade_transactions.csv'
    # Do not apply a download to a baseline that changed during collection.
    current = json.loads(source.with_suffix('.manifest.json').read_text('utf-8'))
    if current['sha256'] != baseline['sha256'] or digest(source.read_bytes()) != baseline['sha256']:
        raise ValueError('The baseline changed after collection started')
    groups = {(month, code): [] for month in selected for code in REGIONS}
    observations, source_refs = {}, {}
    registry = AddressRegistry()
    for request in requests:
        entry = manifest['entries'][request.key]
        observed = entry.get('downloaded_at') or entry['completed_at']
        ref = {'source': ORIGIN + PAGE_PATH, 'query': request.as_dict(),
               'raw_sha256': entry.get('raw_sha256'), 'observed_at': observed,
               'row_count': entry['row_count']}
        province_codes = [code for code, region in REGIONS.items() if region.sido == PROVINCES[request.region]]
        for month in month_range(request.start.strftime('%Y%m'), request.end.strftime('%Y%m')):
            for code in province_codes:
                observations[(month, code)] = observed
                source_refs[(month, code)] = ref
        if not entry['row_count']:
            if not entry.get('empty_confirmed_by_count_endpoint'):
                raise ValueError('An empty export needs official count confirmation')
            continue
        path = exports / request.relative_path
        compressed = path.read_bytes()
        raw = gzip.decompress(compressed)
        if digest(compressed) != entry['gzip_sha256'] or digest(raw) != entry['raw_sha256']:
            raise ValueError('Public CSV download checksum mismatch')
        info = {'source_id': request.key, 'kind': 'sale', 'sido': PROVINCES[request.region],
                'start': request.start.isoformat(), 'end': request.end.isoformat(),
                'sha256': entry['raw_sha256'], 'observed_at': observed}
        count = 0
        for count, raw_row in enumerate(iter_csv_records(raw), 1):
            row, reason = normalize_export_row(raw_row, info, count, registry)
            if reason:
                raise ValueError(f'Cannot normalize {request.key} row {count}: {reason}')
            key = row['CTRT_DAY'][:6], row['CGG_CD']
            if key not in groups or row['CGG_CD'] not in province_codes:
                raise ValueError('Public CSV district/month is outside the requested current registry')
            groups[key].append(row)
        if count != entry['row_count']:
            raise ValueError('Normalized source row count differs from the verified export')
    if set(observations) != set(groups):
        raise ValueError('Missing province coverage for a refreshed month')
    # Stage all replacements and the complete CSV before changing any local input.
    with tempfile.TemporaryDirectory(prefix='csv-refresh-', dir=root / '.work') as tmp:
        staged = Path(tmp)
        for (month, code), rows in groups.items():
            path = cache / f'{month}-{code}.json.gz'
            previous = read_partition(path, month, code) if path.exists() else None
            observed = observations[(month, code)]
            data = {'complete': True, 'normalizer_version': NORMALIZER_VERSION,
                    'month': month, 'code': code, 'count': len(rows), 'rows': rows,
                    'fetched_at': observed, 'rows_sha256': digest(rows_bytes(rows)),
                    'source_export': source_refs[(month, code)],
                    'observation_ledger': observe_partition(previous, rows, observed)}
            write_binary(staged / path.name, gzip.compress(json.dumps(data, ensure_ascii=False,
                separators=(',', ':')).encode('utf-8'), mtime=0))
        output = staged / 'transactions.csv'
        total = 0
        with output.open('w', encoding='utf-8-sig', newline='') as stream:
            writer = csv.DictWriter(stream, fieldnames=DASHBOARD_FIELDNAMES)
            writer.writeheader()
            for month in months:
                for code in sorted(REGIONS):
                    folder = staged if month in selected else cache
                    data = read_partition(folder / f'{month}-{code}.json.gz', month, code)
                    writer.writerows(data['rows'])
                    total += data['count']
        if total == 0:
            raise ValueError('An empty whole-market collection cannot replace the existing state')
        meta = {**baseline, 'end': months[-1], 'rows': total,
                'partition_count': len(months) * len(REGIONS), 'region_count': len(REGIONS),
                'sha256': digest(output.read_bytes()), 'fetched_at': utc_now(),
                'source': ORIGIN + PAGE_PATH, 'refresh_method': 'official_public_csv',
                'refresh_months': 3, 'refreshed_contract_months': selected,
                'historical_audit_month': audit, 'previous_collection_sha256': baseline['sha256'],
                'public_csv_manifest_sha256': digest((exports / 'manifest.json').read_bytes()),
                'note': 'Recent months and one historical month replaced from complete official public CSV exports; older verified partitions reused. All rows and multiplicity retained.'}
        for path in staged.glob('*.json.gz'):
            shutil.copy2(path, cache / path.name)
        output.replace(source)
        write_json(source.with_suffix('.manifest.json'), meta, indent=2)
    return meta


def run(root: Path, exports: Path, cutoff: date, client=None):
    if cutoff != today():
        raise ValueError('A current refresh must use today in Asia/Seoul')
    (root / '.work').mkdir(parents=True, exist_ok=True)
    baseline = verify_baseline(root)
    months, selected, _, requests = refresh_plan(baseline['start'], cutoff)
    if baseline['end'] > months[-1]:
        raise ValueError('Cannot move the collection backwards')
    if any(month not in selected for month in months if month > baseline['end']):
        raise ValueError('Missing older coverage requires a separately scoped recovery')
    manifest = collect(requests, exports, cutoff,
                       client or PublicCSVClient(metadata_timeout=30, csv_timeout=120),
                       daily_limit=20, transport_retries=2, retry_delay=5)
    return publish_to_local_cache(root, exports, baseline, cutoff, manifest)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('.'))
    parser.add_argument('--as-of', type=date.fromisoformat, default=today())
    parser.add_argument('--exports', type=Path, required=True,
                        help='Archive below the restored raw state so source bytes are preserved in its snapshot')
    args = parser.parse_args()
    result = run(args.root, args.exports, args.as_of)
    print(json.dumps({k: result[k] for k in ('rows', 'end', 'refresh_method',
          'refreshed_contract_months', 'fetched_at', 'sha256')}, indent=2))
