import copy
import json

import numpy as np
import pytest

import estate_model as model
from test_estate_pipeline import annual_fixture


@pytest.mark.parametrize('siblings',[False,True])
def test_candidate_features_ignore_current_and_future_prices(siblings):
    summary=annual_fixture()
    before,payload=model.dataset(summary,enhanced=True,siblings=siblings)
    for region in summary['regions']:
        for year in ['2025','2026']:
            for building in region['years'][year]['addresses']:
                building['metrics']['price_per_pyeong']['median']*=10
    after,_=model.dataset(summary,enhanced=True,siblings=siblings)
    features=model.NUMERIC+model.EXTRA_NUMERIC+model.CATEGORICAL
    assert before.loc[before.year<=2025,features].equals(after.loc[after.year<=2025,features])
    assert len(payload)==1080


def test_last_exact_type_and_peer_area_fallback_do_not_depend_on_input_order():
    summary=annual_fixture()
    region=summary['regions'][0]
    region['years']['2025']['addresses']=region['years']['2025']['addresses'][1:]
    # A genuinely new type must use a previous-year pool rather than itself.
    new=copy.deepcopy(region['years']['2026']['addresses'][1])
    new.update(key='new lot | 84㎡',complex_key='new lot')
    new['metrics']['price_per_pyeong']['median']=100000
    region['years']['2026']['addresses'].append(new)
    frame,payload=model.dataset(summary,enhanced=True)
    found={p['building_key']:i for i,p in enumerate(payload) if p['year']=='2026' and p['region_code']=='0'}
    old=frame.loc[found['lot-0 | 84㎡']]
    assert np.isnan(old.prior_price) and old.history_gap==2
    assert old.reference_anchor==old.last_price
    newrow=frame.loc[found[new['key']]]
    assert np.isnan(newrow.last_price) and newrow.reference_anchor==newrow.matched_peer
    assert newrow.matched_peer_count>=5 and np.exp(newrow.reference_anchor)<4000
    # Reversing address order leaves all matched feature vectors identical.
    for r in summary['regions']:
        for bucket in r['years'].values():bucket['addresses'].reverse()
    reversed_frame,reversed_payload=model.dataset(summary,enhanced=True)
    columns=model.NUMERIC+model.EXTRA_NUMERIC+model.CATEGORICAL
    original={ (p['region_code'],p['year'],p['building_key']):i for i,p in enumerate(payload) }
    order=[original[(p['region_code'],p['year'],p['building_key'])] for p in reversed_payload]
    assert frame.iloc[order][columns].reset_index(drop=True).equals(reversed_frame[columns])


def test_three_year_history_expires_and_quantile_uses_finite_sample_rank():
    summary=annual_fixture()
    for year in ['2023','2024','2025']:
        summary['regions'][0]['years'][year]['addresses']=summary['regions'][0]['years'][year]['addresses'][1:]
    frame,payload=model.dataset(summary,enhanced=True)
    i=next(i for i,p in enumerate(payload) if p['region_code']=='0' and p['year']=='2026' and p['building_key']=='lot-0 | 84㎡')
    assert np.isnan(frame.loc[i,'last_price'])
    assert model.quantile(np.arange(1,11),exact=True)==9


def test_candidate_month_is_separate_and_inference_reuses_bytes(tmp_path):
    source=tmp_path/'summary.json';source.write_text(json.dumps(annual_fixture()),encoding='utf-8')
    legacy=model.run(source,tmp_path/'v3.json',tmp_path/'models','2026-09')
    old=tmp_path/'models'/model.VERSION/'2026-09.joblib';old_bytes=old.read_bytes()
    candidate=model.run(source,tmp_path/'v4.json',tmp_path/'models','2026-09',version=model.CANDIDATE_VERSION)
    saved=tmp_path/'models'/model.CANDIDATE_VERSION/'2026-09.joblib';candidate_bytes=saved.read_bytes()
    again=model.run(source,tmp_path/'v4.json',tmp_path/'models','2026-09','infer',model.CANDIDATE_VERSION)
    assert old.read_bytes()==old_bytes and saved.read_bytes()==candidate_bytes
    assert candidate==again and len(candidate['recommendations'])==len(legacy['recommendations'])
    assert all(f['test_year']<2026 for f in candidate['validation'])
    assert all(np.isfinite(r['house_match_score']) and r['reference_low']<=r['fair_price_per_pyeong']<=r['reference_high'] for r in candidate['recommendations'])
    for r in candidate['recommendations']:
        assert r['neutral_price_billion']>0 and r['score_error_scale']>=.05
        observed=r['price_per_pyeong']*r['area_pyeong']/10000
        reconstructed=50+40*np.tanh(np.log(r['neutral_price_billion']/observed)/r['score_error_scale'])*r['trade_count']/(r['trade_count']+5)
        assert round(float(reconstructed),1)==r['house_match_score']
    # Browser calculator data must survive the packed production bundle.
    import gzip
    from dashboard_bundle import build_bundle
    manifest=build_bundle(annual_fixture(),candidate,tmp_path/'site/data/bundle',{'type':'FeatureCollection','features':[]})
    packed=json.loads(gzip.decompress((tmp_path/'site'/manifest['recommendations']['url']).read_bytes()))
    values=dict(zip(packed['fields'],packed['rows'][0][2:]))
    assert values['neutral_price_billion']==candidate['recommendations'][0]['neutral_price_billion']
    assert values['score_error_scale']==candidate['recommendations'][0]['score_error_scale']
    with pytest.raises(FileExistsError):model.run(source,tmp_path/'v4.json',tmp_path/'models','2026-09','train',model.CANDIDATE_VERSION)


def test_calibration_uses_independent_complexes_and_sample_scale(monkeypatch):
    frame,_=model.dataset(annual_fixture(),enhanced=True)
    calls=[]
    original=model.predict
    def record(fitted,rows,weight):
        calls.append(set(rows.group))
        return original(fitted,rows,weight)
    monkeypatch.setattr(model,'predict',record)
    _,interval=model.tune(frame,2025)
    assert calls[0].isdisjoint(calls[-1])
    assert sum([interval['tuning_rows'],interval['calibration_rows']])==180
    assert interval['method'].startswith('sample-scaled')
    assert model.sample_scale([1])[0]>model.sample_scale([10])[0]


def test_json_output_has_portable_korean_text_and_line_endings(tmp_path):
    from estate_io import write_json
    path=tmp_path/'report.json';write_json(path,{'지역':'서울특별시','메모':'과거 검증'},indent=2)
    body=path.read_bytes()
    assert b'\r\n' not in body
    assert json.loads(body.decode('utf-8'))['지역']=='서울특별시'


def test_sibling_anchor_uses_only_earlier_qualified_same_complex_sizes():
    import pandas as pd
    def row(year,area,target,count=6,group='same lot',age=None):
        return {'year':year,'area':area,'target':np.log(target),'count':count,'group':group,
            'age':year-2021 if age is None else age,'prior_price':np.nan,'last_price':np.nan,
            'matched_peer':np.log(9000),'prior_peer':np.log(8500)}
    frame=pd.DataFrame([row(2025,18.132,4936),row(2026,15.031,5854),row(2026,18.132,5515),
                        row(2026,15.031,5854,group='other lot'),row(2026,5,8000),row(2029,15.031,5854)])
    payload=[{'building_key':key} for key in ['large','small','large','small','tiny','small']]
    result=model.sibling_history(frame,payload)
    assert np.exp(result.loc[1,'reference_anchor'])==pytest.approx(4936)
    assert result.loc[1,'sibling_count']==6 and result.loc[1,'sibling_gap']==1
    assert np.isnan(result.loc[0,'sibling_price']) and np.isnan(result.loc[3,'sibling_price'])
    assert np.isnan(result.loc[4,'sibling_price'])
    changed=frame.copy();changed.loc[changed.year>=2026,'target']+=10
    assert model.sibling_history(changed,payload).loc[1,'reference_anchor']==result.loc[1,'reference_anchor']
    sparse=frame.copy();sparse.loc[0,'count']=2
    assert np.isnan(model.sibling_history(sparse,payload).loc[1,'sibling_price'])
    reconstructed=frame.copy();reconstructed.loc[1,'age']=0
    assert np.isnan(model.sibling_history(reconstructed,payload).loc[1,'sibling_price'])
    exact=frame.copy();exact.loc[1,'prior_price']=np.log(5100)
    assert np.exp(model.sibling_history(exact,payload).loc[1,'reference_anchor'])==pytest.approx(5100)
    expired=frame.copy();expired.loc[5,'year']=2030;expired.loc[5,'age']=9
    assert np.isnan(model.sibling_history(expired,payload).loc[5,'sibling_price'])
    order=[5,4,3,2,1,0]
    shuffled=model.sibling_history(frame.iloc[order].reset_index(drop=True),[payload[i] for i in order])
    assert result.reference_anchor.iloc[order].reset_index(drop=True).equals(shuffled.reference_anchor)


def test_sibling_model_is_separate_reproducible_and_has_reference_provenance(tmp_path):
    source=tmp_path/'summary.json';source.write_text(json.dumps(annual_fixture()),encoding='utf-8')
    model.run(source,tmp_path/'v4.json',tmp_path/'models','2026-09',version=model.CANDIDATE_VERSION)
    old=tmp_path/'models'/model.CANDIDATE_VERSION/'2026-09.joblib';original=old.read_bytes()
    result=model.run(source,tmp_path/'v5.json',tmp_path/'models','2026-09',version=model.SIBLING_VERSION)
    saved=tmp_path/'models'/model.SIBLING_VERSION/'2026-09.joblib';frozen=saved.read_bytes()
    again=model.run(source,tmp_path/'v5.json',tmp_path/'models','2026-09','infer',model.SIBLING_VERSION)
    assert result==again and old.read_bytes()==original and saved.read_bytes()==frozen
    assert len(result['recommendations'])==180 and len(result['features_used'])==17
    assert all(r['reference_basis']=={'kind':'exact_prior','year':2025} for r in result['recommendations'])
