"""Exploratory full-history adaptation; every eligible past row keeps weight > 0."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from analyze_estate_retraining import COLS, cluster_gain
from estate_io import write_json
from estate_nowcast import PRICE_FEATURES, metric
from estate_retraining_features import save_parquet
from normalize_molit_capital_history import file_sha256

ROOT = Path(__file__).resolve().parent
METHODS = ('all_history', 'recency_four_years', 'relative_uniform', 'relative_recency_four_years')
PRICE_LEVELS = [c for c in PRICE_FEATURES if c not in ('area', 'age', 'anchor')]


def inputs(frame, method):
    x = frame[COLS].copy()
    if method.startswith('relative_'):
        # Historical levels become contemporaneous log price ratios. The target
        # already uses actual-anchor in the original operational specification.
        x[PRICE_LEVELS] = x[PRICE_LEVELS].sub(frame.anchor, axis=0)
        x = x.drop(columns='anchor')
    return x


def training_weights(frame, evaluation_year, method):
    if not frame.year.between(2007, evaluation_year-1).all():
        raise ValueError('Training includes a future or out-of-scope year')
    w = np.ones(len(frame), dtype=float)
    if 'recency' in method:
        months = frame.month.str[:4].astype(int)*12 + frame.month.str[5:7].astype(int)
        age_months = evaluation_year*12 + 1 - months.to_numpy()
        w = np.exp2(-age_months/48.)
        w /= w.mean()  # Keep regularization scale comparable to unit weights.
    if not np.isfinite(w).all() or not (w > 0).all():
        raise ValueError('Every training row must retain a positive weight')
    return w


def fit_predict(train, test, year, method):
    w = training_weights(train, year, method)
    model = LGBMRegressor(objective='regression_l1', n_estimators=220,
        learning_rate=.04, num_leaves=23, min_child_samples=100,
        reg_lambda=10, random_state=20260907, n_jobs=4, verbosity=-1)
    model.fit(inputs(train, method), train.actual-train.anchor, sample_weight=w)
    pred = test.anchor.to_numpy() + model.predict(inputs(test, method))
    return model, pred, {'rows':len(train), 'positive_weight_rows':int((w>0).sum()),
        'first_year':int(train.year.min()), 'last_year':int(train.year.max()),
        'min_weight':float(w.min()), 'max_weight':float(w.max()),
        'effective_sample_size':float(w.sum()**2/(w@w))}


def choose(development):
    scores = {}
    for method in METHODS[1:]:
        ratios = [development[str(y)][method][k] / development[str(y)]['all_history'][k]
                  for y in (2023, 2024) for k in ('mae_oku', 'complex_balanced_mape_pct')]
        scores[method] = float(np.mean(ratios))
    selected = min(METHODS[1:], key=lambda m:scores[m])
    return {'method':selected, 'scores':scores,
        'rule':'Mean 2023/2024 MAE and complex-balanced MAPE ratios to uniform full history; lower wins.',
        'development_improved':scores[selected] < 1,
        'evaluation_years_used_for_selection':[]}


def run(work, output):
    if output.exists(): raise FileExistsError(output)
    output.mkdir(parents=True)
    manifest = {'schema_version':1, 'status':'exploratory_followup_after_inspecting_2025_2026',
        'feature_sha256':file_sha256(work/'features.parquet'),
        'implementation_sha256':file_sha256(Path(__file__)),
        'protocol_sha256':file_sha256(ROOT/'reports/estate_full_history_protocol_20260909.md'),
        'development_years':[2023,2024], 'reused_evaluation_years':[2025,2026],
        'methods':list(METHODS), 'half_life_months':48,
        'all_data_meaning':'All eligible 2007+ training rows, same warmup and March-December design as first comparison; 2006 and Jan-Feb observations remain in historical inputs.'}
    write_json(output/'input_manifest.json', manifest, indent=2)
    data = pd.read_parquet(work/'features.parquet')
    data = data[data.anchor.notna()].copy()
    development, training, dev_predictions = {}, [], []
    for year in (2023,2024):
        train, test = data[data.year.between(2007,year-1)], data[data.year.eq(year)].copy()
        development[str(year)] = {}
        for method in METHODS:
            _, pred, counts = fit_predict(train,test,year,method)
            development[str(year)][method] = metric(test,pred)
            training.append({'stage':'development','year':year,'method':method,**counts})
            test[method] = pred
            print('development',year,method,development[str(year)][method],flush=True)
        dev_predictions.append(test[['key','complex','month','year','region','actual','price_oku',*METHODS]])
        write_json(output/'development.json',development,indent=2)
    save_parquet(pd.concat(dev_predictions,ignore_index=True),output/'development_predictions.parquet')
    selection = choose(development)
    # Selection is written before opening any old test predictions/outcomes.
    write_json(output/'selection.json',selection,indent=2)
    print('locked selection',json.dumps(selection),flush=True)
    selected = selection['method']
    old = pd.read_parquet(ROOT/'.work/previous-nowcast/predictions.parquet')
    old = old[(old.year>=2025)&(old.month<='2026-07')].copy()
    old['frozen_original'] = old.price_activity_floor
    parts = []
    for year,test in old.groupby('year',sort=True):
        train = data[data.year.between(2007,year-1)]
        model,pred,counts = fit_predict(train,test,int(year),selected)
        test = test.copy(); test[selected] = pred
        parts.append(test[['key','complex','month','year','region','actual','price_oku','frozen_original',selected]])
        training.append({'stage':'reused_evaluation','year':int(year),'method':selected,**counts})
        joblib.dump({'model':model,'method':selected,'trained_through':f'{year-1}-12-31',
            'status':'research_only_not_operational','columns':list(inputs(test,selected))},
            output/f'{selected}_{year}.joblib',compress=3)
        print('reused evaluation',year,selected,metric(test,pred),flush=True)
    pred = pd.concat(parts,ignore_index=True)
    save_parquet(pred,output/'evaluation_predictions.parquet')
    previous = json.loads((ROOT/'reports/estate_retraining_price_20260909.json').read_text())
    metrics = {str(y):metric(g,g[selected].to_numpy()) for y,g in pred.groupby('year')}
    regions = [{'year':int(y),'region':r,**metric(g,g[selected].to_numpy())}
               for (y,r),g in pred.groupby(['year','region'])]
    gates = []
    for year in (2025,2026):
        candidate = metrics[str(year)]
        base,recent,full = (previous['methods'][m][str(year)] for m in ('frozen_original','recent_refit','all_history'))
        region_ok = all(v['mae_oku'] <= 1.02*next(b for b in previous['regions']
            if b['year']==year and b['region']==v['region'] and b['method']=='frozen_original')['mae_oku']
            for v in regions if v['year']==year)
        gates.append({'year':year,
            'gain_vs_frozen_pct':100*(1-candidate['mae_oku']/base['mae_oku']),
            'gain_vs_uniform_full_history_pct':100*(1-candidate['mae_oku']/full['mae_oku']),
            'three_percent_vs_frozen_and_recent':candidate['mae_oku']<=.97*min(base['mae_oku'],recent['mae_oku']),
            'complex_balanced_not_worse':candidate['complex_balanced_mape_pct']<=min(base['complex_balanced_mape_pct'],recent['complex_balanced_mape_pct']),
            'region_deterioration_within_two_percent':region_ok})
    result={'schema_version':1,'asof':'2026-09-09','status':manifest['status'],
        'input':manifest,'development':development,'selection':selection,'training':training,
        'evaluation':metrics,'regions':regions,'gates':gates,
        'numerical_gate_passed':all(g[k] for g in gates for k in ('three_percent_vs_frozen_and_recent','complex_balanced_not_worse','region_deterioration_within_two_percent')),
        'paired_gain':cluster_gain(pred,selected),'production_changed':False,
        'limitations':['Design motivated by already inspected 2025/2026 results; development-only selection does not restore independent confirmation.',
            'Final corrected source records and assumed 61/31-day publication lags; historical boundaries remain unaudited.',
            'All eligible past rows retain positive weight, but time weighting intentionally changes their influence.',
            'Actual transaction-floor conditional valuation; does not establish operational representative-floor accuracy.']}
    write_json(output/'results.json',result,indent=2)
    print(json.dumps({'selection':selection,'evaluation':metrics,'gates':gates},ensure_ascii=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--work',type=Path,default=ROOT/'.work/retraining-v3')
    p.add_argument('--output',type=Path,default=ROOT/'.work/full-history-adaptation-20260909')
    a=p.parse_args();run(a.work,a.output)
