from copy import deepcopy

import pytest

from estate_release_status import attach_release_status


def source():
    return {'generated_at': '2026-09-10', 'collection': {
        'complete': True, 'fetched_at': '2026-09-10T22:48:13+00:00', 'sha256': 'source-hash'}}


def test_skipped_collection_does_not_make_old_data_fresh():
    data = source()
    original = deepcopy(data)
    attach_release_status(data, environ={'ESTATE_REFRESH_STATUS': 'skipped_missing_key',
                          'ESTATE_REFRESH_ATTEMPTED_AT': '2026-09-15T01:00:00Z'}, now='2026-09-15T02:00:00Z')
    assert data['collection'] == original['collection']
    assert data['generated_at'] == original['generated_at']
    assert data['release_status']['transaction_refresh']['status'] == 'skipped_missing_key'


def test_ui_release_preserves_prior_failed_attempt_and_does_not_claim_collection():
    data = source()
    data['release_status'] = {'transaction_refresh': {'status': 'skipped_missing_key', 'attempted_at': '2026-09-15T01:00:00Z'}}
    previous = deepcopy(data['release_status']['transaction_refresh'])
    attach_release_status(data, ui_only=True, environ={'ESTATE_REFRESH_STATUS': 'completed'})
    assert data['release_status']['transaction_refresh'] == previous
    assert data['release_status']['mode'] == 'ui_only'


def test_legacy_release_marks_refresh_unknown():
    data = attach_release_status(source(), ui_only=True, environ={})
    assert data['release_status']['transaction_refresh']['status'] == 'unknown'


def test_completed_attempt_requires_a_new_collection():
    env = {'ESTATE_REFRESH_STATUS': 'completed', 'ESTATE_REFRESH_ATTEMPTED_AT': '2026-09-15T01:00:00Z'}
    with pytest.raises(ValueError, match='fresh complete collection'):
        attach_release_status(source(), environ=env)
    data = source()
    data['collection']['fetched_at'] = '2026-09-15T01:01:00Z'
    assert attach_release_status(data, environ=env)['release_status']['transaction_refresh']['collection_sha256'] == 'source-hash'


def test_attempt_without_timezone_is_not_recorded_as_success():
    with pytest.raises(ValueError, match='timezone'):
        attach_release_status(source(), environ={'ESTATE_REFRESH_STATUS': 'completed', 'ESTATE_REFRESH_ATTEMPTED_AT': '2026-09-10'})
