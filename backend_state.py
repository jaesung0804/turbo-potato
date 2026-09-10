"""Use immutable backend snapshots as transport for existing verified state formats."""
import argparse
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tarfile
import tempfile
from research_backend_client import Client, safe_path


def pull_state(client, name, root, initialize_if_missing=False):
    if initialize_if_missing:
        if name != 'estate-rent-state':
            raise ValueError('Explicit initialization is limited to the new rent state')
        head = client.json('GET', '/snapshot-heads/' + name)
        if head['snapshot_id'] is None:
            if root.exists() and any(root.iterdir()):
                raise ValueError('A new rent state requires an empty local directory')
            root.mkdir(parents=True, exist_ok=True)
            # No receipt is fabricated for an existing head. Client.push already
            # permits the normal first-write CAS when both head and receipt are None.
            return {'snapshot_id': None, 'files': 0, 'downloaded': 0, 'initialized': True}
    return client.pull(name, root)


def fetch_file(client, name, relative_path, output):
    """Read a bounded planning manifest without restoring an entire raw corpus."""
    sid = client.json('GET', '/snapshot-heads/' + name)['snapshot_id']
    if not sid:
        raise ValueError('No saved backend snapshot; migrate the existing state first')
    selected = [entry for entry in client.snapshot_entries(sid) if entry['relative_path'] == relative_path]
    if len(selected) != 1 or not 0 < selected[0]['byte_size'] <= 16 * 1024 * 1024:
        raise ValueError('Expected exactly one bounded manifest in the saved snapshot')
    entry = selected[0]
    client.download(entry['sha256'], output, entry['byte_size'])
    return {'snapshot_id': sid, 'relative_path': relative_path, 'bytes': entry['byte_size']}


def verified_rent_paths(root):
    """Publish only completed partitions, including progress from interrupted runs."""
    from collect_estate_rents import URL, api
    root = Path(root).resolve()
    folder = safe_path(root, 'rents')
    paths, records = [], {}
    for path in sorted(folder.glob('*.json.gz')):
        safe_path(root.resolve(), path.relative_to(root).as_posix())
        match = re.fullmatch(r'(\d{6})-(\d{5})\.json\.gz', path.name)
        if not match or not 1 <= int(match[1][-2:]) <= 12 or match[2] not in api.REGIONS:
            raise ValueError('Invalid rent partition path')
        data = json.loads(gzip.decompress(path.read_bytes()))
        if (data.get('complete') is not True or data.get('source') != URL
                or data.get('month') != match[1] or data.get('code') != match[2]
                or not isinstance(data.get('rows'), list) or not data.get('fetched_at')):
            raise ValueError('Incomplete or mismatched rent partition')
        records[(match[1], match[2])] = data
        paths.append(path.relative_to(root).as_posix())
    if not paths:
        raise ValueError('Cannot persist or reuse rent state without completed partitions')
    manifest = safe_path(root.resolve(), 'rents/manifest.json')
    if manifest.exists():
        meta = json.loads(manifest.read_text())
        if meta.get('source') != URL or meta.get('complete') is not True:
            raise ValueError('Invalid rent collection manifest')
        seen = set()
        for item in meta['partitions']:
            key = (item['month'], item['code'])
            data = records.get(key)
            if (key in seen or not data or item['rows'] != len(data['rows'])
                    or item['fetched_at'] != data['fetched_at']):
                raise ValueError('Rent manifest differs from completed partitions')
            seen.add(key)
        paths.append('rents/manifest.json')
    return paths


def bundle_partitions(root):
    """Group region partitions by month to stay within a free request budget."""
    if not (root / 'raw_manifest.json').exists() or not (root / 'partitions').is_dir():
        return False
    safe_path(root.resolve(), 'partitions')
    groups = {}
    for path in sorted((root / 'partitions').glob('*.json.gz')):
        if path.is_symlink() or not re.fullmatch(r'\d{6}-\d{5}\.json\.gz', path.name):
            raise ValueError('Invalid raw partition path')
        groups.setdefault(path.name[:6], []).append(path)
    folder = safe_path(root.resolve(), 'partition-archives')
    folder.mkdir(exist_ok=True)
    for month, paths in groups.items():
        fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
        try:
            with os.fdopen(fd, 'wb') as stream, gzip.GzipFile(fileobj=stream, filename='', mode='wb', mtime=0, compresslevel=1) as zipped:
                with tarfile.open(fileobj=zipped, mode='w|', format=tarfile.PAX_FORMAT) as archive:
                    for path in paths:
                        info = archive.gettarinfo(str(path), arcname='partitions/' + path.name)
                        info.uid = info.gid = info.mtime = 0
                        info.uname = info.gname = ''
                        info.mode = 0o644
                        with path.open('rb') as source:
                            archive.addfile(info, source)
            os.replace(temporary, folder / (month + '.tar.gz'))
        finally:
            Path(temporary).unlink(missing_ok=True)
    return True


def restore_partitions(root):
    folder = safe_path(root.resolve(), 'partition-archives')
    for path in sorted(folder.glob('*.tar.gz')):
        if not re.fullmatch(r'\d{6}\.tar\.gz', path.name):
            raise ValueError('Invalid monthly archive name')
        with tarfile.open(path, 'r:gz') as archive:
            members, names, total = [], set(), 0
            for member in archive:
                total += member.size
                if (not member.isfile() or not re.fullmatch(r'partitions/' + path.name[:6] + r'-\d{5}\.json\.gz', member.name)
                        or member.name in names or member.size > 64 * 1024 * 1024
                        or total > 2 * 1024 * 1024 * 1024 or len(members) >= 2000):
                    raise ValueError('Invalid monthly archive member')
                names.add(member.name); members.append(member)
            for member in members:
                target = safe_path(root.resolve(), member.name)
                target.parent.mkdir(parents=True, exist_ok=True)
                fd, temporary = tempfile.mkstemp(dir=target.parent, suffix='.tmp')
                try:
                    with os.fdopen(fd, 'wb') as out, archive.extractfile(member) as source:
                        shutil.copyfileobj(source, out, 1024 * 1024)
                    os.replace(temporary, target)
                finally:
                    Path(temporary).unlink(missing_ok=True)


def restore_checkpoints(state, cache, shard_index=0, shard_count=1):
    """Restore only this collector shard; do not rebuild a whole-market CSV in each job."""
    from collect_estate_transactions import REGIONS, month_range, read_partition
    if shard_count not in {1, 2, 4} or not 0 <= shard_index < shard_count:
        raise ValueError('Invalid collector shard')
    manifest = json.loads((state / 'raw_manifest.json').read_text(encoding='utf-8'))
    meta = manifest['collection']
    if manifest.get('schema_version') != 2 or not meta.get('complete') or meta.get('normalizer_version') != 2:
        raise ValueError('Migrate a verified partition-format raw state before collecting')
    codes = set(sorted(REGIONS)[shard_index::shard_count])
    months = set(month_range(meta['start'], meta['end']))
    names, selected = set(), []
    for item in manifest['files']:
        match = re.fullmatch(r'partitions/(\d{6})-(\d{5})\.json\.gz', item['path'])
        if not match or item['path'] in names or match[1] not in months or match[2] not in REGIONS:
            raise ValueError('Invalid or duplicate saved checkpoint')
        names.add(item['path'])
        if match[2] in codes:
            selected.append((item, match[1], match[2]))
    if len(names) != meta['partition_count'] or len(selected) != len(months) * len(codes):
        raise ValueError('Saved state does not cover this collector shard')
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='checkpoint-restore-', dir=cache) as temporary:
        staged = Path(temporary)
        for item, month, code in selected:
            path = safe_path(state.resolve(), item['path'])
            body = path.read_bytes()
            if len(body) != item['size'] or hashlib.sha256(body).hexdigest() != item['sha256']:
                raise ValueError('Saved checkpoint checksum mismatch')
            data = read_partition(path, month, code)
            data['fetched_at'] = item.get('fetched_at')
            (staged / path.name).write_bytes(gzip.compress(
                json.dumps(data, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
        for source in staged.iterdir():
            os.replace(source, safe_path(cache.resolve(), source.name))
    return {'restored_checkpoints': len(selected), 'shard_index': shard_index}


def raw_source_receipt(state):
    sid = json.loads((state / '.backend-receipt.json').read_text(encoding='utf-8'))['snapshot_id']
    if not isinstance(sid, str) or not re.fullmatch(r'[a-f0-9]{32}', sid):
        raise ValueError('A committed backend raw snapshot must be restored first')
    with safe_path(state.resolve(), 'raw_manifest.json').open('rb') as source:
        sha = hashlib.file_digest(source, 'sha256').hexdigest()
    return {'schema_version': 1, 'snapshot_name': 'estate-raw-state',
            'snapshot_id': sid, 'raw_manifest_sha256': sha}


def mark_shard(state, cache, shard_index, shard_count):
    """Bind a shard artifact to the immutable source it read before collection."""
    from estate_io import write_json
    if shard_count not in {1, 2, 4} or not 0 <= shard_index < shard_count:
        raise ValueError('Invalid collector shard')
    value = raw_source_receipt(state) | {'shard_index': shard_index, 'shard_count': shard_count}
    cache.mkdir(parents=True, exist_ok=True)
    # A non-hidden filename travels with actions/upload-artifact's default settings.
    write_json(safe_path(cache.resolve(), f'backend-source-shard-{shard_index}.json'), value)
    return value


def verify_shards(state, cache, shard_count):
    """Refuse a newer destination head or artifacts assembled from mixed origins."""
    if shard_count not in {1, 2, 4}:
        raise ValueError('Invalid collector shard count')
    expected = raw_source_receipt(state)
    names = {f'backend-source-shard-{index}.json' for index in range(shard_count)}
    if {path.name for path in cache.glob('backend-source-shard-*.json')} != names:
        raise ValueError('Missing or unexpected backend shard provenance')
    for index in range(shard_count):
        path = safe_path(cache.resolve(), f'backend-source-shard-{index}.json')
        if path.stat().st_size > 4096:
            raise ValueError('Invalid backend shard provenance')
        value = json.loads(path.read_text(encoding='utf-8'))
        if value != expected | {'shard_index': index, 'shard_count': shard_count}:
            raise ValueError('Raw snapshot changed during collection or shard origins disagree; restart from one saved state')
    return {'snapshot_id': expected['snapshot_id'], 'verified_shards': shard_count}


def reuse_history(state, output, kind):
    """Reuse an immutable, complete historical export without another API collection."""
    prefix, start, end = {
        'history': ('research-history', '201601', '202012'),
        'older-history': ('research-older-history', '200601', '201512'),
    }[kind]
    metadata = safe_path(state.resolve(), prefix + '.json')
    compressed = safe_path(state.resolve(), prefix + '.csv.gz')
    meta = json.loads(metadata.read_text(encoding='utf-8'))
    if (not meta.get('complete') or meta.get('normalizer_version') != 2
            or meta.get('start') != start or meta.get('end') != end or meta.get('rows', 0) <= 0):
        raise ValueError('Historical snapshot is incomplete or covers the wrong period')
    if meta.get('gzip_sha256'):
        with compressed.open('rb') as source:
            if hashlib.file_digest(source, 'sha256').hexdigest() != meta['gzip_sha256']:
                raise ValueError('Historical compressed checksum mismatch')
    with gzip.open(compressed, 'rb') as source:
        if hashlib.file_digest(source, 'sha256').hexdigest() != meta['sha256']:
            raise ValueError('Historical CSV checksum mismatch')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='history-restore-', dir=output) as temporary:
        for source in (metadata, compressed):
            staged = Path(temporary) / source.name
            shutil.copyfile(source, staged)
        for staged in Path(temporary).iterdir():
            os.replace(staged, safe_path(output.resolve(), staged.name))
    return {'reused': prefix, 'rows': meta['rows'], 'start': start, 'end': end}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['pull', 'push', 'check', 'revision', 'restore-checkpoints', 'reuse-history', 'fetch-file', 'mark-shard', 'verify-shards'])
    parser.add_argument('--name', default='estate-raw-state')
    parser.add_argument('--state-dir', type=Path, default=Path('.work/raw-state'))
    parser.add_argument('--cache', type=Path, default=Path('data/molit_cache_v3'))
    parser.add_argument('--shard-index', type=int, default=0)
    parser.add_argument('--shard-count', type=int, default=1)
    parser.add_argument('--history-kind', choices=['history', 'older-history'], default='history')
    parser.add_argument('--output-dir', type=Path, default=Path('.work/history'))
    parser.add_argument('--paths', nargs='+', help='Explicit state-relative paths to publish')
    parser.add_argument('--relative-path', help='Single saved manifest to fetch for planning')
    parser.add_argument('--output', type=Path, help='Destination for fetch-file')
    parser.add_argument('--initialize-if-missing', action='store_true',
                        help='Explicit first collection of estate-rent-state only; existing states must restore successfully')
    args = parser.parse_args()
    if args.initialize_if_missing and args.command != 'pull':
        parser.error('--initialize-if-missing requires pull')
    if args.command == 'fetch-file' and (not args.relative_path or not args.output):
        parser.error('fetch-file requires --relative-path and --output')
    if args.command == 'restore-checkpoints':
        print(json.dumps(restore_checkpoints(args.state_dir, args.cache, args.shard_index, args.shard_count)))
        return
    if args.command == 'mark-shard':
        print(json.dumps(mark_shard(args.state_dir, args.cache, args.shard_index, args.shard_count)))
        return
    if args.command == 'verify-shards':
        print(json.dumps(verify_shards(args.state_dir, args.cache, args.shard_count)))
        return
    if args.command == 'reuse-history':
        print(json.dumps(reuse_history(args.state_dir, args.output_dir, args.history_kind)))
        return
    receipt = args.state_dir / '.backend-receipt.json'
    if args.command == 'revision':
        sid = json.loads(receipt.read_text(encoding='utf-8'))['snapshot_id']
        if not sid:
            raise ValueError('No committed backend snapshot exists yet')
        print('backend:' + sid)
        return
    client = Client(project='estate')
    if args.command == 'check':
        result = client.json('GET', '/ready')
    elif args.command == 'fetch-file':
        result = fetch_file(client, args.name, args.relative_path, args.output)
    elif args.command == 'pull':
        result = pull_state(client, args.name, args.state_dir, args.initialize_if_missing)
        restore_partitions(args.state_dir)
        if args.name == 'estate-rent-state' and result['snapshot_id']:
            verified_rent_paths(args.state_dir)
        receipt.write_text(json.dumps(result), encoding='utf-8')
    else:
        # Existing validators/packers have already constructed this state directory.
        # Explicit immediate children omit the worktree's Git metadata and local secrets.
        bundled = bundle_partitions(args.state_dir)
        paths = args.paths or [p.name for p in args.state_dir.iterdir()
                 if not (bundled and p.name == 'partitions')
                 if p.name not in {'.git', '.backend-receipt.json', '.research-backend'} and not p.name.startswith('.env') and not p.name.endswith('.tmp')]
        if not paths:
            raise ValueError('Cannot persist empty state')
        if args.name == 'estate-rent-state':
            # Incomplete atomic-write .tmp files are never snapshot payloads.
            paths = verified_rent_paths(args.state_dir)
        result = client.push(args.name, args.state_dir, paths)
        receipt.write_text(json.dumps(result), encoding='utf-8')
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    main()
