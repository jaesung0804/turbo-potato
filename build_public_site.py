"""Build a complete, compact Pages release from verified raw data and frozen models."""
import argparse
import gzip
import hashlib
import json
import os
import shutil
from pathlib import Path

from build_real_estate_dashboard_data import build_dashboard_data
from dashboard_bundle import build_bundle
from estate_model import run as run_model, VERSION, CANDIDATE_VERSION, SIBLING_VERSION
from estate_io import write_json
from shapely.geometry import shape, mapping

UI_FILES = ['index.html', 'model.html', 'styles.css', 'app.js', 'data-store.js', 'result-pages.js', 'model.js', '404.html','vendor/leaflet.css','vendor/leaflet.js','vendor/LICENSE.txt']
AMENITIES = ['households', 'elementary_500m', 'nearest_elementary_name', 'nearest_elementary_m',
             'subway_lines', 'subway_station', 'subway_distance_m', 'latitude', 'longitude']


def apply_amenities(summary, path=Path('metadata/amenities_snapshot.json.gz')):
    """A display-only snapshot; ambiguous names or conflicting construction years never match."""
    if not path.exists():
        return {'matched_types': 0, 'snapshot_built_at': None}
    snapshot = json.loads(gzip.decompress(path.read_bytes()))
    matched = 0
    for region in summary['regions']:
        names = {}
        for building in region['all']['addresses']:
            names.setdefault(building['building_name'], set()).add(building['complex_key'])
        for bucket in [region['all'], *region['years'].values()]:
            for b in bucket['addresses']:
                key = '|'.join([region['sido_name'], region['gu_name'], region['dong_name'], b['building_name']])
                old = snapshot['items'].get(key)
                if not old or len(names[b['building_name']]) != 1:
                    continue
                if b.get('built_year') and old.get('built_year') and b['built_year'] != old['built_year']:
                    continue
                for field in AMENITIES:
                    if b.get(field) is None or b.get(field) == []:
                        b[field] = old.get(field)
                b['amenity_source'] = '기존 단지명 일치 자료 · 현장 확인 필요'
                b['amenity_snapshot_built_at'] = snapshot['snapshot_built_at']
                matched += 1
    return {'matched_types': matched, 'snapshot_built_at': snapshot['snapshot_built_at'],
            'note': '현재 확인된 시설 정보가 아닌 기존 표시용 자료이며 과거 가격 학습에 사용하지 않습니다.'}


def light_map(path):
    data = json.loads(path.read_text(encoding='utf-8'))
    features = []
    for f in data['features']:
        geometry = shape(f['geometry']).simplify(.0006, preserve_topology=True)
        if geometry.is_empty:
            raise ValueError('Map simplification removed a district')
        features.append({'type': 'Feature', 'properties': f['properties'], 'geometry': mapping(geometry)})
    return {'type': 'FeatureCollection', 'features': features}


def build(source, output, model_dir=Path('models'), month=None, summary_path=None, require_complete=True, model_version=SIBLING_VERSION):
    if require_complete:
        collection = json.loads(source.with_suffix('.manifest.json').read_text(encoding='utf-8'))
        if not collection.get('complete') or collection.get('normalizer_version')!=2 or hashlib.sha256(source.read_bytes()).hexdigest() != collection['sha256']:
            raise ValueError('A complete, verified raw collection is required for publication')
    else:
        collection = {'complete': False, 'note': 'Local fixture build; not a production collection'}
    work = Path('.work/build')
    work.mkdir(parents=True, exist_ok=True)
    if summary_path is None:
        summary_path = work/'summary.json'
        build_dashboard_data(source, summary_path, {'아파트'}, 0, 0, False)
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    if not summary['years']:
        raise ValueError('No valid transactions')
    if require_complete and summary['total_rows'] != collection['rows']:
        raise ValueError('The summary does not cover the verified raw row count')
    amenities = apply_amenities(summary)
    write_json(summary_path, summary)
    del summary
    recommendation_name='recommendations.json' if model_version==VERSION else 'recommendations-'+model_version+'.json'
    model = run_model(summary_path, work/recommendation_name, model_dir, month,version=model_version)
    summary = json.loads(summary_path.read_text(encoding='utf-8'))
    output.mkdir(parents=True, exist_ok=True)
    for name in UI_FILES:
        (output/name).parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(Path('web')/name, output/name)
    # A returning browser must load matching UI scripts after an HTML release.
    for page in ['index.html', 'model.html']:
        html = (output/page).read_text(encoding='utf-8')
        for name in ['app.js', 'data-store.js', 'result-pages.js', 'model.js', 'styles.css','vendor/leaflet.css','vendor/leaflet.js']:
            version = hashlib.sha256((output/name).read_bytes()).hexdigest()[:16]
            html = html.replace('"'+name+'"', '"'+name+'?v='+version+'"')
        (output/page).write_text(html,encoding='utf-8',newline='\n')
    (output/'.nojekyll').touch()
    # Remove obsolete release payloads; they never belong to the new manifest.
    if (output/'data').exists():
        shutil.rmtree(output/'data')
    manifest = build_bundle(summary, model, output/'data/bundle', light_map(Path('web/data/capital_area_adm_sgg.geojson')))
    if not all(c['complete'] for c in manifest['coverage'].values()):
        raise ValueError('A source partition was truncated; publication refused')
    if len(model['recommendations']) != manifest['coverage'][model['target_year']]['available_types']:
        raise ValueError('The latest model output does not cover every observed type')
    manifest['collection'] = {k: collection.get(k) for k in ['complete', 'normalizer_version', 'start', 'end', 'rows', 'partition_count', 'region_count', 'fetched_at', 'sha256']}
    manifest['release_id'] = os.getenv('GITHUB_RUN_ID', 'local')+'-'+os.getenv('GITHUB_RUN_ATTEMPT', '1')
    manifest['amenities'] = amenities
    manifest['map_note'] = '2025-06-30 시군구 경계. 2026년 개편 지역은 전체 목록에서 조회합니다.'
    comparison_path=Path('reports/estate_model_comparison.json')
    if comparison_path.exists():
        manifest['model_comparison']=json.loads(comparison_path.read_text(encoding='utf-8'))
    importance_path=Path('reports/estate_model_importance.json')
    if importance_path.exists():
        manifest['model_importance']=json.loads(importance_path.read_text(encoding='utf-8'))
    sibling_path=Path('reports/sibling_model_comparison.json')
    if sibling_path.exists():manifest['sibling_comparison']=json.loads(sibling_path.read_text(encoding='utf-8'))
    validation = {k: v for k, v in model.items() if k != 'recommendations'}
    write_json(output/'data/model_validation.json',validation,indent=2)
    manifest['ui_assets'] = {name: hashlib.sha256((output/name).read_bytes()).hexdigest()
                             for name in UI_FILES + ['data/model_validation.json']}
    write_json(output/'data/dashboard_manifest.json',manifest,indent=2)
    print(json.dumps({'source_rows': summary['total_rows'], 'used_rows': summary['used_rows'],
          'coverage': manifest['coverage'], 'model_month': model['model_month'], 'validation': model['validation'],
          'gzip_bytes': sum(p.stat().st_size for p in (output/'data/bundle').glob('*.gz'))}, ensure_ascii=False))
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('data/capital_area_apt_trade_transactions.csv'))
    parser.add_argument('--output', type=Path, default=Path('.work/site'))
    parser.add_argument('--model-dir', type=Path, default=Path('models'))
    parser.add_argument('--model-month')
    parser.add_argument('--model-version',choices=[VERSION,CANDIDATE_VERSION,SIBLING_VERSION],default=SIBLING_VERSION)
    args = parser.parse_args()
    build(args.input, args.output, args.model_dir, args.model_month,model_version=args.model_version)
