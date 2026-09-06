"""Verify actual HTTP release bytes, not merely a deployment commit."""
import argparse
import gzip
import hashlib
import json
import time
from urllib.request import Request, urlopen
from urllib.parse import urljoin


def fetch(url):
    with urlopen(Request(url, headers={'Cache-Control': 'no-cache', 'User-Agent': 'estate-release-verification'}), timeout=40) as response:
        return response.read()


def verify(base, expected_sha):
    base = base.rstrip('/')+'/'
    manifest = json.loads(fetch(urljoin(base, 'data/dashboard_manifest.json')+'?verify='+expected_sha))
    if manifest.get('code_commit') != expected_sha:
        raise ValueError('The public site is still serving a different release')
    if not manifest.get('collection', {}).get('complete') or not all(c['complete'] for c in manifest['coverage'].values()):
        raise ValueError('Public data completeness was not verified')
    for path, sha in manifest['ui_assets'].items():
        if hashlib.sha256(fetch(urljoin(base, path)+'?verify='+expected_sha)).hexdigest() != sha:
            raise ValueError('Public UI or validation mismatch: '+path)
    assets = [manifest[k] for k in ['catalog', 'history', 'recommendations', 'map']] + list(manifest['periods'].values())
    for asset in assets:
        body = fetch(urljoin(base, asset['url']))
        if len(body) != asset['bytes'] or hashlib.sha256(body).hexdigest() != asset['sha256']:
            raise ValueError('Public data checksum mismatch: '+asset['url'])
        json.loads(gzip.decompress(body))
    print(json.dumps({'verified_commit': expected_sha, 'data_through': manifest['generated_at'],
          'model_month': manifest['model']['model_month'], 'coverage': manifest['coverage'],
          'verified_files': 1+len(assets)+len(manifest['ui_assets'])}, ensure_ascii=False))
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--url', required=True); p.add_argument('--sha', required=True)
    a = p.parse_args()
    for attempt in range(6):
        try:
            verify(a.url, a.sha)
            break
        except Exception as e:
            if attempt == 5:
                raise
            print('Public verification waiting for release:', type(e).__name__, flush=True)
            time.sleep(20)
