import json

import pytest

from estate_research_progress import attach_research_progress


def test_capital_research_does_not_replace_forecast(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    summary = {'production_changed': False, 'potential': {'headline': '회고 진단'}}
    (reports / 'estate_retraining_summary_20260909.json').write_text(json.dumps(summary))
    snapshot = {'rows': [{'key': 'fixed', 'research_rank': 1}], 'origin': '2026-09-01'}
    result = attach_research_progress(snapshot, tmp_path)
    assert result['rows'] == snapshot['rows']
    assert result['origin'] == snapshot['origin']
    assert result['capital_retraining'] == summary['potential']
    assert 'capital_retraining' not in snapshot


def test_capital_research_refuses_silent_promotion(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'estate_retraining_summary_20260909.json').write_text(json.dumps({'production_changed': True}))
    with pytest.raises(ValueError, match='Retraining research'):
        attach_research_progress({'rows': []}, tmp_path)


def test_new_research_explanation_preserves_frozen_forecast(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'estate_potential_path_summary.json').write_text(json.dumps({
        'production_changed': False, 'passed': False, 'by_horizon': [],
    }))
    snapshot = {'origin': '2026-09-01', 'rows': [{'research_rank': 1, 'prediction': .1}]}
    result = attach_research_progress(snapshot, tmp_path)
    assert result['rows'] == snapshot['rows']
    assert result['origin'] == snapshot['origin']
    assert 'path_validation' not in snapshot
    assert result['path_validation']['passed'] is False
    assert len(result['path_validation']['summary_sha256']) == 64


def test_research_annotation_cannot_claim_a_model_replacement(tmp_path):
    reports = tmp_path / 'reports'
    reports.mkdir()
    (reports / 'estate_potential_path_summary.json').write_text('{"production_changed":true}')
    with pytest.raises(ValueError, match='silently replace'):
        attach_research_progress({'rows': []}, tmp_path)
