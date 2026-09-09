import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from estate_regional_nowcast import ARTIFACT, MANIFEST, COLS, PRICE_LEVELS, relative_inputs


def test_frozen_release_routes_rows_and_preserves_incheon_exactly():
    artifact = joblib.load(ARTIFACT)
    legacy = joblib.load('metadata/nowcast_2026.joblib')
    spec = json.loads(Path(MANIFEST).read_text())
    assert hashlib.sha256(Path(ARTIFACT).read_bytes()).hexdigest() == spec['sha256']
    assert hashlib.sha256(Path('metadata/nowcast_2026.joblib').read_bytes()).hexdigest() == spec['legacy_artifact_sha256']
    frame = pd.DataFrame(np.random.default_rng(6).normal(size=(12, len(COLS))), columns=COLS)
    frame['region'] = ['인천광역시', '경기도', '서울특별시', 'unknown'] * 3
    frame.index = [8, 2, 15, 1, 20, 3, 88, 10, 5, 6, 13, 31]
    before = frame.copy(deep=True)
    actual = artifact['model'].predict(frame[artifact['columns']])
    new = frame.region.isin(['경기도', '서울특별시']).to_numpy()
    np.testing.assert_array_equal(actual[~new], legacy['model'].predict(frame.loc[~new, COLS]))
    np.testing.assert_array_equal(actual[new], artifact['model'].full_history.predict(relative_inputs(frame.loc[new])))
    pd.testing.assert_frame_equal(frame, before)
    shifted = frame.copy()
    shifted[['anchor', *PRICE_LEVELS]] += 3
    np.testing.assert_allclose(artifact['model'].predict(shifted)[new], actual[new])
    assert artifact['trained_through'] == '2025-12-31'
    assert spec['policy_selected_at'] == '2026-09-09'
    assert spec['training']['rows'] == spec['training']['positive_weight_rows'] == 3762716
