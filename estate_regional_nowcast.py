"""Frozen regional adoption of the validated full-history residual model."""
import numpy as np

from estate_nowcast import PRICE_FEATURES, ACTIVITY_FEATURES, FLOOR_FEATURES

COLS = PRICE_FEATURES + ACTIVITY_FEATURES + FLOOR_FEATURES
PRICE_LEVELS = [c for c in PRICE_FEATURES if c not in ('area', 'age', 'anchor')]
NEW_REGIONS = ('서울특별시', '경기도')
ARTIFACT = 'metadata/nowcast_2026_capital_v2.joblib'
MANIFEST = 'metadata/nowcast_2026_capital_v2.json'


def relative_inputs(frame):
    x = frame[COLS].copy()
    x[PRICE_LEVELS] = x[PRICE_LEVELS].sub(frame.anchor, axis=0)
    return x.drop(columns='anchor')


class RegionalResidualModel:
    """Return log residuals in input order; region never enters a tree."""

    def __init__(self, legacy, full_history):
        self.legacy = legacy
        self.full_history = full_history

    def predict(self, frame):
        use_new = frame.region.isin(NEW_REGIONS).to_numpy()
        result = np.empty(len(frame), dtype=float)
        if use_new.any():
            result[use_new] = self.full_history.predict(relative_inputs(frame.loc[use_new]))
        if (~use_new).any():
            result[~use_new] = self.legacy.predict(frame.loc[~use_new, COLS])
        return result


def component_version(spec, region):
    return spec.get('regional_models', {}).get(region, spec.get('fallback_model', spec.get('version')))
