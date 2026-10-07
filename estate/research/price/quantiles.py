"""Explicit saved-data experiment. Fail closed before reading/training a panel.

No source collection, state initialization, production overwrite or automatic
promotion. The existing backend capacity/recovery gate remains mandatory.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import joblib
import numpy as np
import pandas as pd

from estate.core.io import write_json
from estate.models.nowcast.v1.model import load_transactions
from estate.models.nowcast.quantile_v1.model import fit, evaluate, metrics, VERSION
from estate.data.storage.readiness import run_preflight
from estate.models.nowcast.regional_v2.features import monthly_features
from estate.models.nowcast.confidence.model import radii
from estate.data.storage.client import safe_path, Client


def frozen_baseline(frame):
    """Compare 2026 only to the exact frozen production model and error band."""
    from estate.models.nowcast.regional_v2.model import ARTIFACT, MANIFEST
    spec = json.loads(Path(MANIFEST).read_text())
    confidence = spec['confidence']
    band_spec = json.loads(Path(confidence['manifest']).read_text())
    for path, digest in [(ARTIFACT, spec['sha256']), (confidence['artifact'], band_spec['sha256'])]:
        if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
            raise ValueError('Frozen baseline checksum mismatch')
    if band_spec['price_model_sha256'] != spec['sha256'] or not (frame.year == 2026).all():
        raise ValueError('Frozen baseline scope mismatch')
    artifact = joblib.load(ARTIFACT)
    median = frame.anchor.to_numpy() + artifact['model'].predict(frame[artifact['columns']])
    radius = radii(frame, joblib.load(confidence['artifact']))
    # These are comparison endpoints of the old symmetric 80% band, not newly
    # claimed calibrated quantiles. The report preserves that distinction.
    q = np.column_stack([median-radius, median, median+radius])
    return metrics(frame, q)


def compare(candidate, baseline):
    a = candidate['overall']
    checks = {'pinball_improves': a['mean_pinball_log'] < baseline['mean_pinball_log'],
              'median_error_within_2pct': a['median_mae_oku'] <= baseline['median_mae_oku']*1.02,
              'coverage80_75to85': 75 <= a['coverage80_pct'] <= 85,
              'complex_coverage80_75to85': 75 <= a['complex_balanced_coverage80_pct'] <= 85,
              'subgroup_coverage_70to90': all(70 <= g['coverage80_pct'] <= 90
                  for g in candidate['groups'] if g['transactions'] >= 100),
              'minimum_evaluation_rows': a['transactions'] >= 1000}
    return {'checks': checks, 'passes_diagnostic_gates': all(checks.values()),
            'production_approved': False,
            'reason': 'Retrospective data already used in prior research; inspect all groups and approve an immutable release separately.'}


def destination_receipt(root, client):
    name = 'estate-model-state'
    receipt = root/'.research-backend'/(hashlib.sha256(name.encode()).hexdigest()+'.json')
    if not receipt.is_file():
        raise ValueError('Restore the model destination before training')
    sid = json.loads(receipt.read_text(encoding='utf-8'))['snapshot_id']
    if not sid or client.json('GET', '/snapshot-heads/'+name)['snapshot_id'] != sid:
        raise ValueError('Model destination changed since original restore')
    return sid


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--snapshot-name', required=True)
    parser.add_argument('--input-file', required=True, help='State-relative compressed transaction CSV')
    parser.add_argument('--checkpoint-key', required=True)
    parser.add_argument('--expected-checkpoint-version', type=int, required=True)
    parser.add_argument('--required-growth-bytes', type=int, required=True)
    parser.add_argument('--model-state-dir', type=Path, required=True)
    parser.add_argument('--output', required=True, help='New path relative to the restored model state')
    args = parser.parse_args(argv)
    ready = run_preflight(state_dir=args.state_dir, snapshot_name=args.snapshot_name,
        required_files=[args.input_file], checkpoint_key=args.checkpoint_key,
        expected_checkpoint_version=args.expected_checkpoint_version,
        required_growth_bytes=args.required_growth_bytes)
    if not ready['ready']:
        print(json.dumps({'status': 'blocked_before_training', 'readiness': ready}))
        return 2
    client = Client(project='estate')
    destination = args.model_state_dir.resolve()
    original_destination = destination_receipt(destination, client)
    output = safe_path(destination, args.output)
    if output.exists():
        raise FileExistsError('Candidate output must be a new immutable directory')
    source = safe_path(args.state_dir.resolve(), args.input_file)
    with tempfile.TemporaryDirectory(prefix='estate-quantiles-') as tmp:
        csv = Path(tmp)/'transactions.csv'
        with gzip.open(source, 'rb') as src, csv.open('wb') as dst:
            shutil.copyfileobj(src, dst)
        d, quality = load_transactions(csv)
    if quality['min_date'] > '2007-01-01' or quality['max_date'] < '2026-08-31':
        raise ValueError('Saved panel lacks declared training/evaluation coverage')
    # All available eligible rows, including repeated and unusually low sales.
    frame = monthly_features(d, '2007-03', '2026-08', policy=True)
    for year in range(2007, 2027):
        if not (frame.year == year).any():
            raise ValueError('A required training or evaluation year is absent')
    training = frame.loc[frame.year <= 2025]
    test = frame.loc[(frame.year == 2026) & frame.month.between('2026-03', '2026-08')]
    artifact = fit(training, '2025-12-31')
    candidate = evaluate(test, artifact)
    baseline = frozen_baseline(test)
    report = {'version': VERSION, 'source': quality, 'readiness': ready,
        'original_model_snapshot': original_destination,
        'candidate': candidate, 'baseline': baseline, 'decision': compare(candidate, baseline),
        'evaluation_kind': 'retrospective_reused_2026_data_not_untouched_holdout',
        'baseline_endpoints': 'frozen_symmetric_error_band_not_direct_quantiles',
        'code_sha256': hashlib.sha256(Path('estate/models/nowcast/quantile_v1/model.py').read_bytes()).hexdigest(),
        'feature_engine_sha256': hashlib.sha256(Path('estate/models/nowcast/regional_v2/features.py').read_bytes()).hexdigest()}
    if destination_receipt(destination, client) != original_destination:
        raise ValueError('Model destination changed during training; do not refresh receipt')
    output.mkdir(parents=True)
    model_path = output/'candidate.joblib'
    joblib.dump(artifact, model_path)
    report['artifact_sha256'] = hashlib.sha256(model_path.read_bytes()).hexdigest()
    report['training'] = artifact['training']
    write_json(output/'report.json', report, indent=2)
    print(json.dumps({'status': 'candidate_only', 'decision': report['decision']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
