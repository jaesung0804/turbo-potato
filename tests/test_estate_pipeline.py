import csv
import gzip
import hashlib
import json
import threading
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

import build_real_estate_dashboard_data as summary_builder
import collect_estate_transactions as collector
import estate_model as model
import estate_model_state as model_state
import raw_estate_state as raw_state
from build_public_site import apply_amenities
from dashboard_bundle import build_bundle


def trade(**changes):
    return {'CGG_CD': '11110', 'CGG_NM': '종로구', 'STDG_CD': '10100', 'STDG_NM': '청운동',
            'MNO': '1', 'SNO': '0', 'BLDG_NM': '같은이름', 'BLDG_USG': '아파트',
            'ARCH_AREA': '84.91', 'ARCH_YR': '2001', 'THING_AMT': '100000',
            'CTRT_DAY': '20250110', 'DCLR_SE': '중개거래', 'RTRCN_DAY': '', **changes}


def make_summary(tmp_path, rows):
    source = tmp_path/'trades.csv'; output = tmp_path/'summary.json'
    fields = sorted(set().union(*(r.keys() for r in rows)))
    with source.open('w', newline='', encoding='utf-8-sig') as stream:
        writer = csv.DictWriter(stream, fieldnames=fields); writer.writeheader(); writer.writerows(rows)
    summary_builder.build_dashboard_data(source, output, {'아파트'}, 1, 0, False)
    return json.loads(output.read_text())


def test_all_results_survive_old_limit_and_identical_names(tmp_path):
    rows = [trade(MNO=str(i), ARCH_AREA='84.91') for i in range(120)]
    rows += [trade(MNO='1', ARCH_AREA='84.92'), trade(MNO='1', ARCH_AREA='84.91')]
    s = make_summary(tmp_path, rows)
    b = s['regions'][0]['all']
    assert len(b['addresses']) == 121
    assert b['count'] == sum(a['count'] for a in b['addresses']) == 122
    assert len({a['complex_key'] for a in b['addresses']}) == 120
    assert s['data_through'] == '2025-01-10'


@pytest.mark.parametrize('changes', [dict(THING_AMT='NaN'), dict(THING_AMT='inf'), dict(THING_AMT='-1'),
    dict(ARCH_AREA='0'), dict(CTRT_DAY='20250230'), dict(CTRT_DAY='', RCPT_YR='2025'),
    dict(RTRCN_DAY='cancelled'), dict(DCLR_SE='직거래')])
def test_invalid_or_excluded_trade_does_not_distort_summary(tmp_path, changes):
    s = make_summary(tmp_path, [trade(), trade(**changes)])
    assert s['used_rows'] == 1


def test_unknown_school_is_not_false_and_dash_is_not_cancellation(tmp_path):
    s = make_summary(tmp_path, [trade(RTRCN_DAY='-')])
    assert s['used_rows'] == 1
    assert s['regions'][0]['all']['addresses'][0]['elementary_500m'] is None


def test_no_built_year_propagation_between_different_lots(tmp_path):
    s = make_summary(tmp_path, [trade(MNO='1'), trade(MNO='2', ARCH_YR='')])
    assert sorted(a['built_year'] or 0 for a in s['regions'][0]['all']['addresses']) == [0, 2001]


def api_page(total, days, cancel=False):
    root = ET.Element('response'); ET.SubElement(root, 'resultCode').text = '000'
    ET.SubElement(root, 'totalCount').text = str(total)
    for day in days:
        item = ET.SubElement(root, 'item')
        for k, v in {'dealYear': '2025', 'dealMonth': '1', 'dealDay': str(day), 'dealAmount': '100,000',
                     'excluUseAr': '84.91', 'aptNm': '같은이름', 'umdNm': '청운동', 'jibun': '1',
                     'buildYear': '2001', 'cancelDealType': 'Y' if cancel else '', 'cancelDealDate': '-'}.items():
            ET.SubElement(item, k).text = v
    return root


def test_actual_server_page_size_and_cancellation_flag(monkeypatch):
    pages = [api_page(3, [1, 2]), api_page(3, [3], cancel=True)]
    monkeypatch.setattr(collector, 'request', lambda *args: pages[args[3]-1])
    rows = collector.fetch_partition('key', collector.REGIONS['11110'], '202501', threading.Event())
    assert len(rows) == 3 and rows[-1]['RTRCN_DAY'] == 'cancelled'


@pytest.mark.parametrize('second', [api_page(3, [1, 2]), api_page(4, [3]), api_page(3, [])])
def test_repeated_changed_or_short_pages_fail_closed(monkeypatch, second):
    pages = [api_page(3, [1, 2]), second]
    monkeypatch.setattr(collector, 'request', lambda *args: pages[args[3]-1])
    with pytest.raises(RuntimeError):
        collector.fetch_partition('key', collector.REGIONS['11110'], '202501', threading.Event())


def test_checkpoint_checksum_and_identity(tmp_path):
    rows = [trade()]; body = {'complete': True, 'month': '202501', 'code': '11110', 'count': 1,
        'rows': rows, 'rows_sha256': collector.digest(collector.rows_bytes(rows))}
    path = tmp_path/'202501-11110.json.gz'; path.write_bytes(gzip.compress(json.dumps(body).encode()))
    assert collector.read_partition(path, '202501', '11110')['count'] == 1
    with pytest.raises(ValueError): collector.read_partition(path, '202501', '99999')
    body['rows'][0]['THING_AMT'] = '1'; path.write_bytes(gzip.compress(json.dumps(body).encode()))
    with pytest.raises(ValueError): collector.read_partition(path, '202501', '11110')


def test_registry_has_every_current_district_once():
    assert len(collector.REGIONS) == 83
    assert not collector.OBSOLETE.intersection(collector.REGIONS)
    assert sum(r.code.startswith('11') for r in collector.REGIONS.values()) == 25


def test_raw_state_roundtrip_and_corruption_preserves_existing(tmp_path):
    source = tmp_path/'source'; (source/'data/molit_cache_v3').mkdir(parents=True)
    body = b'column\nvalue\n'; raw = source/'data/capital_area_apt_trade_transactions.csv'; raw.write_bytes(body)
    raw.with_suffix('.manifest.json').write_text(json.dumps({'complete': True, 'rows': 1, 'sha256': hashlib.sha256(body).hexdigest()}))
    state = tmp_path/'state'; raw_state.pack(state, source)
    restored = tmp_path/'restored'; raw_state.restore(state, restored)
    assert (restored/'data/capital_area_apt_trade_transactions.csv').read_bytes() == body
    next(state.glob('objects/*.part')).write_bytes(b'corrupt')
    with pytest.raises(ValueError): raw_state.restore(state, restored)
    assert (restored/'data/capital_area_apt_trade_transactions.csv').read_bytes() == body


def test_model_state_refuses_replacement_before_writing(tmp_path):
    root = tmp_path/'root'; path = root/'models/estate-reference-v3/2026-09.joblib'
    path.parent.mkdir(parents=True); path.write_bytes(b'frozen')
    state = tmp_path/'state'; model_state.pack(state, root)
    before = (state/'model_manifest.json').read_bytes(); path.write_bytes(b'changed')
    with pytest.raises(ValueError): model_state.pack(state, root)
    assert (state/'model_manifest.json').read_bytes() == before
    assert (state/'models/estate-reference-v3/2026-09.joblib').read_bytes() == b'frozen'


def annual_fixture():
    regions=[]
    for index, sido in enumerate(['서울특별시', '경기도', '인천광역시']):
        region={'code': str(index), 'sido_name': sido, 'gu_name': '구'+str(index), 'gu_code': str(index), 'dong_name': '동', 'years': {}}
        for year in range(2021, 2027):
            addresses=[]
            for i in range(60):
                value=(1800+index*500+i*12)*(1+.04*(year-2021))
                metrics={name:{s:val for s in ['avg','median','min','max']} for name,val in
                         [('price_per_pyeong',value),('area_pyeong',25+i%5),('price_billion',value*(25+i%5)/10000)]}
                addresses.append({'key': f'lot-{i} | 84㎡', 'complex_key': f'lot-{i}', 'building_name': f'단지{i}',
                    'area_type': '84㎡', 'count': 1 if i==0 else 5, 'built_year': 2000+i%10, 'metrics': metrics})
            region['years'][str(year)]={'count': sum(a['count'] for a in addresses), 'metrics': addresses[0]['metrics'], 'addresses':addresses}
        region['all']=region['years']['2026'];regions.append(region)
    return {'regions':regions,'generated_at':'2026-09-06','data_through':'2026-09-04','source':'fixture',
            'years':[str(y) for y in range(2021,2027)]}


def test_prior_only_features_and_historical_age():
    s=annual_fixture(); a,_=model.dataset(s)
    for r in s['regions']:
        for b in r['years']['2026']['addresses']:
            b['metrics']['price_per_pyeong']['median'] *= 10
    b,_=model.dataset(s)
    assert a[model.NUMERIC+model.CATEGORICAL].equals(b[model.NUMERIC+model.CATEGORICAL])
    assert a.loc[a.year==2021, 'age'].min() == 12


def test_month_is_immutable_and_validation_excludes_partial_year(tmp_path):
    summary=tmp_path/'summary.json';summary.write_text(json.dumps(annual_fixture()))
    result=model.run(summary,tmp_path/'out.json',tmp_path/'models','2026-09')
    artifact=tmp_path/'models'/model.VERSION/'2026-09.joblib'; original=artifact.read_bytes()
    assert [f['test_year'] for f in result['validation']] == [2023,2024,2025]
    assert len(result['recommendations']) == 180
    assert all(10 <= x['house_match_score'] <= 90 for x in result['recommendations'])
    assert all(x['reference_low'] <= x['fair_price_per_pyeong'] <= x['reference_high'] for x in result['recommendations'])
    assert any('거래 표본 3건 미만' in x['quality_flags'] for x in result['recommendations'])
    assert all(x['expected_growth_pct'] is None for x in result['recommendations'])
    again=model.run(summary,tmp_path/'out.json',tmp_path/'models','2026-09','infer')
    assert artifact.read_bytes() == original and again == result
    with pytest.raises(FileExistsError): model.run(summary,tmp_path/'out.json',tmp_path/'models','2026-09','train')


def test_bundle_preserves_all_source_counts_and_stat_values(tmp_path):
    s=annual_fixture(); m=build_bundle(s,{'recommendations':[]},tmp_path/'data/bundle',{'type':'FeatureCollection','features':[]})
    assert all(x['complete'] for x in m['coverage'].values())
    assert m['coverage']['2026']['available_types'] == 180
    asset=m['periods']['2026'];body=(tmp_path/asset['url']).read_bytes()
    rows=json.loads(gzip.decompress(body))
    assert sum(r[1] for r in rows)==sum(a[1] for r in rows for a in r[3])==m['coverage']['2026']['source_trades']
    assert hashlib.sha256(body).hexdigest()==asset['sha256']
    assert rows[0][3][0][2][8]==s['regions'][0]['years']['2026']['addresses'][0]['metrics']['price_per_pyeong']['avg']


def test_legacy_amenities_do_not_cross_same_name_lots(tmp_path):
    s=make_summary(tmp_path,[trade(MNO='1'),trade(MNO='2')])
    path=tmp_path/'metadata.gz';path.write_bytes(gzip.compress(json.dumps({'snapshot_built_at':'2026-06-17',
       'items':{'서울특별시|종로구|청운동|같은이름':{'households':900,'built_year':2001}}}).encode()))
    assert apply_amenities(s,path)['matched_types']==0
    assert all(a['households'] is None for a in s['regions'][0]['all']['addresses'])
