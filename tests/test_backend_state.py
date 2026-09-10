import io
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import pytest
from backend_state import bundle_partitions, restore_partitions, restore_checkpoints, reuse_history
from backend_state import pull_state, fetch_file, verified_rent_paths
from backend_state import mark_shard, verify_shards
from research_backend_client import BackendError


def test_monthly_archive_is_deterministic_and_restores_bytes(tmp_path):
    root = tmp_path / 'source'
    (root / 'partitions').mkdir(parents=True)
    (root / 'raw_manifest.json').write_text('{}')
    expected = {'202601-11110.json.gz': b'old month', '202609-11110.json.gz': b'new month'}
    for name, body in expected.items():
        (root / 'partitions' / name).write_bytes(body)
    assert bundle_partitions(root)
    before = {p.name: p.read_bytes() for p in (root / 'partition-archives').iterdir()}
    assert bundle_partitions(root)
    assert before == {p.name: p.read_bytes() for p in (root / 'partition-archives').iterdir()}
    target = tmp_path / 'restored'
    shutil.copytree(root / 'partition-archives', target / 'partition-archives')
    restore_partitions(target)
    assert expected == {p.name: p.read_bytes() for p in (target / 'partitions').iterdir()}
    (root / 'partitions/202609-11110.json.gz').write_bytes(b'updated')
    bundle_partitions(root)
    assert before['202601.tar.gz'] == (root / 'partition-archives/202601.tar.gz').read_bytes()
    assert before['202609.tar.gz'] != (root / 'partition-archives/202609.tar.gz').read_bytes()


def test_unsafe_archive_is_rejected_before_extracting_members(tmp_path):
    folder = tmp_path / 'partition-archives'
    folder.mkdir()
    with tarfile.open(folder / '202609.tar.gz', 'w:gz') as archive:
        for name in ['partitions/202609-11110.json.gz', '../outside']:
            member = tarfile.TarInfo(name)
            member.size = 1
            archive.addfile(member, io.BytesIO(b'x'))
    with pytest.raises(ValueError):
        restore_partitions(tmp_path)
    assert not (tmp_path / 'partitions').exists()


def checkpoint_state(tmp_path, monkeypatch):
    import collect_estate_transactions as collector
    codes = sorted(collector.REGIONS)[:2]
    monkeypatch.setattr(collector, 'REGIONS', {code: collector.REGIONS[code] for code in codes})
    state = tmp_path / 'state'
    (state / 'partitions').mkdir(parents=True)
    files = []
    for code in codes:
        rows = [{'CGG_CD': code, 'CTRT_DAY': '20250110'}]
        data = {'complete': True, 'normalizer_version': 2, 'month': '202501', 'code': code,
                'count': 1, 'rows': rows, 'rows_sha256': collector.digest(collector.rows_bytes(rows))}
        body = gzip.compress(json.dumps(data).encode(), mtime=0)
        name = 'partitions/202501-' + code + '.json.gz'
        (state / name).write_bytes(body)
        files.append({'path': name, 'size': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                      'fetched_at': '2026-09-10T00:00:00Z'})
    (state / 'raw_manifest.json').write_text(json.dumps({'schema_version': 2, 'files': files,
        'collection': {'complete': True, 'normalizer_version': 2, 'start': '202501', 'end': '202501',
                       'partition_count': 2}}))
    return state, codes


def test_restored_shard_reuses_complete_checkpoints_without_any_api_fetch(tmp_path, monkeypatch):
    import collect_estate_transactions as collector
    state, codes = checkpoint_state(tmp_path, monkeypatch)
    cache = tmp_path / 'cache'
    restore_checkpoints(state, cache, shard_index=0, shard_count=2)
    assert [path.name for path in cache.iterdir()] == ['202501-' + codes[0] + '.json.gz']
    data = collector.read_partition(next(cache.iterdir()), '202501', codes[0])
    assert data['fetched_at'] == '2026-09-10T00:00:00Z'
    monkeypatch.setattr(collector, 'fetch_partition', lambda *args: pytest.fail('Unexpected API collection'))
    result = collector.collect('', '202501', '202501', cache, tmp_path / 'trades.csv',
                               refresh_months=0, workers=1, shard_index=0, shard_count=2)
    assert result['rows'] == 1 and result['shard_complete']


def test_corrupt_checkpoint_leaves_all_existing_cache_files_unchanged(tmp_path, monkeypatch):
    state, codes = checkpoint_state(tmp_path, monkeypatch)
    cache = tmp_path / 'cache'
    cache.mkdir()
    old = cache / ('202501-' + codes[0] + '.json.gz')
    old.write_bytes(b'previous checkpoint')
    (state / ('partitions/202501-' + codes[1] + '.json.gz')).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='checksum mismatch'):
        restore_checkpoints(state, cache)
    assert old.read_bytes() == b'previous checkpoint'
    assert len(list(cache.iterdir())) == 1


@pytest.mark.parametrize('kind,prefix,start,end', [
    ('history', 'research-history', '201601', '202012'),
    ('older-history', 'research-older-history', '200601', '201512'),
])
def test_reuse_history_verifies_saved_csv_before_replacing_output(tmp_path, kind, prefix, start, end):
    state, output = tmp_path / 'state', tmp_path / 'output'
    state.mkdir()
    body = b'column\nvalue\n'
    compressed = state / (prefix + '.csv.gz')
    compressed.write_bytes(gzip.compress(body, mtime=0))
    metadata = state / (prefix + '.json')
    meta = {'complete': True, 'normalizer_version': 2, 'start': start, 'end': end,
            'rows': 1, 'sha256': hashlib.sha256(body).hexdigest()}
    metadata.write_text(json.dumps(meta))
    assert reuse_history(state, output, kind)['rows'] == 1
    assert (output / compressed.name).read_bytes() == compressed.read_bytes()
    meta['sha256'] = '0' * 64
    metadata.write_text(json.dumps(meta))
    before = (output / metadata.name).read_bytes()
    with pytest.raises(ValueError, match='checksum mismatch'):
        reuse_history(state, output, kind)
    assert (output / metadata.name).read_bytes() == before


class MissingState:
    def __init__(self, sid=None, status=404):
        self.sid, self.status, self.pulls = sid, status, 0

    def json(self, method, path):
        return {'snapshot_id': self.sid}

    def pull(self, name, root):
        self.pulls += 1
        raise BackendError(self.status, 'Restore failed')


def test_rent_initialization_requires_explicit_empty_remote_and_local_state(tmp_path):
    client = MissingState()
    with pytest.raises(BackendError):
        pull_state(client, 'estate-rent-state', tmp_path / 'default')
    assert not (tmp_path / 'default').exists()
    result = pull_state(client, 'estate-rent-state', tmp_path / 'new', True)
    assert result['initialized'] and result['snapshot_id'] is None
    assert list((tmp_path / 'new').iterdir()) == []
    with pytest.raises(ValueError, match='limited'):
        pull_state(client, 'estate-capital-history-state', tmp_path / 'capital', True)
    (tmp_path / 'new' / 'existing').write_text('preserve')
    with pytest.raises(ValueError, match='empty local'):
        pull_state(client, 'estate-rent-state', tmp_path / 'new', True)


@pytest.mark.parametrize('status', [404, 409, 503])
def test_existing_head_restore_error_never_becomes_new_rent_collection(tmp_path, status):
    client = MissingState('a' * 32, status)
    with pytest.raises(BackendError) as error:
        pull_state(client, 'estate-rent-state', tmp_path / 'state', True)
    assert error.value.status == status and client.pulls == 1
    assert not (tmp_path / 'state').exists()


def test_planning_fetch_downloads_only_one_bounded_manifest(tmp_path):
    class SavedState:
        def json(self, method, path):
            return {'snapshot_id': 'a' * 32}

        def snapshot_entries(self, sid):
            assert sid == 'a' * 32
            return iter([{'relative_path': 'collection/manifest.json', 'sha256': 'b' * 64, 'byte_size': 123},
                         {'relative_path': 'collection/seoul/huge.csv.gz', 'sha256': 'c' * 64,
                          'byte_size': 100_000_000}])

        def download(self, sha, target, size):
            assert sha == 'b' * 64 and size == 123 and target == tmp_path / 'checkpoint.json'
            target.write_text('{}')

    result = fetch_file(SavedState(), 'estate-capital-history-state', 'collection/manifest.json',
                        tmp_path / 'checkpoint.json')
    assert result['bytes'] == 123
    with pytest.raises(ValueError, match='bounded manifest'):
        fetch_file(SavedState(), 'estate-capital-history-state', 'collection/seoul/huge.csv.gz',
                   tmp_path / 'checkpoint.json')
    assert (tmp_path / 'checkpoint.json').read_text() == '{}'
    with pytest.raises(ValueError, match='migrate'):
        fetch_file(MissingState(), 'estate-capital-history-state', 'collection/manifest.json',
                   tmp_path / 'checkpoint.json')


def rent_partition(root):
    from collect_estate_rents import URL
    path = root / 'rents/202307-11740.json.gz'
    path.parent.mkdir(parents=True)
    data = {'source': URL, 'complete': True, 'code': '11740', 'month': '202307',
            'fetched_at': '2026-09-11T00:00:00Z', 'rows': [{'dealYear': '2023'}]}
    path.write_bytes(gzip.compress(json.dumps(data).encode(), mtime=0))
    return path, data


def test_saved_rent_partitions_resume_without_api_and_ignore_interrupted_temp(tmp_path, monkeypatch):
    import collect_estate_rents as rents
    path, data = rent_partition(tmp_path)
    (path.parent / '202308-11740.json.tmp').write_text('interrupted write')
    (tmp_path / 'local-report.json').write_text('not a state payload')
    assert verified_rent_paths(tmp_path) == ['rents/' + path.name]
    monkeypatch.setattr(rents.api, 'request', lambda *args: pytest.fail('Unexpected rent API call'))
    rents.collect(['11740'], ['202307'], path.parent, 'test-only')
    assert verified_rent_paths(tmp_path) == ['rents/' + path.name, 'rents/manifest.json']
    data['month'] = '202308'
    path.write_bytes(gzip.compress(json.dumps(data).encode(), mtime=0))
    with pytest.raises(ValueError, match='mismatched'):
        verified_rent_paths(tmp_path)


def test_rent_paths_accept_the_relative_workflow_state_directory(tmp_path, monkeypatch):
    state = tmp_path / '.work/market-data'
    path, _ = rent_partition(state)
    monkeypatch.chdir(tmp_path)
    assert verified_rent_paths(Path('.work/market-data')) == ['rents/' + path.name]


def test_empty_rent_state_cannot_be_committed(tmp_path):
    (tmp_path / 'rents').mkdir()
    (tmp_path / 'rents/202307-11740.json.tmp').write_text('interrupted')
    with pytest.raises(ValueError, match='without completed'):
        verified_rent_paths(tmp_path)


def test_push_updates_displayed_revision_and_excludes_unselected_reports(tmp_path, monkeypatch, capsys):
    import backend_state
    receipt = tmp_path / '.backend-receipt.json'
    receipt.write_text(json.dumps({'snapshot_id': 'a' * 32}))
    (tmp_path / 'collection').mkdir()
    (tmp_path / 'collection/manifest.json').write_text('{}')
    (tmp_path / 'local-report.json').write_text('{}')
    class SavedState:
        def push(self, name, root, paths):
            assert name == 'estate-capital-history-state' and root == tmp_path
            assert paths == ['collection']
            return {'snapshot_id': 'b' * 32, 'files': 1, 'changed': True}
    monkeypatch.setattr(backend_state, 'Client', lambda **kwargs: SavedState())
    monkeypatch.setattr('sys.argv', ['backend_state.py', 'push', '--name', 'estate-capital-history-state',
                                   '--state-dir', str(tmp_path), '--paths', 'collection'])
    backend_state.main()
    capsys.readouterr()
    monkeypatch.setattr('sys.argv', ['backend_state.py', 'revision', '--state-dir', str(tmp_path)])
    backend_state.main()
    assert capsys.readouterr().out.strip() == 'backend:' + 'b' * 32


def shard_source(state, sid='a' * 32, body='original manifest'):
    state.mkdir(parents=True, exist_ok=True)
    (state / '.backend-receipt.json').write_text(json.dumps({'snapshot_id': sid}))
    (state / 'raw_manifest.json').write_text(body)


def test_assembling_four_shards_uses_the_same_saved_origin(tmp_path):
    state, cache = tmp_path / 'state', tmp_path / 'cache'
    shard_source(state)
    for index in range(4):
        mark_shard(state, cache, index, 4)
    assert verify_shards(state, cache, 4) == {'snapshot_id': 'a' * 32, 'verified_shards': 4}
    assert len(list(cache.glob('backend-source-shard-*.json'))) == 4
    (cache / 'backend-source-shard-2.json').unlink()
    with pytest.raises(ValueError, match='Missing'):
        verify_shards(state, cache, 4)


@pytest.mark.parametrize('new_sid,new_body', [('b' * 32, 'original manifest'),
                                           ('a' * 32, 'changed manifest')])
def test_late_pull_cannot_hide_a_writer_change_during_collection(tmp_path, new_sid, new_body):
    state, cache = tmp_path / 'state', tmp_path / 'cache'
    shard_source(state)
    for index in range(4):
        mark_shard(state, cache, index, 4)
    # Simulate the assemble runner restoring a newer head after shards ran.
    shard_source(state, new_sid, new_body)
    with pytest.raises(ValueError, match='changed during collection'):
        verify_shards(state, cache, 4)


def test_mixed_shard_origins_are_rejected_even_if_one_matches_current_head(tmp_path):
    state, cache = tmp_path / 'state', tmp_path / 'cache'
    shard_source(state)
    mark_shard(state, cache, 0, 2)
    shard_source(state, 'b' * 32)
    mark_shard(state, cache, 1, 2)
    with pytest.raises(ValueError, match='origins disagree'):
        verify_shards(state, cache, 2)
