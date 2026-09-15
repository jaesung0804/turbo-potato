import copy
import json
from pathlib import Path

import pytest
import numpy as np

from estate_model_review import build_review
from estate_potential_comparison import METHODS, compare_common_origins


def cohort(origin='2020-01-01', **kwargs):
    return [dict(origin=origin, method=method, selected=40, observed=30,
                 observed_complexes=25, complex_median_excess_pct=i, **kwargs)
            for i, method in enumerate(METHODS)]


def test_missing_baseline_cannot_pass_a_vacuous_common_origin_check():
    report = compare_common_origins(cohort()[:-1])
    assert report['common_origins'] == 0
    assert report['excluded_origins'][0]['reasons'] == {'momentum': 'missing_method'}
    assert all(r['same_origins'] == 0 for r in report['comparison'])
    assert report['paired_median_difference_pp'] is None


def test_recompute_coverage_from_counts_not_reported_percentage():
    rows = cohort(observation_rate_pct=99)
    rows[0].update(observed=19, observed_complexes=19)
    assert compare_common_origins(rows)['common_origins'] == 0


def test_all_methods_use_identical_origins_with_missing_outcomes_preserved():
    rows = cohort() + cohort('2021-01-01')
    rows[-1]['complex_median_excess_pct'] = None
    untouched = copy.deepcopy(rows)
    result = compare_common_origins(rows)
    assert rows == untouched
    assert result['common_origins'] == 1
    assert result['paired_origins'][0]['model_missing'] == 10
    assert result['paired_origins'][0]['baseline_missing'] == 10
    assert result['paired_median_difference_pp'] == -1
    assert result['promotion_decision'] == 'not_assessed'


@pytest.mark.parametrize('change', [
    {'observed': 50}, {'selected': True}, {'observed_complexes': 31}, {'observed': -1}
])
def test_inconsistent_outcome_counts_are_rejected(change):
    rows = cohort()
    rows[0].update(change)
    with pytest.raises(ValueError):
        compare_common_origins(rows)


def test_duplicate_origin_cannot_overweight_or_mix_horizons():
    with pytest.raises(ValueError, match='Duplicate'):
        compare_common_origins(cohort() * 2)


def test_live_evaluator_numpy_scalars_are_accepted_and_serializable():
    rows = cohort()
    for row in rows:
        row['complex_median_excess_pct'] = np.float64(row['complex_median_excess_pct'])
    result = compare_common_origins(rows)
    assert result['common_origins'] == 1
    json.dumps(result, allow_nan=False)


def test_existing_frozen_evaluation_reproduces_reported_comparisons():
    source = json.loads(Path('reports/estate_retraining_potential_20260909.json').read_text())
    review = build_review()
    comparison = review['potential']
    assert comparison['common_origins'] == source['common_sufficiently_observed_origins'] == 17
    for actual, original in zip(comparison['comparison'], source['comparison_same_model_eligible_origins']):
        assert actual['method'] == original['method']
        assert actual['same_origins'] == original['same_origins']
        assert actual['median_origin_complex_excess_pct'] == pytest.approx(original['median_origin_complex_excess_pct'])
    assert review['production_changed'] is False
    assert len(review['source_sha256']) == 3
