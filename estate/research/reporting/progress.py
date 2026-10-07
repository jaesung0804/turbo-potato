"""Attach current research explanations without rewriting frozen forecasts."""
from __future__ import annotations

import gzip
import hashlib
import json
from pathlib import Path


def attach_research_progress(potential, root=Path('.')):
    result = dict(potential)
    path = root / 'reports/estate_retraining_summary_20260909.json'
    if path.exists():
        summary = json.loads(path.read_text())
        if summary.get('production_changed') is not False:
            raise ValueError('Retraining research must not overwrite frozen forecasts')
        result['capital_retraining'] = summary['potential']
    path = root / 'reports/estate_potential_path_summary.json'
    if path.exists():
        raw = path.read_bytes()
        summary = json.loads(raw)
        if summary.get('production_changed') is not False:
            raise ValueError('Path experiment must not silently replace the released forecast')
        result['path_validation'] = {**summary, 'summary_sha256': hashlib.sha256(raw).hexdigest()}
    path = root / 'metadata/potential_refresh_status.json'
    if path.exists():
        result['refresh'] = json.loads(path.read_text())
    path = root / 'metadata/verified_households_seoul_20260908.json.gz'
    if path.exists():
        registry = json.loads(gzip.decompress(path.read_bytes()))
        records = registry['records']
        result['household_register'] = {
            'official_complexes': len(records),
            'positive_households': sum(bool(r.get('whole_complex_households')) for r in records),
            'complete_fields': sum(bool(r.get('denominator_fields_complete')) for r in records),
            'observed_at': registry.get('observed_at'),
            'source_url': registry.get('dataset_url'),
            'used_for_model': False,
            'meaning': '서울 공식 단지 원장은 확보했습니다. 실거래 단지의 지번·동별 범위와 공적으로 대조한 항목부터 현재 표시를 정정하며, 현재 원장을 과거 회전율 학습에 소급하지 않습니다.',
        }
    return result
