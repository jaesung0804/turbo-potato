"""Frozen-specification capital sales diagnostics, protocol 2026-09-09."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from estate_io import write_json
from estate_nowcast import PRICE_FEATURES, ACTIVITY_FEATURES, FLOOR_FEATURES, metric
from estate_retraining_features import save_parquet
from normalize_molit_capital_history import file_sha256

ROOT = Path(__file__).resolve().parent
COLS = PRICE_FEATURES + ACTIVITY_FEATURES + FLOOR_FEATURES
METHODS = ('frozen_original', 'recent_refit', 'all_history', 'rolling_five_years')


def fit_price(train):
    m = LGBMRegressor(objective='regression_l1', n_estimators=220,
        learning_rate=.04, num_leaves=23, min_child_samples=100,
        reg_lambda=10, random_state=20260907, n_jobs=4, verbosity=-1)
    m.fit(train[COLS], train.actual - train.anchor)
    return m


def price_prediction(model, frame):
    return frame.anchor.to_numpy() + model.predict(frame[COLS])


def register_inputs(work, output):
    output.mkdir(parents=True, exist_ok=True)
    path = output / 'input_manifest.json'
    # A manifest is created before outcomes. An existing experiment is never
    # silently reused with changed input or code.
    manifest = {'schema_version': 1, 'protocol_commit': '0d7e9a2bc37f2b36dc62efc048c29fd693939aa9',
        'protocol_sha256': file_sha256(ROOT/'reports/estate_retraining_protocol_20260909.md'),
        'preparation': json.loads((work/'preparation.json').read_text()),
        'feature_implementation_sha256': file_sha256(ROOT/'estate_retraining_features.py'),
        'transactions_sha256': file_sha256(work/'transactions.parquet'),
        'legacy_predictions_sha256': file_sha256(ROOT/'.work/previous-nowcast/predictions.parquet'),
        'frozen_model_sha256': file_sha256(ROOT/'.work/previous-nowcast/selected_model.joblib'),
        'analysis_sha256': file_sha256(Path(__file__)),
        'code_commit_at_registration': subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip(),
        'asof': '2026-09-09', 'evaluation_latest_month': '2026-07'}
    if path.exists():
        old=json.loads(path.read_text())
        if {k:v for k,v in old.items() if k!='code_commit_at_registration'} != {k:v for k,v in manifest.items() if k!='code_commit_at_registration'}:
            raise ValueError('Registered experiment inputs or implementation changed')
        return old
    else:
        write_json(path, manifest, indent=2)
    return manifest


def cluster_gain(frame, candidate):
    base_error = np.abs(np.expm1(frame.frozen_original-frame.actual))*frame.price_oku
    new_error = np.abs(np.expm1(frame[candidate]-frame.actual))*frame.price_oku
    c = pd.DataFrame({'complex':frame.complex, 'gain':base_error-new_error}).groupby('complex').gain.agg(['sum','count'])
    rng = np.random.default_rng(20260909)
    values, n = c.to_numpy(), len(c)
    draws = []
    for _ in range(2000):
        sample = values[rng.integers(n,size=n)]
        draws.append(sample[:,0].sum()/sample[:,1].sum())
    return {'gain_mae_oku':float((base_error-new_error).mean()),
            'paired_complex_bootstrap_95pct':np.quantile(draws,[.025,.975]).tolist(),
            'clusters':n, 'draws':2000,
            'limitation':'Complex dependence across both years retained; not independent market-cycle uncertainty.'}


def comparable_keys(frame):
    f = frame.copy()
    f['_amount'] = (f.price_oku*10000).round(4)
    fields = ['key','month','floor','age','_amount']
    f['_occurrence'] = f.groupby(fields,dropna=False,sort=False).cumcount()
    return f, fields+['_occurrence']


def price_experiment(work, output):
    path = output/'price_results.json'
    if path.exists(): raise FileExistsError(path)
    feature_input={'sha256':file_sha256(work/'features.parquet'),
                   'manifest':json.loads((work/'features.json').read_text())}
    frozen_features=output/'price_feature_input_manifest.json'
    if frozen_features.exists() and json.loads(frozen_features.read_text())!=feature_input:
        raise ValueError('Price feature artifact differs from registered input')
    write_json(frozen_features,feature_input,indent=2)
    extended = pd.read_parquet(work/'features.parquet')
    extended = extended[extended.anchor.notna()].copy()
    old = pd.read_parquet(ROOT/'.work/previous-nowcast/predictions.parquet')
    old = old[(old.year>=2025)&(old.month<='2026-07')].copy()
    old['frozen_original'] = old.price_activity_floor
    results, training, fitted = [], [], {}
    for year, test in old.groupby('year',sort=True):
        test = test.copy()
        for method, first in [('recent_refit',2023),('all_history',2007),('rolling_five_years',year-5)]:
            train = extended[extended.year.between(first,year-1)]
            model = fit_price(train)
            test[method] = price_prediction(model,test)
            training.append({'method':method,'evaluation_year':int(year),'first_year':int(first),
                'last_year':int(year)-1,'rows':len(train),'months':int(train.month.nunique())})
            if year==2026:
                fitted[method] = model
                joblib.dump({'model':model,'columns':COLS,'trained_through':'2025-12-31',
                             'version':'estate-nowcast-capital-history-research-20260909-'+method,
                             'status':'research_only_not_operational'},output/(method+'.joblib'),compress=3)
            print('price fit',year,method,'train',len(train),'test',len(test),flush=True)
        results.append(test)
    pred = pd.concat(results,ignore_index=True)
    save_parquet(pred[['key','complex','month','year','region','actual','price_oku']+list(METHODS)], output/'price_predictions.parquet')
    metrics = {m:{str(y):metric(g,g[m].to_numpy()) for y,g in pred.groupby('year')} for m in METHODS}
    regions = [{ 'year':int(y),'region':region,'method':m,**metric(g,g[m].to_numpy())}
               for (y,region),g in pred.groupby(['year','region']) for m in METHODS]
    gates=[]
    for year in (2025,2026):
        b, r, a = (metrics[m][str(year)] for m in ('frozen_original','recent_refit','all_history'))
        region_ok=all(next(v for v in regions if v['year']==year and v['region']==region and v['method']=='all_history')['mae_oku']
                      <=1.02*next(v for v in regions if v['year']==year and v['region']==region and v['method']=='frozen_original')['mae_oku']
                      for region in ('서울특별시','경기도','인천광역시'))
        gates.append({'year':year,'gain_vs_frozen_pct':100*(1-a['mae_oku']/b['mae_oku']),
                      'gain_vs_recent_refit_pct':100*(1-a['mae_oku']/r['mae_oku']),
                      'three_percent_vs_both':a['mae_oku']<=.97*min(b['mae_oku'],r['mae_oku']),
                      'complex_balanced_not_worse':a['complex_balanced_mape_pct']<=min(b['complex_balanced_mape_pct'],r['complex_balanced_mape_pct']),
                      'region_deterioration_within_two_percent':region_ok})
    # The preserved model can isolate input refresh from coefficient changes.
    fixed=joblib.load(ROOT/'.work/previous-nowcast/selected_model.joblib')['model']
    original=old[old.year.eq(2026)].copy()
    new=extended[extended.year.eq(2026)&extended.month.le('2026-07')].copy()
    original,keys=comparable_keys(original)
    new,_=comparable_keys(new)
    original['_old_prediction']=original.frozen_original
    new= new.merge(original[keys+['_old_prediction']],on=keys,how='inner',validate='one_to_one')
    refresh=price_prediction(fixed,new)
    refresh_summary={'original_rows':len(original),'refreshed_rows':len(extended[extended.year.eq(2026)]),
        'common_rows':len(new),'old_rows_unmatched':len(original)-len(new),
        'old_inputs':metric(new,new._old_prediction.to_numpy()),'refreshed_inputs':metric(new,refresh),
        'matching':'key, month, actual floor, age, amount and multiplicity; legacy cache has no contract day',
        'normalization':'same units and feature equations; updated observations and older fallback history'}
    result={'schema_version':1,'status':'retrospective_reused_sample','methods':metrics,'regions':regions,
        'training':training,'gates':gates,'primary_candidate':'all_history',
        'primary_gate_passed':all(all(v for k,v in g.items() if k in ('three_percent_vs_both','complex_balanced_not_worse','region_deterioration_within_two_percent')) for g in gates),
        'paired_gain':{m:cluster_gain(pred,m) for m in METHODS[1:]},
        'input_refresh_2026':refresh_summary,'operational_decision':'retain_estate_nowcast_v1',
        'evaluation':'fixed original transactions and original features; 2025 Mar-Dec, 2026 Mar-Jul',
        'limitations':['Previously inspected test periods; this is not new independent confirmation.',
                       'Conditional on actual sale floor; not accuracy of representative-floor app prices.',
                       'Training inputs use final corrected records and assumed historical publication lag.',
                       'Existing price baseline uses archived predictions; refits use current library versions.',
                       'January and February remain outside the original evaluation design.']}
    write_json(path,result,indent=2)
    print(json.dumps({'methods':metrics,'gates':gates},ensure_ascii=False),flush=True)
    return result


def data_audit(work, output):
    d=pd.read_parquet(work/'transactions.parquet')
    p=json.loads((work/'preparation.json').read_text())
    quality=json.loads((work/'features.json').read_text())['source']
    rows=[{'province':r,'year':int(y),'used_rows':len(g),'exact_types':int(g.key.nunique()),
           'complexes':int(g.complex.nunique())} for (r,y),g in d.groupby(['region','year'])]
    # Known changes flag present-address backcasts, not invented property IDs.
    flagged={
        'incheon_2026_new_gu_on_older_contracts':d.gu.isin(['28125','28155','28275','28290']) & d.date.lt('2026-07-01'),
        'hwaseong_2026_gu_on_older_contracts':d.gu.isin(['41591','41593','41595','41597']) & d.date.lt('2026-02-01'),
        'bucheon_gu_during_abolished_period':d.gu.isin(['41192','41194','41196']) & d.date.ge('2016-07-04') & d.date.lt('2024-01-01')}
    boundary=[{'issue':name,'rows':int(mask.sum()),'exact_types':int(d.loc[mask,'key'].nunique())}
              for name,mask in flagged.items()]
    history_keys=set(d.loc[d.year.le(2020),'key'])
    recent=d[d.year.ge(2021)].drop_duplicates('key')
    continuity=[{'province':r,'recent_exact_types':len(g),
                 'same_key_exists_before_2021':int(g.key.isin(history_keys).sum())}
                for r,g in recent.groupby('region')]
    result={'schema_version':1,'asof':'2026-09-09','raw_rows':p['rows'],'new_csv_rows':p['new_csv_rows'],
        'quality':quality,'province_year':rows,'source_sha256':p['data_sha256'],
        'normalization':'log(amount in 10000 KRW / exact area sqm); no fitted scaler or collection-date rebasing',
        'collection_observations':p['sources'],'uploaded_source_matches':p['uploaded_source_matches'],
        'exclusions':p['normalization_sources'],'ownership':p['ownership'],
        'boundary_issues':boundary,'boundary_status':'historical_boundary_and_physical_identity_not_certified',
        'key_continuity':continuity,'continuity_note':'Absence can mean a new building/type or a changed key; no fuzzy merge was applied.',
        'sources':['https://www.incheon.go.kr/IC01070101',
                   'https://sosa.bucheon.go.kr/site/homepage/menu/viewMenu?menuid=171001002003002',
                   'https://www.hscity.go.kr/dongtan/index.do'],
        'availability':'61/31-day assumed lag; upload and download dates remain observation metadata, not historical release dates',
        'strict_first_observation':'All newly collected old CSV rows were first observed in 2026; a proven-public-vintage historical test cannot use them.',
        'independent_server_count_note':'File integrity and declared scope checked; no universal independent server-count proof.'}
    write_json(output/'data_audit.json',result,indent=2)
    return result


def potential_experiment(work, output):
    from analyze_estate_potential import COLS as PCOLS, CASES
    from analyze_estate_potential_horizons import LAW_CHANGE, price_snapshot, labels, measure, finite
    path=output/'potential_results.json'
    if path.exists(): raise FileExistsError(path)
    d=pd.read_parquet(work/'transactions.parquet',columns=['key','complex','gu','area','built','date','day','floor','log_price'])
    d=d[d.floor.between(3,20)&d.date.le('2026-09-09')].copy()
    d['available_date']=d.date+pd.to_timedelta(np.where(d.date<LAW_CHANGE,61,31),unit='D')
    a=d[d.gu.str.startswith('11')|d.gu.eq('41210')].copy()
    b=d[~(d.gu.str.startswith('11')|d.gu.eq('41210'))].copy()
    del d
    train_parts, test_parts,coverage=[],[],[]
    # Today's source-address boundaries are deliberately diagnostic only.
    for origin in pd.date_range('2007-01-01','2024-07-01',freq='2QS'):
        for scope,source,parts in [('A',a,train_parts),('B',b,test_parts)]:
            f=price_snapshot(source,origin,'historical_policy')
            values=labels(source,f,origin,24,'historical_policy')
            values['scope']=scope
            parts.append(values)
            coverage.append({'scope':scope,'origin':str(origin.date()),'entry_candidates':len(values),
                             'exit_three_trades':int(values.exit_n.ge(3).sum()),
                             'relative_outcome_observed':int(values.target.notna().sum())})
        print('potential features',origin.date(),len(train_parts[-1]),len(test_parts[-1]),flush=True)
    train_data=pd.concat(train_parts,ignore_index=True)
    test_data=pd.concat(test_parts,ignore_index=True)
    save_parquet(train_data,output/'potential_train_A.parquet')
    save_parquet(test_data,output/'potential_test_B.parquet')
    results,skipped,predictions=[],[],[]
    for origin,test in test_data.groupby('origin'):
        train=train_data[train_data.label_available.le(origin)&train_data.target.notna()&~train_data.complex.isin(CASES)]
        # All B outcomes and B benchmark complexes are outside the fit.
        if not set(train.complex).isdisjoint(set(test_data.complex)):
            raise AssertionError('Geographic outcome leakage')
        for name,signal in [('laggard',-test.relative_momentum),('cheap_peer',-test.relative_level),('momentum',test.relative_momentum)]:
            results.append({'origin':origin,**measure(test,signal,name)})
        if len(train)<500 or train.origin.nunique()<3:
            skipped.append({'origin':origin,'rows':len(train),'origins':int(train.origin.nunique())})
            continue
        model=LGBMRegressor(objective='regression_l1',n_estimators=120,num_leaves=7,
            min_child_samples=100,reg_lambda=20,learning_rate=.04,n_jobs=4,verbosity=-1,random_state=20260908)
        model.fit(train[PCOLS],train.target)
        signal=model.predict(test[PCOLS])
        results.append({'origin':origin,**measure(test,signal,'learned_price',len(train),int(train.origin.nunique()))})
        keep=test[['key','complex','origin','entry_n','exit_n','growth','target']].copy()
        keep['signal']=signal
        predictions.append(keep)
        print('potential fit',origin,'train',len(train),'B',len(test),flush=True)
    model_results=[r for r in results if r['method']=='learned_price']
    eligible=[r for r in model_results if r['observation_rate_pct']>=50 and r['observed_complexes']>=20]
    model_eligible_origins={r['origin'] for r in eligible}
    eligible_origins={origin for origin in model_eligible_origins
                      if all(r['observation_rate_pct']>=50 and r['observed_complexes']>=20
                             for r in results if r['origin']==origin)}
    comparison=[]
    for method in ('learned_price','laggard','cheap_peer','momentum'):
        same=[r for r in results if r['method']==method and r['origin'] in eligible_origins]
        comparison.append({'method':method,'same_origins':len(same),
            'median_origin_complex_excess_pct':float(np.median([r['complex_median_excess_pct'] for r in same])) if same else None,
            'median_observation_rate_pct':float(np.median([r['observation_rate_pct'] for r in same])) if same else None,
            'positive_origins':sum(r['complex_median_excess_pct']>0 for r in same)})
    result=finite({'schema_version':1,'status':'current_source_boundary_retrospective_diagnostic',
        'primary_geographic_confirmation':'blocked_pending_historical_boundary_and_identity_audit',
        'training_scope':'Seoul and Gwangmyeong only; excludes named cases; no B benchmark outcomes',
        'evaluation_scope':'All other Gyeonggi and Incheon; no outcome-based geographic selection',
        'horizon_months':24,'outcome_window_months':[18,24],
        'model_evaluated_origins':len(model_results),'sufficiently_observed_origins':len(eligible),
        'common_sufficiently_observed_origins':len(eligible_origins),
        'sufficient_observation_gate_passed':len(eligible_origins)>=6,
        'comparison_same_model_eligible_origins':comparison,'results':results,'coverage':coverage,
        'skipped_training':skipped,'operational_decision':'retain_Seoul_Gwangmyeong_v2_scope',
        'limitations':['Current source addresses are not certified historical boundaries or physical IDs.',
                       'Same complexes and overlapping origins are dependent; origin counts are not independent cycles.',
                       '2021+ capital sales have been used in prior pricing experiments.',
                       'Missing future outcomes remain missing; selected top decile is fixed before observing outcomes.',
                       'This does not overturn earlier failed five-year or multi-path tests.']})
    save_parquet(pd.concat(predictions,ignore_index=True),output/'potential_predictions_B.parquet')
    write_json(path,result,indent=2)
    print(json.dumps({k:result[k] for k in ['status','model_evaluated_origins','sufficiently_observed_origins','comparison_same_model_eligible_origins']},ensure_ascii=False),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('stage',choices=['register','audit','price','potential'])
    p.add_argument('--work',default='.work/retraining-v3')
    p.add_argument('--output',default='.work/retraining-results-20260909')
    a=p.parse_args()
    work,output=Path(a.work),Path(a.output)
    register_inputs(work,output)
    if a.stage=='audit':data_audit(work,output)
    elif a.stage=='price':price_experiment(work,output)
    elif a.stage=='potential':potential_experiment(work,output)
