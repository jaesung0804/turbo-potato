"""Predeclared external-data ablations on the existing monthly pricing cohort.

This is retrospective research: current KB vintages and corrected lease files
cannot establish genuine historical first-publication availability. The default
KB period is two months old; leases have a 31-day assumed lag and receipt-year
floor. Neither asking prices nor future outcomes enter input features.
"""
from pathlib import Path
import argparse
import json
import numpy as np
import pandas as pd
from estate_kb import read_kb, period_rows
from lightgbm import LGBMRegressor
from estate_nowcast import PRICE_FEATURES, ACTIVITY_FEATURES, FLOOR_FEATURES, metric
from collect_seoul_lease_files import FILES

BASE = PRICE_FEATURES + ACTIVITY_FEATURES + FLOOR_FEATURES
KB = [f'kb_{kind}_{n}m' for kind in ['sale','rent'] for n in [1,3,6,12]]
LEASE = ['lease_ratio90','lease_ratio180','lease_momentum','lease_new_n90',
         'lease_new_n180','lease_renewal_n180','lease_unknown_n180','lease_last_age']
SIGNATURE = ['key','date','floor','deposit','monthly_rent','contract_type']



def index_features(frame, indices, months_lag=2):
    f = frame.copy()
    codes = set(indices.gu)
    f['_index_gu'] = f.gu.where(f.gu.isin(codes), f.gu.str[:2])
    f['_index_period'] = (pd.PeriodIndex(f.month, freq='M') - months_lag).astype(str)
    p = indices.pivot(index=['gu','period'],columns='kind',values='value').sort_index()
    for kind in ['sale','rent']:
        for n in [1,3,6,12]:
            # Reindex monthly before shifts, so a missing month is never skipped.
            chunks=[]
            for gu,g in p.groupby(level='gu'):
                s=g.droplevel('gu')[kind]
                s.index=pd.PeriodIndex(s.index,freq='M')
                s=s.reindex(pd.period_range(s.index.min(),s.index.max(),freq='M'))
                z=np.log(s/s.shift(n))
                chunks.extend({'_index_gu':gu,'_index_period':str(k),f'kb_{kind}_{n}m':v} for k,v in z.items())
            f=f.merge(pd.DataFrame(chunks),on=['_index_gu','_index_period'],how='left',validate='many_to_one')
    return f.drop(columns=['_index_gu','_index_period'])


def lease_snapshot(leases, origin, availability='lag31', unique=True):
    origin=pd.Timestamp(origin)
    cutoff=origin-pd.Timedelta(days=31)
    d=leases[(leases.date<=cutoff)&(leases.receipt_year<=origin.year)].copy()
    if availability == 'annual_file':
        # Conservative file-vintage scenario, not an asserted original release.
        published=d.source_file_year.map({y:pd.Timestamp(v[1]) for y,v in FILES.items()})
        d=d[published < origin]
    if unique:
        d=d.drop_duplicates(SIGNATURE)
    d=d[d.date>cutoff-pd.Timedelta(days=365)]
    new=d[d.contract_type.eq('신규')]
    out=pd.DataFrame(index=pd.Index(d.key.unique(),name='key'))
    def agg(x,start,end):
        return x[x.date.between(start,end)].groupby('key').log_rent.agg(['median','count'])
    for days in [90,180]:
        g=agg(new,cutoff-pd.Timedelta(days=days-1),cutoff)
        out[f'lease_new_n{days}']=g['count']
        out[f'lease_log{days}']=g['median'].where(g['count']>=3)
    prior=agg(new,cutoff-pd.Timedelta(days=359),cutoff-pd.Timedelta(days=180))
    out['lease_momentum']=out.lease_log180-prior['median'].where(prior['count']>=3)
    for name,category in [('renewal','갱신'),('unknown','미상')]:
        g=agg(d[d.contract_type.eq(category)],cutoff-pd.Timedelta(days=179),cutoff)
        out[f'lease_{name}_n180']=g['count']
    out['lease_last_age']=(origin-new.groupby('key').date.max()).dt.days
    for c in out:
        if '_n' in c:
            out[c]=out[c].fillna(0)
    return out.reset_index()


def lease_features(frame,leases,availability='lag31',unique=True):
    pieces=[]
    for month,g in frame.groupby('month',sort=True):
        s=lease_snapshot(leases,month+'-01',availability,unique)
        x=g.merge(s,on='key',how='left',validate='many_to_one')
        for n in [90,180]:
            x[f'lease_ratio{n}']=x[f'lease_log{n}']-x.anchor
        pieces.append(x)
    return pd.concat(pieces,ignore_index=True)


def model(cols,train):
    m=LGBMRegressor(objective='regression_l1',n_estimators=220,learning_rate=.04,
        num_leaves=23,min_child_samples=100,reg_lambda=10,random_state=20260907,n_jobs=4,verbosity=-1)
    m.fit(train[cols],train.actual-train.anchor)
    return m


def paired_gain(frame, first, second):
    g=frame.copy()
    g['gain']=(np.abs(np.expm1(g[first]-g.actual))-np.abs(np.expm1(g[second]-g.actual)))*g.price_oku
    a=g.groupby('complex').gain.agg(['sum','count']).to_numpy()
    rng=np.random.default_rng(20260908)
    boot=[]
    for _ in range(1000):
        b=a[rng.integers(0,len(a),len(a))].sum(axis=0);boot.append(b[0]/b[1])
    return {'mean_oku':float(g.gain.mean()),'complex_bootstrap_95pct':np.quantile(boot,[.025,.975]).tolist()}


def run(frame, out):
    candidates={'base':BASE,'kb':BASE+KB,'lease':BASE+LEASE,'kb_lease':BASE+KB+LEASE}
    parts=[]
    for year in [2024,2025,2026]:
        train=frame[frame.year<year]
        test=frame[(frame.year==year)&(frame.month<='2026-07')].copy()
        for name,cols in candidates.items():
            m=model(cols,train)
            test[name]=test.anchor+m.predict(test[cols])
        print('evaluated',year,'train',len(train),'test',len(test),flush=True)
        parts.append(test)
    pred=pd.concat(parts,ignore_index=True)
    result={'protocol':'reports/estate_extension_protocol.md','methods':{},'coverage':{},'paired_gain':{}}
    for year,g in pred.groupby('year'):
        for scope,mask in [('capital',pd.Series(True,index=g.index)),('seoul',g.gu.str.startswith('11')),
            ('gyeonggi',g.gu.str.startswith('41')),('incheon',g.gu.str.startswith('28')),
            ('seoul_new_jeonse3',g.gu.str.startswith('11')&g.lease_ratio180.notna()),
            ('recent_sales_under3',g.n90.lt(3))]:
            x=g[mask]
            if len(x):
                result['methods'][f'{year}_{scope}']={name:metric(x,x[name].to_numpy()) for name in candidates}
        result['coverage'][str(year)]={'seoul_transactions':int(g.gu.str.startswith('11').sum()),
            'new_jeonse_180_matched':int(g.lease_ratio180.notna().sum()),
            'kb_complete':int(g[KB].notna().all(axis=1).sum())}
    held=pred[pred.year>=2025]
    for name in ['kb','lease','kb_lease']:
        result['paired_gain'][name]=paired_gain(held,'base',name)
    pred.to_parquet(out/'predictions.parquet',index=False)
    (out/'metrics.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(result['paired_gain']),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--out',type=Path,default=Path('.work/market-research/ablation'))
    args=p.parse_args();args.out.mkdir(parents=True,exist_ok=True)
    cached=args.out/'features.parquet'
    if cached.exists():
        f=pd.read_parquet(cached)
    else:
        indices=read_kb(Path('.work/external-inspect/kb-monthly-202608.xlsx'))
        indices.to_parquet('.work/market-research/kb_indices.parquet',index=False)
        f=pd.read_parquet('.work/nowcast/features.parquet')
        f=f[f.anchor.notna()&f.v5.notna()]
        f=index_features(f,indices)
        f=lease_features(f,pd.read_parquet('.work/market-research/seoul_jeonse.parquet'))
        f.to_parquet(cached,index=False)
        print('features',f.shape,flush=True)
    run(f,args.out)
