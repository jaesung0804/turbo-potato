"""Descriptive case horizons, not a successful investment backtest.

Exact area, floors 3–20, log-median prices and pre-origin eligible peers.
Historical entries are not executable asks. Availability assumes 31 days.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from estate_nowcast import load_transactions
from analyze_estate_potential import CASES


def analyze(d):
    d=d[d.floor.between(3,20)]
    meta=d.sort_values('date').groupby('key').first()[['complex','gu','area','STDG_NM']]
    rows=[]
    for origin in pd.to_datetime(['2024-01-01','2024-07-01']):
        cutoff=origin-pd.Timedelta(days=31)
        past=d[d.date.between(cutoff-pd.Timedelta(days=179),cutoff)].groupby('key').log_price.agg(['median','count'])
        b=meta.join(past)
        b=b[b['count']>=3].copy()
        b['peer']=b.gu+':'+(b.area//15).astype(int).astype(str)
        for lo,hi in [(6,12),(12,18),(18,24)]:
            start=origin+pd.DateOffset(months=lo)
            end=origin+pd.DateOffset(months=hi)
            future=d[d.date.between(start,end)].groupby('key').log_price.agg(['median','count'])
            b['exit']=future['median']
            b['exit_n']=future['count']
            b['growth']=b['exit']-b['median']
            b.loc[b.exit_n<3,'growth']=np.nan
            for _,r in b[b.complex.isin(CASES)].iterrows():
                peers=b[(b.peer==r.peer)&(b.complex!=r.complex)&b.growth.notna()]
                benchmark=peers.growth.median() if peers.complex.nunique()>=5 else np.nan
                rows.append({'origin':str(origin.date()),'exit_window':[str(start.date()),str(end.date())],
                    'complex':r.complex,'area':r.area,'entry_n':r['count'],'exit_n':r.exit_n,
                    'entry_oku':np.exp(r['median'])*r.area/10000,
                    'exit_oku':np.exp(r['exit'])*r.area/10000,
                    'growth_pct':np.expm1(r.growth)*100,'peer_complexes':peers.complex.nunique(),
                    'excess_pct':np.expm1(r.growth-benchmark)*100})
    return json.loads(pd.DataFrame(rows).to_json(orient='records',force_ascii=False))


if __name__=='__main__':
    d,_=load_transactions('data/capital_area_apt_trade_transactions.csv')
    summary=analyze(d)
    Path('reports/estate_case_horizons.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2),encoding='utf-8')
    print(pd.DataFrame(summary).to_string(index=False))
