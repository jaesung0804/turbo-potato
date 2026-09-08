import json
import math

import numpy as np
import pandas as pd
import pytest

from estate_valuation import (
    attach_current_comparisons, attach_recent_comparison_prices, attach_transaction_replay, canonical_price,
    compare_price, replay_transactions, score_at_price,
)


def test_reported_neutral_price_reentry_and_user_regression():
    scale = 0.08094263165106064
    # A user enters exactly the number they can see, not hidden binary precision.
    assert canonical_price(.51814999) == .5181
    assert score_at_price(.51814999, .5181, scale) == 50
    comparison = compare_price(.5181, .52, scale)
    assert comparison['score'] == 48.2
    assert comparison['discount_pct'] == -.37
    assert comparison['comparison_price_billion'] == .52


def test_one_current_score_independent_of_old_annual_score_and_sample_quality():
    base = {'building_key': 'a', 'price_billion': .52, 'house_match_score': 83.4,
            'recent_price_comparison': {'status': 'available', 'median_price_billion': .52,
                'window_start': '2026-06-10', 'window_end': '2026-09-07',
                'data_through': '2026-09-07', 'window_days': 90, 'trade_count': 3},
            'neutral_price_billion': 2.5, 'current_valuation': {
                'status': 'available', 'price_billion': .5181, 'month': '2026-09',
                'feature_cutoff': '2026-08-01', 'score_error_scale': .08094263165106064,
                'effective_sample_size': 1}}
    model = {'target_year': '2026', 'recommendations': [base]}
    attach_current_comparisons(model)
    comparison = base['valuation_comparison'].copy()
    assert comparison['score'] == 48.2
    base['house_match_score'] = 10
    base['current_valuation']['effective_sample_size'] = 1000
    attach_current_comparisons(model)
    assert base['valuation_comparison'] == comparison
    base['current_valuation'] = {'status': 'insufficient_history'}
    attach_current_comparisons(model)
    assert base['valuation_comparison'] == {'status': 'insufficient_history'}
    assert model['valuation']['available_types'] == 0


def test_recent_comparison_uses_exact_inclusive_90_days_and_never_annual_fallback():
    d = pd.DataFrame({'key': ['a', 'a', 'a', 'empty'],
        'date': pd.to_datetime(['2026-06-09', '2026-06-10', '2026-09-07', '2026-01-01']),
        'price_oku': [100, .5, .54, 1]})
    valuation = {'status': 'available', 'price_billion': .5181, 'month': '2026-09',
        'feature_cutoff': '2026-08-01', 'recent_trade_count': 3, 'score_error_scale': .08094263165106064}
    model = {'model_month': '2026-09', 'target_year': '2026', 'recommendations': [
        {'building_key': 'a', 'price_billion': 100, 'current_valuation': dict(valuation)},
        {'building_key': 'empty', 'price_billion': .52, 'current_valuation': dict(valuation)}]}
    attach_recent_comparison_prices(model, d, {'source_sha256': 'source'})
    attach_current_comparisons(model)
    a, empty = model['recommendations']
    assert a['recent_price_comparison'] == {
        'status': 'available', 'window_days': 90, 'window_start': '2026-06-10',
        'window_end': '2026-09-07', 'data_through': '2026-09-07',
        'end_basis': 'latest_observed_contract_date_capped_at_valuation_month',
        'inclusive_boundaries': True, 'trade_count': 2, 'median_price_billion': .52,
        'first_contract_date': '2026-06-10', 'last_contract_date': '2026-09-07'}
    assert a['price_billion'] == 100  # Annual statistics remain separate.
    assert a['valuation_comparison']['comparison_price_billion'] == .52
    assert a['valuation_comparison']['score'] == 48.2
    assert a['valuation_comparison']['evidence_level'] == 'sparse_history'
    assert empty['recent_price_comparison']['trade_count'] == 0
    assert empty['valuation_comparison'] == {'status': 'no_recent_transactions'}
    assert model['valuation']['available_types'] == 1
    assert model['valuation']['recent_evidence_types'] == 0
    assert model['valuation']['recent_comparison']['source_sha256'] == 'source'
    assert score_at_price(empty['current_valuation']['price_billion'], .5181, .08) == 50


def test_recent_comparison_never_reads_beyond_valuation_month_and_sparse_filter_keeps_score():
    d = pd.DataFrame({'key': ['a']*4, 'date': pd.to_datetime(
        ['2026-07-03', '2026-07-04', '2026-09-30', '2026-10-01']),
        'price_oku': [.5, .52, .54, 99]})
    v = {'status': 'available', 'price_billion': .5181, 'month': '2026-09',
        'feature_cutoff': '2026-08-01', 'recent_trade_count': 3, 'score_error_scale': .08094263165106064}
    rec = {'building_key': 'a', 'price_billion': 200, 'current_valuation': v}
    model = {'model_month': '2026-09', 'target_year': '2026', 'recommendations': [rec]}
    attach_recent_comparison_prices(model, d)
    attach_current_comparisons(model)
    c = dict(rec['valuation_comparison'])
    assert c['comparison_trade_count'] == 3
    assert c['comparison_window_start'] == '2026-07-03'
    assert c['comparison_window_end'] == '2026-09-30'
    assert c['evidence_level'] == 'recent_evidence'
    assert model['valuation']['recent_evidence_types'] == 1
    v['recent_trade_count'] = 0
    attach_current_comparisons(model)
    assert rec['valuation_comparison']['score'] == c['score']
    assert rec['valuation_comparison']['evidence_level'] == 'sparse_history'
    assert model['valuation']['recent_evidence_types'] == 0


@pytest.mark.parametrize('invalid', [None, 0, -1, float('nan'), float('inf'), 'bad'])
def test_invalid_inputs_never_invent_a_neutral_score(invalid):
    assert score_at_price(invalid, 1, .1) is None
    assert score_at_price(1, invalid, .1) is None
    assert score_at_price(1, 1, invalid) is None


def test_price_score_is_monotonic_and_zero_discount_is_always_fifty():
    for fair in [.05, .5181, 1., 5.4321, 10, 150.9999]:
        prices = np.geomspace(fair / 2, fair * 2, 100)
        scores = [score_at_price(fair, p, .08) for p in prices]
        assert scores == sorted(scores, reverse=True)
        assert score_at_price(fair, canonical_price(fair), .08) == 50
        assert all(10 <= s <= 90 for s in scores)


def transaction_frame(target_price=100., later_price=100.):
    dates = pd.to_datetime(['2025-12-01', '2026-01-10', '2026-02-01',
                            '2026-03-12', '2026-03-20', '2026-04-15'])
    prices = [100, 100, 100, target_price, later_price, 110]
    return pd.DataFrame({'date': dates, 'month': dates.to_period('M').astype(str),
        'day': dates.values.astype('datetime64[D]').astype('int64'),
        'year': dates.year, 'key': 'exact', 'complex': 'building', 'peer': 'gu:3',
        'region': '서울특별시', 'gu': 'gu', 'area': 50., 'built': 2000,
        'floor': [10., 10., 10., 3., 15., 10.], 'log_price': np.log(prices),
        'price_oku': np.array(prices) * 50 / 10000})


def test_contract_replay_cannot_use_its_own_or_later_sale_price():
    artifact = {'trained_through': '2025-12-31', 'selected': 'ew90', 'model': None}
    spec = {'prediction_year': '2026', 'score_error_scale': .1,
            'assumed_reporting_lag_days': 31, 'sha256': 'test'}
    a = replay_transactions(transaction_frame(), artifact, spec, '2026-04')
    b = replay_transactions(transaction_frame(10, 99999), artifact, spec, '2026-04')
    original = {r['contract_date']: r for r in a['by_key']['exact']['recent_transactions']}
    changed = {r['contract_date']: r for r in b['by_key']['exact']['recent_transactions']}
    for date in ['2026-03-12', '2026-03-20']:
        assert original[date]['neutral_price_billion'] == changed[date]['neutral_price_billion'] == .5
        assert changed[date]['feature_cutoff'] == '2026-01-29'
        assert changed[date]['score'] == score_at_price(.5, changed[date]['price_billion'], .1)
    assert changed['2026-03-12']['score'] > original['2026-03-12']['score']
    assert a['metadata']['eligible_transactions'] == a['metadata']['valued_transactions'] == 3
    # 2026 Jan/Feb are not silently appraised using unsupported current-year labels.
    assert a['by_key']['exact']['period_start'] == '2026-03-12'
    assert a['by_key']['exact']['recent_transactions'][0]['floor'] == 10
    json.dumps(b, allow_nan=False)


def test_replay_rejects_in_sample_training_and_keeps_uncovered_properties():
    artifact = {'trained_through': '2026-01-31', 'selected': 'ew90', 'model': None}
    spec = {'prediction_year': '2026', 'score_error_scale': .1, 'sha256': 'test'}
    with pytest.raises(ValueError, match='follow model training'):
        replay_transactions(transaction_frame(), artifact, spec, '2026-04')
    model = {'recommendations': [{'building_key': 'missing'}]}
    attach_transaction_replay(model, {'metadata': {'data_through': '2026-04-15'}, 'by_key': {}})
    assert model['recommendations'][0]['transaction_valuation']['status'] == 'no_supported_contracts'


def test_score_has_same_precision_contract_as_public_javascript():
    import subprocess
    cases = [(f, p, .08094263165106064)
             for f in [.05185, .5181, .51815, 8.55555, 15.29999]
             for p in [.05185, .5181, .52, 8.55555, 15.29999]]
    js = '''const rows=JSON.parse(process.argv[1]);
const price=x=>Math.round(x*10000)/10000;
console.log(JSON.stringify(rows.map(([f,p,s])=>Math.round((50+40*Math.tanh(Math.log(price(f)/price(p))/s))*10)/10)));'''
    output = subprocess.check_output(['node', '-e', js, json.dumps(cases)], text=True)
    assert json.loads(output) == [score_at_price(*row) for row in cases]
