import gzip
import hashlib
import json

import pytest

from analyze_estate_reporting_lag import audit, inspect_partition
from estate_vintages import advance

A = '2026-09-01T00:00:00+00:00'
B = '2026-09-02T00:00:00+00:00'
C = '2026-09-03T00:00:00+00:00'
ROW = {'CTRT_DAY': '20260801', 'RTRCN_DAY': '', 'price': '100'}


def partition(rows, ledger):
    return {'complete': True, 'month': '202608', 'code': '11110',
            'rows': rows, 'observation_ledger': ledger}


def test_baseline_old_contract_is_not_a_measured_reporting_delay():
    data = partition([ROW, ROW], advance(None, [ROW, ROW], A))
    result = inspect_partition(data)
    assert result['events'] == []
    assert result['counts']['baseline_left_censored_rows'] == 2
    assert result['counts']['baseline_left_censored_signatures'] == 1


def test_unchanged_poll_narrows_new_signature_appearance_interval():
    ledger = advance(None, [], A)
    ledger = advance(ledger, [], B)
    ledger = advance(ledger, [ROW], C)
    event = inspect_partition(partition([ROW], ledger))['events'][0]
    assert event['prior_known_absent_at'] == B
    assert event['first_observed_at'] == C
    assert event['interval_width_days'] == 1
    assert event['first_observed_age_days'] == 33
    assert event['absence_bound_kind'] == 'explicit_poll'


def test_legacy_archive_has_only_conservative_absence_bound():
    other = {**ROW, 'price': '99'}
    ledger = advance(None, [], A)
    ledger = advance(ledger, [other], B)
    ledger = advance(ledger, [other, ROW], C)
    ledger['schema_version'] = 1
    del ledger['observation_times']
    del ledger['observation_times_complete_since']
    result = inspect_partition(partition([other, ROW], ledger))
    assert {event['prior_known_absent_at'] for event in result['events']} == {A}
    assert result['counts']['partitions_with_explicit_poll_history'] == 0


def test_corrections_and_duplicate_increases_are_not_invented_contract_links():
    corrected = {**ROW, 'RTRCN_DAY': '20260902'}
    ledger = advance(None, [ROW], A)
    ledger = advance(ledger, [ROW, ROW, corrected], B)
    ledger = advance(ledger, [corrected], C)
    result = inspect_partition(partition([corrected], ledger))
    assert result['counts']['post_baseline_new_signatures'] == 1
    assert result['counts']['new_cancelled_signatures'] == 1
    assert result['counts']['new_active_signatures'] == 0
    assert result['counts']['additional_row_copies_on_existing_signatures'] == 1
    assert result['counts']['removed_row_copies'] == 2


def test_corrupt_or_incomplete_source_cannot_establish_absence():
    data = partition([ROW], advance(None, [ROW], A))
    data['rows'] = []
    with pytest.raises(ValueError, match='multiplicity'):
        inspect_partition(data)
    data['complete'] = False
    with pytest.raises(ValueError, match='Incomplete'):
        inspect_partition(data)


def test_end_to_end_provenance_and_korean_calendar_bins(tmp_path):
    new = {**ROW, 'CTRT_DAY': '20260901'}
    at = '2026-09-01T16:00:00+00:00'  # September 2 in Korea.
    ledger = advance(advance(None, [], A), [new], at)
    data = partition([new], ledger)
    body = gzip.compress(json.dumps(data).encode(), mtime=0)
    (tmp_path / 'partitions').mkdir()
    path = tmp_path / 'partitions' / '202608-11110.json.gz'
    path.write_bytes(body)
    manifest = {'collection': {'rows': 1}, 'files': [{
        'path': 'partitions/202608-11110.json.gz', 'size': len(body),
        'sha256': hashlib.sha256(body).hexdigest(), 'fetched_at': at}]}
    (tmp_path / 'raw_manifest.json').write_text(json.dumps(manifest))
    report = audit(tmp_path)
    assert report['first_observed_by_kst_date']['2026-09-02']['new_signatures'] == 1
    assert report['contract_to_first_observed_age_days'][0]['active_signatures'] == 1
    assert report['interpretation']['is_true_publication_delay'] is False
    assert report['interpretation']['correction_weight_fitted'] is False
    assert report['source']['all_partition_checksums_verified'] is True
    path.write_bytes(body + b'x')
    with pytest.raises(ValueError, match='checksum'):
        audit(tmp_path)
