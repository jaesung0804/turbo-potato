"""로컬에서 계산한 공개 사이트를 묶고, 해시 검증한 파일만 배포한다."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
from urllib.request import urlopen
import zipfile

from estate_ui_release import copy_ui

MAX_BYTES = 128 * 1024 * 1024


def release_files(site):
    manifest = json.loads((site/'data/dashboard_manifest.json').read_text(encoding='utf-8'))
    files = dict(manifest['ui_assets'])
    assets = [manifest[k] for k in ('catalog', 'history', 'recommendations', 'map')]
    assets += list(manifest['periods'].values())
    assets += [manifest[k] for k in ('potential', 'transaction_valuation') if manifest.get(k)]
    files.update({a['url']: a['sha256'] for a in assets})
    for name, expected in files.items():
        safe_name(name)
        if hashlib.sha256((site/name).read_bytes()).hexdigest() != expected:
            raise ValueError('사이트 파일 체크섬 불일치: '+name)
    if not manifest['collection']['complete'] or not all(c['complete'] for c in manifest['coverage'].values()):
        raise ValueError('완전한 공개 집계가 필요합니다.')
    return manifest, sorted(set(files)|{'data/dashboard_manifest.json', '.nojekyll'})


def safe_name(name):
    p = PurePosixPath(name)
    if not name or '\\' in name or ':' in name or p.is_absolute() or '..' in p.parts:
        raise ValueError('안전하지 않은 압축 경로')
    return name


def stamp(site, code_sha):
    manifest, _ = release_files(site)
    if not re.fullmatch('[a-f0-9]{40}', manifest.get('local_build_commit', '')):
        previous = manifest.get('code_commit', '')
        manifest['local_build_commit'] = previous if re.fullmatch('[a-f0-9]{40}', previous) else code_sha
    manifest['code_commit'] = code_sha
    manifest['release_id'] = os.getenv('GITHUB_RUN_ID', 'local-model')+'-'+os.getenv('GITHUB_RUN_ATTEMPT', '1')
    manifest['ui_assets'].update(copy_ui(site))
    (site/'data/dashboard_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')


def pack(site, output, code_sha):
    if output.exists():
        raise FileExistsError(output)
    stamp(site, code_sha)
    _, files = release_files(site)
    if sum((site/name).stat().st_size for name in files) > MAX_BYTES:
        raise ValueError('공개 사이트 크기 제한 초과')
    with zipfile.ZipFile(output, 'x', compression=zipfile.ZIP_DEFLATED) as archive:
        for name in files:
            archive.write(site/name, name)
    print(json.dumps({'file': str(output), 'bytes': output.stat().st_size,
                      'sha256': hashlib.sha256(output.read_bytes()).hexdigest()}))


def unpack(body, expected, output, code_sha):
    import io
    if len(body) > MAX_BYTES or not re.fullmatch('[a-f0-9]{64}', expected) or hashlib.sha256(body).hexdigest() != expected:
        raise ValueError('공개 배포 파일 체크섬 불일치')
    if output.exists() and any(output.iterdir()):
        raise ValueError('빈 배포 폴더를 지정하세요.')
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        members = archive.infolist()
        if len(members) > 200 or sum(m.file_size for m in members) > MAX_BYTES:
            raise ValueError('압축 해제 크기 제한 초과')
        if len({m.filename for m in members}) != len(members):
            raise ValueError('중복 압축 경로')
        for member in members:
            safe_name(member.filename)
            if member.is_dir() or (member.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError('일반 파일만 허용합니다.')
        for member in members:
            target = output/member.filename
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(archive.read(member))
    release_files(output)
    stamp(output, code_sha)


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['pack', 'download'])
    p.add_argument('--site', type=Path, default=Path('.work/site'))
    p.add_argument('--output', type=Path, default=Path('.work/site.zip'))
    p.add_argument('--sha', required=True)
    p.add_argument('--tag')
    p.add_argument('--checksum')
    a = p.parse_args()
    if a.command == 'pack':
        pack(a.site, a.output, a.sha)
    else:
        if not re.fullmatch(r'estate-quantiles-[a-zA-Z0-9.-]+', a.tag or ''):
            raise ValueError('허용되지 않은 배포 태그')
        url = 'https://github.com/jaesung0804/turbo-potato/releases/download/'+a.tag+'/site.zip'
        with urlopen(url, timeout=60) as response:
            body = response.read(MAX_BYTES+1)
        unpack(body, a.checksum or '', a.site, a.sha)
