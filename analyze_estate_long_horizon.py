"""Longer-history, longer-horizon research with mature labels and fixed candidates."""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from estate_nowcast import load_transactions
from analyze_estate_potential import CASES, COLS
from analyze_estate_market_extension import KB, LEASE, index_features, lease_snapshot, SIGNATURE

OUT=Path('.work/market-research/potential')


def inputs():
    OUT.mkdir(exist_ok=True)
    p=OUT/'sales.parquet'
    if p.exists():return pd.read_parquet(p)
    current,q=load_transactions('data/capital_area_apt_trade_transactions.csv')
    older,h=load_transactions('.work/history/transactions.csv')
    current=current[current.gu.str.startswith('11')|current.gu.eq('41210')]
    if older.date.max()>=current.date.min():raise ValueError('Sales source periods overlap')
    d=pd.concat([older,current],ignore_index=True)
    d.to_parquet(p,index=False)
    (OUT/'sources.json').write_text(json.dumps({'current':q,'older':h},ensure_ascii=False,indent=2))
    return d


def snapshots(d,leases,indices,window,origins=None,inference_only=False):
    d=d[d.floor.between(3,20)].copy()
    meta=d.sort_values('day').groupby('key').first()[['complex','gu','region','area','built','STDG_NM','BLDG_NM']]
    outputs=[]
    if origins is None:origins=pd.date_range('2017-01-01','2025-07-01',freq='2QS')
    for origin in origins:
        cutoff=origin-pd.Timedelta(days=31)
        def agg(start,end):
            return d[d.date.between(start,end)].groupby('key').log_price.agg(['median','count'])
        b=agg(cutoff-pd.Timedelta(days=window-1),cutoff)
        f=meta.join(b.rename(columns={'median':'entry','count':'entry_n'}),how='inner')
        f=f[f.entry_n>=3].copy()
        recent=agg(cutoff-pd.Timedelta(days=89),cutoff)
        half=agg(cutoff-pd.Timedelta(days=179),cutoff)
        prior=agg(cutoff-pd.Timedelta(days=359),cutoff-pd.Timedelta(days=180))
        counts=agg(cutoff-pd.Timedelta(days=364),cutoff)
        f['n90']=recent['count'].reindex(f.index).fillna(0)
        f['n180']=half['count'].reindex(f.index).fillna(0)
        f['n365']=counts['count'].reindex(f.index).fillna(0)
        f['age']=origin.year-f.built
        f['momentum']=half['median'].reindex(f.index)-prior['median'].reindex(f.index)
        f['peer']=f.gu+':'+(f.area//15).astype(int).astype(str)
        f['relative_level']=f.entry-f.groupby('peer').entry.transform('median')
        f['peer_momentum']=f.groupby('peer').momentum.transform('median')
        f['relative_momentum']=f.momentum-f.peer_momentum
        f['activity']=(f.n90+1)/((f.n365-f.n90+1)/3)
        f['last_age']=(origin-d[d.date<=cutoff].groupby('key').date.max().reindex(f.index)).dt.days
        lease=lease_snapshot(leases,origin).set_index('key')
        f=f.join(lease)
        f['lease_ratio90']=f.lease_log90-f.entry
        f['lease_ratio180']=f.lease_log180-f.entry
        # Older lease records have no new/renewal field. Keep that signal named
        # separately; it must never be described as verified new-tenant demand.
        unknown=leases[(leases.date<=cutoff)&(leases.date>cutoff-pd.Timedelta(days=180))
                       &(leases.receipt_year<=origin.year)&leases.contract_type.eq('미상')].drop_duplicates(SIGNATURE)
        proxy=unknown.groupby('key').log_rent.agg(['median','count'])
        f['lease_unknown_ratio']=proxy['median'].where(proxy['count']>=3)-f.entry
        f['origin']=str(origin.date());f['month']=str(origin.to_period('M'))
        f=f.reset_index()
        f=index_features(f,indices)
        if inference_only:
            f['window']=window
            outputs.append(f)
            continue
        for horizon in [12,24]:
            end=origin+pd.DateOffset(months=horizon)
            exit=agg(end-pd.DateOffset(months=6),end)
            x=f.set_index('key').copy()
            x['exit']=exit['median'].reindex(x.index)
            x['exit_n']=exit['count'].reindex(x.index).fillna(0)
            x['growth']=x.exit-x.entry
            x.loc[x.exit_n<3,'growth']=np.nan
            x['benchmark']=np.nan
            # Equal weight per complex in the benchmark; exclude all own types.
            for _,g in x.groupby('peer'):
                pool=g[g.growth.notna()].groupby('complex').growth.median()
                for c,idx in g.groupby('complex').groups.items():
                    other=pool[pool.index!=c]
                    if len(other)>=5:x.loc[idx,'benchmark']=other.median()
            x['target']=x.growth-x.benchmark
            x['horizon']=horizon;x['window']=window
            x['label_available']=str((end+pd.Timedelta(days=31)).date())
            outputs.append(x.reset_index())
        print('potential features',window,origin.date(),len(f),flush=True)
    return pd.concat(outputs,ignore_index=True)


def evaluate(f):
    results=[];case_rows=[]
    for (window,horizon),data in f.groupby(['window','horizon']):
        for origin in ['2023-01-01','2023-07-01','2024-01-01','2024-07-01']:
            train=data[(data.label_available<=origin)&data.target.notna()&~data.complex.isin(CASES)]
            test=data[data.origin.eq(origin)].copy()
            for name,cols in [('price',COLS),('external',COLS+KB+LEASE+['lease_unknown_ratio'])]:
                m=LGBMRegressor(objective='regression_l1',n_estimators=120,num_leaves=7,
                    min_child_samples=100,reg_lambda=20,learning_rate=.04,n_jobs=4,
                    verbosity=-1,random_state=20260908)
                m.fit(train[cols],train.target)
                test['signal']=m.predict(test[cols])
                test['percentile']=test.signal.rank(pct=True)*100
                top=test.sort_values(['signal','key'],ascending=[False,True]).head(max(1,int(len(test)*.1)))
                known=top[top.target.notna()]
                all_known=test[test.target.notna()]
                # Selected cohort always includes unobserved outcomes.
                g=known.groupby('complex').target.median().to_numpy()
                rng=np.random.default_rng(20260908);boot=[]
                if len(g)>=2:
                    for _ in range(500):
                        boot.append(np.median(g[rng.integers(0,len(g),len(g))]))
                result={'entry_window_days':int(window),'horizon_months':int(horizon),'origin':origin,'method':name,
                    'train_rows':len(train),'cohort':len(test),'selected':len(top),'observed':len(known),
                    'observed_complexes':len(g),'median_excess_pct':float(np.expm1(known.target.median())*100),
                    'complex_balanced_median_excess_pct':float(np.expm1(np.median(g))*100),
                    'all_observed_median_excess_pct':float(np.expm1(all_known.target.median())*100),
                    'positive_excess_pct':float(known.target.gt(0).mean()*100),
                    'after_2pct_roundtrip_positive_pct':float(known.target.gt(np.log(1.02)).mean()*100),
                    'after_5pct_roundtrip_positive_pct':float(known.target.gt(np.log(1.05)).mean()*100),
                    'median_growth_pct':float(np.expm1(known.growth.median())*100),
                    'complex_median_bootstrap_95pct':(np.expm1(np.quantile(boot,[.025,.975]))*100).tolist() if boot else None}
                results.append(result)
                c=test[test.complex.isin(CASES)].copy()
                c['method']=name;c['entry_oku']=np.exp(c.entry)*c.area/10000
                c['exit_oku']=np.exp(c.exit)*c.area/10000
                case_rows.extend(json.loads(c[['origin','complex','area','horizon','window','method','percentile','entry_oku','exit_oku','target','lease_ratio180']].to_json(orient='records')))
            print('potential evaluated',window,horizon,origin,flush=True)
    def nullable(value):
        if isinstance(value,dict):return {k:nullable(v) for k,v in value.items()}
        if isinstance(value,list):return [nullable(v) for v in value]
        if isinstance(value,float) and not np.isfinite(value):return None
        return value
    return nullable({'results':results,'cases':case_rows,'notes':[
        'Retrospective corrected data and current index vintage; 31-day rent lag and two-month index lag are assumptions.',
        'Older unknown lease contracts remain separate from verified new leases.',
        'Repeated cohort dates are not independent complexes. Bootstrap is descriptive per origin, not proof across market regimes.',
        'Historical median entry is not an executable ask. 2/5% cost cases are uniform sensitivities, not property-specific costs.',
        'Named success cases excluded from training; no promotion to production ranking.',
        'Null statistics mean no measured outcome or fewer than two observed complexes for bootstrap.']})


if __name__=='__main__':
    d=inputs()
    leases=pd.concat([pd.read_parquet('.work/market-research/seoul_jeonse.parquet'),
                      pd.read_parquet('.work/market-research/older/seoul_jeonse.parquet')],ignore_index=True)
    indices=pd.read_parquet('.work/market-research/kb_indices.parquet')
    frames=[]
    for window in [90,180]:
        p=OUT/f'features_{window}.parquet'
        if p.exists():g=pd.read_parquet(p)
        else:g=snapshots(d,leases,indices,window);g.to_parquet(p,index=False)
        frames.append(g)
    result=evaluate(pd.concat(frames,ignore_index=True))
    (OUT/'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
