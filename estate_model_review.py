"""Build a small public review from existing, dated evaluation reports only."""
import hashlib
import json
from pathlib import Path

from estate_potential_comparison import compare_common_origins


def build_review(root=Path('.')):
    sources = {}

    def read(name):
        path = root / 'reports' / name
        raw = path.read_bytes()
        sources[name] = hashlib.sha256(raw).hexdigest()
        return json.loads(raw)

    expansion = read('estate_retraining_potential_20260909.json')
    five = read('estate_potential_five_year_summary.json')
    paths = read('estate_potential_path_summary.json')
    return {
        'schema_version': 1, 'review_date': '2026-09-15', 'evaluation_date': '2026-09-09',
        'kind': 'existing_evaluation_review', 'production_changed': False,
        'potential': {
            **compare_common_origins(expansion['results']),
            'training_scope': expansion['training_scope'], 'evaluation_scope': expansion['evaluation_scope'],
            'confirmation': expansion['primary_geographic_confirmation'],
            'operational_decision': expansion['operational_decision'],
            'horizon_months': expansion['horizon_months'],
            'limitations': expansion['limitations'],
        },
        'long_horizon': {
            'five_year': {'passed': five['passed'], 'common_origins': five['regimes']['historical_policy']['common_origins']},
            'multiple_paths': {'passed': paths['passed'], 'gates': paths['gates']},
        },
        'source_sha256': sources,
    }


def publish_review(output, root=Path('.')):
    body = (json.dumps(build_review(root), ensure_ascii=False, separators=(',', ':'), allow_nan=False) + '\n').encode()
    target = Path(output) / 'data/model_review.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(body)
    return {'url': 'data/model_review.json', 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
