"""Compare frozen research results on explicitly matched prediction origins.

This module reads small evaluation rows, never predictions or training inputs.
It does not select a new model or interpret overlapping origins as independent.
"""
from __future__ import annotations

import math
from numbers import Real
from statistics import median

METHODS = ('learned_price', 'laggard', 'cheap_peer', 'momentum')


def compare_common_origins(results, *, min_observation_rate=0.5, min_complexes=20):
    if not 0 < min_observation_rate <= 1 or min_complexes < 1:
        raise ValueError('Invalid observation thresholds')
    indexed = {}
    for row in results:
        if row.get('method') not in METHODS:
            continue
        identity = (row['origin'], row['method'])
        if identity in indexed:
            raise ValueError('Duplicate origin/method; compare one horizon and regime at a time')
        for key in ('selected', 'observed', 'observed_complexes'):
            if type(row.get(key)) is not int or row[key] < 0:
                raise ValueError('Invalid outcome counts')
        if not row['observed_complexes'] <= row['observed'] <= row['selected']:
            raise ValueError('Inconsistent outcome counts')
        indexed[identity] = row

    def reason(row):
        if row is None:
            return 'missing_method'
        if not row['selected'] or row['observed'] / row['selected'] < min_observation_rate:
            return 'insufficient_observation_rate'
        if row['observed_complexes'] < min_complexes:
            return 'insufficient_complexes'
        value = row.get('complex_median_excess_pct')
        if isinstance(value, bool) or not isinstance(value, Real) or not math.isfinite(value):
            return 'missing_complex_outcome'
        return None

    origins = sorted(origin for origin, method in indexed if method == 'learned_price')
    common, excluded = [], []
    for origin in origins:
        failures = {method: why for method in METHODS
                    if (why := reason(indexed.get((origin, method))))}
        if failures:
            excluded.append({'origin': origin, 'reasons': failures})
        else:
            common.append(origin)
    comparison = []
    for method in METHODS:
        rows = [indexed[(origin, method)] for origin in common]
        comparison.append({
            'method': method, 'same_origins': len(rows),
            'median_origin_complex_excess_pct': median(float(r['complex_median_excess_pct']) for r in rows) if rows else None,
            'median_observation_rate_pct': median(100 * r['observed'] / r['selected'] for r in rows) if rows else None,
            'positive_origins': sum(float(r['complex_median_excess_pct']) > 0 for r in rows),
        })
    pairs = []
    for origin in common:
        model, baseline = indexed[(origin, 'learned_price')], indexed[(origin, 'laggard')]
        pairs.append({
            'origin': origin,
            'model_excess_pct': float(model['complex_median_excess_pct']),
            'baseline_excess_pct': float(baseline['complex_median_excess_pct']),
            'difference_pp': float(model['complex_median_excess_pct']) - float(baseline['complex_median_excess_pct']),
            'model_selected': model['selected'], 'model_observed': model['observed'],
            'model_missing': model['selected'] - model['observed'],
            'baseline_selected': baseline['selected'], 'baseline_observed': baseline['observed'],
            'baseline_missing': baseline['selected'] - baseline['observed'],
        })
    return {
        'model_evaluated_origins': len(origins),
        'model_sufficient_origins': sum(reason(indexed[(origin, 'learned_price')]) is None for origin in origins),
        'common_origins': len(common), 'common_origin_dates': common,
        'required_methods': list(METHODS),
        'thresholds': {'observation_rate': min_observation_rate, 'observed_complexes': min_complexes},
        'comparison': comparison, 'paired_origins': pairs, 'excluded_origins': excluded,
        'paired_median_difference_pp': median(r['difference_pp'] for r in pairs) if pairs else None,
        'model_beats_baseline_origins': sum(r['difference_pp'] > 0 for r in pairs),
        'aggregation': 'median_of_origin_complex_medians',
        'independent_market_cycles': False,
        'promotion_decision': 'not_assessed',
    }
