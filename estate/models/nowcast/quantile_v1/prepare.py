"""완전성과 해시가 확인된 로컬 CSV에서 월별 학습 자료를 만든다."""
import argparse
import hashlib
import json
from pathlib import Path

from estate.models.nowcast.v1.model import load_transactions
from estate.models.nowcast.regional_v2.features import monthly_features, save_parquet
from estate.core.io import write_json


def prepare(source, output, first='2021-03', last='2026-09'):
    source, output = Path(source), Path(output)
    if output.exists():
        raise FileExistsError('새 특징 파일 경로를 지정하세요.')
    collection = json.loads(source.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    if not collection.get('complete') or collection.get('normalizer_version') != 2:
        raise ValueError('완전한 국토부 정규화 자료가 필요합니다.')
    if hashlib.sha256(source.read_bytes()).hexdigest() != collection['sha256']:
        raise ValueError('원자료 체크섬 불일치')
    data, quality = load_transactions(source)
    if quality['raw_rows'] != collection['rows']:
        raise ValueError('원본 건수 불일치')
    frame = monthly_features(data, first, last, policy=True, uniform_lag=31, legacy_order=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    save_parquet(frame, output)
    spec = {'source': quality, 'collection': collection, 'rows': len(frame), 'first': first, 'last': last,
        'feature_engine': 'array_equivalent_legacy_ties_v1',
        'feature_sha256': hashlib.sha256(output.read_bytes()).hexdigest(),
        'implementation_sha256': hashlib.sha256(Path('estate/models/nowcast/regional_v2/features.py').read_bytes()).hexdigest(),
        'availability_policy': '31日 reporting lag; January/February excluded',
        'no_history_rows': int(frame.anchor.isna().sum())}
    write_json(output.with_suffix('.json'), spec, indent=2)
    return spec


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--first', default='2021-03')
    p.add_argument('--last', default='2026-09')
    a = p.parse_args()
    print(json.dumps(prepare(a.source, a.output, a.first, a.last), ensure_ascii=False))
