import math
import numpy as np
import pandas as pd
from estate_preference_features import brand, match_master, area_inventory, active_events, tree_nearest, address, mixed_use, parking_per_household, corridor_type, heating_type
import estate_model as model
from test_estate_model_candidate import annual_fixture


def test_brand_is_a_category_without_an_assumed_quality_rank():
    assert brand('삼성 래미안 2차') == 'raemian'
    assert brand('현대아이파크') == 'ipark'
    assert brand('압구정 현대13차') == 'hyundai_legacy'
    assert brand('롯데 캐슬') == 'lotte_castle'
    assert brand('e-편한세상') == 'elife'
    assert brand('별빛마을') == 'unidentified'
    assert brand('래미안 자이') == 'raemian+xi'


def test_mixed_use_requires_an_explicit_classification_not_a_building_name():
    assert mixed_use('주상복합') == 1
    assert mixed_use('도시형 생활주택(주상복합)') == 1
    assert mixed_use('아파트') == 0
    assert mixed_use('도시형 생활주택(아파트)') == 0
    for value in [None,'','미확인','타워팰리스','주상복합아파트 추정','연립주택']:
        assert np.isnan(mixed_use(value))


def test_parking_inventory_must_reconcile_and_zero_placeholder_is_unknown():
    row={'totprk_ecct':'600','grnd_prkg_ecct':'100','undgr_prkg_ecct':'500','nmhsh':'400'}
    assert parking_per_household(row)==math.log1p(1.5)
    for change in [{'totprk_ecct':'601'},{'nmhsh':'0'},{'undgr_prkg_ecct':''},
                   {'grnd_prkg_ecct':'-1','undgr_prkg_ecct':'601'},
                   {'totprk_ecct':'0','grnd_prkg_ecct':'0','undgr_prkg_ecct':'0'}]:
        assert np.isnan(parking_per_household({**row,**change}))


def test_structural_categories_are_not_quality_ranks():
    assert corridor_type('계단식')=='staircase'
    assert corridor_type('복도식')=='corridor'
    assert corridor_type('계단식, 복도식')==corridor_type('복도식,계단식')=='mixed'
    assert heating_type('지역난방, 열병합')=='district'
    assert heating_type('개별난방+기타')=='mixed'
    assert np.isnan(corridor_type('')) and np.isnan(heating_type('기타'))


def test_mixed_use_audit_separates_unknowns_and_detects_overvaluation():
    from compare_preference_models import mixed_use_segments
    test=pd.DataFrame({'is_mixed_use':[1,1,0,np.nan],'prior_price':[np.nan,1,1,1],'group':['a','a','b','c']})
    actual=np.array([100.,100.,100.,100.]);baseline=np.array([150.,150.,100.,100.])
    result=mixed_use_segments(test,actual,baseline,np.array([110.,110.,100.,100.]))
    assert result['mixed_use']['rows']==2 and result['mixed_use']['complexes']==1
    assert result['mixed_use']['mean_signed_error_price_per_pyeong']==10
    assert result['mixed_use']['overestimate_gt_20pct_rate']==0
    assert result['mixed_use']['delta_vs_v4']['delta_mae']==-40
    assert result['unknown']['rows']==1 and result['mixed_use_without_prior']['rows']==1


def test_lot_name_year_matching_rejects_ambiguous_complexes():
    region={'sido_name':'서울특별시'}
    b={'building_name':'같은이름','complex_key':'강남구 역삼동 1 같은이름','built_year':2000}
    row={'apt_nm':'같은이름','use_aprv_yr':'2000','apt_cd':'A','nmhsh':'500'}
    master={'서울특별시 강남구 역삼동 1':[row]}
    assert match_master(master,region,b)['nmhsh']=='500'
    assert match_master(master,region,{**b,'built_year':2010}) is None
    assert match_master(master,region,{**b,'complex_key':'강남구 역삼동 2 같은이름'}) is None
    master['서울특별시 강남구 역삼동 1'].append({**row,'apt_cd':'B'})
    assert match_master(master,region,b) is None


def test_master_address_spelling_is_normalized_without_merging_lots():
    assert address('경기도 성남분당구 백현동 545 백현마을')==address('경기도 성남시 분당구 백현동 545')
    assert address('서울특별시 강남구 역삼동 1-2번지 테스트') != address('서울특별시 강남구 역삼동 1-3')


def test_area_band_inventory_must_reconcile_and_is_not_exact_area():
    assert area_inventory(100,[10,50,30,10],84.91)=={'band_households':50,'band_share':.5}
    assert area_inventory(100,[10,50,30,10],84.92)==area_inventory(100,[10,50,30,10],84.91)
    assert area_inventory(100,[10,50,30,None],85) is None
    assert area_inventory(200,[10,50,30,10],85) is None
    assert area_inventory(100,[10,50,30,10],60)['band_households']==10


def test_station_events_do_not_turn_plans_into_operating_stations():
    def event(effective,known,status):
        return {'station_id':'A','effective_at':effective,'known_at':known,'status':status,'source_url':'https://example.org/official'}
    events=[event('2020-01-01','2020-01-01','planned'),event('2021-02-01','2021-02-01','construction'),
            event('2022-05-28','2022-05-24','operating')]
    assert active_events(events,'2021-01-01','operating')==[]
    assert len(active_events(events,'2021-03-01','construction'))==1
    assert len(active_events(events,'2023-01-01','operating'))==1
    assert active_events(events,'2023-01-01','construction')==[]
    assert active_events([event('2020-01-01','2026-01-01','operating')],'2025-01-01','operating')==[]


def test_geographic_distance_is_metres_and_preserves_missing():
    assert tree_nearest(np.array([[37.,127.]]),[(37.,127.)])[0]<.01
    assert 110000<tree_nearest(np.array([[37.,127.]]),[(38.,127.)])[0]<112000
    assert np.isnan(tree_nearest(np.array([[37.,127.]]),[])[0])


def test_experiment_schema_survives_time_slicing_without_changing_v4():
    base,_=model.dataset(annual_fixture(),enhanced=True)
    original=model.fit(base)
    candidate=base.copy();candidate['brand_name']=['a','b']*(len(base)//2)
    candidate['log_households']=math.log1p(500)
    candidate.attrs={'extra_numeric':['log_households'],'extra_categorical':['brand_name']}
    training=candidate[candidate.year<2025]
    fitted=model.fit(training)
    assert len(original['numeric_features'])==13 and len(original['categories'])==4
    assert 'brand_name' in fitted['categories'] and 'log_households' in fitted['numeric_features']
    assert np.isfinite(model.predict(fitted,candidate[candidate.year==2025],.5)).all()
    assert model.fit(base)['numeric_features']==original['numeric_features']


def test_kapt_v5_json_preserves_real_inventory_and_rejects_wrong_identity():
    import json,pytest
    from collect_apt_preference_metadata import decode
    item={'kaptCode':'A1','kaptdaCnt':100,'kaptMparea60':10,'kaptMparea85':50,'kaptMparea135':30,'kaptMparea136':10,'codeAptNm':'주상복합'}
    body=json.dumps({'response':{'header':{'resultCode':'00'},'body':{'item':item}}}).encode()
    result=decode(body,'A1')
    assert result['total_households']==100 and result['inventory_consistent']
    assert result['area_band_households']==[10,50,30,10]
    assert result['complex_type']=='주상복합' and result['is_mixed_use']==1
    with pytest.raises(ValueError):decode(body,'A2')


def test_official_station_parser_never_evaluates_scripts():
    import pytest
    from collect_metro_reference import parse_sheet
    assert parse_sheet('{result:"ok",list:[{LAT:"37",},]}')['list'][0]['LAT']=='37'
    with pytest.raises(Exception):parse_sheet('{result: process.exit(1)}')
