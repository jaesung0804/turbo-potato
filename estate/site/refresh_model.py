"""저장된 공개 집계를 보존하고 검증된 로컬 자료로 현재 모델 출력만 교체한다."""
import argparse
import gzip
import hashlib
import json
from pathlib import Path

from estate.site.bundle import asset
from estate.core.io import write_json
from estate.models.nowcast.release import attach_nowcast
from estate.site.refresh_ui import refresh


def update(output, source, code_sha):
    output, source = Path(output), Path(source)
    collection = json.loads(source.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    if not collection.get('complete') or hashlib.sha256(source.read_bytes()).hexdigest() != collection['sha256']:
        raise ValueError('Complete verified local inference source required')
    manifest = json.loads((output/'data/dashboard_manifest.json').read_text(encoding='utf-8'))
    def read_asset(item):
        body = (output/item['url']).read_bytes()
        if hashlib.sha256(body).hexdigest() != item['sha256']:
            raise ValueError('Saved public asset checksum mismatch')
        return json.loads(gzip.decompress(body))
    catalog = read_asset(manifest['catalog'])
    packed = read_asset(manifest['recommendations'])
    fields = packed['fields']
    records = []
    for row in packed['rows']:
        records.append({'building_key': catalog['addresses'][row[0]]['key'], 'region_code': row[1],
                        **dict(zip(fields, row[2:]))})
    identity = lambda r: (r['region_code'], r['building_key'])
    old_prices = {identity(r): r.get('current_valuation', {}).get('price_billion') for r in records}
    model = {**packed['metadata'], 'recommendations': records}
    attach_nowcast(model, source)
    model['nowcast']['inference_source'] = {'sha256': collection['sha256'], 'rows': collection['rows'],
        'fetched_at': collection.get('fetched_at'), 'scope': '검증된 로컬 원자료. 공개 집계 원본과 별도로 출처 기록.'}
    packed['metadata'] = {k: v for k, v in model.items() if k != 'recommendations'}
    # Scoring sorts recommendations in place. Join by identity, never row order.
    by_identity = {identity(r): r for r in records}
    for row in packed['rows']:
        record = by_identity[(row[1], catalog['addresses'][row[0]]['key'])]
        row[2:] = [record.get(k) for k in fields]
    manifest['recommendations'] = asset(output/'data/bundle', 'recommendations', packed)
    manifest['model'] = packed['metadata']
    manifest['code_commit'] = code_sha
    manifest['release_status']['mode'] = 'local_model_release'
    manifest['release_status']['model_refresh'] = {'status': 'completed',
        'model_version': model['nowcast']['version'], 'inference_source_sha256': collection['sha256']}
    # Annual aggregates, all period files, catalog and transaction ledger stay
    # byte-identical. Current predictions carry their own input provenance.
    changed = sum(old_prices[identity(r)] != r.get('current_valuation', {}).get('price_billion') for r in records)
    manifest['release_status']['model_refresh']['changed_point_prices'] = changed
    write_json(output/'data/model_validation.json', packed['metadata'], indent=2)
    manifest['ui_assets']['data/model_validation.json'] = hashlib.sha256((output/'data/model_validation.json').read_bytes()).hexdigest()
    write_json(output/'data/dashboard_manifest.json', manifest, indent=2)
    print(json.dumps({'types': len(records), 'changed_point_prices': changed,
                      'quantile_types': model['nowcast']['available_types']}, ensure_ascii=False))
    return manifest


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, default=Path('.work/local-model-site'))
    p.add_argument('--sha', required=True)
    p.add_argument('--base-url', default='https://jaesung0804.github.io/turbo-potato/')
    a = p.parse_args()
    refresh(a.base_url, a.output, a.sha)
    update(a.output, a.source, a.sha)
