"""Fixed preference ablations: select on 2023/24, confirm on 2025; keep v4 live."""
import argparse
import gzip
import hashlib
import json
import time
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
import lightgbm
import estate_model as model
from estate_io import write_json
from estate_preference_features import prepare_features

VARIANTS = {
    'v4': ([], []),
    'brand': ([], ['brand_name']),
    'households': (['log_households'], []),
    'school': (['school_log_distance','school_within_500m'], []),
    'transit': (['transit_log_distance'], []),
    'preferences': (['log_households','school_log_distance','school_within_500m','transit_log_distance'], ['brand_name']),
}


def clustered_interval(test, actual, baseline, candidate):
    loss = np.abs(actual-candidate)-np.abs(actual-baseline)
    groups=pd.DataFrame({'group':test.group.to_numpy(),'loss':loss}).groupby('group').loss.agg(['sum','size'])
    rng=np.random.default_rng(202609);values=[]
    for _ in range(300):
        sampled=groups.iloc[rng.integers(0,len(groups),len(groups))]
        values.append(float(sampled['sum'].sum()/sampled['size'].sum()))
    return {'delta_mae':round(float(loss.mean()),2),'ci95':[round(float(v),2) for v in np.quantile(values,[.025,.975])],
            'resampling_unit':'apartment complex; all its types stay together','repeats':300}


def experiment(summary_path, source_dir, output_dir, station_file):
    output_dir.mkdir(parents=True,exist_ok=True)
    body=summary_path.read_bytes();summary=json.loads(body)
    cache=Path('.work/preference_frame.joblib')
    fingerprint=hashlib.sha256(body).hexdigest()
    if cache.exists() and (saved:=joblib.load(cache)).get('summary_sha256')==fingerprint:
        frame,payload=saved['frame'],saved['payload']
    else:
        frame,payload=model.dataset(summary,enhanced=True)
        joblib.dump({'summary_sha256':fingerprint,'frame':frame,'payload':payload},cache,compress=3)
    keys=[(p['year'],p['region_code'],p['building_key']) for p in payload]
    snapshot_path=Path('metadata/preference_features_snapshot.json.gz')
    if source_dir is not None:
        extra,provenance=prepare_features(summary,payload,source_dir,station_file)
        provenance['feature_code_sha256']=hashlib.sha256(Path('estate_preference_features.py').read_bytes()).hexdigest()
        snapshot={'keys':keys,'columns':list(extra),'values':extra.astype(object).where(extra.notna(),None).values.tolist(),'provenance':provenance}
        snapshot_path.write_bytes(gzip.compress(json.dumps(snapshot,ensure_ascii=False,allow_nan=False).encode(),mtime=0))
    else:
        snapshot=json.loads(gzip.decompress(snapshot_path.read_bytes()))
        lookup={tuple(k):v for k,v in zip(snapshot['keys'],snapshot['values'])}
        if len(lookup)!=len(snapshot['keys']) or any(k not in lookup for k in keys):
            raise ValueError('Feature snapshot does not cover this data; rebuild with --source-dir')
        extra=pd.DataFrame([lookup[k] for k in keys],columns=snapshot['columns'])
        provenance=snapshot['provenance']
    frame=pd.concat([frame,extra],axis=1)
    predictions={};results={}
    for name,(numeric,categorical) in VARIANTS.items():
        f=frame.copy();f.attrs={'extra_numeric':numeric,'extra_categorical':categorical}
        started=time.monotonic();folds=model.evaluate(f,2025)
        training=f[f.year<2025];test=f[f.year==2025]
        weight,_=model.tune(training,2024);fitted=model.fit(training)
        predictions[name]=np.exp(model.predict(fitted,test,weight))
        results[name]={'extra_numeric':numeric,'extra_categorical':categorical,'feature_count':17+len(numeric)+len(categorical),
            'folds':folds,'elapsed_seconds':round(time.monotonic()-started,2)}
        print(name,[(v['test_year'],v['model']['mae_price_per_pyeong']) for v in folds],flush=True)
    # The 2025 outcomes are deliberately absent from this rule.
    for r in results.values():
        selection=[v for v in r['folds'] if v['test_year'] in [2023,2024]]
        r['selection_mae']=sum(v['model']['rows']*v['model']['mae_price_per_pyeong'] for v in selection)/sum(v['model']['rows'] for v in selection)
    best=min(r['selection_mae'] for r in results.values())
    eligible=[name for name,r in results.items() if r['selection_mae']<=best*1.005]
    selected=min(eligible,key=lambda name:(results[name]['feature_count'],results[name]['selection_mae'],name))
    test=frame[frame.year==2025];actual=np.exp(test.target.to_numpy())
    for name,r in results.items():
        r['holdout_delta']=clustered_interval(test,actual,predictions['v4'],predictions[name])
        r['selection_mae']=round(r['selection_mae'],2)
    # Save a separate prospective candidate, even if v4 is selected as simplest.
    f=frame.copy();n,c=VARIANTS['preferences'];f.attrs={'extra_numeric':n,'extra_categorical':c}
    training=f[f.year<=2025];weight,interval=model.tune(training,2025)
    artifact={**model.fit(training),'version':'estate-preferences-v5-research','trained_through':2025,
        'ml_weight':weight,'interval':interval,'data_sha256':fingerprint,'temporal_status':provenance['temporal_status']}
    candidate_path=output_dir/'preferences-v5-research.joblib';joblib.dump(artifact,candidate_path,compress=3)
    current=f[f.year==f.year.max()]
    current_payload=[p for p in payload if int(p['year'])==int(f.year.max())]
    current_fair=np.exp(model.predict(artifact,current,weight))
    errors=model.interval_errors(interval,current)
    rows=[]
    for (idx,row),p,fair,error in zip(current.iterrows(),current_payload,current_fair,errors):
        gap=np.log(fair/p['price_per_pyeong']);n=p['trade_count'];confidence=n/(n+5)
        rows.append({**p,'research_model':artifact['version'],'fair_price_per_pyeong':round(float(fair),1),
            'review_score':round(float(50+40*np.tanh(gap/max(error,.05))*confidence),1),
            'brand_name':row.brand_name,'matched_total_households':round(float(np.expm1(row.log_households))) if pd.notna(row.log_households) else None,
            'school_point_distance_m':round(float(np.expm1(row.school_log_distance))) if pd.notna(row.school_log_distance) else None,
            'verified_metro_subset_distance_m':round(float(np.expm1(row.transit_log_distance))) if pd.notna(row.transit_log_distance) else None})
    current_path=output_dir/'current_research_results.json.gz'
    current_path.write_bytes(gzip.compress(json.dumps(rows,ensure_ascii=False,allow_nan=False).encode(),mtime=0))
    report={'schema_version':1,'status':'research_only','production_model':'estate-reference-v4',
        'summary_sha256':fingerprint,'data_through':summary['data_through'],'runtime':{'lightgbm':lightgbm.__version__},
        'source_provenance':provenance,'selection_rule':'Choose the fewest features within 0.5% of the best pooled 2023/24 MAE; 2025 is excluded from selection. Fixed LightGBM architecture and prior-year weight tuning for every variant.',
        'selected_on_2023_2024':selected,'results':results,
        'candidate_artifact_sha256':hashlib.sha256(candidate_path.read_bytes()).hexdigest(),
        'current_research_rows':len(rows),'current_results_sha256':hashlib.sha256(current_path.read_bytes()).hexdigest(),
        'limitations':['This is reference-price error, not future-return performance.','Snapshot backcasts do not prove historical availability; no automatic production promotion.',
            'All six variants reuse historical holdouts for exploratory reporting; 2025 was already seen in prior v4 research.',
            'School proximity is not assignment, campus adjacency or walking safety. Transit covers a date-verified subset, not every station.',
            'Exact-area household inventory and actual routed walking time remain unobserved; no fabricated values.']}
    write_json(output_dir/'comparison.json',report,indent=2)
    print('Selected:',selected,results[selected]['holdout_delta'],flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary',type=Path,default=Path('.work/build/summary.json'))
    p.add_argument('--source-dir',type=Path,help='Rebuild features from original master/school CSVs; otherwise use the committed research snapshot')
    p.add_argument('--output-dir',type=Path,default=Path('.work/preference-experiment'))
    p.add_argument('--stations',type=Path,default=Path('metadata/metro_verified_events.json'))
    a=p.parse_args();experiment(a.summary,a.source_dir,a.output_dir,a.stations if a.stations.exists() else None)
