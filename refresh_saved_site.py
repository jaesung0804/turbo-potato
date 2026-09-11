"""Refresh only the UI of a checksum-verified, bounded existing public release.

Never collect, train, restore private state, or change the data observation date.
"""
import argparse
import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path, PurePosixPath
from urllib.parse import urljoin, urlsplit
from urllib.request import Request, urlopen

from estate_ui_release import copy_ui

MAX_RELEASE_BYTES = 64 * 1024 * 1024
MAX_FILE_BYTES = 16 * 1024 * 1024


def safe_data_path(path):
    if not isinstance(path, str) or '\\' in path or urlsplit(path).scheme or urlsplit(path).netloc:
        raise ValueError('Invalid public data path')
    parts = PurePosixPath(path).parts
    if not parts or parts[0] != 'data' or '..' in parts or '?' in path or '#' in path:
        raise ValueError('Public data must remain inside data/')
    return path


def fetch(base, path, limit):
    request = Request(urljoin(base, path), headers={'Cache-Control': 'no-cache', 'User-Agent': 'estate-ui-release'})
    with urlopen(request, timeout=45) as response:
        body = response.read(limit + 1)
    if len(body) > limit:
        raise ValueError('Public release exceeds the bounded download limit')
    return body


def refresh(base, output, code_commit, source=Path('web'), reader=None):
    if urlsplit(base).scheme != 'https' or not code_commit:
        raise ValueError('An HTTPS saved release and explicit code commit are required')
    base = base.rstrip('/') + '/'
    reader = reader or (lambda path, limit: fetch(base, path, limit))
    old_bytes = reader('data/dashboard_manifest.json', 4 * 1024 * 1024)
    manifest = json.loads(old_bytes)
    if not manifest.get('collection', {}).get('complete') or manifest['collection'].get('normalizer_version') != 2:
        raise ValueError('A verified complete saved collection is required')
    if not manifest.get('coverage') or not all(c.get('complete') for c in manifest['coverage'].values()):
        raise ValueError('Saved release coverage is incomplete')
    assets = [manifest[k] for k in ['catalog', 'history', 'recommendations', 'map']]
    assets += list(manifest['periods'].values())
    assets += [manifest[k] for k in ['potential', 'transaction_valuation'] if manifest.get(k)]
    expected = {}
    for item in assets:
        path = safe_data_path(item['url'])
        size = item['bytes']
        if isinstance(size, bool) or not isinstance(size, int) or not 0 < size <= MAX_FILE_BYTES:
            raise ValueError('Invalid saved asset size')
        if path in expected and expected[path] != (item['sha256'], size):
            raise ValueError('Conflicting saved asset references')
        expected[path] = (item['sha256'], size)
    validation = 'data/model_validation.json'
    expected[validation] = (manifest['ui_assets'][validation], MAX_FILE_BYTES)
    if sum(size for _, size in expected.values()) > MAX_RELEASE_BYTES:
        raise ValueError('Saved release exceeds the total download budget')
    output = Path(output)
    if output.exists() and any(output.iterdir()):
        raise ValueError('Use an empty output directory to avoid mixing releases')

    def download(pair):
        path, (sha, size) = pair
        body = reader(path, size)
        if len(body) > size or (path != validation and len(body) != size) or hashlib.sha256(body).hexdigest() != sha:
            raise ValueError('Saved public asset checksum mismatch: ' + path)
        target = output / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(body)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(download, expected.items()))
    manifest['data_code_commit'] = manifest.get('data_code_commit', manifest['code_commit'])
    manifest['ui_source_release'] = {'release_id': manifest['release_id'],
                                   'manifest_sha256': hashlib.sha256(old_bytes).hexdigest()}
    manifest['code_commit'] = code_commit
    manifest['release_id'] = os.getenv('GITHUB_RUN_ID', 'local-ui') + '-' + os.getenv('GITHUB_RUN_ATTEMPT', '1')
    manifest['ui_assets'] = copy_ui(output, source)
    manifest['ui_assets'][validation] = expected[validation][0]
    (output / 'data/dashboard_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='https://jaesung0804.github.io/turbo-potato/')
    parser.add_argument('--output', type=Path, default=Path('.work/site'))
    parser.add_argument('--sha', default=os.getenv('GITHUB_SHA'))
    args = parser.parse_args()
    result = refresh(args.url, args.output, args.sha)
    print(json.dumps({'ui_only': True, 'code_commit': result['code_commit'], 'data_code_commit': result['data_code_commit'],
                      'data_through_unchanged': result['generated_at']}, ensure_ascii=False))
