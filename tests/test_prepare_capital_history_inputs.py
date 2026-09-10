import csv
import gzip
import hashlib
import io
import json

import pytest
import collect_estate_transactions as collector
from get_molit_apt_trade_data import DASHBOARD_FIELDNAMES
import prepare_capital_history_inputs as inputs


def csv_body(day):
    row = dict.fromkeys(DASHBOARD_FIELDNAMES, '')
    row.update(CTRT_DAY=day, CGG_CD='11110')
    stream = io.StringIO(newline='')
    writer = csv.DictWriter(stream, fieldnames=DASHBOARD_FIELDNAMES)
    writer.writeheader()
    writer.writerow(row)
    return stream.getvalue().encode('utf-8-sig'), row


def source_states(tmp_path):
    states = [tmp_path / name for name in ('older', 'history', 'raw')]
    for state, prefix, start, end in (
            (states[0], 'research-older-history', '200601', '201512'),
            (states[1], 'research-history', '201601', '202012')):
        state.mkdir()
        body, _ = csv_body(start + '01')
        (state / (prefix + '.csv.gz')).write_bytes(gzip.compress(body, mtime=0))
        (state / (prefix + '.json')).write_text(json.dumps({
            'complete': True, 'normalizer_version': 2, 'start': start, 'end': end,
            'sha256': hashlib.sha256(body).hexdigest(), 'rows': 1, 'fetched_at': '2026-09-01'}))
    body, row = csv_body('20260101')
    data = {'complete': True, 'normalizer_version': 2, 'month': '202601', 'code': '11110',
            'rows': [row], 'count': 1, 'rows_sha256': collector.digest(collector.rows_bytes([row]))}
    zipped = gzip.compress(json.dumps(data).encode(), mtime=0)
    raw = states[2]
    (raw / 'partitions').mkdir(parents=True)
    (raw / 'partitions/202601-11110.json.gz').write_bytes(zipped)
    (raw / 'raw_manifest.json').write_text(json.dumps({'schema_version': 2,
        'collection': {'complete': True, 'normalizer_version': 2, 'start': '202601', 'end': '202601',
                       'rows': 1, 'partition_count': 1, 'region_count': 1,
                       'sha256': hashlib.sha256(body).hexdigest()},
        'files': [{'path': 'partitions/202601-11110.json.gz', 'size': len(zipped),
                   'sha256': hashlib.sha256(zipped).hexdigest(), 'fetched_at': '2026-09-01'}]}))
    return states


def test_backend_inputs_preserve_import_commits_and_current_snapshot_ids_without_git(tmp_path, monkeypatch):
    states = source_states(tmp_path)
    before = {str(p): p.read_bytes() for state in states for p in state.rglob('*') if p.is_file()}
    monkeypatch.setattr(inputs.subprocess, 'check_output', lambda *a, **k: pytest.fail('Unexpected Git lookup'))
    kwargs = {}
    for name, value in zip(('older', 'history', 'raw'), 'abc'):
        kwargs[name + '_state_revision'] = 'backend:' + value * 32
        kwargs[name + '_state_source_commit'] = value * 40
    out = tmp_path / 'result'
    result = inputs.prepare(*states, out, **kwargs)
    assert result['history']['rows'] == 2 and result['recent']['rows'] == 1
    for item, value in zip(result['history']['sources'], 'ab'):
        assert item['source_revision'] == 'backend:' + value * 32
        assert item['source_commit'] == item['original_git_commit'] == value * 40
        assert item['source_commit_role'] == 'original_git_import'
    recent = json.loads((out / 'data/capital_area_apt_trade_transactions.manifest.json').read_text())
    assert recent['state_provenance'] == result['recent_state_provenance']
    assert recent['state_provenance']['source_revision'] == 'backend:' + 'c' * 32
    assert before == {str(p): p.read_bytes() for state in states for p in state.rglob('*') if p.is_file()}


def test_legacy_inputs_keep_git_provenance_without_backend_metadata(tmp_path, monkeypatch):
    states = source_states(tmp_path)
    calls = []
    def git(args, **kwargs):
        calls.append(args)
        return str(states.index(type(states[0])(args[2])) + 1) * 40 + '\n'
    monkeypatch.setattr(inputs.subprocess, 'check_output', git)
    result = inputs.prepare(*states, tmp_path / 'result')
    assert len(calls) == 3
    assert [source['source_commit'] for source in result['history']['sources']] == ['1' * 40, '2' * 40]
    assert result['recent_source_commit'] == '3' * 40
    assert 'recent_state_provenance' not in result
    assert 'state_provenance' not in result['recent']


@pytest.mark.parametrize('revision,commit', [('backend:' + 'a' * 32, None), (None, 'a' * 40),
                                         ('backend:missing', 'a' * 40)])
def test_incomplete_backend_provenance_fails_before_restoring(tmp_path, revision, commit):
    with pytest.raises(ValueError, match='original Git import'):
        inputs.prepare(tmp_path, tmp_path, tmp_path, tmp_path / 'result',
                       older_state_revision=revision, older_state_source_commit=commit)
    assert not (tmp_path / 'result').exists()
