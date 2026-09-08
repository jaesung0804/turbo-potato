"""Operational invariants for immutable monthly research records."""
from datetime import datetime, timezone
import gzip
import json
from pathlib import Path
import subprocess

import pytest

from estate_io import write_json
from publish_estate_potential import public_payload
from refresh_estate_potential import (
    STATE_NAME, archive_snapshot, bootstrap, month_origin, next_scheduled_at,
    read_json, read_state, refresh, restore, restore_history, sha, verify_current,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def saved(tmp_path):
    state = tmp_path / 'state'
    bootstrap(state, ROOT)
    return state


def inputs(tmp_path):
    current = tmp_path / 'current.csv'
    current.write_bytes(b'fixture current input\n')
    write_json(current.with_suffix('.manifest.json'), {
        'complete': True, 'normalizer_version': 2, 'start': '202101', 'end': '202610',
        'sha256': sha(current.read_bytes()), 'fetched_at': '2026-10-09T20:00:00+00:00',
    })
    history = tmp_path / 'history'
    history.mkdir()
    body = b'fixture older input\n'
    (history / 'research-history.csv.gz').write_bytes(gzip.compress(body))
    write_json(history / 'research-history.json', {
        'complete': True, 'normalizer_version': 2, 'start': '201601', 'end': '202012',
        'region_count': 26, 'partition_count': 1560, 'sha256': sha(body),
    })
    return current, history


def test_month_uses_korean_calendar_and_rejects_backdating():
    now = datetime(2026, 9, 30, 16, tzinfo=timezone.utc)
    assert month_origin(now) == '2026-10-01'
    assert month_origin(now, '2026-10-01') == '2026-10-01'
    with pytest.raises(ValueError, match='current Korean'):
        month_origin(now, '2026-09-01')
    with pytest.raises(ValueError, match='timezone'):
        month_origin(datetime(2026, 10, 1))
    assert next_scheduled_at(now) == '2026-10-10T05:00:00+00:00'
    assert next_scheduled_at(datetime(2026, 12, 10, 5, tzinfo=timezone.utc)) == '2027-01-10T05:00:00+00:00'


def test_baseline_is_byte_preserved_and_same_month_skips_all_research(saved):
    original = (ROOT / 'metadata/potential_shadow_2026-09_policy-v2.json.gz').read_bytes()
    assert (saved / 'snapshots/2026-09/snapshot.json.gz').read_bytes() == original
    before = (saved / STATE_NAME).read_bytes()
    def forbidden(command):
        raise AssertionError('An existing origin must never be retrained')
    result = refresh(saved, Path('missing-current.csv'), Path('missing-history'),
                     now=datetime(2026, 9, 10, 5, tzinfo=timezone.utc), runner=forbidden)
    assert result['status'] == 'already_frozen'
    assert (saved / STATE_NAME).read_bytes() == before
    assert read_json(saved / 'snapshots/2026-09/training_manifest.json')['status'] == 'not_saved_by_original_baseline_exporter'


def test_existing_forecast_cannot_be_overwritten(saved):
    folder = saved / 'snapshots/2026-09'
    before = (saved / STATE_NAME).read_bytes()
    with pytest.raises(FileExistsError, match='cannot be overwritten'):
        archive_snapshot(saved, folder / 'snapshot.json.gz', folder / 'validation.json',
                         folder / 'public.json.gz', folder / 'training_manifest.json', baseline=True)
    assert (saved / STATE_NAME).read_bytes() == before


def test_current_source_checksum_and_collection_month_are_required(tmp_path):
    current, _ = inputs(tmp_path)
    assert verify_current(current, '2026-10-01')['end'] == '202610'
    current.write_bytes(b'changed input')
    with pytest.raises(ValueError, match='matching current-month'):
        verify_current(current, '2026-10-01')
    meta = read_json(current.with_suffix('.manifest.json'))
    meta.update(sha256=sha(current.read_bytes()), end='202609')
    write_json(current.with_suffix('.manifest.json'), meta)
    with pytest.raises(ValueError, match='matching current-month'):
        verify_current(current, '2026-10-01')


def test_history_refuses_other_scope_and_corrupt_csv_without_replacing_output(tmp_path):
    _, history = inputs(tmp_path)
    output = tmp_path / 'restored.csv'
    output.write_bytes(b'previous valid source')
    meta = read_json(history / 'research-history.json')
    meta['start'] = '200601'
    write_json(history / 'research-history.json', meta)
    with pytest.raises(ValueError, match='2016'):
        restore_history(history, output)
    meta['start'] = '201601'
    meta['sha256'] = '0' * 64
    write_json(history / 'research-history.json', meta)
    with pytest.raises(ValueError, match='checksum'):
        restore_history(history, output)
    assert output.read_bytes() == b'previous valid source'


def test_restore_checks_archive_before_replacing_public_or_status(saved, tmp_path):
    output, status = tmp_path / 'public.gz', tmp_path / 'status.json'
    output.write_bytes(b'previous public')
    status.write_bytes(b'previous status')
    (saved / 'snapshots/2026-09/snapshot.json.gz').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum'):
        restore(saved, output, status)
    assert output.read_bytes() == b'previous public'
    assert status.read_bytes() == b'previous status'


def test_research_execution_failure_keeps_previous_latest(saved, tmp_path):
    current, history = inputs(tmp_path)
    before = (saved / STATE_NAME).read_bytes()
    def failed(command):
        raise subprocess.CalledProcessError(1, command)
    with pytest.raises(subprocess.CalledProcessError):
        refresh(saved, current, history, now=datetime(2026, 10, 10, 5, tzinfo=timezone.utc), runner=failed)
    assert (saved / STATE_NAME).read_bytes() == before
    assert read_state(saved)['latest'] == '2026-09'
    assert not (saved / 'snapshots/2026-10').exists()


def test_insufficient_observations_record_failure_and_keep_latest(saved, tmp_path):
    current, history = inputs(tmp_path)
    commands = []
    def no_observations(command):
        commands.append(command)
        assert '--horizons' in command and command[command.index('--horizons') + 1] == '24'
        assert command[command.index('--regimes') + 1:command.index('--regimes') + 3] == ['historical_policy', 'uniform61']
        write_json(Path(command[command.index('--output') + 1]), {'results': []})
    result = refresh(saved, current, history, now=datetime(2026, 10, 10, 5, tzinfo=timezone.utc), runner=no_observations)
    assert len(commands) == 1
    assert result['status'] == 'retained_previous_insufficient_outcomes'
    assert read_state(saved)['latest'] == '2026-09'
    assert len(list((saved / 'attempts').glob('*.json'))) == 1
    assert not (saved / 'snapshots/2026-10').exists()


def test_validation_source_mismatch_cannot_change_latest(saved, tmp_path):
    folder = saved / 'snapshots/2026-09'
    report = read_json(folder / 'validation.json')
    report['sources']['current']['source_sha256'] = '0' * 64
    path = tmp_path / 'wrong-report.json'
    write_json(path, report)
    before = (saved / STATE_NAME).read_bytes()
    with pytest.raises(ValueError, match='source does not match'):
        archive_snapshot(saved, folder / 'snapshot.json.gz', path, folder / 'public.json.gz',
                         folder / 'training_manifest.json', baseline=True)
    assert (saved / STATE_NAME).read_bytes() == before


def test_restore_preserves_prediction_bytes_and_emits_separate_schedule(saved, tmp_path):
    output, status_path = tmp_path / 'public.gz', tmp_path / 'status.json'
    status = restore(saved, output, status_path)
    assert output.read_bytes() == (saved / 'snapshots/2026-09/public.json.gz').read_bytes()
    assert read_json(status_path) == status
    assert status['cadence'] == 'monthly'
    assert status['scheduled_day'] == 10 and status['scheduled_time_utc'] == '05:00'
    assert status['archived_origin'] == '2026-09-01'
    assert status['manifest_sha256'] == sha((saved / STATE_NAME).read_bytes())


def new_month_files(saved, work):
    """Opaque model bytes exercise storage integrity, not model fitting."""
    work.mkdir()
    previous = saved / 'snapshots/2026-09'
    snapshot = json.loads(gzip.decompress((previous / 'snapshot.json.gz').read_bytes()))
    snapshot.update(origin='2026-10-01', created_at='2026-10-10T05:00:00+00:00', feature_cutoff='2026-08-31')
    report = read_json(previous / 'validation.json')
    training = {k: snapshot[k] for k in ('model', 'origin', 'features', 'source_sha256')}
    training.update(status='preserved_fitted_models_and_mature_training_tables', regimes={})
    artifacts = {}
    for regime in ('historical_policy', 'uniform61'):
        files = []
        for kind, extension in (('model', 'joblib'), ('training', 'parquet')):
            name = f'{kind}_{regime}.{extension}'
            path = work / name
            path.write_bytes(('opaque fixture ' + name).encode())
            artifacts[name] = path
            files.append({'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path.read_bytes())})
        rows = snapshot['training_rows'] if regime == 'historical_policy' else snapshot['lag_sensitivity']['training_rows']
        training['regimes'][regime] = {'rows': rows, 'latest_label_available': '2026-08-01', 'files': files}
    training_path = work / 'training_manifest.json'
    write_json(training_path, training)
    snapshot['training_manifest_sha256'] = sha(training_path.read_bytes())
    snapshot_path, report_path, public_path = work / 'snapshot.gz', work / 'report.json', work / 'public.gz'
    body = gzip.compress(json.dumps(snapshot, ensure_ascii=False).encode(), mtime=0)
    snapshot_path.write_bytes(body)
    write_json(report_path, report)
    public = public_payload(snapshot, sha(body), report)
    public_path.write_bytes(gzip.compress(json.dumps(public, ensure_ascii=False).encode(), mtime=0))
    return snapshot_path, report_path, public_path, training_path, artifacts


def test_new_month_preserves_training_and_advances_latest_after_validation(saved, tmp_path):
    baseline = (saved / 'snapshots/2026-09/manifest.json').read_bytes()
    snap, report, public, training, artifacts = new_month_files(saved, tmp_path / 'new-month')
    result = archive_snapshot(saved, snap, report, public, training, artifacts=artifacts)
    assert result['origin'] == '2026-10-01'
    assert read_state(saved)['latest'] == '2026-10'
    assert (saved / 'snapshots/2026-09/manifest.json').read_bytes() == baseline
    target = tmp_path / 'restored.gz'
    restore(saved, target)
    assert target.read_bytes() == public.read_bytes()
    for name, path in artifacts.items():
        assert (saved / 'snapshots/2026-10/training' / name).read_bytes() == path.read_bytes()


def test_training_fingerprint_mismatch_does_not_advance_latest(saved, tmp_path):
    snap, report, public, training, artifacts = new_month_files(saved, tmp_path / 'new-month')
    artifacts['model_historical_policy.joblib'].write_bytes(b'changed fitted model')
    before = (saved / STATE_NAME).read_bytes()
    with pytest.raises(ValueError, match='Training artifact fingerprint'):
        archive_snapshot(saved, snap, report, public, training, artifacts=artifacts)
    assert (saved / STATE_NAME).read_bytes() == before
    assert not (saved / 'snapshots/2026-10').exists()
