import numpy as np
import pandas as pd
from collect_seoul_lease_files import normalize
from analyze_estate_market_extension import index_features,lease_snapshot
from estate_kb import period_rows


def leases():
    return pd.DataFrame({'key':['a']*6,'date':pd.to_datetime(['2024-01-01','2024-01-01','2024-01-05','2024-01-06','2024-02-20','2024-01-10']),
        'floor':[5,5,6,7,8,9],'deposit':[100,100,120,140,9999,9999],
        'monthly_rent':[0]*6,'contract_type':['신규']*6,'receipt_year':[2024]*5+[2025],
        'source_file_year':[2024]*6,'log_rent':np.log([100,100,120,140,9999,9999])})


def test_no_future_or_later_receipt_lease_leaks_and_repeat_rows_do_not_add_evidence():
    d=leases()
    result=lease_snapshot(d,'2024-03-01').iloc[0]
    assert result.lease_new_n90==3
    assert np.isclose(result.lease_log90,np.log(120))
    assert lease_snapshot(d,'2024-03-01',unique=False).iloc[0].lease_new_n90==4
    assert lease_snapshot(d,'2024-03-01',availability='annual_file').empty
    # Only two distinct public signatures cannot establish a new-lease median.
    assert np.isnan(lease_snapshot(d.iloc[:3],'2024-03-01').iloc[0].lease_log90)


def test_kb_period_lag_and_missing_month_are_explicit():
    f=pd.DataFrame({'gu':['11740'],'month':['2024-04']})
    records=[{'gu':'11740','period':p,'kind':k,'value':v} for k in ['sale','rent']
        for p,v in [('2023-12',90),('2024-01',100),('2024-02',110),('2024-03',9000)]]
    d=pd.DataFrame(records)
    assert np.isclose(index_features(f,d,2).kb_sale_1m.iloc[0],np.log(1.1))
    d=d[~d.period.eq('2024-01')]
    assert np.isnan(index_features(f,d,2).kb_sale_1m.iloc[0])


def test_workbook_year_markers_and_months():
    values=list(period_rows([["'99.12",1],[2000.1,2],[2,3]],0))
    assert [str(p) for p,_ in values]==['1999-12','2000-01','2000-02']


def test_receipt_year_is_not_contract_year_and_monthly_rent_is_excluded():
    row={'건물용도':'아파트','계약일':'20231215','임대면적':'59.6400','보증금(만원)':'40,000',
         '임대료(만원)':'0','층':'5','전월세구분':'전세','본번':'0509','부번':'0000',
         '자치구명':'강동구','법정동명':'암사동','건물명':'선사현대아파트','자치구코드':'11740',
         '신규계약구분':'신규','접수년도':'2024'}
    raw=pd.DataFrame([row,{**row,'임대료(만원)':'50'}])
    d=normalize(raw)
    assert len(d)==1
    assert d.iloc[0].key=='강동구 암사동 509 선사현대아파트 | 59.64㎡'
    assert d.iloc[0].receipt_year==2024 and d.iloc[0].date.year==2023
