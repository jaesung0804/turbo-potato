"""Preserve and restore immutable monthly v2 research forecasts.

The 2026-09 baseline is imported byte-for-byte, never retrospectively refitted.
Later months use the same Seoul/Gwangmyeong 24-month design and both reporting
assumptions. A new forecast is a research record, not a performance promotion.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

from estate_io import write_binary, write_json
from publish_estate_potential import public_payload

MODEL = 'price_activity_relative_growth_24m_policy_v2'
BASELINE = '2026-09'
STATE_NAME = 'potential_manifest.json'
REGIMES = ('historical_policy', 'uniform61')


def sha(body):
    return hashlib.sha256(body).hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def month_origin(now=None, requested=None):
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError('The creation time must include a timezone')
    current = now.astimezone(ZoneInfo('Asia/Seoul')).strftime('%Y-%m-01')
    if requested is not None and requested != current:
        raise ValueError('New forecasts must use the current Korean calendar month start')
    return current


def checked_file(root, item):
    name = item['path']
    if not re.fullmatch(r'[a-zA-Z0-9_.-]+(?:/[a-zA-Z0-9_.-]+)*', name) or '..' in name.split('/'):
        raise ValueError('Unsafe potential state path')
    path = root / name
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Potential state links may not escape the snapshot')
    body = path.read_bytes()
    if len(body) != item['bytes'] or sha(body) != item['sha256']:
        raise ValueError('Potential state checksum mismatch: ' + name)
    return body


def read_state(state):
    state = Path(state)
    m = read_json(state / STATE_NAME)
    if m.get('schema_version') != 1 or m.get('model') != MODEL or not m.get('snapshots'):
        raise ValueError('Unsupported or empty potential state')
    if m['latest'] != max(m['snapshots']):
        raise ValueError('The latest pointer must reference the newest preserved month')
    if {p.name for p in (state / 'snapshots').iterdir() if p.is_dir()} != set(m['snapshots']):
        raise ValueError('Unreferenced potential snapshot directory')
    for month, item in m['snapshots'].items():
        if not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
            raise ValueError('Invalid snapshot month')
        expected = f'snapshots/{month}/manifest.json'
        if item['path'] != expected:
            raise ValueError('Snapshot manifest path does not match its month')
        meta = json.loads(checked_file(state, item))
        if meta['origin'] != month + '-01' or meta['model'] != MODEL:
            raise ValueError('Snapshot identity mismatch')
        names = [f['path'] for f in meta['files']]
        if len(names) != len(set(names)) or not {'snapshot.json.gz', 'public.json.gz', 'validation.json', 'training_manifest.json'} <= set(names):
            raise ValueError('Missing or duplicated snapshot payload')
        for payload in meta['files']:
            checked_file(state / 'snapshots' / month, payload)
    return m


def next_scheduled_at(now=None):
    now = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    scheduled = now.replace(day=10, hour=5, minute=0, second=0, microsecond=0)
    if scheduled <= now:
        year, month = (scheduled.year + 1, 1) if scheduled.month == 12 else (scheduled.year, scheduled.month + 1)
        scheduled = scheduled.replace(year=year, month=month)
    return scheduled.isoformat()


def restore(state, output=Path('metadata/potential_candidates.json.gz'), status_output=None):
    """Validate every saved object before replacing the public research payload."""
    state, output = Path(state), Path(output)
    m = read_state(state)
    body = (state / 'snapshots' / m['latest'] / 'public.json.gz').read_bytes()
    payload = json.loads(gzip.decompress(body))
    if payload['origin'] != m['latest'] + '-01' or payload['model'] != MODEL:
        raise ValueError('The public payload does not match the latest snapshot')
    write_binary(output, body)
    meta = read_json(state / 'snapshots' / m['latest'] / 'manifest.json')
    status = {'status': 'restored', 'origin': payload['origin'], 'archived_origin': payload['origin'],
              'cohort_size': payload['cohort_size'], 'archive_created_at': payload['created_at'],
              'archive_preserved_at': meta['archived_at'], 'public_sha256': sha(body),
              'manifest_sha256': sha((state / STATE_NAME).read_bytes()),
              'cadence': 'monthly', 'scheduled_day': 10, 'scheduled_time_utc': '05:00',
              'next_scheduled_at': next_scheduled_at(), 'last_attempt': m.get('last_attempt'),
              'model_status': 'research_candidate', 'scope': payload['scope']}
    if status_output is not None:
        write_json(status_output, status, indent=2)
    return status


def validation_status(report):
    """Report existing observability criteria; do not invent a return hurdle."""
    regimes = {}
    for regime in REGIMES:
        rows = [r for r in report['results'] if r.get('availability_regime') == regime
                and r.get('horizon_months') == 24 and r.get('method') == 'learned_price']
        regimes[regime] = {'evaluations': len(rows),
                           'with_observed_headline': sum(r.get('observed_complexes', 0) >= 20 for r in rows)}
    ready = all(r['with_observed_headline'] > 0 for r in regimes.values())
    return {'status': 'observed_research_diagnostic' if ready else 'insufficient_outcomes',
            'regimes': regimes, 'performance_promoted': False,
            'meaning': 'The existing 20 observed-complex display condition is required in each lag scenario. No positive-return or new model-selection gate is applied.'}


def archive_snapshot(state, snapshot_path, report_path, public_path, training_path,
                     artifacts=None, source_commits=None, baseline=False):
    state = Path(state)
    snapshot_body = Path(snapshot_path).read_bytes()
    snapshot = json.loads(gzip.decompress(snapshot_body))
    report = read_json(report_path)
    public_body = Path(public_path).read_bytes()
    public = json.loads(gzip.decompress(public_body))
    # Reuse the publisher's source/design guard rather than maintain a weaker copy.
    expected = public_payload(snapshot, sha(snapshot_body), report)
    # Textual guidance may evolve after the baseline was frozen. The complete
    # prediction rows, identities and source/validation results must still match.
    for key in ('origin', 'created_at', 'feature_cutoff', 'model', 'source_sha256',
                'training_rows', 'latest_training_label_available', 'rows', 'cohort_size', 'validation'):
        if public.get(key) != expected[key]:
            raise ValueError('Public payload differs from the validated forecast: ' + key)
    if snapshot['model'] != MODEL:
        raise ValueError('Only the unchanged v2 model may enter this state')
    month = snapshot['origin'][:7]
    if snapshot['origin'] != month + '-01' or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', month):
        raise ValueError('Invalid monthly forecast origin')
    old = read_state(state) if (state / STATE_NAME).exists() else None
    dest = state / 'snapshots' / month
    if (old and month in old['snapshots']) or dest.exists():
        raise FileExistsError('An existing monthly forecast cannot be overwritten')
    if old and month <= old['latest']:
        raise ValueError('Historical backfills cannot replace the latest monthly forecast')
    status = validation_status(report)
    if status['status'] != 'observed_research_diagnostic':
        raise ValueError('Both lag scenarios require observable research diagnostics')
    sources = {'snapshot.json.gz': Path(snapshot_path), 'public.json.gz': Path(public_path),
               'validation.json': Path(report_path), 'training_manifest.json': Path(training_path)}
    for name, path in (artifacts or {}).items():
        if not re.fullmatch(r'(model|training)_(historical_policy|uniform61)\.(joblib|parquet)', name):
            raise ValueError('Unexpected training artifact name')
        sources['training/' + name] = Path(path)
    training = read_json(training_path)
    if not baseline and snapshot.get('training_manifest_sha256') != sha(Path(training_path).read_bytes()):
        raise ValueError('Training manifest fingerprint does not match the forecast')
    if not baseline:
        for key in ('model', 'origin', 'features', 'source_sha256'):
            if training.get(key) != snapshot.get(key):
                raise ValueError('Training provenance differs from the forecast: ' + key)
        required = {f'{kind}_{regime}.{extension}' for regime in REGIMES
                    for kind, extension in (('model', 'joblib'), ('training', 'parquet'))}
        if set(artifacts or {}) != required:
            raise ValueError('Both fitted models and exact training tables must be preserved')
        for regime in REGIMES:
            trained = training['regimes'][regime]
            expected_rows = snapshot['training_rows'] if regime == 'historical_policy' else snapshot['lag_sensitivity']['training_rows']
            if trained['rows'] != expected_rows:
                raise ValueError('Preserved training-row count differs from the forecast')
            if trained['latest_label_available'] > snapshot['origin']:
                raise ValueError('A preserved training label is not mature at the forecast origin')
            if {i['path'] for i in trained['files']} != {f'model_{regime}.joblib', f'training_{regime}.parquet'}:
                raise ValueError('Both training files must be fingerprinted for each lag scenario')
            for item in trained['files']:
                if item['path'] not in artifacts:
                    raise ValueError('Training manifest references an unpreserved artifact')
                body = Path(artifacts[item['path']]).read_bytes()
                if len(body) != item['bytes'] or sha(body) != item['sha256']:
                    raise ValueError('Training artifact fingerprint mismatch')
    files = [{'path': name, 'bytes': path.stat().st_size, 'sha256': sha(path.read_bytes())}
             for name, path in sorted(sources.items())]
    meta = {'schema_version': 1, 'model': MODEL, 'origin': snapshot['origin'],
            'created_at': snapshot['created_at'], 'archived_at': datetime.now(timezone.utc).isoformat(),
            'status': 'research_candidate', 'imported_baseline': baseline,
            'source_sha256': snapshot['source_sha256'], 'source_state_commits': source_commits or {},
            'training_rows': snapshot['training_rows'], 'training_artifacts_status': training['status'],
            'latest_training_label_available': snapshot['latest_training_label_available'],
            'validation': status, 'files': files,
            'code_commit': os.environ.get('GITHUB_SHA'),
            'code_sha256': {name: sha(Path(name).read_bytes()) for name in
                            ('refresh_estate_potential.py', 'export_estate_potential_v2.py',
                             'analyze_estate_potential_horizons.py', 'publish_estate_potential.py')}}
    state.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.potential-', dir=state) as temp:
        staged = Path(temp) / month
        staged.mkdir()
        for name, path in sources.items():
            target = staged / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
        write_json(staged / 'manifest.json', meta, indent=2)
        manifest_body = (staged / 'manifest.json').read_bytes()
        dest.parent.mkdir(parents=True, exist_ok=True)
        staged.rename(dest)
    index = old or {'schema_version': 1, 'model': MODEL, 'snapshots': {}}
    index['snapshots'][month] = {'path': f'snapshots/{month}/manifest.json',
                               'bytes': len(manifest_body), 'sha256': sha(manifest_body)}
    index['latest'] = month
    index['last_attempt'] = {'status': 'preserved_research_candidate', 'origin': snapshot['origin'],
                             'attempted_at': meta['archived_at']}
    write_json(state / STATE_NAME, index, indent=2)
    (state / '.gitattributes').write_text('snapshots/**/*.gz -text\nsnapshots/**/*.joblib -text\nsnapshots/**/*.parquet -text\n*.json text eol=lf\n')
    return {'status': 'preserved_research_candidate', 'origin': snapshot['origin'], 'files': len(files)}


def bootstrap(state, root=Path('.')):
    state, root = Path(state), Path(root)
    if (state / STATE_NAME).exists():
        m = read_state(state)
        return {'status': 'existing_state', 'origin': m['latest'] + '-01'}
    with tempfile.TemporaryDirectory() as temp:
        manifest = Path(temp) / 'training_manifest.json'
        write_json(manifest, {'schema_version': 1, 'status': 'not_saved_by_original_baseline_exporter',
                              'meaning': 'Import the original 2026-09 forecast without refitting. Its raw source fingerprints and training-row count are preserved; fitted model and training-table files were not saved by that exporter.'})
        return archive_snapshot(state, root / 'metadata/potential_shadow_2026-09_policy-v2.json.gz',
                                root / 'reports/estate_potential_horizons.json',
                                root / 'metadata/potential_candidates.json.gz', manifest, baseline=True)


def restore_history(state, output):
    """Require the original 2016–2020 scope, excluding the later 2006 extension."""
    state, output = Path(state), Path(output)
    meta = read_json(state / 'research-history.json')
    if (not meta.get('complete') or meta.get('normalizer_version') != 2
            or meta.get('start') != '201601' or meta.get('end') != '202012'
            or meta.get('region_count') != 26 or meta.get('partition_count') != 1560):
        raise ValueError('Expected the complete 2016–2020 Seoul/Gwangmyeong history')
    body = gzip.decompress((state / 'research-history.csv.gz').read_bytes())
    if sha(body) != meta['sha256']:
        raise ValueError('Historical CSV checksum mismatch')
    write_binary(output, body)
    return meta


def verify_current(source, origin):
    source = Path(source)
    meta = read_json(source.with_suffix('.manifest.json'))
    if (not meta.get('complete') or meta.get('normalizer_version') != 2
            or meta.get('start') != '202101' or meta.get('end', '') < origin[:7].replace('-', '')
            or sha(source.read_bytes()) != meta['sha256']):
        raise ValueError('A complete matching current-month raw collection is required')
    fetched = datetime.fromisoformat(meta['fetched_at'].replace('Z', '+00:00'))
    if fetched.tzinfo is None or fetched.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat() < origin:
        raise ValueError('The raw collection was not observed in the forecast month')
    return meta


def refresh(state, current, history_state, origin=None, source_commits=None, now=None, runner=None):
    state, current = Path(state), Path(current)
    now = now or datetime.now(timezone.utc)
    origin = month_origin(now, origin)
    bootstrap(state)
    previous = read_state(state)
    if origin[:7] in previous['snapshots']:
        return {'status': 'already_frozen', 'origin': origin, 'latest': previous['latest']}
    verify_current(current, origin)
    runner = runner or (lambda command: subprocess.run(command, check=True))
    with tempfile.TemporaryDirectory(prefix='potential-refresh-') as temp:
        work = Path(temp)
        history = work / 'history.csv'
        restore_history(history_state, history)
        report, snapshot, public = work / 'validation.json', work / 'snapshot.json.gz', work / 'public.json.gz'
        cache, training = work / 'features', work / 'training'
        asof = now.astimezone(ZoneInfo('Asia/Seoul')).date().isoformat()
        runner([sys.executable, 'analyze_estate_potential_horizons.py', '--current', str(current),
                '--history', str(history), '--asof', asof, '--origin-start', '2017-01-01',
                '--origin-end', origin, '--horizons', '24', '--regimes', *REGIMES,
                '--cache', str(cache), '--output', str(report)])
        status = validation_status(read_json(report))
        if status['status'] == 'insufficient_outcomes':
            # This is a data-observability failure, not a negative-return gate.
            # Keep the previous public pointer; no forecast for this month exists.
            previous['last_attempt'] = {'status': 'retained_previous_insufficient_outcomes',
                                         'origin': origin, 'attempted_at': now.isoformat(),
                                         'validation': status, 'validation_sha256': sha(report.read_bytes())}
            attempt = state / 'attempts' / (now.strftime('%Y%m%dT%H%M%S%fZ') + '.json')
            write_json(attempt, {'attempt': previous['last_attempt'], 'report': read_json(report)}, indent=2)
            write_json(state / STATE_NAME, previous, indent=2)
            return previous['last_attempt']
        runner([sys.executable, 'export_estate_potential_v2.py', '--origin', origin,
                '--current', str(current), '--history', str(history), '--cache', str(cache),
                '--output', str(snapshot), '--artifacts-dir', str(training)])
        runner([sys.executable, 'publish_estate_potential.py', '--snapshot', str(snapshot),
                '--report', str(report), '--output', str(public)])
        artifacts = {p.name: p for p in training.iterdir() if p.suffix in ('.joblib', '.parquet')}
        return archive_snapshot(state, snapshot, report, public, training / 'manifest.json',
                                artifacts=artifacts, source_commits=source_commits)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['bootstrap', 'restore', 'refresh', 'restore-history'])
    parser.add_argument('--state-dir', required=True)
    parser.add_argument('--output')
    parser.add_argument('--status-output', default='metadata/potential_refresh_status.json')
    parser.add_argument('--current', default='data/capital_area_apt_trade_transactions.csv')
    parser.add_argument('--history-state', default='.work/history-state')
    parser.add_argument('--origin')
    parser.add_argument('--raw-state-commit')
    parser.add_argument('--history-state-commit')
    args = parser.parse_args()
    if args.command == 'bootstrap':
        result = bootstrap(args.state_dir)
    elif args.command == 'restore':
        result = restore(args.state_dir, args.output or 'metadata/potential_candidates.json.gz', args.status_output)
    elif args.command == 'restore-history':
        if not args.output:
            parser.error('restore-history requires --output')
        result = restore_history(args.state_dir, args.output)
    else:
        try:
            result = refresh(args.state_dir, args.current, args.history_state, args.origin,
                             {'estate-raw-state': args.raw_state_commit,
                              'estate-history-state': args.history_state_commit})
        except Exception as error:
            # Preserve a small operational failure record only when the existing
            # archive itself is valid. Never turn an exception into a success.
            state = Path(args.state_dir)
            if (state / STATE_NAME).exists():
                previous = read_state(state)
                previous['last_attempt'] = {'status': 'failed_retained_previous',
                                             'origin': args.origin or month_origin(),
                                             'attempted_at': datetime.now(timezone.utc).isoformat(),
                                             'error_type': type(error).__name__}
                write_json(state / STATE_NAME, previous, indent=2)
            raise
    print(json.dumps(result, ensure_ascii=False))
    if result.get('status') == 'retained_previous_insufficient_outcomes':
        raise SystemExit(2)


if __name__ == '__main__':
    main()
