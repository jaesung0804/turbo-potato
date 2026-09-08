"""Same properties/floors, monthly updates versus 3/6-month frozen valuations.

Refreshes start in March, when the prior-year training labels have cleared the
assumed 31-day lag. This measures refresh cadence, not different return targets.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from estate_nowcast import load_transactions, history_features, HALF_LIVES, metric
from analyze_estate_market_extension import BASE, model, paired_gain


def frozen_features(d, target, origin):
    day=int(origin.to_datetime64().astype('datetime64[D]').astype(int))
    cutoff=day-31
    past=d[(d.day<=cutoff)&(d.day>cutoff-365)]
    pools={(col,n):past[past.day>cutoff-n].groupby(col).log_price.agg(['median','count'])
           for col in ['complex','peer'] for n in [90,365]}
    keys=set(target.key)
    groups={k:g.sort_values('day') for k,g in d[d.key.isin(keys)].groupby('key',sort=False)}
    records=[]
    for key,g in target.groupby('key',sort=False):
        meta=g.iloc[0]
        f=history_features(groups[key],day,31)
        for (col,n),pool in pools.items():
            identity=meta.complex if col=='complex' else meta.gu+':'+str(int(meta.area//15))
            row=pool.loc[identity] if identity in pool.index else {'median':np.nan,'count':0}
            f[f'{col}{n}'],f[f'{col}_n{n}']=row['median'],row['count']
        f['anchor']=next((f[c] for c in ['ew90','complex90','complex365','peer90','peer365'] if np.isfinite(f[c])),np.nan)
        for c in ['last','median90','median365','prior_annual']+[f'ew{h}' for h in HALF_LIVES]:
            if not np.isfinite(f[c]):f[c]=f['anchor']
        f.update({'key':key,'age':meta.age,'area':meta.area})
        records.append(f)
    snapshot=pd.DataFrame(records).set_index('key')
    x=target[['key','floor','low_floor']].join(snapshot,on='key',validate='many_to_one')
    x['floor_delta']=x.floor-x.hist_floor
    return x


def main():
    out=Path('.work/market-research/cadence');out.mkdir(exist_ok=True)
    f=pd.read_parquet('.work/market-research/ablation/features.parquet')
    p=pd.read_parquet('.work/market-research/ablation/predictions.parquet')
    test=p[(p.year>=2025)&p.gu.str.startswith('11')].copy().reset_index(drop=True)
    d,_=load_transactions('data/capital_area_apt_trade_transactions.csv')
    d=d[d.gu.str.startswith('11')]
    models={y:model(BASE,f[f.year<y]) for y in [2025,2026]}
    test['monthly']=test['base']
    for span in [3,6]:
        test[f'frozen{span}']=np.nan
        month=pd.to_datetime(test.month+'-01')
        origins=[pd.Timestamp(y,int(((m-3)//span)*span+3),1) for y,m in zip(month.dt.year,month.dt.month)]
        for origin,g in test.groupby(origins,sort=True):
            x=frozen_features(d,g,origin)
            values=x.anchor+models[origin.year].predict(x[BASE])
            test.loc[g.index,f'frozen{span}']=values.to_numpy()
            print('cadence',span,origin.date(),len(g),flush=True)
    results={'scope':'Seoul; same trades and floors; March-based refresh blocks',
        'metrics':{},'coverage':{'target_rows':len(test),'valid_all':int(test[['monthly','frozen3','frozen6']].notna().all(axis=1).sum())}}
    for year,g in test.groupby('year'):
        g=g[g[['monthly','frozen3','frozen6']].notna().all(axis=1)]
        results['metrics'][str(year)]={c:metric(g,g[c].to_numpy()) for c in ['monthly','frozen3','frozen6']}
    (out/'metrics.json').write_text(json.dumps(results,ensure_ascii=False,indent=2))
    print(json.dumps(results),flush=True)


if __name__=='__main__':main()
