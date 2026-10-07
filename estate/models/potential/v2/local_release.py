"""로컬 검증 후 고정한 월별 v2 후보를 읽는다. 이후 정상 월별 후보가 우선한다."""
import gzip
import hashlib
import json
from pathlib import Path
import re
from urllib.request import urlopen

SPEC=Path('metadata/potential_local_202610.json')

def choose(saved, spec_path=SPEC, reader=None):
    if not spec_path.exists():
        return saved
    spec=json.loads(spec_path.read_text(encoding='utf-8'))
    if saved and saved.get('origin','')>spec['origin']:
        return saved
    url=spec['url']
    if not re.fullmatch(r'https://github\.com/jaesung0804/turbo-potato/releases/download/estate-quantiles-[A-Za-z0-9.-]+/potential-public\.json\.gz',url):
        raise ValueError('허용되지 않은 잠재력 공개본 주소')
    cache=Path('models/potential')/(spec['sha256']+'.json.gz')
    if cache.exists():
        body=cache.read_bytes()
    else:
        if reader:
            body=reader(url)
        else:
            with urlopen(url,timeout=60) as response:
                body=response.read(4_000_001)
    if len(body)>4_000_000 or len(body)!=spec['bytes'] or hashlib.sha256(body).hexdigest()!=spec['sha256']:
        raise ValueError('로컬 잠재력 공개본 체크섬 불일치')
    payload=json.loads(gzip.decompress(body))
    if payload['origin']!=spec['origin'] or payload['model']!=spec['model'] or payload['latest_training_label_available']>payload['origin']:
        raise ValueError('고정 예측의 모델·시점 불일치')
    rows=payload['rows']
    if len(rows)!=payload['cohort_size'] or len({r['key'] for r in rows})!=len(rows) or sorted(r['research_rank'] for r in rows)!=list(range(1,len(rows)+1)):
        raise ValueError('후보 목록·순위 불완전')
    cache.parent.mkdir(parents=True,exist_ok=True);cache.write_bytes(body)
    return payload
