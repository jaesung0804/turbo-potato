"""Exploratory 6–12 month relative-price ranking, with label-maturity embargo.

Retrospectively corrected transactions + assumed 31-day availability lag. Entry
is a historical median, not an executable ask. No-trade exits are disclosed.
This is a falsification exercise, never an automatically promoted production score.
"""
from pathlib import Path
import json
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from estate_nowcast import load_transactions

COLS=['area','age','n90','n180','n365','momentum','relative_level','peer_momentum','relative_momentum','activity','last_age']
CASES=['광명시 철산동 367 철산한신','강동구 암사동 509 선사현대아파트']


def snapshots(d):
    # Match exact area, and keep floors 3–20 consistently in all windows.
    d=d[d.floor.between(3,20)].copy()
    meta=d.sort_values('day').groupby('key').first()[['complex','gu','region','area','built','STDG_NM','BLDG_NM']]
    outputs=[];coverage=[]
    for origin in pd.date_range('2022-01-01','2025-01-01',freq='2QS'):
        cutoff=origin-pd.Timedelta(days=31)
        past=d[d.date<=cutoff]
        def agg(start,end):
            g=d[d.date.between(start,end)].groupby('key')
            return g.log_price.agg(['median','count'])
        b=agg(cutoff-pd.Timedelta(days=179),cutoff)
        f=meta.join(b.rename(columns={'median':'entry','count':'n180'}),how='inner')
        f=f[f.n180>=3].copy()
        recent=agg(cutoff-pd.Timedelta(days=89),cutoff)
        prior=agg(cutoff-pd.Timedelta(days=359),cutoff-pd.Timedelta(days=180))
        counts=agg(cutoff-pd.Timedelta(days=364),cutoff)
        f['n90']=recent['count'].reindex(f.index).fillna(0)
        f['n365']=counts['count'].reindex(f.index).fillna(0)
        f['age']=origin.year-f.built
        f['momentum']=f.entry-prior['median'].reindex(f.index)
        f['peer']=f.gu+':'+(f.area//15).astype(int).astype(str)
        # Equal-weight type-level peer prices; not raw trade-volume weighting.
        f['relative_level']=f.entry-f.groupby('peer').entry.transform('median')
        f['peer_momentum']=f.groupby('peer').momentum.transform('median')
        f['relative_momentum']=f.momentum-f.peer_momentum
        f['activity']=(f.n90+1)/((f.n365-f.n90+1)/3)
        f['last_age']=(origin-past.groupby('key').date.max().reindex(f.index)).dt.days
        outcome=agg(origin+pd.Timedelta(days=180),origin+pd.Timedelta(days=365))
        f['exit']=outcome['median'].reindex(f.index)
        f['exit_n']=outcome['count'].reindex(f.index).fillna(0)
        eligible=len(f);observed=f.exit_n>=3
        coverage.append({'origin':str(origin.date()),'eligible':eligible,'exit_observed':int(observed.sum()),'exit_missing_or_under3':int((~observed).sum())})
        # Outcomes are used ONLY for evaluation labels. Peer benchmark excludes
        # the subject complex; peers are members of the pre-origin cohort.
        f['growth']=f.exit-f.entry
        f.loc[~observed,'growth']=np.nan
        f['benchmark']=np.nan
        for _,g in f.groupby('peer'):
            for c,idx in g.groupby('complex').groups.items():
                pool=g[(g.complex!=c)&g.growth.notna()]
                if pool.complex.nunique()>=5:f.loc[idx,'benchmark']=pool.growth.median()
        f['target']=f.growth-f.benchmark
        f['origin']=str(origin.date())
        f['label_available']=str((origin+pd.Timedelta(days=396)).date())
        f['cutoff']=str(cutoff.date())
        outputs.append(f.reset_index())
        print('origin',origin.date(),'eligible',eligible,'labeled',int(f.target.notna().sum()),flush=True)
    return pd.concat(outputs,ignore_index=True),coverage


def evaluate(f):
    results=[];scored=[]
    for origin in ['2024-01-01','2024-07-01','2025-01-01']:
        train=f[(f.label_available<=origin)&f.target.notna()&~f.complex.isin(CASES)]
        test=f[f.origin==origin].copy()
        m=LGBMRegressor(objective='regression_l1',n_estimators=120,num_leaves=7,min_child_samples=100,reg_lambda=20,learning_rate=.04,n_jobs=4,verbosity=-1,random_state=20260907)
        m.fit(train[COLS],train.target)
        test['predicted_excess']=m.predict(test[COLS])
        # Select first, then count available outcomes. Never select only winners
        # or only cases with an observed exit.
        for name,signal in [('learned',test.predicted_excess),('laggard',-test.relative_momentum),('cheap_peer',-test.relative_level)]:
            selected=signal.rank(method='first',ascending=False)<=max(1,int(len(test)*.1))
            g=test[selected];known=g[g.target.notna()]
            results.append({'origin':origin,'method':name,'training_rows':len(train),'cohort':len(test),'top_decile_selected':len(g),'measured_exits':len(known),'median_excess_pct':float(np.expm1(known.target.median())*100),'positive_excess_pct':float((known.target>0).mean()*100),'all_observed_positive_pct':float((test.loc[test.target.notna(),'target']>0).mean()*100),'median_growth_pct':float(np.expm1(known.growth.median())*100),'rank_correlation':float(signal.corr(test.target,method='spearman'))})
        test['predicted_percentile']=test.predicted_excess.rank(pct=True)*100
        scored.append(test)
    return results,pd.concat(scored,ignore_index=True)


if __name__=='__main__':
    out=Path('.work/potential');out.mkdir(parents=True,exist_ok=True)
    d,quality=load_transactions('data/capital_area_apt_trade_transactions.csv')
    f,coverage=snapshots(d);f.to_parquet(out/'features.parquet',index=False)
    results,scored=evaluate(f);scored.to_parquet(out/'scored.parquet',index=False)
    cases=scored[scored.complex.isin(CASES)][['origin','complex','area','entry','exit','n180','exit_n','momentum','relative_level','activity','target','predicted_percentile']].copy()
    cases['entry_oku']=np.exp(cases.entry)*cases.area/10000
    cases['exit_oku']=np.exp(cases.exit)*cases.area/10000
    cases['excess_pct']=np.expm1(cases.target)*100
    summary={'purpose':'exploratory relative-price ranking, not causal proof or executable investment returns','source':quality,'availability':'assumed 31-day lag; final corrected transaction data','floor_scope':'3–20 consistently','exit_window':'origin +180 through +365 days; at least 3 trades','embargo':'training labels must have finished +31-day availability before test origin','named_cases_excluded_from_training':CASES,'coverage':coverage,'results':results,'cases':json.loads(cases.to_json(orient='records',force_ascii=False)),'sillim':[]}
    for origin,g in scored[scored.STDG_NM=='신림동'].groupby('origin'):
        a=g[g.target.notna()]
        summary['sillim'].append({'origin':origin,'eligible_types':len(g),'observed_types':len(a),'complexes':a.complex.nunique(),'median_excess_pct':float(np.expm1(a.target.median())*100),'positive_excess_pct':float((a.target>0).mean()*100)})
    Path('reports/estate_potential_validation.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False))
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
