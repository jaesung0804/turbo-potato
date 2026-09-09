"""Monthly, availability-lagged apartment price experiments.

The annual v5 reference and this per-property next-month valuation have different
targets. Both are evaluated against the SAME subsequent transactions here. A
31-day assumed publication lag is a sensitivity design, not archived as-of data.
Final cancellation status remains retrospective. No listing price is a label.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
from lightgbm import LGBMRegressor

HALF_LIVES = (30, 60, 90, 180)
PRICE_FEATURES = ['area', 'age', 'anchor', 'last', 'median90', 'median365',
                  'ew30', 'ew60', 'ew90', 'ew180', 'peer90', 'peer365',
                  'complex90', 'complex365', 'prior_annual']
ACTIVITY_FEATURES = ['n30', 'n90', 'n180', 'n365', 'last_age', 'spread90',
                     'eff90', 'mass90', 'active_days90', 'concentration',
                     'activity_ratio', 'complex_n90', 'complex_n365',
                     'peer_n90', 'peer_n365']
FLOOR_FEATURES = ['floor', 'floor_delta', 'low_floor']


def weighted_median(values, weights):
    values, weights = np.asarray(values, float), np.asarray(weights, float)
    good = np.isfinite(values) & np.isfinite(weights) & (weights > 0)
    if not good.any():
        return np.nan
    order = np.argsort(values[good], kind='stable')
    v, w = values[good][order], weights[good][order]
    return float(v[np.searchsorted(np.cumsum(w), w.sum() / 2)])


def load_transactions(path):
    raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    mask = ~raw.RTRCN_DAY.str.strip().isin(['', '-', '--'])
    cancelled = int(mask.sum())
    direct = int((~mask & raw.DCLR_SE.eq('직거래')).sum())
    d = raw[~mask & ~raw.DCLR_SE.eq('직거래') & raw.BLDG_USG.eq('아파트')].copy()
    for col in ['THING_AMT', 'ARCH_AREA', 'FLR', 'ARCH_YR']:
        d[col] = pd.to_numeric(d[col].str.replace(',', ''), errors='coerce')
    d['date'] = pd.to_datetime(d.CTRT_DAY, format='%Y%m%d', errors='coerce')
    d = d[d.date.notna() & d.THING_AMT.gt(0) & d.ARCH_AREA.gt(0)
          & np.isfinite(d.THING_AMT) & np.isfinite(d.ARCH_AREA)].copy()
    lot = d.MNO.str.lstrip('0').replace('', '0')
    sub = d.SNO.str.lstrip('0')
    lot = lot + np.where(sub.ne(''), '-' + sub, '')
    d['complex'] = d.CGG_NM.str.strip() + ' ' + d.STDG_NM.str.strip() + ' ' + lot + ' ' + d.BLDG_NM.str.strip()
    # Match the public exact-area identity without binary floating point strings.
    area_text = raw.loc[d.index, 'ARCH_AREA'].str.replace(',', '', regex=False).str.strip()
    area_text = area_text.map(lambda v: v.rstrip('0').rstrip('.') if '.' in v else v)
    d['key'] = d.complex + ' | ' + area_text + '㎡'
    d['region'] = d.CGG_CD.str[:2].map({'11': '서울특별시', '41': '경기도', '28': '인천광역시'})
    d['gu'] = d.CGG_CD
    d['dong'] = d.gu + ':' + d.STDG_NM
    d['area'] = d.ARCH_AREA
    d['floor'] = d.FLR
    d['built'] = d.ARCH_YR
    d['year'] = d.date.dt.year
    d['month'] = d.date.dt.to_period('M').astype(str)
    d['day'] = d.date.values.astype('datetime64[D]').astype('int64')
    d['log_price'] = np.log(d.THING_AMT / d.area)
    d['price_oku'] = d.THING_AMT / 10000
    d['peer'] = d.gu + ':' + (d.area // 15).astype(int).astype(str)
    quality = {'raw_rows': len(raw), 'used_rows': len(d), 'cancelled': cancelled,
               'direct_excluded': direct, 'unknown_deal_type': int(d.DCLR_SE.eq('').sum()),
               'source_sha256': hashlib.sha256(Path(path).read_bytes()).hexdigest(),
               'min_date': str(d.date.min().date()), 'max_date': str(d.date.max().date())}
    return d, quality


def history_features(group, origin_day, lag_days):
    """All inputs stop at origin minus lag; current labels are never read."""
    cutoff = origin_day - lag_days
    days = group.day.to_numpy()
    end = np.searchsorted(days, cutoff, side='right')
    hist = group.iloc[:end]
    p = hist.log_price.to_numpy()
    age = origin_day - hist.day.to_numpy()
    out = {}
    for n in (30, 90, 180, 365):
        # Windows end at the assumed observable cutoff, not at origin.
        use = age <= lag_days + n
        v = p[use]
        out[f'n{n}'] = len(v)
        out[f'median{n}'] = float(np.median(v)) if len(v) else np.nan
    recent = age <= lag_days + 365
    for h in HALF_LIVES:
        out[f'ew{h}'] = weighted_median(p[recent], np.exp2(-age[recent] / h))
    use = age <= lag_days + 90
    w = np.exp2(-age[recent] / 90)
    out['eff90'] = float(w.sum() ** 2 / (w @ w)) if len(w) else 0.
    out['mass90'] = float(w.sum())
    out['last'] = float(p[-1]) if len(p) else np.nan
    out['last_age'] = float(age[-1]) if len(p) else np.nan
    out['spread90'] = float(np.quantile(p[use], .75) - np.quantile(p[use], .25)) if use.sum() >= 2 else np.nan
    out['active_days90'] = int(hist.loc[use, 'day'].nunique())
    out['hist_floor'] = float(hist.loc[use, 'floor'].median()) if use.sum() else np.nan
    prev = hist[hist.year == pd.Timestamp(origin_day, unit='D').year - 1]
    out['prior_annual'] = float(prev.log_price.median()) if len(prev) else np.nan
    out['concentration'] = out['n30'] / max(out['n90'], 1)
    # Pseudocount stabilizes both low-volume periods; no division by zero.
    out['activity_ratio'] = ((out['n90'] + 1) / 90) / ((out['n365'] - out['n90'] + 1) / 275)
    return out


def build_features(d, lag_days=31, first='2023-03', last='2026-08'):
    records = []
    target_keys = set(d.loc[d.month.between(first, last), 'key'])
    groups = {k: g.sort_values('day') for k, g in d[d.key.isin(target_keys)].groupby('key', sort=False)}
    for month in pd.period_range(first, last, freq='M'):
        # Prior calendar year used by annual v5 must already have completed
        # the assumed reporting lag. January/February are intentionally omitted.
        if month.month < 3:
            continue
        origin = month.start_time
        day = int(origin.to_datetime64().astype('datetime64[D]').astype(int))
        current = d[d.month == str(month)]
        past = d[(d.day <= day - lag_days) & (d.day > day - lag_days - 365)]
        if not len(current) or not len(past):
            continue
        pools = {}
        for col in ('complex', 'peer'):
            for n in (90, 365):
                p = past[past.day > day - lag_days - n]
                pools[(col, n)] = p.groupby(col).log_price.agg(['median', 'count'])
        for key, target in current.groupby('key', sort=False):
            meta = target.iloc[0]
            f = history_features(groups[key], day, lag_days)
            for col in ('complex', 'peer'):
                for n in (90, 365):
                    pool = pools[(col, n)]
                    if meta[col] in pool.index:
                        row = pool.loc[meta[col]]
                        f[f'{col}{n}'], f[f'{col}_n{n}'] = row['median'], row['count']
                    else:
                        f[f'{col}{n}'], f[f'{col}_n{n}'] = np.nan, 0
            f['anchor'] = next((f[c] for c in ['ew90', 'complex90', 'complex365', 'peer90', 'peer365'] if np.isfinite(f[c])), np.nan)
            for h in HALF_LIVES:
                if not np.isfinite(f[f'ew{h}']):
                    f[f'ew{h}'] = f['anchor']
            for c in ['last', 'median90', 'median365', 'prior_annual']:
                if not np.isfinite(f[c]):
                    f[c] = f['anchor']
            for row in target.itertuples():
                records.append({**f, 'key': key, 'complex': meta['complex'], 'month': str(month),
                    'contract_date': str(pd.Timestamp(row.date).date()),
                    'lag_days': lag_days, 'feature_cutoff': str((origin-pd.Timedelta(days=lag_days)).date()),
                    'year': month.year, 'region': meta['region'], 'gu': meta['gu'],
                    'area': row.area, 'age': month.year - row.built,
                    'floor': row.floor, 'floor_delta': row.floor - f['hist_floor'],
                    'low_floor': float(row.floor <= 2) if np.isfinite(row.floor) else np.nan,
                    'actual': row.log_price, 'price_oku': row.price_oku})
        print('features', str(month), len(records), flush=True)
    return pd.DataFrame(records)


def annual_v5(d, evaluation):
    """Reproduce v5 calendar-year features and fit for each historical year."""
    import estate_model as em
    regions = {}
    for (key, year), g in d.groupby(['key', 'year'], sort=False):
        r = g.iloc[0]
        region = regions.setdefault(r.dong, {'code': r.dong, 'sido_name': r.region,
            'gu_name': r.CGG_NM, 'gu_code': r.gu, 'dong_name': r.STDG_NM, 'years': {}})
        bucket = region['years'].setdefault(str(year), {'addresses': []})
        pp = g.THING_AMT / (g.area / 3.305785)
        bucket['addresses'].append({'key': key, 'complex_key': r.complex,
            'built_year': float(r.built), 'building_name': r.BLDG_NM,
            'count': len(g), 'metrics': {'price_per_pyeong': {'median': float(pp.median())},
            'price_billion': {'median': float(g.price_oku.median())},
            'area_pyeong': {'median': float(r.area / 3.305785)}}})
    frame, payload = em.dataset({'regions': list(regions.values())}, True, True)
    frame['key'] = [p['building_key'] for p in payload]
    values = []
    for year in sorted(evaluation.year.unique()):
        train = frame[frame.year < year]
        test = frame[frame.year == year]
        weight, _ = em.tune(train, year - 1)
        fitted = em.fit(train)
        pred = em.predict(fitted, test, weight) - np.log(3.305785)
        values.extend({'year': year, 'key': k, 'v5': float(p)} for k, p in zip(test.key, pred))
        print('v5', year, 'weight', weight, flush=True)
    return evaluation.merge(pd.DataFrame(values), on=['year', 'key'], how='left', validate='many_to_one')


def metric(frame, pred):
    err = pred - frame.actual.to_numpy()
    ape = np.abs(np.expm1(err))
    return {'transactions': len(frame), 'complexes': int(frame.complex.nunique()),
        'mape_pct': round(float(ape.mean() * 100), 3),
        'complex_balanced_mape_pct': round(float(pd.Series(ape, index=frame.index).groupby(frame.complex).mean().mean()*100), 3),
        'median_ape_pct': round(float(np.median(ape) * 100), 3),
        'mae_oku': round(float((ape * frame.price_oku.to_numpy()).mean()), 4),
        'bias_pct': round(float(np.expm1(err).mean() * 100), 3),
        'within10_pct': round(float((ape <= .1).mean() * 100), 2)}


def experiment(frame, outdir):
    outdir.mkdir(parents=True, exist_ok=True)
    coverage = {'eligible_transactions': len(frame), 'missing_anchor': int(frame.anchor.isna().sum()),
                'missing_v5': int(frame.v5.isna().sum())}
    frame = frame[frame.anchor.notna() & frame.v5.notna()].copy()
    candidates = {
        'price_residual': PRICE_FEATURES,
        'price_activity': PRICE_FEATURES + ACTIVITY_FEATURES,
        'price_activity_floor': PRICE_FEATURES + ACTIVITY_FEATURES + FLOOR_FEATURES,
    }
    # Fixed small models. 2024 is the only candidate-selection period.
    years = [2024, 2025, 2026]
    parts = []
    latest_models = {}
    for year in years:
        train = frame[frame.year < year]
        test = frame[frame.year == year].copy()
        for name, cols in candidates.items():
            m = LGBMRegressor(objective='regression_l1', n_estimators=220,
                learning_rate=.04, num_leaves=23, min_child_samples=100,
                reg_lambda=10, random_state=20260907, n_jobs=4, verbosity=-1)
            m.fit(train[cols], train.actual - train.anchor)
            test[name] = test.anchor + m.predict(test[cols])
            if year == max(years):
                latest_models[name] = (m, cols)
        parts.append(test)
        print('fit', year, 'training', len(train), 'evaluation', len(test), flush=True)
    predictions = pd.concat(parts, ignore_index=True)
    methods = ['v5', 'last', 'median90', 'median365'] + [f'ew{h}' for h in HALF_LIVES] + list(candidates)
    tune = predictions[predictions.year == 2024]
    chosen = min([f'ew{h}' for h in HALF_LIVES] + list(candidates), key=lambda c: metric(tune, tune[c].to_numpy())['mae_oku'])
    selected_model, selected_columns = latest_models.get(chosen, (None, []))
    joblib.dump({'selected': chosen, 'model': selected_model, 'columns': selected_columns,
        'trained_through': '2025-12-31', 'selection_year': 2024,
        'purpose': 'experimental conditional monthly transaction valuation'}, outdir/'selected_model.joblib', compress=3)
    summary = {'selection_year': 2024, 'selected': chosen, 'coverage': coverage,
        'assumed_lag_days': sorted(frame.lag_days.unique().tolist()) if 'lag_days' in frame else 'see run manifest',
        'train_schedule': 'expanding completed earlier years',
        'future_test_years': [2025, 2026], 'methods': {}, 'limitations': [
        'Retrospectively corrected data; fixed 31-day assumed lag is not archived public availability.',
        'March-December only; 2026 stops in August and late August reports may be incomplete.',
        'Evaluation covers properties that subsequently traded, not all housing stock.',
        'Future transaction floor is treated as a known property characteristic for conditional valuation.',
        'Household counts are NOT used: current metadata lacks verified historical identity and availability.',
        'Results assess subsequent transaction pricing, not investment returns or causality.']}
    for method in methods:
        summary['methods'][method] = {str(y): metric(g, g[method].to_numpy()) for y, g in predictions.groupby('year')}
        complete = predictions[(predictions.year == 2026) & (predictions.month <= '2026-07')]
        summary['methods'][method]['2026_Mar_Jul'] = metric(complete, complete[method].to_numpy())
    # Paired cluster bootstrap: resample apartment complexes, keeping all their
    # transactions together. Independent resampling of transactions is invalid.
    held = predictions[(predictions.year >= 2025) & (predictions.month <= '2026-07')].copy()
    held['gain'] = (np.abs(np.expm1(held.v5 - held.actual)) - np.abs(np.expm1(held[chosen] - held.actual))) * held.price_oku
    clusters = held.groupby('complex').gain.agg(['sum', 'count'])
    rng = np.random.default_rng(20260907)
    a = clusters.to_numpy(); samples = []
    for _ in range(1000):
        v = a[rng.integers(0, len(a), len(a))].sum(axis=0)
        samples.append(v[0] / v[1])
    summary['paired_gain_oku'] = {'mean': float(held.gain.mean()), 'cluster_bootstrap_95pct': np.quantile(samples, [.025, .975]).tolist()}
    summary['segments'] = {}
    for name, mask in {'서울': held.region.eq('서울특별시'), '경기': held.region.eq('경기도'),
        '인천': held.region.eq('인천광역시'), 'recent_none': held.n90.eq(0),
        'recent_1to2': held.n90.between(1,2), 'recent_under3': held.n90.lt(3),
        'recent_3plus': held.n90.ge(3), 'stale180': held.last_age.gt(180),
        'up5': (held.median90-held.prior_annual).gt(np.log(1.05)),
        'down5': (held.median90-held.prior_annual).lt(np.log(.95)),
        'low_floor': held.low_floor.eq(1)}.items():
        g = held[mask]
        if len(g): summary['segments'][name] = {m: metric(g, g[m].to_numpy()) for m in ['v5', chosen]}
    predictions.to_parquet(outdir/'predictions.parquet', index=False)
    (outdir/'metrics.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return summary


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', default='data/capital_area_apt_trade_transactions.csv')
    p.add_argument('--out', default='.work/nowcast')
    p.add_argument('--lag-days', type=int, default=31)
    p.add_argument('--features', type=Path, help='Reuse previously generated features, including v5 predictions')
    p.add_argument('--predict-sample', type=Path, help='JSON sample with exact key identities; experimental inference only')
    p.add_argument('--artifact', type=Path, help='Trusted locally trained selected_model.joblib')
    p.add_argument('--month', default='2026-09', help='Monthly prediction origin, YYYY-MM')
    args = p.parse_args()
    out = Path(args.out); out.mkdir(parents=True, exist_ok=True)
    if args.features:
        experiment(pd.read_parquet(args.features), out)
        return
    d, quality = load_transactions(args.source)
    quality['assumed_lag_days'] = args.lag_days
    (out/'data_quality.json').write_text(json.dumps(quality, ensure_ascii=False, indent=2))
    if args.predict_sample:
        if not args.artifact:
            p.error('--predict-sample requires --artifact')
        predict_sample(d, json.loads(args.predict_sample.read_text()), joblib.load(args.artifact),
                       args.month, args.lag_days).to_json(out/'sample_predictions.json', orient='records', force_ascii=False, indent=2)
        return
    features = build_features(d, args.lag_days)
    features = annual_v5(d, features)
    features.to_parquet(out/'features.parquet', index=False)
    experiment(features, out)


def predict_sample(d, sample, artifact, month, lag_days=31, feature_engine=None):
    """Conditional current valuation at historical median floor, not a live quote.

    No asking prices are accepted. The monthly availability cutoff is exactly
    the same as in validation. Templates have missing labels and cannot enter
    history. A missing exact identity is an error, not an automatic name match.
    """
    origin = pd.Period(month).start_time
    if pd.Timestamp(artifact['trained_through']) >= origin:
        raise ValueError('Prediction must be later than model training')
    cutoff = origin - pd.Timedelta(days=lag_days)
    wanted = {r['key'] for r in sample}
    base = d[d.date < origin].copy()
    templates = []
    known_groups = {k: g.sort_values('day') for k, g in base[base.key.isin(wanted)].groupby('key', sort=False)}
    for key in sorted(wanted):
        known = known_groups.get(key, base.iloc[:0])
        if known.empty:
            raise ValueError(f'No known property identity before prediction origin: {key}')
        row = known.iloc[-1].copy()
        floors = known[(known.date <= cutoff) & (known.date > cutoff-pd.Timedelta(days=365))].floor
        row['floor'] = floors.median() if len(floors) else np.nan
        row['date'], row['month'], row['year'] = origin, month, origin.year
        row['day'] = int(origin.to_datetime64().astype('datetime64[D]').astype(int))
        row['log_price'] = row['price_oku'] = np.nan
        templates.append(row)
    synthetic = pd.concat([base, pd.DataFrame(templates)], ignore_index=True)
    if feature_engine == 'array_equivalent_legacy_ties_v1':
        from estate_retraining_features import monthly_features
        f = monthly_features(synthetic, month, month, policy=False, uniform_lag=lag_days, legacy_order=True)
    else:
        f = build_features(synthetic, lag_days, month, month)
    if f.empty:
        raise ValueError('No supported prediction rows')
    if artifact['model'] is None:
        predicted = f[artifact['selected']].to_numpy()
    else:
        predicted = f.anchor + artifact['model'].predict(f[artifact['columns']])
    f['estimated_price_oku'] = np.exp(predicted) * f.area / 10000
    f['floor_basis'] = 'previous observable 365-day median; no individual listing floor supplied'
    return f[list(dict.fromkeys(['key','month','feature_cutoff','lag_days','floor','floor_basis',
              'estimated_price_oku','n90','last_age','eff90','active_days90','region',
              *PRICE_FEATURES,*ACTIVITY_FEATURES,*FLOOR_FEATURES]))]


if __name__ == '__main__':
    main()
