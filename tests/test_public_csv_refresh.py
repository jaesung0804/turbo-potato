"""Verify CSV refresh safety against a restored complete canonical state."""
from collections import Counter
import csv
from datetime import date
import gzip
import io
import json

import pytest

import refresh_estate_public_csv as refresh
from collect_estate_transactions import read_partition, rows_bytes, digest
from collect_molit_capital_csv import PublicCSVClient, CollectionError
from estate_vintages import rows_as_observed
from get_molit_apt_trade_data import DASHBOARD_FIELDNAMES

CUTOFF = date(2026, 10, 7)
FIELDS = ['NO', '시군구', '번지', '본번', '부번', '단지명', '전용면적(㎡)',
          '계약년월', '계약일', '거래금액(만원)', '층', '건축년도', '도로명',
          '해제사유발생일', '거래유형', '중개사소재지']


@pytest.fixture
def baseline(tmp_path, monkeypatch):
    monkeypatch.setattr(refresh, 'today', lambda: CUTOFF)
    selected = {k: v for k, v in refresh.REGIONS.items() if k in {'11110', '28237', '41115'}}
    monkeypatch.setattr(refresh, 'REGIONS', selected)
    cache = tmp_path / 'data/molit_cache_v3'
    cache.mkdir(parents=True)
    rows = []
    for month in refresh.month_range('202606', '202609'):
        for code in sorted(selected):
            row = dict.fromkeys(DASHBOARD_FIELDNAMES, '')
            row.update(CGG_CD=code, CTRT_DAY=month+'01', THING_AMT='30000',
                       ARCH_AREA='59.9700', BLDG_USG='아파트', BLDG_NM='old')
            rows.append(row)
            part = {'complete': True, 'normalizer_version': 2, 'month': month, 'code': code,
                    'rows': [row], 'count': 1, 'rows_sha256': digest(rows_bytes([row])),
                    'fetched_at': '2026-09-11T00:00:00+00:00'}
            (cache / f'{month}-{code}.json.gz').write_bytes(gzip.compress(json.dumps(part).encode()))
    source = tmp_path / 'data/capital_area_apt_trade_transactions.csv'
    with source.open('w', encoding='utf-8-sig', newline='') as stream:
        writer = csv.DictWriter(stream, fieldnames=DASHBOARD_FIELDNAMES)
        writer.writeheader(); writer.writerows(rows)
    meta = {'complete': True, 'normalizer_version': 2, 'start': '202606', 'end': '202609',
            'rows': len(rows), 'partition_count': 12, 'region_count': 3,
            'fetched_at': '2026-09-11T00:00:00+00:00', 'sha256': digest(source.read_bytes())}
    source.with_suffix('.manifest.json').write_text(json.dumps(meta))
    return tmp_path, source, meta


class FakeClient:
    def __init__(self, malformed=False, fail_province=None):
        self.sido_codes = {'seoul': '11000', 'gyeonggi': '41000', 'incheon': '28000'}
        self.malformed = malformed
        self.fail_province = fail_province
        self.downloads = []

    def initialize(self):
        pass

    def payload(self, fields):
        region = next(r for r in refresh.REGIONS.values() if r.sido == fields['sidoNm'])
        months = refresh.month_range(fields['srhFromDt'].replace('-', '')[:6], fields['srhToDt'].replace('-', '')[:6])
        records = []
        for month in months:
            base = {'NO': '1', '시군구': region.sido+' '+region.sgg+' 시험동',
                    '번지': '123', '본번': '0123', '부번': '0000', '단지명': '시험',
                    '전용면적(㎡)': '59.9700', '계약년월': month, '계약일': '01',
                    '거래금액(만원)': '40,000', '층': '3', '건축년도': '2000',
                    '도로명': '시험로', '해제사유발생일': '-', '거래유형': '중개거래',
                    '중개사소재지': region.sgg}
            if self.malformed:
                base['시군구'] = region.sido+' 없는구 시험동'
            records.extend([base, dict(base), {**base, '해제사유발생일': '26.10.02'},
                            {**base, '거래유형': '직거래'}])
        stream = io.StringIO(newline='')
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader(); writer.writerows(records)
        return stream.getvalue().encode('cp949'), len(records)

    def count(self, fields):
        return self.payload(fields)[1]

    def download(self, fields):
        if fields['sidoNm'] == self.fail_province:
            raise CollectionError('Official service unavailable')
        self.downloads.append(dict(fields))
        return self.payload(fields)[0], {'content-type': 'text/csv'}


def test_refresh_preserves_duplicate_cancelled_direct_rows_and_older_history(baseline):
    root, source, old = baseline
    _, selected, audit, requests = refresh.refresh_plan(old['start'], CUTOFF)
    untouched = next(m for m in refresh.month_range('202606', '202609') if m not in selected)
    prior = (root / f'data/molit_cache_v3/{untouched}-11110.json.gz').read_bytes()
    client = FakeClient()
    meta = refresh.run(root, root / 'exports', CUTOFF, client)
    assert meta['end'] == '202610' and meta['partition_count'] == 15
    assert meta['historical_audit_month'] == audit
    assert len(client.downloads) == len(requests) <= 6
    assert (root / f'data/molit_cache_v3/{untouched}-11110.json.gz').read_bytes() == prior
    data = read_partition(root / 'data/molit_cache_v3/202608-11110.json.gz', '202608', '11110')
    assert data['count'] == 4
    assert Counter(row['DCLR_SE'] for row in data['rows']) == {'중개거래': 3, '직거래': 1}
    assert sum(bool(row['RTRCN_DAY']) for row in data['rows']) == 1
    assert data['rows'][0] == data['rows'][1]
    assert data['rows'][0]['ARCH_AREA'] == '59.9700'
    assert rows_as_observed(data['observation_ledger'], old['fetched_at'])[0]['BLDG_NM'] == 'old'
    assert meta['sha256'] == digest(source.read_bytes())
    assert refresh.verify_baseline(root)['rows'] == meta['rows']


@pytest.mark.parametrize('client', [FakeClient(malformed=True), FakeClient(fail_province='인천광역시')])
def test_incomplete_or_unresolved_export_never_replaces_inputs(baseline, client):
    root, source, old = baseline
    cache = root / 'data/molit_cache_v3'
    before = {p.name: p.read_bytes() for p in cache.iterdir()}
    with pytest.raises((ValueError, CollectionError)):
        refresh.run(root, root / 'exports', CUTOFF, client)
    assert digest(source.read_bytes()) == old['sha256']
    assert {p.name: p.read_bytes() for p in cache.iterdir()} == before


def test_corrupt_restored_partition_stops_before_network(baseline):
    root, source, old = baseline
    (root / 'data/molit_cache_v3/202606-11110.json.gz').write_bytes(b'corrupt')
    client = FakeClient()
    with pytest.raises((OSError, ValueError)):
        refresh.run(root, root / 'exports', CUTOFF, client)
    assert not client.downloads
    assert digest(source.read_bytes()) == old['sha256']


def test_plan_stays_within_recent_three_months_and_one_rotation():
    for cutoff in (date(2026, 1, 7), date(2026, 10, 7)):
        months, selected, audit, requests = refresh.refresh_plan('202101', cutoff)
        assert set(selected) == set(months[-3:]+[audit])
        assert len(requests) <= 9
        assert all(r.start.year == r.end.year and r.end <= cutoff for r in requests)
