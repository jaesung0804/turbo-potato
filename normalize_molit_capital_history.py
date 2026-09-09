"""Stream current MOLIT CSV snapshots into separate retrospective research files.

Original columns and row multiplicity survive in canonical JSONL. Registry name
matches are address components as supplied today, never verified past addresses
or physical property IDs. No source, model input, or website is overwritten.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
import csv
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from get_molit_apt_trade_data import CAPITAL_AREA_LAWD_CODES, DASHBOARD_FIELDNAMES
from estate_io import write_binary

SCHEMA_VERSION = 1
IMPLEMENTATION_SHA256 = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
MISSING = {'', '-', '--'}
SIDOS = {'서울특별시': '11', '인천광역시': '28', '경기도': '41'}
COMMON_COLUMNS = {'NO', '시군구', '번지', '본번', '부번', '단지명',
                  '전용면적(㎡)', '계약년월', '계약일', '층', '건축년도', '도로명'}


def file_sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def raw_sha256(path):
    h = hashlib.sha256()
    with gzip.open(path, 'rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def registry_entries():
    # Reuse the project's current registry, including its explicit former names.
    from collect_estate_transactions import REGIONS
    entries = {(r.code, r.sido, r.sgg) for r in CAPITAL_AREA_LAWD_CODES}
    entries.update((r.code, r.sido, r.sgg) for r in REGIONS.values())
    return sorted(entries)


class AddressRegistry:
    def __init__(self, entries=None):
        self.names = {}
        for code, sido, district in (registry_entries() if entries is None else entries):
            self.names.setdefault(sido + ' ' + district, set()).add((code, sido, district))

    def match(self, address):
        # Test exact word-boundary prefixes, not fuzzy names or aliases. Usually
        # only 3-5 prefixes exist, independent of millions of input rows.
        candidates = set()
        longest = 0
        for match in re.finditer(' ', address):
            prefix = address[:match.start()]
            for code, sido, district in self.names.get(prefix, ()):
                remainder = address[match.end():]
                if remainder:
                    # A listed city district (화성시 병점구) is the complete
                    # registry name when both that and former 화성시 exist.
                    if len(prefix) > longest:
                        candidates.clear()
                        longest = len(prefix)
                    candidates.add((code, sido, district, remainder))
        if len(candidates) == 1:
            code, sido, district, dong = next(iter(candidates))
            return {'district_code': code, 'sido': sido, 'district': district,
                    'legal_dong_as_published': dong, 'match': 'unique_exact_registry_name'}
        return {'district_code': None, 'sido': None, 'district': None,
                'legal_dong_as_published': None,
                'match': 'ambiguous' if candidates else 'unresolved'}


def decimal_text(value, field, flags, *, minimum=None, positive=False):
    cleaned = value.strip().replace(',', '')
    if cleaned in MISSING:
        flags.append(field + '_missing')
        return None
    try:
        if not re.fullmatch(r'[+-]?\d+(?:\.\d+)?', cleaned):
            raise InvalidOperation
        number = Decimal(cleaned)
        if not number.is_finite():
            raise InvalidOperation
    except InvalidOperation:
        flags.append(field + '_invalid')
        return None
    if (positive and number <= 0) or (minimum is not None and number < minimum):
        flags.append(field + '_out_of_range')
        return None
    # Keep all source decimal places, with no binary float intermediary.
    return format(number, 'f')


def contract_date(raw, flags):
    month, day = raw['계약년월'].strip(), raw['계약일'].strip()
    try:
        if not re.fullmatch(r'\d{6}', month) or not re.fullmatch(r'\d{1,2}', day):
            raise ValueError
        return date(int(month[:4]), int(month[4:]), int(day)).isoformat()
    except ValueError:
        flags.append('contract_date_invalid')
        return None


def normalize_row(raw, source, row_number, registry):
    flags = []
    address_text = raw['시군구'].strip()
    address = registry.match(address_text)
    if address['match'] != 'unique_exact_registry_name':
        flags.append('address_' + address['match'])
    if address['sido'] and address['sido'] != source['sido']:
        flags.append('source_sido_mismatch')
    when = contract_date(raw, flags)
    if when and not source['start'] <= when <= source['end']:
        flags.append('contract_date_outside_request')
    area = decimal_text(raw['전용면적(㎡)'], 'area', flags, positive=True)
    floor = decimal_text(raw['층'], 'floor', flags)
    built = decimal_text(raw['건축년도'], 'built_year', flags)
    main, sub = raw['본번'].strip(), raw['부번'].strip()
    if not re.fullmatch(r'\d+', main) or not re.fullmatch(r'\d+', sub):
        flags.append('lot_numbers_unresolved')
    lot = None
    if 'lot_numbers_unresolved' not in flags:
        main_clean, sub_clean = main.lstrip('0') or '0', sub.lstrip('0')
        lot = main_clean + ('-' + sub_clean if sub_clean else '')
        published_lot = raw['번지'].strip()
        # Mountain parcels or discrepancies cannot safely share dashboard keys.
        if published_lot not in MISSING:
            pieces = published_lot.split('-')
            numeric = len(pieces) <= 2 and all(re.fullmatch(r'\d+', p) for p in pieces)
            source_lot = None
            if numeric:
                source_lot = str(int(pieces[0]))
                if len(pieces) == 2 and int(pieces[1]) != 0:
                    source_lot += '-' + str(int(pieces[1]))
            if source_lot != lot:
                flags.append('lot_source_disagrees')
    if not raw['단지명'].strip() or raw['단지명'].strip() in MISSING:
        flags.append('building_name_missing')
    row = {
        'schema_version': SCHEMA_VERSION, 'kind': source['kind'],
        'raw': raw,
        'provenance': {'source_id': source['source_id'], 'row_number': row_number,
                       'source_file_sha256': source['sha256'],
                       'observed_at': source['observed_at'],
                       'observation_basis': source.get('observation_basis', 'downloaded_at'),
                       'publication_at': None, 'historical_publication_vintage': False},
        'contract_date': when, 'receipt_year': None,
        'address': {'as_published': raw['시군구'], **address,
                    'historical_address': None, 'current_display_address': None,
                    'basis': 'downloaded_source_text', 'lot_normalized': lot,
                    'physical_property_id': None},
        'building_name': raw['단지명'].strip(), 'area_sqm': area,
        'floor': floor, 'built_year': built, 'currency_unit': '10000_KRW',
        'property_type': raw.get('주택유형', '아파트').strip(),
    }
    if source['kind'] == 'sale':
        cancellation = raw.get('해제사유발생일', '').strip()
        cancelled = cancellation not in MISSING
        if '해제사유발생일' not in raw:
            cancelled = None
            flags.append('cancellation_status_unavailable')
        deal_type = raw.get('거래유형', '').strip()
        if deal_type in MISSING:
            deal_type = None
            flags.append('deal_type_unknown')
        elif deal_type not in {'중개거래', '직거래'}:
            flags.append('deal_type_unrecognized')
        row.update(amount_10k_krw=decimal_text(raw['거래금액(만원)'], 'amount', flags, positive=True),
                   cancelled=cancelled, cancellation_date_raw=raw.get('해제사유발생일'),
                   deal_type=deal_type,
                   direct_trade=(deal_type == '직거래' if deal_type in {'직거래', '중개거래'} else None))
    else:
        contract = raw.get('계약구분', '').strip()
        if contract not in {'신규', '갱신'}:
            contract = '미상'
            flags.append('contract_type_unknown')
        row.update(deposit_10k_krw=decimal_text(raw['보증금(만원)'], 'deposit', flags, minimum=0),
                   monthly_rent_10k_krw=decimal_text(raw['월세금(만원)'], 'monthly_rent', flags, minimum=0),
                   lease_type=raw.get('전월세구분', '').strip() or None,
                   contract_type=contract, cancelled=None,
                   renewal_right_raw=raw.get('갱신요구권 사용'),
                   contract_period_raw=raw.get('계약기간'),
                   previous_deposit_10k_krw=decimal_text(raw.get('종전계약 보증금(만원)', ''), 'previous_deposit', flags, minimum=0),
                   previous_monthly_rent_10k_krw=decimal_text(raw.get('종전계약 월세(만원)', ''), 'previous_monthly_rent', flags, minimum=0))
        deposit, monthly = row['deposit_10k_krw'], row['monthly_rent_10k_krw']
        financials_known = deposit is not None and monthly is not None
        row['is_pure_jeonse'] = (Decimal(monthly) == 0 and Decimal(deposit) > 0
                                and row['property_type'] == '아파트') if financials_known else None
        if row['lease_type'] not in {'전세', '월세'}:
            flags.append('lease_type_unknown')
        elif financials_known and ((row['lease_type'] == '전세' and not row['is_pure_jeonse'])
                                   or (row['lease_type'] == '월세' and Decimal(monthly) == 0)):
            flags.append('lease_type_financials_disagree')
        flags.append('cancellation_status_unavailable')
    row['quality_flags'] = flags
    return row


def dashboard_row(record):
    """Return (row, reason); retained cancellations/direct trades are not filtered."""
    if record['kind'] != 'sale':
        return None, 'not_sale'
    bad = set(record['quality_flags']) & {
        'address_unresolved', 'address_ambiguous', 'source_sido_mismatch',
        'contract_date_invalid', 'contract_date_outside_request',
        'area_missing', 'area_invalid', 'area_out_of_range',
        'amount_missing', 'amount_invalid', 'amount_out_of_range',
        'lot_numbers_unresolved', 'lot_source_disagrees', 'building_name_missing',
        'cancellation_status_unavailable'}
    if bad:
        return None, '|'.join(sorted(bad))
    raw, address = record['raw'], record['address']
    result = dict.fromkeys(DASHBOARD_FIELDNAMES, '')
    result.update(RCPT_YR=record['contract_date'][:4], CGG_CD=address['district_code'],
                  CGG_NM=address['district'], STDG_NM=address['legal_dong_as_published'],
                  MNO=raw['본번'].strip(), SNO=raw['부번'].strip(),
                  BLDG_NM=record['building_name'], CTRT_DAY=record['contract_date'].replace('-', ''),
                  THING_AMT=record['amount_10k_krw'], ARCH_AREA=record['area_sqm'],
                  FLR=record['floor'] or '', ARCH_YR=record['built_year'] or '',
                  BLDG_USG=record['property_type'], DCLR_SE=record['deal_type'] or '',
                  RTRCN_DAY=(record['cancellation_date_raw'].strip() or 'cancelled') if record['cancelled'] else '',
                  OPBIZ_RESTAGNT_SGG_NM=raw.get('중개사소재지', '').strip())
    return result, None


def imported_request(meta):
    """Recover scope from an imported CSV's own preamble, not its filename."""
    fields = {}
    for record in meta.get('metadata_rows', []):
        if len(record) == 1 and ' : ' in record[0]:
            key, value = record[0].split(' : ', 1)
            if key in fields:
                raise ValueError('Duplicate imported CSV scope field')
            fields[key.strip()] = value.strip()
    query = meta['query']
    expected = '아파트(매매)' if query['kind'] == 'sale' else '아파트(전월세)'
    dates = re.fullmatch(r'(\d{4}-\d{2}-\d{2}) ~ (\d{4}-\d{2}-\d{2})', fields.get('계약일자', ''))
    if (fields.get('실거래구분') != expected or fields.get('주소구분') != '지번주소'
            or fields.get('시군구') != '전체' or fields.get('읍면동') != '전체'
            or fields.get('면적') != '전체' or fields.get('금액선택') != '전체'
            or not dates or dates.groups() != (query['start'], query['end'])):
        raise ValueError('Imported CSV preamble does not prove its declared scope')
    return {'sidoNm': fields['시도'], 'sggNm': '전체', 'emdNm': '전체',
            'srhFromDt': dates[1], 'srhToDt': dates[2], 'srhThingNo': 'A',
            'srhDelngSecd': '1' if query['kind'] == 'sale' else '2'}


def source_metadata(meta_path, entry=None):
    path = Path(meta_path).resolve()
    meta = dict(entry) if entry is not None else json.loads(path.read_text(encoding='utf-8'))
    if entry is not None:
        meta['kind'] = meta['query']['kind']
        meta['request'] = meta.get('request_fields') or imported_request(meta)
        meta['sha256'] = meta.get('raw_sha256')
    if meta.get('status') != 'complete' or meta.get('kind') not in {'sale', 'rent'}:
        raise ValueError(f'Not a completed MOLIT source: {path}')
    request = meta['request']
    sido = request['sidoNm']
    if sido not in SIDOS:
        raise ValueError('Expected a capital-area province')
    for name in ('sggNm', 'emdNm'):
        if request.get(name) != '전체':
            raise ValueError('Only complete province partitions may establish source ownership')
    start, end = request['srhFromDt'], request['srhToDt']
    if request.get('srhThingNo') != 'A' or request.get('srhDelngSecd') != ('1' if meta['kind'] == 'sale' else '2'):
        raise ValueError('Expected apartment source with the declared contract kind')
    if date.fromisoformat(start) > date.fromisoformat(end):
        raise ValueError('Reversed source period')
    observation_basis = 'downloaded_at' if meta.get('downloaded_at') else 'imported_at'
    observation_text = meta.get(observation_basis)
    if not observation_text:
        raise ValueError('No evidenced download or import observation time')
    observed = datetime.fromisoformat(observation_text)
    if observed.tzinfo is None:
        raise ValueError('Source observation requires a timezone')
    empty = meta.get('empty_confirmed_by_count_endpoint') and meta.get('row_count') == 0 and meta.get('file') is None
    raw_path = (path.parent / meta['file']).resolve() if entry is not None and not empty else path.with_suffix('.csv.gz')
    if entry is not None and not empty and not raw_path.is_relative_to(path.parent):
        raise ValueError('Raw source path must remain inside its collection directory')
    if not empty and not raw_path.is_file():
        raise FileNotFoundError(raw_path)
    return {'source_id': f"molit-csv:{meta['kind']}:{SIDOS[sido]}:{start}:{end}:{meta['sha256']}",
            'kind': meta['kind'], 'sido': sido, 'sido_code': SIDOS[sido],
            'start': start, 'end': end, 'sha256': meta['sha256'],
            'observed_at': observed.astimezone(timezone.utc).isoformat(),
            'observation_basis': observation_basis,
            'path': None if empty else str(raw_path), 'metadata_path': str(path), 'metadata': meta}


def validate_sources(sources, as_of):
    seen_hashes = set()
    for i, source in enumerate(sources):
        if source['sha256'] and source['sha256'] in seen_hashes:
            raise ValueError('Repeated source file ingestion')
        if source['sha256']:
            seen_hashes.add(source['sha256'])
        for previous in sources[:i]:
            if (source['kind'], source['sido']) == (previous['kind'], previous['sido']):
                if max(source['start'], previous['start']) <= min(source['end'], previous['end']):
                    raise ValueError('Overlapping source partitions; choose one snapshot explicitly')
    replacements = [s for s in sources if s['kind'] == 'sale'
                    and s['start'] <= '2026-01-01' <= s['end']]
    if replacements and ({s['sido_code'] for s in replacements} != set(SIDOS.values())
                         or any(s['end'] < as_of for s in replacements)):
        raise ValueError('2026 replacement requires all three complete province sources through as_of')
    return bool(replacements)


def sale_owner(sido_code, contract, replace_2026=False):
    """Explicit, disjoint source selection; old Gwangmyeong never overlaps CSV."""
    if sido_code not in SIDOS.values() or not contract:
        return None
    if '2006-01-01' <= contract <= '2020-12-31':
        return 'existing_history' if sido_code == '11' else 'molit_csv'
    if '2021-01-01' <= contract <= '2025-12-31':
        return 'existing_recent'
    if '2026-01-01' <= contract <= '2026-12-31':
        return 'molit_csv' if replace_2026 else 'existing_recent'
    return None


def source_rows(source):
    meta = source['metadata']
    if source['path'] is None:
        return
    if meta.get('gzip_sha256') and file_sha256(source['path']) != meta['gzip_sha256']:
        raise ValueError('Compressed source checksum mismatch')
    if raw_sha256(source['path']) != source['sha256']:
        raise ValueError(f"Source checksum mismatch: {source['path']}")
    with gzip.open(source['path'], 'rt', encoding=meta['encoding'], newline='') as stream:
        reader = csv.reader(stream)
        for _ in range(meta['header_row'] - 1):
            next(reader)
        header = next(reader)
        needed = COMMON_COLUMNS | ({'거래금액(만원)'} if source['kind'] == 'sale'
                                   else {'전월세구분', '보증금(만원)', '월세금(만원)'})
        if header != meta['columns'] or not needed.issubset(header) or len(set(header)) != len(header):
            raise ValueError('CSV schema does not match its verified source metadata')
        count = 0
        for values in reader:
            # Match collector.inspect_csv: blank records are not transactions.
            # row_number is the 1-based nonblank data-record ordinal, not NO.
            if not any(value.strip() for value in values):
                continue
            count += 1
            if len(values) != len(header):
                raise ValueError(f'Malformed CSV row {count}')
            yield count, dict(zip(header, values))
        if count != meta['row_count']:
            raise ValueError('CSV row count does not match completed download')


class CompleteWriter(io.RawIOBase):
    """A gzip sink must consume every byte even when the filesystem short-writes."""
    def __init__(self, raw):
        self.raw = raw

    def writable(self):
        return True

    def write(self, value):
        remaining = memoryview(value)
        total = len(remaining)
        while remaining:
            count = self.raw.write(remaining)
            if not count:
                raise OSError('Incomplete normalized output write')
            remaining = remaining[count:]
        return total

    def flush(self):
        if not self.raw.closed:
            self.raw.flush()

    def tell(self):
        return self.raw.tell()

    def close(self):
        if not self.closed:
            self.flush()
            self.raw.close()
        super().close()


def gzip_text(stack, path, encoding='utf-8'):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    raw = stack.enter_context(CompleteWriter(Path(path).open('xb', buffering=0)))
    compressed = stack.enter_context(gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0, compresslevel=3))
    return stack.enter_context(io.TextIOWrapper(compressed, encoding=encoding, newline=''))


class PartitionWriter:
    """Bound each output to 100,000 rows; memory does not depend on row count."""
    def __init__(self, stage, max_rows=100000):
        self.stage, self.max_rows = Path(stage), max_rows
        self.active, self.parts, self.outputs, self.output_rows = {}, Counter(), [], Counter()

    def finish(self, state):
        # Close compression completely before a filesystem write. Keeping
        # many filesystem-backed compression streams open proved unreliable
        # on the remote workspace; a bounded compressed buffer is portable.
        state['stack'].close()
        body = state['buffer'].getvalue()
        gzip.decompress(body)  # Verify the finalized stream before saving.
        path = self.stage / state['name']
        write_binary(path, body)
        if file_sha256(path) != hashlib.sha256(body).hexdigest():
            raise OSError('Saved normalized partition differs from buffer')
        state['buffer'].close()

    def write(self, key, record, *, dashboard=False):
        state = self.active.get(key)
        if state is not None and state['rows'] >= self.max_rows:
            self.finish(state)
            del self.active[key]
            state = None
        if state is None:
            self.parts[key] += 1
            suffix = '.csv.gz' if dashboard else '.jsonl.gz'
            name = f'{key}/part-{self.parts[key]:04d}{suffix}'
            stack = ExitStack()
            buffer = io.BytesIO()
            compressed = stack.enter_context(gzip.GzipFile(filename='', fileobj=buffer, mode='wb', mtime=0, compresslevel=3))
            stream = stack.enter_context(io.TextIOWrapper(compressed, encoding='utf-8-sig' if dashboard else 'utf-8', newline=''))
            writer = csv.DictWriter(stream, fieldnames=DASHBOARD_FIELDNAMES) if dashboard else stream
            if dashboard:
                writer.writeheader()
            state = self.active[key] = {'stack': stack, 'writer': writer, 'rows': 0, 'name': name, 'buffer': buffer}
            self.outputs.append(name)
        if dashboard:
            state['writer'].writerow(record)
        else:
            state['writer'].write(json.dumps(record, ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n')
        state['rows'] += 1
        self.output_rows[state['name']] += 1

    def trade(self, record):
        self.write(f"trade/{record['CGG_CD'][:2]}/{record['CTRT_DAY'][:4]}", record, dashboard=True)

    def close(self):
        for state in self.active.values():
            self.finish(state)
        self.active.clear()


def merge_existing(path, owner, writer, as_of, replace_2026):
    counts = Counter()
    minimum, maximum = None, None
    path = Path(path)
    checksum = file_sha256(path)
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8-sig', newline='') as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != DASHBOARD_FIELDNAMES:
            raise ValueError(f'Existing dashboard schema changed: {path}')
        for row in reader:
            counts['raw_rows'] += 1
            value = row['CTRT_DAY']
            try:
                if not re.fullmatch(r'\d{8}', value):
                    raise ValueError
                when = datetime.strptime(value, '%Y%m%d').date().isoformat()
            except ValueError:
                counts['invalid_date_excluded'] += 1
                continue
            if when > as_of or sale_owner(row['CGG_CD'][:2], when, replace_2026) != owner:
                counts['outside_owned_scope_excluded'] += 1
                continue
            writer.trade(row)
            counts['written_rows'] += 1
            minimum = when if minimum is None else min(minimum, when)
            maximum = when if maximum is None else max(maximum, when)
    sidecar = path.with_suffix('.manifest.json')
    metadata = json.loads(sidecar.read_text()) if sidecar.is_file() else None
    if file_sha256(path) != checksum:
        raise ValueError('Existing input changed during normalization')
    return {'path': str(path.resolve()), 'sha256': checksum, 'owner': owner,
            'counts': dict(counts), 'contract_min': minimum, 'contract_max': maximum,
            'source_manifest': metadata, 'historical_publication_vintage': False}


def sources_from_paths(paths):
    sources = []
    for path in paths:
        value = json.loads(Path(path).read_text(encoding='utf-8'))
        checksum = file_sha256(path)
        if isinstance(value, dict) and 'entries' in value:
            if value.get('status') != 'complete':
                raise ValueError('Collection manifest is not complete')
            sources.extend({**source_metadata(path, entry), 'metadata_sha256': checksum}
                           for entry in value['entries'].values())
        else:
            sources.append({**source_metadata(path), 'metadata_sha256': checksum})
    return sources


def normalize_sources(meta_paths, out, *, as_of='2026-09-09', existing_history=None, existing_recent=None,
                      existing_history_commits=(), existing_recent_commits=()):
    date.fromisoformat(as_of)
    sources = sources_from_paths(meta_paths)
    if not sources:
        raise ValueError('At least one completed source is required')
    sources.sort(key=lambda s: (s['kind'], s['sido_code'], s['start']))
    replace_2026 = validate_sources(sources, as_of)
    out = Path(out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    if any((out / name).exists() for name in ['canonical', 'trade', 'normalization_manifest.json']):
        raise FileExistsError('Research outputs already exist; choose a new output directory')
    stage = Path(tempfile.mkdtemp(prefix='normalize-', dir=out))
    summaries, existing = [], []
    counts = Counter({'sale_canonical_rows': 0, 'rent_canonical_rows': 0,
                      'rent_pure_jeonse_rows': 0, 'rent_monthly_rent_rows': 0,
                      'rent_financials_unknown_rows': 0, 'rent_other_known_financials_rows': 0})
    all_contract_types = Counter({'신규': 0, '갱신': 0, '미상': 0})
    try:
        writer = PartitionWriter(stage)
        try:
            registry = AddressRegistry()
            for source in sources:
                flags, matches, rejected, local = Counter(), Counter(), Counter(), Counter()
                contract_types = Counter({'신규': 0, '갱신': 0, '미상': 0}) if source['kind'] == 'rent' else Counter()
                if source['kind'] == 'rent':
                    local.update({'pure_jeonse_rows': 0, 'monthly_rent_rows': 0,
                                  'lease_financials_unknown_rows': 0, 'other_known_financials_rows': 0})
                address_examples = []
                minimum, maximum = None, None
                for number, raw in source_rows(source):
                    record = normalize_row(raw, source, number, registry)
                    key = f"canonical/{source['kind']}/{source['sido_code']}/{source['start'][:4]}/{source['start']}_{source['end']}"
                    writer.write(key, record)
                    local['canonical_rows'] += 1
                    counts[source['kind'] + '_canonical_rows'] += 1
                    if source['kind'] == 'rent':
                        contract_types[record['contract_type']] += 1
                        all_contract_types[record['contract_type']] += 1
                        if record['is_pure_jeonse'] is True:
                            local['pure_jeonse_rows'] += 1
                            counts['rent_pure_jeonse_rows'] += 1
                        if record['monthly_rent_10k_krw'] is not None and Decimal(record['monthly_rent_10k_krw']) > 0:
                            local['monthly_rent_rows'] += 1
                            counts['rent_monthly_rent_rows'] += 1
                        if record['is_pure_jeonse'] is None:
                            local['lease_financials_unknown_rows'] += 1
                            counts['rent_financials_unknown_rows'] += 1
                        elif not record['is_pure_jeonse'] and Decimal(record['monthly_rent_10k_krw']) == 0:
                            local['other_known_financials_rows'] += 1
                            counts['rent_other_known_financials_rows'] += 1
                    flags.update(record['quality_flags'])
                    matches[record['address']['match']] += 1
                    if record['address']['match'] != 'unique_exact_registry_name' and len(address_examples) < 20:
                        if raw['시군구'] not in address_examples:
                            address_examples.append(raw['시군구'])
                    when = record['contract_date']
                    if when:
                        minimum = when if minimum is None else min(minimum, when)
                        maximum = when if maximum is None else max(maximum, when)
                    if source['kind'] != 'sale':
                        continue
                    if not when or when > as_of or sale_owner(source['sido_code'], when, replace_2026) != 'molit_csv':
                        rejected['outside_owned_scope'] += 1
                        continue
                    converted, reason = dashboard_row(record)
                    if converted is None:
                        rejected[reason] += 1
                    else:
                        writer.trade(converted)
                        local['dashboard_rows'] += 1
                summaries.append({**source, 'counts': dict(local), 'quality_flags': dict(flags),
                                  'address_matches': dict(matches), 'dashboard_exclusions': dict(rejected),
                                  'contract_type_counts': dict(contract_types),
                                  'unresolved_address_examples': address_examples,
                                  'contract_min': minimum, 'contract_max': maximum})
                print(json.dumps({'source_id': source['source_id'], 'counts': dict(local),
                                  'address_matches': dict(matches)}, ensure_ascii=False), flush=True)
            for path, owner, commits in ((existing_history, 'existing_history', existing_history_commits),
                                         (existing_recent, 'existing_recent', existing_recent_commits)):
                if path:
                    if any(not re.fullmatch(r'[0-9a-f]{40}', commit) for commit in commits):
                        raise ValueError('Source commit provenance must be a full Git SHA')
                    existing.append({**merge_existing(path, owner, writer, as_of, replace_2026),
                                     'declared_source_commits': list(commits)})
        finally:
            writer.close()
        names = writer.outputs
        # Validate stream trailers/CRC before publishing a completion manifest.
        # A checksum of a truncated output alone does not prove valid content.
        for name in names:
            with gzip.open(stage / name, 'rb') as check:
                for _ in iter(lambda: check.read(1024 * 1024), b''):
                    pass
        if any((stage / name).stat().st_size >= 95 * 1024 * 1024 for name in names):
            raise ValueError('An output exceeds 95 MiB; lower PartitionWriter max_rows before publishing')
        counts['dashboard_rows'] = sum(s['counts'].get('dashboard_rows', 0) for s in summaries) + sum(s['counts'].get('written_rows', 0) for s in existing)
        manifest = {'schema_version': SCHEMA_VERSION, 'complete': True,
                    'complete_scope': 'supplied and verified sources only; not a claim of full historical coverage',
                    'as_of_contract_cutoff': as_of, 'generated_at': datetime.now(timezone.utc).isoformat(),
                    'counts': dict(counts), 'contract_type_counts': dict(all_contract_types),
                    'sources': summaries, 'existing_inputs': existing,
                    'replace_existing_2026': replace_2026, 'historical_publication_vintages': False,
                    'canonical_scope': 'new MOLIT CSV source rows, including unresolved addresses and all lease types',
                    'multiplicity': 'All source rows retained. Repeated files and overlapping source partitions rejected. No contract IDs invented.',
                    'row_number_policy': 'One-based nonblank data-record ordinal after the CSV header; source NO remains in raw.',
                    'ownership': {'2006_2020_Seoul': 'existing_history', '2006_2020_Gyeonggi_Incheon': 'molit_csv',
                                  '2021_2025_capital': 'existing_recent', '2026_capital': 'molit_csv' if replace_2026 else 'existing_recent'},
                    'address_policy': 'Unique exact project-registry prefix only; source address is not a verified contract-time address or property ID.',
                    'partition_max_rows': writer.max_rows,
                    'normalizer_sha256': IMPLEMENTATION_SHA256,
                    'registry_entries': registry_entries(),
                    'outputs': {name: {'sha256': file_sha256(stage / name), 'bytes': (stage / name).stat().st_size,
                                       'rows': writer.output_rows[name]} for name in names}}
        (stage / 'normalization_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        # Hard links refuse pre-existing targets; publish the completion manifest last.
        for name in names + ['normalization_manifest.json']:
            (out / name).parent.mkdir(parents=True, exist_ok=True)
            os.link(stage / name, out / name)
        return manifest
    finally:
        shutil.rmtree(stage)


def discover_metadata(source_dir):
    manifest = Path(source_dir) / 'manifest.json'
    if manifest.is_file():
        return [manifest]
    paths = []
    for path in sorted(Path(source_dir).rglob('*.json')):
        value = json.loads(path.read_text(encoding='utf-8'))
        if isinstance(value, dict) and value.get('status') == 'complete' and value.get('kind') in {'sale', 'rent'}:
            paths.append(path)
    return paths


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-meta', type=Path, action='append', default=[])
    parser.add_argument('--source-dir', type=Path)
    parser.add_argument('--manifest', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--as-of', default='2026-09-09')
    parser.add_argument('--existing-history', type=Path)
    parser.add_argument('--existing-recent', type=Path)
    parser.add_argument('--existing-history-source-commit', action='append', default=[])
    parser.add_argument('--existing-recent-source-commit', action='append', default=[])
    args = parser.parse_args()
    paths = args.source_meta + ([args.manifest] if args.manifest else []) + (discover_metadata(args.source_dir) if args.source_dir else [])
    result = normalize_sources(paths, args.out, as_of=args.as_of,
                               existing_history=args.existing_history, existing_recent=args.existing_recent,
                               existing_history_commits=args.existing_history_source_commit,
                               existing_recent_commits=args.existing_recent_source_commit)
    print(json.dumps({'counts': result['counts'], 'replace_existing_2026': result['replace_existing_2026']}, ensure_ascii=False))


if __name__ == '__main__':
    main()
