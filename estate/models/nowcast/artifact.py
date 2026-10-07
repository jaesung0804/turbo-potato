"""로컬에서 검증·선택한 불변 모델을 해시 검증 후 빌드에 공급한다."""
import hashlib
import json
from pathlib import Path
import re
import urllib.request


def active_nowcast():
    manifest = Path('metadata/nowcast_quantiles_2026.json')
    if not manifest.exists():
        from estate.models.nowcast.regional_v2.model import ARTIFACT, MANIFEST
        return Path(ARTIFACT), Path(MANIFEST)
    spec = json.loads(manifest.read_text(encoding='utf-8'))
    path = Path(spec['artifact'])
    if path.is_absolute() or '..' in path.parts or path.parts[:2] != ('models', 'nowcast'):
        raise ValueError('Invalid frozen model cache path')
    def valid(body):
        return len(body) == spec['artifact_bytes'] and hashlib.sha256(body).hexdigest() == spec['sha256']
    if path.exists():
        if not valid(path.read_bytes()):
            raise ValueError('Frozen quantile model cache checksum mismatch')
        return path, manifest
    url = spec['artifact_url']
    if not re.fullmatch(r'https://github\.com/jaesung0804/turbo-potato/releases/download/estate-quantiles-[a-zA-Z0-9.-]+/model\.joblib', url):
        raise ValueError('Unapproved frozen model origin')
    if not 0 < spec['artifact_bytes'] < 5_000_000:
        raise ValueError('Frozen model size exceeds bound')
    with urllib.request.urlopen(url, timeout=60) as response:
        body = response.read(spec['artifact_bytes']+1)
    if not valid(body):
        raise ValueError('Downloaded quantile model checksum mismatch')
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_bytes(body)
    temp.replace(path)
    return path, manifest
