"""Direct conditional price quantiles; candidate, never an automatic release.

The target is the transaction price distribution at a specified floor/month,
not a confidence interval for an unknown mean or a probability of a gift.
Existing source eligibility rules apply; this model adds no price trimming,
deduplication, winsorization, or low-price-dependent weights.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

from estate.models.nowcast.regional_v2.model import COLS, relative_inputs

LEVELS = (.1, .5, .9)
NAMES = ('p10', 'p50', 'p90')
VERSION = 'estate-direct-quantile-candidate-v1'


def check_training(frame, through):
    if frame.empty:
        raise ValueError('Empty training data')
    months = pd.to_datetime(frame.month + '-01', errors='raise')
    cutoff = pd.to_datetime(frame.feature_cutoff, errors='raise')
    ends = months + pd.offsets.MonthEnd(0)
    if not ((months >= pd.Timestamp('2007-01-01')) & (ends <= pd.Timestamp(through))).all():
        raise ValueError('Training includes a future or out-of-scope month')
    if not (cutoff <= months - pd.Timedelta(days=31)).all():
        raise ValueError('Features cross the publication-lag cutoff')
    if not np.isfinite(frame[['actual', 'anchor', 'area']].to_numpy()).all() or not frame.area.gt(0).all():
        raise ValueError('Invalid target, anchor or area; do not silently discard rows')
    # Every eligible transaction keeps positive weight, independent of price.
    age = (pd.Timestamp(through).year * 12 + pd.Timestamp(through).month
           - (months.dt.year * 12 + months.dt.month)).to_numpy()
    weights = np.exp2(-age / 48.)
    weights /= weights.mean()
    return weights


class QuantileResidualModel:
    """Same relative lagged features, separate pinball objective for each tail."""

    def __init__(self, models):
        if len(models) != len(LEVELS):
            raise ValueError('Require P10, P50 and P90 models')
        self.models = tuple(models)

    def raw_quantiles(self, frame):
        x = relative_inputs(frame)
        result = np.column_stack([model.predict(x) for model in self.models])
        if result.shape != (len(frame), len(LEVELS)) or not np.isfinite(result).all():
            raise ValueError('Invalid quantile predictions')
        return result

    def predict_quantiles(self, frame):
        # Increasing rearrangement prevents crossing without forcing symmetry.
        return np.sort(self.raw_quantiles(frame), axis=1)

    def predict(self, frame):
        return self.predict_quantiles(frame)[:, 1]


class CalibratedQuantileModel(QuantileResidualModel):
    """Out-of-time quantile offsets, fitted before the evaluation year."""

    def __init__(self, base, offsets):
        self.base = base
        self.offsets = np.asarray(offsets, dtype=float)
        if self.offsets.shape != (3,) or not np.isfinite(self.offsets).all():
            raise ValueError('Require three finite calibration offsets')

    def raw_quantiles(self, frame):
        return self.base.raw_quantiles(frame) + self.offsets


class RetainedMedianQuantileModel(QuantileResidualModel):
    """Keep the validated L1 (= P50) model and train both conditional tails."""

    def __init__(self, tails, median):
        self.tails, self.median = tails, median

    def raw_quantiles(self, frame):
        q = self.tails.raw_quantiles(frame)
        q[:, 1] = self.median.predict(frame)
        return q

    def predict_quantiles(self, frame):
        q = self.raw_quantiles(frame)
        q[:, 0] = np.minimum(q[:, 0], q[:, 1])
        q[:, 2] = np.maximum(q[:, 2], q[:, 1])
        return q


class ConditionalTailModel(QuantileResidualModel):
    """Direct conditional error quantiles around a frozen L1 median model."""

    def __init__(self, median, lower, upper):
        self.median, self.lower, self.upper = median, lower, upper

    def raw_quantiles(self, frame):
        median = self.median.predict(frame)
        x = relative_inputs(frame)
        x['median_residual'] = median
        return np.column_stack([median+self.lower.predict(x), median, median+self.upper.predict(x)])

    def predict_quantiles(self, frame):
        q = self.raw_quantiles(frame)
        q[:, 0] = np.minimum(q[:, 0], q[:, 1])
        q[:, 2] = np.maximum(q[:, 2], q[:, 1])
        return q


def fit_conditional_tails(frame, median_artifact, through='2026-06-30'):
    # Median was frozen before these contracts; these are out-of-training errors.
    if not (pd.to_datetime(frame.month+'-01') > pd.Timestamp(median_artifact['trained_through'])).all():
        raise ValueError('Tail labels overlap the median model training period')
    check_training(frame, '2026-05-31')
    if frame.month.max() > '2026-05' or through != '2026-06-30':
        raise ValueError('Tail training and reporting lag scope changed')
    median = median_artifact['model']
    point = median.predict(frame)
    x = relative_inputs(frame); x['median_residual'] = point
    y = frame.actual.to_numpy()-frame.anchor.to_numpy()-point
    tails = []
    for level in (.1, .9):
        model = LGBMRegressor(objective='quantile', alpha=level, n_estimators=120,
            learning_rate=.04, num_leaves=7, min_child_samples=500, reg_lambda=20,
            random_state=20261008, n_jobs=4, verbosity=-1)
        model.fit(x, y)
        tails.append(model)
    return {'model': ConditionalTailModel(median, *tails), 'columns': median_artifact['columns'],
        'quantile_levels': list(LEVELS), 'version': 'estate-conditional-quantiles-v1',
        'trained_through': through, 'base_trained_through': median_artifact['trained_through'],
        'feature_engine': 'array_equivalent_legacy_ties_v1',
        'training': {'rows': len(frame), 'positive_weight_rows': len(frame),
            'first_month': str(frame.month.min()), 'last_month': str(frame.month.max()),
            'reporting_lag_days': 31, 'all_weights': 1,
            'method': 'P10/P90 conditional residual pinball; frozen L1 P50 retained'}}

def fit(frame, through, *, trees=220, leaves=23):
    weights = check_training(frame, through)
    x, y = relative_inputs(frame), frame.actual - frame.anchor
    models = []
    for level in LEVELS:
        model = LGBMRegressor(objective='quantile', alpha=level,
            n_estimators=trees, learning_rate=.04, num_leaves=leaves,
            min_child_samples=100, reg_lambda=10, random_state=20261008,
            n_jobs=4, verbosity=-1)
        model.fit(x, y, sample_weight=weights)
        models.append(model)
    return {'model': QuantileResidualModel(models), 'columns': COLS,
            'trained_through': through, 'quantile_levels': list(LEVELS),
            'version': VERSION, 'feature_engine': 'array_stable_publication_v1',
            'training': {'rows': len(frame), 'trees': trees, 'leaves': leaves,
            'positive_weight_rows': int((weights > 0).sum()),
            'first_month': str(frame.month.min()), 'last_month': str(frame.month.max()),
            'minimum_weight': float(weights.min()),
            'effective_sample_size': float(weights.sum() ** 2 / (weights @ weights))}}


def metrics(frame, log_quantiles):
    q = np.asarray(log_quantiles, dtype=float)
    actual = frame.actual.to_numpy()
    if q.shape != (len(frame), 3) or not len(frame) or not np.isfinite(q).all():
        raise ValueError('Metrics require aligned, finite P10/P50/P90 predictions')
    if not np.isfinite(actual).all() or (np.diff(q, axis=1) < 0).any():
        raise ValueError('Invalid targets or crossing quantiles')
    errors = actual[:, None] - q
    pinball = np.maximum(np.asarray(LEVELS)*errors, (np.asarray(LEVELS)-1)*errors)
    factor = frame.area.to_numpy() / 10000
    amounts = np.exp(q)*factor[:, None]
    observed = np.exp(actual)*factor
    covered = (actual >= q[:, 0]) & (actual <= q[:, 2])
    identities = frame.region.astype(str) + '|' + frame.complex.astype(str)
    # Keep transaction-weighted and complex-balanced results separate.
    balanced = pd.Series(covered.astype(float)).groupby(identities.to_numpy()).mean().mean()
    return {'transactions': len(frame), 'complexes': int(identities.nunique()),
            'pinball_log': dict(zip(NAMES, map(float, pinball.mean(axis=0)))),
            'mean_pinball_log': float(pinball.mean()),
            'cdf_pct': dict(zip(NAMES, map(float, (actual[:, None] <= q).mean(axis=0)*100))),
            'coverage80_pct': float(covered.mean()*100),
            'complex_balanced_coverage80_pct': float(balanced*100),
            'median_mae_oku': float(np.abs(amounts[:, 1]-observed).mean()),
            'median_mape_pct': float((np.abs(amounts[:, 1]/observed-1)).mean()*100),
            'mean_width_oku': float((amounts[:, 2]-amounts[:, 0]).mean())}


def evaluate(frame, artifact):
    if not (pd.to_datetime(frame.month+'-01') > pd.Timestamp(artifact['trained_through'])).all():
        raise ValueError('Evaluation overlaps training')
    raw = artifact['model'].raw_quantiles(frame)
    q = frame.anchor.to_numpy()[:, None] + artifact['model'].predict_quantiles(frame)
    result = {'overall': metrics(frame, q),
              'raw_crossing_pct': float((np.diff(raw, axis=1) < 0).any(axis=1).mean()*100),
              'groups': []}
    groups = {'region': frame.region,
              'month': frame.month,
              'history': np.where(frame.n90 < 3, 'sparse_lt3', 'supported_ge3'),
              'floor': np.where(frame.floor <= 3, 'low_1to3', np.where(frame.floor.isna(), 'missing', 'above3'))}
    for dimension, labels in groups.items():
        for label in sorted(set(labels)):
            mask = np.asarray(labels) == label
            result['groups'].append({'dimension': dimension, 'label': label,
                **metrics(frame.loc[mask], q[mask])})
    return result


def attach_quantiles(valuation, row, spec):
    """Do not publish candidate tails until their own validation is approved."""
    from estate.models.nowcast.valuation import canonical_price
    config = spec.get('quantiles', {})
    if config.get('levels') != list(LEVELS) or config.get('validation_passed') is not True:
        raise ValueError('Direct quantile release is not validated')
    if (not config.get('validation_period') or
            not isinstance(config.get('historical_coverage_pct'), (float, int)) or
            not 0 <= config['historical_coverage_pct'] <= 100):
        raise ValueError('Missing quantile coverage evidence')
    values = [float(row[name+'_price_oku']) for name in NAMES]
    if not np.isfinite(values).all() or min(values) <= 0 or values != sorted(values):
        raise ValueError('Invalid ordered quantile prices')
    if canonical_price(values[1]) != valuation['price_billion']:
        raise ValueError('P50 does not match the canonical point price')
    valuation['quantiles'] = {'status': 'available', 'levels': list(LEVELS),
        'prices_billion': dict(zip(NAMES, map(canonical_price, values))),
        'method': config.get('method', 'direct_pinball_relative_lagged_features'),
        'median_method': config.get('median_method', 'direct_pinball'),
        'basis': 'conditional_transaction_price_at_representative_floor',
        'nominal_coverage': .8, 'validation_period': config['validation_period'],
        'historical_coverage_pct': config['historical_coverage_pct']}
    return valuation
