import numpy as np
import pandas as pd
import pytest

from estate_nowcast import build_features, history_features
from estate_retraining_features import array_history, monthly_features


def test_array_reproduction_preserves_cutoff_outliers_and_missing_floors():
    origin=int(np.datetime64('2026-06-01','D').astype(int))
    days=np.array([origin-700,origin-396,origin-121,origin-61,origin-31,origin-30,origin+4])
    p=np.log([50.,60.,90.,100.,80.,9999.,1.])
    floors=np.array([1.,np.nan,3.,12.,8.,20.,np.nan])
    f=pd.DataFrame({'day':days,'log_price':p,'floor':floors,
                    'year':pd.to_datetime(days,unit='D').year})
    expected=history_features(f,origin,31)
    actual=array_history((days,p,floors),origin,2026,31)
    for key in expected: assert actual[key]==pytest.approx(expected[key],nan_ok=True),key


def test_historical_policy_excludes_old_law_contract_not_yet_observable():
    origin=int(np.datetime64('2020-04-01','D').astype(int))
    days=np.array([int(np.datetime64(x,'D').astype(int)) for x in ['2020-01-01','2020-02-20','2020-02-25']])
    values=(days,np.log([100.,9999.,110.]),np.array([10.,10.,10.]))
    f=array_history(values,origin,2020,31,True)
    assert f['n90']==2
    assert f['last']==pytest.approx(np.log(110.))


def test_monthly_reproduction_matches_reference_for_peers_and_exact_types():
    records=[]
    for key,area,complex_name,prices in [('a',50.,'A',[100.,90.,120.]),('b',55.,'A',[110.,105.,130.]),('c',52.,'B',[90.,100.,110.])]:
        for date,price,floor in zip(['2025-09-01','2026-03-01','2026-06-15'],prices,[3.,8.,12.]):
            t=pd.Timestamp(date)
            records.append({'key':key,'complex':complex_name,'peer':'gu:3','gu':'gu',
              'region':'서울특별시','area':area,'built':2000.,'floor':floor,'date':t,
              'day':int(t.to_datetime64().astype('datetime64[D]').astype(int)),
              'year':t.year,'month':str(t.to_period('M')),'log_price':np.log(price),
              'price_oku':price*area/10000})
    d=pd.DataFrame(records)
    expected=build_features(d,31,'2026-06','2026-06').sort_values('key').reset_index(drop=True)
    actual=monthly_features(d,'2026-06','2026-06',policy=False,uniform_lag=31).sort_values('key').reset_index(drop=True)
    for col in expected.columns:
        if col in ('hist_floor',): continue
        if pd.api.types.is_numeric_dtype(expected[col]):
            np.testing.assert_allclose(actual[col],expected[col],rtol=1e-12,atol=1e-12,equal_nan=True,err_msg=col)
        else: assert actual[col].tolist()==expected[col].tolist(),col


def test_release_reproduction_preserves_same_day_transaction_order():
    rng = np.random.default_rng(91)
    dates = pd.to_datetime(['2026-01-01']*20+['2026-04-30']*20+['2026-06-15']*10)
    order = rng.permutation(len(dates)); dates = dates[order]
    prices = rng.uniform(80, 120, len(dates))
    d = pd.DataFrame({'key':'a','complex':'A','peer':'g:5','region':'경기도',
        'gu':'g','area':84.,'built':2000.,'floor':rng.integers(1,25,len(dates)),
        'date':dates,'day':dates.values.astype('datetime64[D]').astype(int),
        'year':dates.year,'month':dates.to_period('M').astype(str),
        'log_price':np.log(prices),'price_oku':prices*84/10000})
    a = build_features(d,31,'2026-06','2026-06')
    b = monthly_features(d,'2026-06','2026-06',policy=False,uniform_lag=31,legacy_order=True)
    for col in a.select_dtypes('number'):
        if col != 'hist_floor':
            np.testing.assert_allclose(a[col], b[col], equal_nan=True, rtol=1e-12, atol=1e-12, err_msg=col)
