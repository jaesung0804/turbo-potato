import numpy as np
import pandas as pd
import pytest

from estate.models.nowcast.quantile_v1.model import (
    LEVELS, QuantileResidualModel, fit, check_training, metrics, evaluate, attach_quantiles)
from estate.models.nowcast.regional_v2.model import COLS, PRICE_LEVELS


def panel(n=1200):
    # Synthetic contract tests only; never a market evaluation or release.
    frame = pd.DataFrame(0., index=range(n), columns=COLS)
    frame['anchor'] = np.log(100.)
    frame[PRICE_LEVELS] = np.log(100.)
    frame['area'], frame['age'], frame['floor'], frame['n90'] = 84., 20., 8., 4.
    frame['month'], frame['feature_cutoff'], frame['year'] = '2025-06', '2025-05-01', 2025
    frame['actual'] = np.log([60.]*(n//5)+[100.]*(3*n//5)+[130.]*(n//5))
    frame['region'], frame['complex'] = '서울특별시', 'same parcel'
    return frame


def test_direct_pinball_fit_keeps_cheap_and_repeated_rows_and_asymmetric_tails():
    data = panel()
    before = data.copy(deep=True)
    artifact = fit(data, '2025-12-31')
    assert artifact['training']['positive_weight_rows'] == len(data)
    assert artifact['training']['rows'] == len(data)
    q = np.exp(data.anchor.to_numpy()[:, None]+artifact['model'].predict_quantiles(data))
    np.testing.assert_allclose(np.median(q, axis=0), [60, 100, 130], rtol=.02)
    assert not np.isclose(np.log(q[0, 1]/q[0, 0]), np.log(q[0, 2]/q[0, 1]))
    pd.testing.assert_frame_equal(data, before)
    assert [m.objective for m in artifact['model'].models] == ['quantile']*3
    assert [m.alpha for m in artifact['model'].models] == list(LEVELS)


def test_publication_and_training_cutoffs_fail_closed():
    data = panel()
    with pytest.raises(ValueError, match='future'):
        check_training(data, '2024-12-31')
    data.loc[0, 'feature_cutoff'] = '2025-05-31'
    with pytest.raises(ValueError, match='publication-lag'):
        check_training(data, '2025-12-31')
    data = panel()
    data.loc[0, 'actual'] = np.nan
    with pytest.raises(ValueError, match='do not silently discard'):
        check_training(data, '2025-12-31')


class Constant:
    def __init__(self, value):
        self.value = value

    def predict(self, frame):
        return np.full(len(frame), self.value)


def test_crossing_rearrangement_and_scale_invariance():
    model = QuantileResidualModel([Constant(.2), Constant(0.), Constant(-.4)])
    frame = panel(10)
    np.testing.assert_array_equal(model.predict_quantiles(frame)[0], [-.4, 0, .2])
    np.testing.assert_array_equal(model.predict(frame), np.zeros(10))
    shifted = frame.copy()
    shifted[['anchor', *PRICE_LEVELS]] += 3
    np.testing.assert_array_equal(model.predict_quantiles(frame), model.predict_quantiles(shifted))


def test_retained_median_does_not_move_when_tails_cross():
    from estate.models.nowcast.quantile_v1.model import RetainedMedianQuantileModel
    model = RetainedMedianQuantileModel(QuantileResidualModel([Constant(.2), Constant(.3), Constant(-.4)]), Constant(.1))
    np.testing.assert_array_equal(model.predict_quantiles(panel(10))[0], [.1, .1, .1])


def test_evaluation_is_future_only_and_reports_groups():
    model = QuantileResidualModel([Constant(-.5), Constant(0), Constant(.3)])
    artifact = {'model': model, 'trained_through': '2025-12-31'}
    data = panel(10)
    with pytest.raises(ValueError, match='overlaps'):
        evaluate(data, artifact)
    data['month'], data['year'] = '2026-03', 2026
    result = evaluate(data, artifact)
    assert result['overall']['transactions'] == 10
    assert result['overall']['coverage80_pct'] == 80
    assert {g['dimension'] for g in result['groups']} == {'month', 'region', 'history', 'floor'}
    with pytest.raises(ValueError, match='crossing'):
        metrics(data, np.tile([3., 2., 1.], (10, 1)))


def test_monthly_inference_p50_is_identical_to_point_price_and_cutoff_is_shared():
    from estate.models.nowcast.v1.model import predict_sample
    dates = pd.to_datetime(['2026-06-01', '2026-07-01', '2026-08-20'])
    d = pd.DataFrame({'date': dates, 'month': dates.to_period('M').astype(str),
        'day': dates.values.astype('datetime64[D]').astype('int64'),
        'year': 2026, 'key': 'exact', 'complex': 'building', 'peer': 'gu:3',
        'region': '서울특별시', 'gu': 'gu', 'area': 50., 'built': 2000,
        'floor': [10., 12., 1.], 'log_price': np.log([100., 100., 9999.]),
        'price_oku': [0.5, 0.5, 49.995]})
    artifact = {'trained_through': '2025-12-31', 'columns': COLS,
        'model': QuantileResidualModel([Constant(-.3), Constant(0), Constant(.4)]),
        'quantile_levels': list(LEVELS)}
    r = predict_sample(d, [{'key': 'exact'}], artifact, '2026-09',
                       feature_engine='array_stable_publication_v1').iloc[0]
    assert r['p50_price_oku'] == r['estimated_price_oku'] == pytest.approx(.5)
    assert r['p10_price_oku'] == pytest.approx(.5*np.exp(-.3))
    assert r['p90_price_oku'] == pytest.approx(.5*np.exp(.4))
    assert r['floor'] == 11 and r['n90'] == 2
    assert r['feature_cutoff'] == '2026-08-01'


def test_quantile_release_requires_own_validation_and_matching_p50():
    row = dict(zip(['p10_price_oku','p50_price_oku','p90_price_oku'], [7., 10., 12.]))
    spec = {'quantiles': {'levels': list(LEVELS), 'validation_passed': False,
        'validation_period': '2026-03~2026-08', 'historical_coverage_pct': 79.}}
    v = {'price_billion': 10.}
    with pytest.raises(ValueError, match='not validated'):
        attach_quantiles(v, row, spec)
    spec['quantiles']['validation_passed'] = True
    assert attach_quantiles(v, row, spec)['quantiles']['prices_billion']['p10'] == 7.
    assert 'confidence' not in v
    with pytest.raises(ValueError, match='P50'):
        attach_quantiles({'price_billion': 11.}, row, spec)


def test_experiment_stops_before_reading_or_training_when_storage_is_blocked(tmp_path, monkeypatch, capsys):
    import estate.research.price.quantiles as runner
    monkeypatch.setattr(runner, 'run_preflight', lambda **_: {'ready': False, 'blockers': ['live_capacity_missing']})
    monkeypatch.setattr(runner, 'load_transactions', lambda _: pytest.fail('Must not read inputs'))
    monkeypatch.setattr(runner, 'fit', lambda *_: pytest.fail('Must not train'))
    output = tmp_path/'output'
    result = runner.main(['--state-dir', str(tmp_path), '--snapshot-name', 'saved-panel',
        '--input-file', 'panel.csv.gz', '--checkpoint-key', 'quantile.test',
        '--expected-checkpoint-version', '1', '--required-growth-bytes', '1000000',
        '--model-state-dir', str(tmp_path), '--output', 'output'])
    assert result == 2
    assert not output.exists()
    assert 'blocked_before_training' in capsys.readouterr().out
