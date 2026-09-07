import pandas as pd
import pytest
from estate_market_features import latest_index_vintage,lease_features,asking_features

def test_later_index_revision_cannot_enter_old_prediction():
    d=pd.DataFrame([{'series_id':'official-sale','period':'2024-01','available_at':'2024-02-15','value':100},{'series_id':'official-sale','period':'2024-01','available_at':'2024-03-15','value':110}])
    assert latest_index_vintage(d,'2024-02-20').value.tolist()==[100]
    with pytest.raises(ValueError,match='available_at'):latest_index_vintage(d.drop(columns='available_at'),'2024-02-20')

def test_renewals_and_unavailable_rents_do_not_inflate_market_jeonse_ratio():
    leases=pd.DataFrame([{'key':'A','contract_date':'2024-01-01','available_at':'2024-01-10','monthly_rent':0,'deposit':50,'contract_type':'신규'}]*3+[{'key':'A','contract_date':'2024-01-01','available_at':'2024-01-10','monthly_rent':0,'deposit':90,'contract_type':'갱신'}]*10)
    sales=pd.DataFrame([{'key':'A','contract_date':'2024-01-01','available_at':'2024-01-10','price':100,'cancelled':False}]*3)
    assert lease_features(leases,sales,'A','2024-02-01')['new_jeonse_ratio']==.5
    assert lease_features(leases,sales,'A','2024-01-05')['new_jeonse_ratio'] is None

def test_listing_mix_change_is_not_same_listing_price_growth():
    a=[{'key':'A','listing_id':str(i),'available_at':'2024-01-01','status':'active','price':100} for i in range(3)]
    b=[{**r,'available_at':'2024-02-01','price':95} for r in a]
    c=[{'key':'A','listing_id':'new','available_at':'2024-02-01','status':'active','price':1000}]
    out=asking_features(pd.DataFrame(a+b+c),'A','2024-02-01',100)
    assert out['matched_28d_change']==pytest.approx(-.05)
    assert out['price_cut_share']==1
