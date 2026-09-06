"""Train a new immutable monthly reference-price model, or infer with its saved model."""
import argparse
from pathlib import Path
from estate_model import run, VERSION, CANDIDATE_VERSION

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--summary', type=Path, default=Path('.work/build/summary.json'))
    p.add_argument('--output', type=Path, default=Path('.work/build/recommendations.json'))
    p.add_argument('--model-dir', type=Path, default=Path('models'))
    p.add_argument('--model-month')
    p.add_argument('--mode', choices=['auto', 'train', 'infer'], default='auto')
    p.add_argument('--model-version', choices=[VERSION,CANDIDATE_VERSION], default=VERSION)
    a = p.parse_args()
    result = run(a.summary, a.output, a.model_dir, a.model_month, a.mode,a.model_version)
    print('Model month:', result['model_month'], 'reference comparisons:', len(result['recommendations']))
