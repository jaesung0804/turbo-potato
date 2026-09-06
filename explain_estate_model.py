"""Measure held-out reference-price sensitivity, not future-return importance."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
from estate_model import dataset, fit, tune, predict, NUMERIC, EXTRA_NUMERIC, CATEGORICAL
from estate_io import write_json


def explain(summary_path, output):
    frame, _ = dataset(json.loads(summary_path.read_text(encoding='utf-8')), enhanced=True)
    training = frame[frame.year < 2025]
    test = frame[frame.year == 2025].reset_index(drop=True)
    weight, _ = tune(training, 2024)
    fitted = fit(training)
    actual = np.exp(test.target.to_numpy())
    def error(rows):
        return float(np.abs(actual - np.exp(predict(fitted, rows, weight))).mean())
    original = error(test)
    rng = np.random.default_rng(202609)
    permutations = [rng.permutation(len(test)) for _ in range(3)]
    groups = {
        'price_history': ['prior_price', 'prior_peer', 'momentum', 'reference_anchor', 'last_price', 'history_gap', 'matched_peer'],
        'location': CATEGORICAL[:3],
        'area': ['area', 'area_band'],
        'age': ['age'],
        'past_sample_counts': ['prior_count', 'peer_count', 'matched_peer_count'],
        'year': ['year'],
    }
    def sensitivity(name, columns):
        errors = []
        for permutation in permutations:
            changed = test.copy()
            changed[columns] = test.iloc[permutation][columns].to_numpy()
            errors.append(error(changed) - original)
        return {'name': name, 'columns': columns, 'mae_increase': round(float(np.mean(errors)), 2),
                'repeat_std': round(float(np.std(errors)), 2)}
    result = {
        'model_version': 'estate-reference-v4', 'test_year': 2025, 'trained_through': 2024,
        'weight_selected_on': 2024, 'ml_weight': weight, 'rows': len(test),
        'baseline_mae': round(original, 2), 'unit': '만원/평', 'repeats': 3, 'seed': 202609,
        'summary_sha256': hashlib.sha256(summary_path.read_bytes()).hexdigest(),
        'method': 'Held-out 2025 permutation sensitivity of the complete reference-price predictor, including its anchor. Correlated or derived inputs can create unrealistic combinations when shuffled; values are neither causal effects nor growth forecasts. Zero for year is expected because the test year is constant.',
        'groups': sorted([sensitivity(k, v) for k, v in groups.items()], key=lambda r: -r['mae_increase']),
        'features': sorted([sensitivity(k, [k]) for k in NUMERIC + EXTRA_NUMERIC + CATEGORICAL], key=lambda r: -r['mae_increase']),
    }
    write_json(output, result, indent=2)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary', type=Path, default=Path('.work/build/summary.json'))
    parser.add_argument('--output', type=Path, default=Path('reports/estate_model_importance.json'))
    args = parser.parse_args()
    explain(args.summary, args.output)
