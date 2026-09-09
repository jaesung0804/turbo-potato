import numpy as np
import pandas as pd
import pytest

from estate_price_confidence import grades, complex_weights, weighted_quantile


def test_error_grades_have_explicit_price_bounds_and_no_trade_count_shortcut():
    assert grades(np.log([1.099, 1.101, 1.199, 1.201, 1.349, 1.351])).tolist() == ['A','B','B','C','C','D']
    # Repeating one frequent complex cannot dominate equal-complex calibration.
    f = pd.DataFrame({'region': ['인천광역시']*102, 'complex': ['a']*100+['b','c']})
    values = np.array([.01]*100+[.2,.3])
    assert weighted_quantile(values, complex_weights(f), .8) == .3
    with pytest.raises(ValueError):
        weighted_quantile([1, np.nan], [1, 1], .8)


def test_published_band_keeps_prices_scores_and_requires_matching_model():
    import copy
    import json
    from pathlib import Path
    from estate_price_confidence import FEATURES, attach_confidence
    from estate_valuation import score_at_price

    spec = json.loads(Path('metadata/nowcast_2026_capital_v2.json').read_text())
    f = pd.DataFrame([{**dict.fromkeys(FEATURES, 3.), 'key': 'sample',
        'region': '경기도', 'area': 84., 'age': 20., 'floor': 10.,
        'floor_delta': 0., 'low_floor': 0., 'n365': 10., 'last_age': 60., 'spread90': .04}])
    v = {'status': 'available', 'price_billion': 7.5, 'score_error_scale': .08}
    m = {'nowcast': spec, 'recommendations': [{'building_key': 'sample', 'current_valuation': v}]}
    before = score_at_price(v['price_billion'], 7.1, .08)
    attach_confidence(m, f, spec['confidence'])
    assert score_at_price(v['price_billion'], 7.1, .08) == before
    assert v['confidence']['lower_price_billion'] < v['price_billion'] < v['confidence']['upper_price_billion']
    assert v['confidence']['grade'] in 'ABCD'
    bad = copy.deepcopy(m); bad['nowcast']['sha256'] = 'different-price-model'
    with pytest.raises(ValueError, match='active price release'):
        attach_confidence(bad, f, spec['confidence'])
