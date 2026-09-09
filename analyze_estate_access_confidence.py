"""Locked follow-up diagnostics; see the dated protocol before interpreting results."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from estate_io import write_json
from estate_nowcast import metric
from estate_price_confidence import inputs, radii, grades, complex_weights, weighted_quantile

METHOD = 'relative_recency_four_years'
ROOT = Path(__file__).resolve().parent
HUBS = {
    'city_hall': (37.566, 126.978), 'yeouido': (37.521, 126.924),
    'gangnam': (37.498, 127.028), 'gasan': (37.481, 126.882),
    'pangyo': (37.395, 127.111), 'songdo': (37.389, 126.644),
    'cheongna': (37.532, 126.640), 'yeongjong': (37.487, 126.493),
    'namdong': (37.405, 126.690), 'bupyeong': (37.507, 126.722)}
JOBS = {'songdo': 61922, 'yeongjong': 22879, 'cheongna': 11840}
CONTROLS = ['area', 'age', 'floor', 'floor_delta', 'n90', 'n365', 'last_age', 'spread90', 'eff90']


def datasets():
    dev = pd.read_parquet('.work/retraining-v3/features.parquet', filters=[('year', 'in', [2023, 2024])])
    dev = dev[dev.anchor.notna()].reset_index(drop=True)
    predictions = pd.read_parquet('.work/full-history-adaptation-20260909/development_predictions.parquet')
    for col in ['key', 'month', 'region', 'actual']:
        if not dev[col].equals(predictions[col]):
            raise ValueError('Development row alignment differs: ' + col)
    dev['base'] = predictions[METHOD].to_numpy()
    old = pd.read_parquet('.work/previous-nowcast/predictions.parquet')
    test = old[(old.year >= 2025) & (old.month <= '2026-07')].copy().reset_index(drop=True)
    ep = pd.read_parquet('.work/full-history-adaptation-20260909/evaluation_predictions.parquet')
    for col in ['key', 'month', 'region', 'actual']:
        if not test[col].equals(ep[col]):
            raise ValueError('Evaluation row alignment differs: ' + col)
    test['base'] = ep[METHOD].to_numpy()
    test['active'] = np.where(test.region.eq('인천광역시'), test.price_activity_floor, test.base)
    return dev, old, test


def coordinates():
    identity = pd.read_parquet('.work/retraining-v3/transactions.parquet',
        columns=['region', 'complex', 'CGG_NM', 'STDG_NM', 'built']).drop_duplicates()
    prefix = identity.CGG_NM.str.strip() + ' ' + identity.STDG_NM.str.strip() + ' '
    identity['name'] = [c[len(p):].partition(' ')[2] if c.startswith(p) else ''
                        for c, p in zip(identity.complex, prefix)]
    identity['match'] = identity.region + '|' + identity.CGG_NM.str.strip() + '|' + identity.STDG_NM.str.strip() + '|' + identity.name
    unique = identity.groupby('match').complex.nunique()
    snapshot = json.loads(gzip.decompress(Path('metadata/amenities_snapshot.json.gz').read_bytes()))
    out = []
    for r in identity.itertuples():
        v = snapshot['items'].get(r.match)
        if not v or unique[r.match] != 1 or not r.name:
            continue
        if pd.notna(r.built) and v.get('built_year') and r.built != v['built_year']:
            continue
        lat, lon = v.get('latitude'), v.get('longitude')
        if lat is None or lon is None or not (33 <= lat <= 39 and 125 <= lon <= 130):
            continue
        out.append({'region': r.region, 'complex': r.complex, 'latitude': lat, 'longitude': lon})
    return pd.DataFrame(out).drop_duplicates(['region', 'complex'])


def access_features(frame):
    frame = frame.copy()
    lat, lon = np.deg2rad(frame.latitude), np.deg2rad(frame.longitude)
    for name, (a, b) in HUBS.items():
        a, b = np.deg2rad([a, b])
        h = np.sin((lat-a)/2)**2 + np.cos(lat)*np.cos(a)*np.sin((lon-b)/2)**2
        frame['km_' + name] = 6371 * 2 * np.arcsin(np.sqrt(np.clip(h, 0, 1)))
    frame['log_partial_jobs_access'] = np.log1p(sum(n * np.exp(-frame['km_'+name]/10) for name, n in JOBS.items()))
    return frame


def access_experiment(dev, test, out):
    coords = coordinates()
    d = access_features(dev[dev.region.eq('인천광역시')].merge(coords, on=['region', 'complex'], how='left', validate='many_to_one'))
    t = access_features(test[test.region.eq('인천광역시')].merge(coords, on=['region', 'complex'], how='left', validate='many_to_one'))
    train = d[(d.year == 2023) & d.latitude.notna()]
    tune = d[(d.year == 2024) & d.latitude.notna()]
    cols = {'controls': CONTROLS, 'coordinates': CONTROLS+['latitude', 'longitude'],
            'access': CONTROLS+['latitude', 'longitude']+['km_'+h for h in HUBS]+['log_partial_jobs_access']}
    base = metric(tune, tune.base.to_numpy())
    models, selection, development = {}, {}, []
    for name, features in cols.items():
        m = LGBMRegressor(objective='regression_l1', n_estimators=120, learning_rate=.03,
            num_leaves=7, min_child_samples=100, reg_lambda=20, random_state=20260909, n_jobs=4, verbosity=-1)
        m.fit(train[features], train.actual-train.base)
        delta = m.predict(tune[features])
        scores = []
        for strength in [0., .25, .5, 1.]:
            v = metric(tune, tune.base.to_numpy()+strength*delta)
            score = np.mean([v[k]/base[k] for k in ['mae_oku', 'complex_balanced_mape_pct']])
            row = {'method': name, 'strength': strength, 'selection_score': float(score), **v}
            development.append(row); scores.append(row)
        selection[name] = min(scores, key=lambda x: x['selection_score'])
        models[name] = m
    selected = min(selection, key=lambda k: selection[k]['selection_score'])
    lock = {'choices': selection, 'selected': selected, 'development': development,
            'train_year': 2023, 'selection_year': 2024, 'evaluation_years_used_for_selection': []}
    write_json(out/'access_selection.json', lock, indent=2)
    print('access selection locked', selected, selection[selected]['strength'], flush=True)
    for name, features in cols.items():
        mask = t.latitude.notna()
        t[name] = t.base
        t.loc[mask, name] += selection[name]['strength']*models[name].predict(t.loc[mask, features])
    evaluation = []
    for year, full in t.groupby('year'):
        for scope, f in [('all', full), ('matched', full[full.latitude.notna()])]:
            for method in ['price_activity_floor', 'base', *cols]:
                evaluation.append({'year': int(year), 'scope': scope, 'method': method,
                                   **metric(f, f[method].to_numpy())})
    gates = []
    for year, f in t.groupby('year'):
        old, new = metric(f, f.price_activity_floor.to_numpy()), metric(f, f[selected].to_numpy())
        coverage = float(f.latitude.notna().mean())
        gates.append({'year': int(year), 'coordinate_coverage': coverage,
            'mae_gain_vs_legacy_pct': 100*(1-new['mae_oku']/old['mae_oku']),
            'passed': bool(coverage >= .75 and new['mae_oku'] <= .98*old['mae_oku']
                           and new['complex_balanced_mape_pct'] <= old['complex_balanced_mape_pct'])})
    result = {'status': 'exploratory_spatial_proxy_not_actual_commute', 'selection': lock,
        'hubs': HUBS, 'partial_jobs': JOBS, 'employment_reference_year': 2021,
        'employment_published_at': '2022-12-19', 'employment_source': 'https://www.ifez.go.kr/main/pst/view.do?pst_id=noti03&pst_sn=271919',
        'coordinates': 'Current legacy snapshot, unambiguous name and construction year only; not archived historical coordinates.',
        'evaluation': evaluation, 'gates': gates, 'numerical_gate_passed': all(g['passed'] for g in gates),
        'train_transactions': len(train), 'development_transactions': len(tune),
        'limitations': ['Only three IFEZ employment counts; not all capital-area jobs.', 'Distances are to analyst-defined approximate representative points, not travel time.', '2025/2026 reused after prior inspection. Spatial association is not causation.']}
    write_json(out/'access_results.json', result, indent=2)
    t[['key', 'complex', 'year', 'region', 'actual', 'price_oku', 'base', 'price_activity_floor', *cols, 'latitude']].to_parquet(out/'access_predictions.parquet', index=False)
    return result


def confidence_stats(f):
    error = np.abs(f.actual-f.active)
    coverage = (error <= f.radius).to_numpy()
    w = complex_weights(f)
    return {'transactions': len(f), 'complexes': int(f[['region', 'complex']].drop_duplicates().shape[0]),
        'coverage_pct': float(coverage.mean()*100),
        'complex_coverage_pct': float(np.average(coverage, weights=w)*100),
        'mape_pct': float(np.expm1(f.active-f.actual).abs().mean()*100),
        'median_upper_radius_pct': float(np.expm1(f.radius).median()*100),
        'median_total_width_pct': float((np.exp(f.radius)-np.exp(-f.radius)).median()*100)}


def confidence_experiment(dev, old, test, out):
    train = pd.concat([dev[(dev.year == 2024) & ~dev.region.eq('인천광역시')].assign(active=lambda f:f.base),
                       old[(old.year == 2024) & old.region.eq('인천광역시')].assign(active=lambda f:f.price_activity_floor)], ignore_index=True)
    model = LGBMRegressor(objective='quantile', alpha=.8, n_estimators=180, learning_rate=.035,
        num_leaves=15, min_child_samples=200, reg_lambda=20, random_state=20260909, n_jobs=4, verbosity=-1)
    w = complex_weights(train); w /= w.mean()
    model.fit(inputs(train), np.abs(train.actual-train.active), sample_weight=w)
    calibration = test[test.year == 2025].copy()
    raw = model.predict(inputs(calibration))
    calibration['nonconformity'] = np.abs(calibration.actual-calibration.active)-raw
    offsets = {r: weighted_quantile(f.nonconformity, complex_weights(f), .8)
               for r, f in calibration.groupby('region')}
    artifact = {'model': model, 'region_offsets': offsets, 'trained_year': 2024, 'calibration_year': 2025,
                'target_coverage': .8, 'version': 'estate-price-confidence-v1'}
    write_json(out/'confidence_calibration.json', {k:v for k,v in artifact.items() if k!='model'}, indent=2)
    joblib.dump(artifact, out/'price_confidence_2026.joblib', compress=3)
    print('confidence calibration locked', offsets, flush=True)
    test = test.copy(); test['radius'] = radii(test, artifact); test['grade'] = grades(test.radius)
    rows = []
    for year, f in test.groupby('year'):
        rows.append({'year': int(year), 'scope': 'all', 'group': 'all', **confidence_stats(f)})
        for col in ['region', 'grade']:
            for name, g in f.groupby(col):
                rows.append({'year': int(year), 'scope': col, 'group': name, **confidence_stats(g)})
        if year == 2026:
            for name, g in [('input_trades_0_2', f[f.n90 < 3]), ('input_trades_3_plus', f[f.n90 >= 3])]:
                rows.append({'year': int(year), 'scope': 'input_history', 'group': name, **confidence_stats(g)})
            for name, g in f[f.n90 < 3].groupby('grade'):
                rows.append({'year': int(year), 'scope': 'sparse_grade', 'group': name, **confidence_stats(g)})
    current = [r for r in rows if r['year'] == 2026]
    overall = next(r for r in current if r['scope'] == 'all')
    grade_rows = sorted([r for r in current if r['scope'] == 'grade'], key=lambda r:r['group'])
    enough = [r for r in grade_rows if r['complexes'] >= 200 and r['transactions'] >= 500]
    gates = {'overall': overall['coverage_pct'] >= 75 and overall['complex_coverage_pct'] >= 75,
             'regions': all(min(r['coverage_pct'], r['complex_coverage_pct']) >= 70 for r in current if r['scope'] == 'region'),
             'supported_grades': all(min(r['coverage_pct'], r['complex_coverage_pct']) >= 75 for r in enough),
             'grade_error_order': all(a['mape_pct'] <= b['mape_pct']+.5 for a,b in zip(grade_rows,grade_rows[1:]))}
    result = {'status': 'exploratory_reused_evaluation', 'version': artifact['version'], 'training_transactions': len(train),
        'calibration_transactions': len(calibration), 'evaluation': rows, 'gates': gates,
        'numerical_gate_passed': all(gates.values()), 'supported_grades': [r['group'] for r in enough],
        'calibration': {k:v for k,v in artifact.items() if k!='model'},
        'limitations': ['Bands are calibrated against actual transaction floors; representative-floor predictions need comparable properties.',
                        '80% is a historical target, not a guaranteed probability for a listing or future price increase.',
                        '2025/2026 are reused research periods; no independent future validation.']}
    write_json(out/'confidence_results.json', result, indent=2)
    test[['key','complex','region','year','actual','active','price_oku','radius','grade','n90','last_age']].to_parquet(out/'confidence_predictions.parquet', index=False)
    return result


def main():
    p = argparse.ArgumentParser(); p.add_argument('--output', type=Path, required=True)
    p.add_argument('--published-main', required=True)
    args = p.parse_args(); args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output/'run.json', {'published_main': args.published_main,
        'protocol_sha256': hashlib.sha256(Path('reports/estate_access_confidence_protocol_20260909.md').read_bytes()).hexdigest(),
        'implementation_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}, indent=2)
    dev, old, test = datasets()
    a = access_experiment(dev, test, args.output)
    c = confidence_experiment(dev, old, test, args.output)
    print(json.dumps({'access_gates': a['gates'], 'confidence_gates': c['gates']}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
