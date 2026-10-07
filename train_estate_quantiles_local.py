"""검증된 로컬 특징 자료로 분위수 모델을 학습·비교한다. 수집·배포는 하지 않는다."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from analyze_estate_quantiles import frozen_baseline, compare
from estate_io import write_json
from estate_quantile_nowcast import fit, evaluate, VERSION, LEVELS, CalibratedQuantileModel, RetainedMedianQuantileModel


def run(features: Path, output: Path, retained_median=False, trees=220, leaves=23, calibration_period='2025'):
    if output.exists():
        raise FileExistsError('결과 폴더가 이미 있습니다. 새 경로를 지정하세요.')
    manifest = json.loads(features.with_suffix('.json').read_text(encoding='utf-8'))
    digest = hashlib.sha256(features.read_bytes()).hexdigest()
    if digest != manifest['feature_sha256']:
        raise ValueError('특징 파일 체크섬 불일치')
    if manifest['implementation_sha256'] != hashlib.sha256(Path('estate_retraining_features.py').read_bytes()).hexdigest():
        raise ValueError('특징 생성 코드가 변경되었습니다. 자료를 다시 검증하세요.')
    frame = pd.read_parquet(features)
    if not np.isfinite(frame[['actual', 'area']].to_numpy()).all():
        raise ValueError('유효하지 않은 특징 자료가 있습니다.')
    no_history = frame.loc[frame.anchor.isna()].groupby('month').size().to_dict()
    # Same production eligibility: no known self/complex/peer anchor means no
    # prediction. Keep source rows; record every unscorable row, independent of price.
    frame = frame.loc[frame.anchor.notna()].copy()
    train = frame.loc[frame.month.between('2021-03', '2025-12')]
    test = frame.loc[frame.month.between('2026-03', '2026-08')]
    recent = frame.loc[frame.month.eq('2026-09')]
    if set(train.year.unique()) != set(range(2021, 2026)) or set(test.month.unique()) != {f'2026-{m:02d}' for m in range(3, 9)}:
        raise ValueError('선언된 학습·평가 월이 빠졌습니다.')
    artifact = fit(train, '2025-12-31', trees=trees, leaves=leaves)
    artifact['feature_engine'] = manifest.get('feature_engine', 'array_stable_publication_v1')
    print('학습 완료, 동일 거래 표본 비교 중', flush=True)
    alternatives = {'direct': evaluate(test, artifact)}
    if retained_median:
        if calibration_period == '2026-spring':
            temporary = artifact
            cal = frame.loc[frame.month.between('2026-03', '2026-05')]
        else:
            temporary = fit(train.loc[train.year <= 2024], '2024-12-31', trees=trees, leaves=leaves)
            cal = train.loc[train.year == 2025]
        q = cal.anchor.to_numpy()[:, None] + temporary['model'].raw_quantiles(cal)
        offsets = [float(np.quantile(cal.actual.to_numpy()-q[:, i], level)) for i, level in enumerate(LEVELS)]
        artifact['model'] = CalibratedQuantileModel(artifact['model'], offsets)
        artifact['calibration'] = {'period': str(cal.month.min())+'~'+str(cal.month.max()), 'model_trained_through': temporary['trained_through'],
            'rows': len(cal), 'offsets': offsets, 'method': 'out_of_time_quantile_residual_offsets'}
        if calibration_period == '2026-spring':
            artifact['base_trained_through'] = '2025-12-31'
            artifact['trained_through'] = '2026-06-30'
            artifact['calibration']['available_after'] = '2026-07-01'
            artifact['calibration']['publication_lag_days'] = 31
            test = frame.loc[frame.month.between('2026-07', '2026-08')]
        alternatives['calibrated_direct'] = evaluate(test, artifact)
        from estate_regional_nowcast import ARTIFACT, MANIFEST
        baseline_spec = json.loads(Path(MANIFEST).read_text(encoding='utf-8'))
        if hashlib.sha256(Path(ARTIFACT).read_bytes()).hexdigest() != baseline_spec['sha256']:
            raise ValueError('기존 중앙값 모델 체크섬 불일치')
        old = joblib.load(ARTIFACT)
        artifact['model'] = RetainedMedianQuantileModel(artifact['model'], old['model'])
        artifact['columns'] = old['columns']
        artifact['retained_median_sha256'] = baseline_spec['sha256']
    candidate, baseline = evaluate(test, artifact), frozen_baseline(test)
    report = {'version': VERSION, 'execution': 'local', 'features': manifest,
              'training': artifact['training'], 'candidate': candidate, 'baseline': baseline,
              'alternatives': alternatives, 'calibration': artifact.get('calibration'),
              'retained_median_sha256': artifact.get('retained_median_sha256'),
              'unscorable_no_prior_history_by_month': no_history,
              'decision': compare(candidate, baseline),
              'evaluation_kind': '기존 연구에서 사용한 2026년 자료의 재사용 평가. 독립 홀드아웃 아님.',
              'evaluation_period': str(test.month.min())+'~'+str(test.month.max()),
              'alternative_direct_evaluation_period': '2026-03~2026-08',
              'scope_note': '후보는 확보·검증된 2021년 이후 자료로 학습. 기존 모델은 2007년 이후 학습. 동일 평가 거래에서 비교.',
              'baseline_endpoints': '기존 중앙 추정값과 대칭 오차 구간. 직접 학습한 분위수와 구분.',
              'late_month_note': '9월은 신고 지연에 따라 추가될 수 있어 별도 진단만 수행.'}
    if len(recent):
        report['september_diagnostic'] = {'candidate': evaluate(recent, artifact), 'baseline': frozen_baseline(recent)}
    output.mkdir(parents=True)
    joblib.dump(artifact, output/'candidate.joblib', compress=3)
    report['artifact_sha256'] = hashlib.sha256((output/'candidate.joblib').read_bytes()).hexdigest()
    write_json(output/'report.json', report, indent=2)
    print(json.dumps({'candidate': candidate['overall'], 'baseline': baseline, 'decision': report['decision']}, ensure_ascii=False), flush=True)
    return report


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--features', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--retained-median', action='store_true')
    p.add_argument('--trees', type=int, default=220)
    p.add_argument('--leaves', type=int, default=23)
    p.add_argument('--calibration-period', choices=['2025', '2026-spring'], default='2025')
    a = p.parse_args()
    run(a.features, a.output, a.retained_median, a.trees, a.leaves, a.calibration_period)
