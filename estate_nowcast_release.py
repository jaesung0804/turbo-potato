"""Publish monthly valuation and its canonical current price comparison.

Old annual scores remain in the payload for audit; they are not current scores.
"""
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from estate_nowcast import load_transactions, predict_sample
from estate_valuation import attach_current_comparisons, attach_recent_comparison_prices, canonical_price
from estate_regional_nowcast import ARTIFACT as ACTIVE_ARTIFACT, MANIFEST as ACTIVE_MANIFEST, component_version

ARTIFACT = Path(ACTIVE_ARTIFACT)
MANIFEST = Path(ACTIVE_MANIFEST)


def attach_nowcast(model, source, artifact_path=ARTIFACT, manifest_path=MANIFEST):
    spec = json.loads(manifest_path.read_text())
    body = artifact_path.read_bytes()
    if hashlib.sha256(body).hexdigest() != spec['sha256']:
        raise ValueError('Monthly valuation artifact checksum mismatch')
    month = model['model_month']
    # Never extrapolate an unvalidated January/February or new training year.
    supported = month[:4] == spec['prediction_year'] and int(month[5:]) >= 3
    meta = {**spec, 'month': month, 'status': 'available' if supported else 'unsupported_period',
            'available_types': 0, 'total_types': len(model['recommendations'])}
    for rec in model['recommendations']:
        rec.pop('current_valuation', None)
        rec.pop('valuation_comparison', None)
        rec.pop('recent_price_comparison', None)
    model['nowcast'] = meta
    model.pop('recent_price_comparison', None)
    if not supported:
        for rec in model['recommendations']:
            rec['current_valuation'] = {'status': 'unsupported_period'}
        return attach_current_comparisons(model)
    artifact = joblib.load(artifact_path)
    if artifact['trained_through'] != spec['trained_through']:
        raise ValueError('Monthly valuation training date mismatch')
    d, quality = load_transactions(source)
    attach_recent_comparison_prices(model, d, quality)
    origin = pd.Period(month).start_time
    known = set(d.loc[d.date < origin, 'key'])
    sample = [{'key': r['building_key']} for r in model['recommendations'] if r['building_key'] in known]
    predicted = predict_sample(d, sample, artifact, month,
                               int(spec.get('assumed_reporting_lag_days', 31)),
                               spec.get('feature_engine')) if sample else pd.DataFrame()
    by_key = {r['key']: r for r in predicted.to_dict('records')}
    regions = d.groupby('key', sort=False).region.first().to_dict() if 'region' in d else {}
    for rec in model['recommendations']:
        r = by_key.get(rec['building_key'])
        if not r or not np.isfinite(r['estimated_price_oku']) or r['estimated_price_oku'] <= 0:
            rec['current_valuation'] = {'status': 'insufficient_history'}
            continue
        rec['current_valuation'] = {
            'status': 'available', 'price_billion': canonical_price(r['estimated_price_oku']),
            'raw_price_billion': float(r['estimated_price_oku']),
            'model_version': component_version(spec, regions.get(rec['building_key'])),
            'release_version': spec.get('version'),
            'month': month, 'feature_cutoff': r['feature_cutoff'],
            'floor': float(r['floor']) if np.isfinite(r['floor']) else None,
            'floor_basis': 'previous_observable_365_day_median',
            'floor_history_start': str((pd.Timestamp(r['feature_cutoff']) - pd.Timedelta(days=364)).date()),
            'floor_history_end': r['feature_cutoff'],
            'recent_trade_count': int(r['n90']),
            'recent_history_start': str((pd.Timestamp(r['feature_cutoff']) - pd.Timedelta(days=90)).date()),
            'recent_history_end': r['feature_cutoff'],
            'last_trade_age_days': int(r['last_age']) if np.isfinite(r['last_age']) else None,
            'effective_sample_size': float(r['eff90']),
            'active_trade_days': int(r['active_days90']),
            'score_error_scale': spec['score_error_scale'],
        }
        meta['available_types'] += 1
    if spec.get('confidence') and not predicted.empty:
        from estate_price_confidence import attach_confidence
        attach_confidence(model, predicted, spec['confidence'])
    return attach_current_comparisons(model)
