import csv
import gzip
import hashlib
import io
import json
from pathlib import Path

import pytest

import normalize_molit_capital_history as normalizer


FIXTURES = Path(__file__).parent / 'fixtures' / 'molit_capital_csv'


def source(tmp_path, kind='sale', mutate=None, rows=None):
    meta = json.loads((FIXTURES / f'{kind}.json').read_text(encoding='utf-8'))
    records = list(csv.reader(io.StringIO((FIXTURES / f'{kind}.csv').read_text(encoding='utf-8'))))
    if rows is not None:
        records = records[:16] + rows
    if mutate:
        mutate(meta, records)
    stream = io.StringIO(newline='')
    csv.writer(stream).writerows(records)
    body = stream.getvalue().encode('utf-8')
    meta['sha256'] = hashlib.sha256(body).hexdigest()
    meta['row_count'] = len(records) - meta['header_row']
    path = tmp_path / f'{kind}.json'
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding='utf-8')
    path.with_suffix('.csv.gz').write_bytes(gzip.compress(body, mtime=0))
    return path, normalizer.source_metadata(path)


def canonical_files(out):
    for path in sorted((out / 'canonical').rglob('*.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            yield from map(json.loads, stream)


def test_real_lease_preserves_monthly_rows_precision_unknown_type_and_snapshot_date(tmp_path):
    path, meta = source(tmp_path, 'rent')
    _, raw = next(normalizer.source_rows(meta))
    raw['전월세구분'], raw['보증금(만원)'], raw['월세금(만원)'] = '월세', '0', '85'
    record = normalizer.normalize_row(raw, meta, 1, normalizer.AddressRegistry())
    assert record['raw'] == raw
    assert record['area_sqm'] == '59.3350'
    assert record['deposit_10k_krw'] == '0' and record['monthly_rent_10k_krw'] == '85'
    assert record['is_pure_jeonse'] is False
    assert record['contract_type'] == '미상' and record['cancelled'] is None
    assert record['receipt_year'] is None
    assert record['provenance']['observed_at'].startswith('2026-09-09')
    assert record['provenance']['publication_at'] is None
    assert record['address']['historical_address'] is None
    # The real 2011 export already uses the newer Hwaseong district name.
    row3 = list(normalizer.source_rows(meta))[2][1]
    normalized = normalizer.normalize_row(row3, meta, 3, normalizer.AddressRegistry())
    assert normalized['address']['district_code'] == '41595'
    assert normalized['address']['historical_address'] is None
    assert normalized['is_pure_jeonse'] is True
    row3['전월세구분'] = '월세'
    normalized = normalizer.normalize_row(row3, meta, 3, normalizer.AddressRegistry())
    assert 'lease_type_financials_disagree' in normalized['quality_flags']
    row3['월세금(만원)'] = '-1'
    normalized = normalizer.normalize_row(row3, meta, 3, normalizer.AddressRegistry())
    assert normalized['monthly_rent_10k_krw'] is None and normalized['is_pure_jeonse'] is None
    output = normalizer.normalize_sources([path], tmp_path / 'lease_out')
    assert output['counts']['rent_pure_jeonse_rows'] == 4
    assert output['counts']['rent_monthly_rent_rows'] == 0
    assert output['contract_type_counts'] == {'신규': 0, '갱신': 0, '미상': 4}


def test_sale_adapter_retains_cancellation_direct_trade_and_unknowns(tmp_path):
    _, meta = source(tmp_path)
    _, raw = next(normalizer.source_rows(meta))
    registry = normalizer.AddressRegistry()
    record = normalizer.normalize_row(raw, meta, 1, registry)
    row, reason = normalizer.dashboard_row(record)
    assert reason is None and row['DCLR_SE'] == '' and row['RTRCN_DAY'] == ''
    assert row['ARCH_AREA'] == '84.7900' and row['THING_AMT'] == '31000'
    assert row['CGG_CD'] == '41115' and row['STDG_CD'] == ''
    raw['해제사유발생일'], raw['거래유형'] = '2026-09-01', '직거래'
    record = normalizer.normalize_row(raw, meta, 1, registry)
    row, reason = normalizer.dashboard_row(record)
    assert reason is None and row['RTRCN_DAY'] == '2026-09-01' and row['DCLR_SE'] == '직거래'


def test_unresolved_ambiguous_and_mountain_addresses_remain_canonical(tmp_path):
    _, meta = source(tmp_path)
    _, raw = next(normalizer.source_rows(meta))
    duplicate_registry = normalizer.AddressRegistry([('11111', '경기도', '수원시 팔달구'),
                                                     ('22222', '경기도', '수원시 팔달구')])
    rec = normalizer.normalize_row(raw, meta, 1, duplicate_registry)
    assert rec['address']['match'] == 'ambiguous'
    assert normalizer.dashboard_row(rec)[0] is None
    raw['시군구'] = '경기도 확인불가구 화서동'
    rec = normalizer.normalize_row(raw, meta, 1, normalizer.AddressRegistry())
    assert rec['raw']['시군구'] == raw['시군구'] and rec['address']['district_code'] is None
    raw['시군구'], raw['번지'] = '경기도 수원시 팔달구 화서동', '산646'
    rec = normalizer.normalize_row(raw, meta, 1, normalizer.AddressRegistry())
    assert 'lot_source_disagrees' in rec['quality_flags']
    assert normalizer.dashboard_row(rec)[0] is None
    raw['번지'] = '646-0'
    rec = normalizer.normalize_row(raw, meta, 1, normalizer.AddressRegistry())
    assert 'lot_source_disagrees' not in rec['quality_flags']
    assert normalizer.dashboard_row(rec)[0] is not None


def test_streamed_export_keeps_multiplicity_and_rejects_reingestion(tmp_path):
    records = list(csv.reader(io.StringIO((FIXTURES / 'sale.csv').read_text(encoding='utf-8'))))
    path, _ = source(tmp_path, rows=[records[16], records[16]])
    result = normalizer.normalize_sources([path], tmp_path / 'out')
    rows = list(canonical_files(tmp_path / 'out'))
    assert len(rows) == 2 and rows[0]['raw'] == rows[1]['raw']
    assert [r['provenance']['row_number'] for r in rows] == [1, 2]
    assert result['counts']['dashboard_rows'] == 2
    with pytest.raises(ValueError, match='Repeated'):
        normalizer.normalize_sources([path, path], tmp_path / 'duplicate')
    with pytest.raises(FileExistsError):
        normalizer.normalize_sources([path], tmp_path / 'out')


def test_checksum_and_row_count_fail_without_publishing(tmp_path):
    path, _ = source(tmp_path)
    meta = json.loads(path.read_text())
    meta['row_count'] += 1
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='row count'):
        normalizer.normalize_sources([path], tmp_path / 'out')
    assert not (tmp_path / 'out' / 'normalization_manifest.json').exists()
    assert not (tmp_path / 'out' / 'canonical').exists()
    meta['sha256'] = '0' * 64
    path.write_text(json.dumps(meta))
    with pytest.raises(ValueError, match='checksum'):
        normalizer.normalize_sources([path], tmp_path / 'bad_hash')


def test_merge_ownership_excludes_old_gwangmyeong_and_replaced_2026(tmp_path):
    assert normalizer.sale_owner('11', '2020-12-31') == 'existing_history'
    assert normalizer.sale_owner('41', '2020-12-31') == 'molit_csv'
    assert normalizer.sale_owner('28', '2021-01-01') == 'existing_recent'
    assert normalizer.sale_owner('11', '2026-09-09', True) == 'molit_csv'
    assert normalizer.sale_owner('11', '2026-09-09', False) == 'existing_recent'
    path = tmp_path / 'history.csv'
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=normalizer.DASHBOARD_FIELDNAMES)
        writer.writeheader()
        for code in ['11110', '41210']:
            row = dict.fromkeys(normalizer.DASHBOARD_FIELDNAMES, '')
            row.update(CGG_CD=code, CTRT_DAY='20201231')
            writer.writerow(row)
    writer = normalizer.PartitionWriter(tmp_path / 'parts')
    result = normalizer.merge_existing(path, 'existing_history', writer, '2026-09-09', False)
    writer.close()
    assert result['counts']['written_rows'] == 1
    assert result['counts']['outside_owned_scope_excluded'] == 1


def test_partial_2026_replacement_cannot_silently_remove_existing_rows():
    sources = [{'kind': 'sale', 'sido': '서울특별시', 'sido_code': '11',
                'start': '2026-01-01', 'end': '2026-09-09', 'sha256': 'a'}]
    with pytest.raises(ValueError, match='three complete'):
        normalizer.validate_sources(sources, '2026-09-09')
    for sido, code in [('경기도', '41'), ('인천광역시', '28')]:
        sources.append({**sources[0], 'sido': sido, 'sido_code': code, 'sha256': code})
    assert normalizer.validate_sources(sources, '2026-09-09')


def test_collector_manifest_and_bounded_output_parts(tmp_path):
    _, source_meta = source(tmp_path)
    m = source_meta['metadata']
    entry = {**m, 'query': {'kind': 'sale'}, 'request_fields': m['request'],
             'raw_sha256': m['sha256'], 'file': 'sale.csv.gz'}
    path = tmp_path / 'manifest.json'
    path.write_text(json.dumps({'status': 'complete', 'entries': {'test': entry}}))
    assert normalizer.discover_metadata(tmp_path) == [path]
    result = normalizer.normalize_sources([path], tmp_path / 'out')
    assert result['counts']['sale_canonical_rows'] == 4
    writer = normalizer.PartitionWriter(tmp_path / 'bounded', max_rows=2)
    for n in range(5):
        writer.write('canonical/sale/41/2006', {'n': n})
    writer.close()
    assert len(writer.outputs) == 3
    assert [len(gzip.open(tmp_path / 'bounded' / p, 'rt').readlines()) for p in writer.outputs] == [2, 2, 1]


def test_blank_csv_records_match_collector_count_contract(tmp_path):
    path, meta = source(tmp_path)
    body = gzip.decompress(path.with_suffix('.csv.gz').read_bytes()) + b'\r\n"",""\r\n'
    path.with_suffix('.csv.gz').write_bytes(gzip.compress(body, mtime=0))
    value = json.loads(path.read_text())
    value['sha256'] = hashlib.sha256(body).hexdigest()
    path.write_text(json.dumps(value))
    assert len(list(normalizer.source_rows(normalizer.source_metadata(path)))) == 4
