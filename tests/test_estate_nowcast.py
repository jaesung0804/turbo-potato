import numpy as np
import pandas as pd
import pytest

from estate_nowcast import history_features, weighted_median, predict_sample


def history(days, prices):
    return pd.DataFrame({'day': days, 'log_price': np.log(prices),
        'year': [2025] * len(days), 'floor': [10] * len(days)})


def test_unavailable_and_future_transactions_cannot_change_history():
    origin = int(np.datetime64('2026-06-01', 'D').astype(int))
    old = history([origin-120, origin-50, origin-31], [100, 105, 110])
    future = history([origin-30, origin-1, origin+20], [9999, 1, 9999])
    a = history_features(old, origin, 31)
    b = history_features(pd.concat([old, future], ignore_index=True), origin, 31)
    for key in a:
        assert a[key] == pytest.approx(b[key], nan_ok=True), key
    assert a['last'] == pytest.approx(np.log(110))
    assert a['n30'] == 2


def test_recent_single_outlier_does_not_replace_a_supported_price_level():
    origin = int(np.datetime64('2026-06-01', 'D').astype(int))
    old = history(list(range(origin-40, origin-31)), [100] * 9)
    outlier = history([origin-31], [50])
    f = history_features(pd.concat([old, outlier], ignore_index=True), origin, 31)
    assert f['last'] == pytest.approx(np.log(50))
    assert f['ew90'] == pytest.approx(np.log(100))
    assert 1 <= f['eff90'] <= 10


def test_missing_history_does_not_become_zero_price():
    f = history_features(history([], []), 20000, 31)
    assert np.isnan(f['last']) and np.isnan(f['ew90'])
    assert f['n90'] == f['eff90'] == f['mass90'] == 0


def test_weighted_median_is_order_invariant_and_rejects_invalid_weights():
    assert weighted_median([1, 2, 99], [1, 3, 1]) == 2
    assert weighted_median([99, 1, 2], [1, 1, 3]) == 2
    assert weighted_median([1, 99], [2, -10]) == 1
    assert np.isnan(weighted_median([1, 2], [0, np.nan]))


def test_inference_uses_same_cutoff_and_does_not_turn_templates_into_trades():
    dates = pd.to_datetime(['2026-06-01', '2026-07-01', '2026-08-20'])
    d = pd.DataFrame({'date': dates, 'month': dates.to_period('M').astype(str),
        'day': dates.values.astype('datetime64[D]').astype('int64'),
        'year': 2026, 'key': 'exact', 'complex': 'building', 'peer': 'gu:3',
        'region': '서울특별시', 'gu': 'gu', 'area': 50., 'built': 2000,
        'floor': [10., 12., 1.], 'log_price': np.log([100., 100., 9999.]),
        'price_oku': [0.5, 0.5, 49.995]})
    artifact = {'trained_through': '2025-12-31', 'selected': 'ew90', 'model': None}
    result = predict_sample(d, [{'key': 'exact'}], artifact, '2026-09')
    assert len(result) == 1
    assert result.estimated_price_oku.iloc[0] == pytest.approx(.5)
    assert result.floor.iloc[0] == 11
    assert result.n90.iloc[0] == 2
    assert result.feature_cutoff.iloc[0] == '2026-08-01'
    with pytest.raises(ValueError, match='later than model training'):
        predict_sample(d, [{'key': 'exact'}], {**artifact, 'trained_through': '2026-12-31'}, '2026-09')


def test_release_checks_artifact_and_keeps_annual_ranking(tmp_path, monkeypatch):
    import hashlib
    import json
    import joblib
    import estate_nowcast_release as release
    artifact = tmp_path / 'model.joblib'
    joblib.dump({'trained_through': '2025-12-31'}, artifact)
    manifest = tmp_path / 'model.json'
    spec = {'sha256': hashlib.sha256(artifact.read_bytes()).hexdigest(),
            'prediction_year': '2026', 'trained_through': '2025-12-31', 'score_error_scale': .1}
    manifest.write_text(json.dumps(spec))
    d = pd.DataFrame({'date': pd.to_datetime(['2026-07-01']), 'key': ['exact'], 'price_oku': [8.2]})
    monkeypatch.setattr(release, 'load_transactions', lambda _: (d, {}))
    monkeypatch.setattr(release, 'predict_sample', lambda *args: pd.DataFrame([{
        'key':'exact', 'estimated_price_oku': 8., 'feature_cutoff': '2026-08-01',
        'floor': np.nan, 'n90': 2, 'last_age': 62, 'eff90': 1.8, 'active_days90': 2}]))
    model = {'model_month':'2026-09', 'recommendations':[
        {'building_key':'exact', 'house_match_score':61., 'neutral_price_billion':7.},
        {'building_key':'new', 'house_match_score':50., 'neutral_price_billion':6.}]}
    release.attach_nowcast(model, None, artifact, manifest)
    assert model['recommendations'][0]['house_match_score'] == 61
    assert model['recommendations'][0]['neutral_price_billion'] == 7
    assert model['recommendations'][0]['current_valuation']['price_billion'] == 8
    assert model['recommendations'][0]['current_valuation']['floor'] is None
    assert model['recommendations'][0]['current_valuation']['recent_history_start'] == '2026-05-03'
    assert model['recommendations'][0]['recent_price_comparison']['median_price_billion'] == 8.2
    assert model['recommendations'][0]['valuation_comparison']['comparison_trade_count'] == 1
    assert model['recommendations'][1]['current_valuation']['status'] == 'insufficient_history'
    assert model['nowcast']['available_types'] == 1
    json.dumps(model, allow_nan=False)
    model['model_month'] = '2027-01'
    release.attach_nowcast(model, None, artifact, manifest)
    assert model['nowcast']['status'] == 'unsupported_period'
    artifact.write_bytes(b'corrupted')
    with pytest.raises(ValueError, match='checksum'):
        release.attach_nowcast(model, None, artifact, manifest)
