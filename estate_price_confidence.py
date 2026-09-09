"""Empirical price-error bands, independent of the price discount score."""
import numpy as np

FEATURES = ['area', 'age', 'floor', 'floor_delta', 'low_floor', 'n30', 'n90',
            'n365', 'last_age', 'spread90', 'eff90', 'active_days90',
            'complex_n90', 'peer_n90']


def inputs(frame):
    x = frame[FEATURES].copy()
    x['region_code'] = frame.region.map({'서울특별시': 0, '경기도': 1, '인천광역시': 2}).fillna(-1)
    return x


def grades(radius):
    return np.select([radius <= np.log(1.1), radius <= np.log(1.2),
                      radius <= np.log(1.35)], ['A', 'B', 'C'], default='D')


def radii(frame, artifact):
    raw = artifact['model'].predict(inputs(frame))
    offset = frame.region.map(artifact['region_offsets']).to_numpy(dtype=float)
    return np.maximum(.01, raw + offset)


def complex_weights(frame):
    identities = frame.region.astype(str) + '|' + frame.complex.astype(str)
    return 1 / identities.groupby(identities).transform('size').to_numpy()


def weighted_quantile(values, weights, q):
    values, weights = np.asarray(values), np.asarray(weights)
    if len(values) == 0 or not np.isfinite(values).all() or not (weights > 0).all():
        raise ValueError('Calibration needs finite residuals and positive weights')
    order = np.argsort(values, kind='stable')
    return float(values[order][np.searchsorted(np.cumsum(weights[order]), q * weights.sum())])


def attach_confidence(model, frame, config):
    """Attach a separately versioned band without changing any price or score."""
    import hashlib
    import json
    from pathlib import Path
    import joblib
    from estate_valuation import canonical_price

    spec = json.loads(Path(config['manifest']).read_text())
    body = Path(config['artifact']).read_bytes()
    if hashlib.sha256(body).hexdigest() != spec['sha256']:
        raise ValueError('Price confidence checksum mismatch')
    if not spec['validation_passed'] or spec['price_model_sha256'] != model['nowcast']['sha256']:
        raise ValueError('Confidence calibration does not match the active price release')
    artifact = joblib.load(config['artifact'])
    radius = radii(frame, artifact)
    labels = grades(radius)
    by_key = {key: (float(q), str(g)) for key, q, g in zip(frame.key, radius, labels)}
    counts = {}
    for rec in model['recommendations']:
        v = rec['current_valuation']
        pair = by_key.get(rec['building_key'])
        if v['status'] != 'available' or pair is None or not np.isfinite(pair[0]):
            v['confidence'] = {'status': 'unavailable', 'grade': None}
            continue
        q, grade = pair
        supported = grade in spec['supported_grades']
        v['confidence'] = {'status': 'available' if supported else 'unvalidated_grade',
            'grade': grade, 'version': spec['version'], 'nominal_coverage': .8,
            'lower_price_billion': canonical_price(v['price_billion']*np.exp(-q)) if supported else None,
            'upper_price_billion': canonical_price(v['price_billion']*np.exp(q)) if supported else None,
            'upper_radius_pct': float(np.expm1(q)*100),
            'validation_period': '2026-03~2026-07', 'basis': 'historical_actual_floor_price_errors',
            'historical_grade_coverage_pct': spec['grade_validation'].get(grade, {}).get('coverage_pct')}
        counts[grade] = counts.get(grade, 0) + 1
    model['nowcast']['price_confidence'] = {**spec, 'current_grade_counts': counts}
    return model
