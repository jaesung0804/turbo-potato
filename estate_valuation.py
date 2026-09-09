"""One price comparison contract, plus publication-lagged transaction replay.

The smooth score is a presentation of the price discount, not a probability.
Transaction counts describe evidence quality independently of the price gap.
Historical replays use a frozen out-of-time model and an assumed publication
lag; the source's final cancellation status is not a true historical vintage.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
from pathlib import Path
from estate_regional_nowcast import ARTIFACT, MANIFEST, component_version

SCORE_VERSION = 'estate-discount-v1'
REPLAY_VERSION = 'estate-transaction-replay-v1'
DEFAULT_REPLAY = Path('metadata/transaction_valuation_2026_capital_v2.json.gz')
RECENT_COMPARISON_DAYS = 90
MIN_EXPLORATION_TRADES = 3


def _positive(value):
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def canonical_price(value):
    """억원 rounded to the displayed 만원; identical to JS positive Math.round."""
    value = _positive(value)
    if value is None:
        return None
    if not math.isfinite(value * 10000):
        return None
    rounded = math.floor(value * 10000 + .5) / 10000
    return rounded if rounded > 0 else None


def score_at_price(neutral, price, scale):
    neutral, price, scale = canonical_price(neutral), canonical_price(price), _positive(scale)
    if neutral is None or price is None or scale is None:
        return None
    if neutral == price:
        return 50.0
    score = 50 + 40 * math.tanh(math.log(neutral / price) / scale)
    return math.floor(score * 10 + .5) / 10


def compare_price(neutral, price, scale):
    neutral, price = canonical_price(neutral), canonical_price(price)
    score = score_at_price(neutral, price, scale)
    if score is None:
        return {'status': 'insufficient_history'}
    return {'status': 'available', 'score_version': SCORE_VERSION,
            'neutral_price_billion': neutral, 'comparison_price_billion': price,
            'score': score, 'discount_pct': round((1 - price / neutral) * 100, 2),
            'score_error_scale': float(scale)}


def attach_recent_comparison_prices(model, transactions, source_quality=None):
    """Aggregate the already-loaded clean source over exactly 90 calendar days.

    The end is the last observed contract date, capped at the valuation month.
    This describes currently published contracts, not their publication dates.
    Empty windows stay empty; annual medians are never substituted.
    """
    import pandas as pd

    if transactions.empty or not transactions.date.notna().any():
        raise ValueError('Recent comparison requires a dated source to establish its data cutoff')
    month_end = pd.Period(model['model_month']).end_time.normalize()
    end = transactions.loc[transactions.date.le(month_end), 'date'].max()
    if pd.isna(end):
        raise ValueError('Recent comparison has no dated contracts within the valuation period')
    end = end.normalize()
    start = end - pd.Timedelta(days=RECENT_COMPARISON_DAYS - 1)
    window = transactions.loc[transactions.date.between(start, end)]
    grouped = window.groupby('key', sort=False).agg(
        trade_count=('price_oku', 'size'), median_price_billion=('price_oku', 'median'),
        first_contract_date=('date', 'min'), last_contract_date=('date', 'max'))
    basis = {'window_days': RECENT_COMPARISON_DAYS, 'window_start': str(start.date()),
             'window_end': str(end.date()), 'data_through': str(end.date()),
             'end_basis': 'latest_observed_contract_date_capped_at_valuation_month',
             'inclusive_boundaries': True}
    rows = grouped.to_dict('index')
    for rec in model['recommendations']:
        row = rows.get(rec['building_key'])
        recent = {**basis, 'status': 'no_recent_transactions', 'trade_count': 0,
                  'median_price_billion': None, 'first_contract_date': None,
                  'last_contract_date': None}
        if row:
            recent.update(status='available', trade_count=int(row['trade_count']),
                          median_price_billion=canonical_price(row['median_price_billion']),
                          first_contract_date=str(row['first_contract_date'].date()),
                          last_contract_date=str(row['last_contract_date'].date()))
        rec['recent_price_comparison'] = recent
    model['recent_price_comparison'] = {**basis,
        'source_sha256': (source_quality or {}).get('source_sha256'),
        'source_used_rows': len(transactions), 'window_transactions': len(window),
        'window_types': len(grouped),
        'source_note': '최종 공개 자료 중 해제·직거래를 제외한 동일 단지·정확한 면적의 계약일 기준 중앙가입니다. 공개일 기준 집계가 아닙니다.'}
    return model


def attach_current_comparisons(model):
    """Compare current F50 with the recent window; retain annual stats separately."""
    available = 0
    recent_evidence = 0
    for rec in model['recommendations']:
        v = rec.get('current_valuation') or {}
        recent = rec.get('recent_price_comparison') or {}
        comparison = {'status': v.get('status', 'insufficient_history')}
        if v.get('status') == 'available':
            comparison = compare_price(v.get('price_billion'), recent.get('median_price_billion'),
                                       v.get('score_error_scale'))
            if recent.get('status') != 'available':
                comparison = {'status': 'no_recent_transactions'}
            if comparison['status'] == 'available':
                enough = (v.get('recent_trade_count', 0) >= MIN_EXPLORATION_TRADES
                          and recent.get('trade_count', 0) >= MIN_EXPLORATION_TRADES)
                comparison.update(comparison_kind='recent_90d_median_at_current_valuation',
                                  comparison_period=f"{recent['window_start']}~{recent['window_end']}",
                                  comparison_window_start=recent['window_start'],
                                  comparison_window_end=recent['window_end'],
                                  comparison_window_days=recent['window_days'],
                                  comparison_data_through=recent['data_through'],
                                  comparison_trade_count=recent['trade_count'],
                                  evidence_level='recent_evidence' if enough else 'sparse_history',
                                  min_evidence_trades=MIN_EXPLORATION_TRADES,
                                  valuation_month=v['month'], feature_cutoff=v['feature_cutoff'],
                                  model_version=v.get('model_version') or model.get('nowcast', {}).get('version'))
                available += 1
                recent_evidence += int(enough)
        rec['valuation_comparison'] = comparison
    model['recommendations'].sort(key=lambda r: (
        r['valuation_comparison']['status'] != 'available',
        -r['valuation_comparison'].get('score', 0),
        r.get('region_code', ''), r['building_key']))
    model['valuation'] = {
        'version': SCORE_VERSION, 'available_types': available,
        'recent_evidence_types': recent_evidence,
        'default_evidence_filter': {'min_model_input_trades': MIN_EXPLORATION_TRADES,
                                    'min_comparison_trades': MIN_EXPLORATION_TRADES},
        'recent_comparison': model.get('recent_price_comparison'),
        'price_precision_oku': .0001,
        'score_formula': '50 + 40 * tanh(log(F50 / price) / score_error_scale)',
        'score_note': '50점은 표시된 기준가와 같은 가격입니다. 할인율이 클수록 점수가 높으며 거래수는 점수를 낮추지 않고 자료 품질로 따로 표시합니다.',
        'discount_note': '할인율 = (기준가 - 비교가격) / 기준가 × 100. 양수는 기준가보다 낮은 가격입니다.',
        'comparison_note': '현재 월 기준가와 자료마감일까지 최근 90일 실거래 중앙가를 비교합니다. 90일 거래가 없으면 미산출하며 연간 중앙가로 대체하지 않습니다. 계약월별 재평가는 현재 선택한 모델 사양을 과거 월의 입력에 적용한 소급 분석입니다.',
        'evidence_note': '기본 탐색은 기준가 입력 이력과 비교 90일 거래가 각각 3건 이상인 평형입니다. 3건은 정확도 보장 기준이 아니며, 전체 평형 보기로 희소 자료도 조회할 수 있습니다.',
        'legacy_annual_scores': 'audit_only',
    }
    return model


def _safe_number(value):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def replay_transactions(d, artifact, spec, month):
    """Appraise each supported contract with the preceding information cutoff.

    No annual-average model scores enter these results. Each contract's floor
    is an observed property characteristic. Its price is exclusively the target.
    """
    import numpy as np
    import pandas as pd
    from estate_nowcast import build_features

    year = int(month[:4])
    if str(year) != str(spec['prediction_year']) or int(month[5:]) < 3:
        raise ValueError('Transaction replay requires a supported prediction month')
    if pd.Timestamp(artifact['trained_through']) >= pd.Timestamp(f'{year}-01-01'):
        raise ValueError('Transaction replay labels must follow model training')
    first, lag = f'{year}-03', int(spec.get('assumed_reporting_lag_days', 31))
    end = pd.Period(month).end_time
    source = d[d.date <= end].copy()
    if spec.get('feature_engine') == 'array_equivalent_v1':
        from estate_retraining_features import monthly_features
        features = monthly_features(source, first, month, policy=False, uniform_lag=lag)
    else:
        features = build_features(source, lag, first, month)
    wanted = source[source.month.between(first, month)]
    total = len(wanted)
    if features.empty:
        raise ValueError('No supported transaction replay rows')
    if artifact['model'] is None:
        prediction = features[artifact['selected']].to_numpy()
    else:
        prediction = features.anchor + artifact['model'].predict(features[artifact['columns']])
    features['neutral'] = np.exp(prediction) * features.area / 10000
    valid = np.isfinite(features.neutral) & features.neutral.gt(0) & features.price_oku.gt(0)
    features = features.loc[valid].copy()
    comparisons = [compare_price(f, p, spec['score_error_scale'])
                   for f, p in zip(features.neutral, features.price_oku)]
    features['score'] = [r['score'] for r in comparisons]
    features['discount_pct'] = [r['discount_pct'] for r in comparisons]
    features['neutral'] = [r['neutral_price_billion'] for r in comparisons]
    features['observed'] = [r['comparison_price_billion'] for r in comparisons]
    rows = {}
    for key, group in features.groupby('key', sort=True):
        monthly = []
        for period, g in group.groupby('month', sort=True):
            monthly.append({
                'month': period, 'trade_count': len(g),
                'feature_cutoff': str(g.feature_cutoff.iloc[0]),
                'median_score': round(float(g.score.median()), 1),
                'median_discount_pct': round(float(g.discount_pct.median()), 2),
                'median_price_billion': canonical_price(g.observed.median()),
                'median_neutral_price_billion': canonical_price(g.neutral.median()),
                'recent_history_median_count': int(g.n90.median()),
            })
        recent = group.sort_values(['contract_date', 'floor', 'observed'], ascending=[False, False, False]).head(3)
        latest = [{
            'contract_date': str(r.contract_date), 'valuation_month': r.month,
            'feature_cutoff': r.feature_cutoff, 'floor': _safe_number(r.floor),
            'price_billion': r.observed, 'neutral_price_billion': r.neutral,
            'score': r.score, 'discount_pct': r.discount_pct,
            'recent_trade_count': int(r.n90),
        } for r in recent.itertuples()]
        rows[key] = {
            'status': 'available',
            'method': 'retrospective_policy_revaluation' if spec.get('policy_selected_at') else 'contract_month_ex_ante',
            'policy_selected_at': spec.get('policy_selected_at'),
            'model_version': component_version(spec, group.region.iloc[0]) if 'region' in group else spec.get('version'),
            'trade_count': len(group), 'period_start': str(group.contract_date.min()),
            'period_end': str(group.contract_date.max()),
            'median_score': round(float(group.score.median()), 1),
            'median_discount_pct': round(float(group.discount_pct.median()), 2),
            'monthly': monthly, 'recent_transactions': latest,
        }
    error = np.abs(features.neutral / features.observed - 1)
    meta = {
        'version': REPLAY_VERSION, 'score_version': SCORE_VERSION,
        'model_version': spec.get('version'), 'artifact_sha256': spec['sha256'],
        'policy_selected_at': spec.get('policy_selected_at'),
        'retrospective_policy_revaluation': bool(spec.get('policy_selected_at')),
        'score_error_scale': spec['score_error_scale'],
        'trained_through': artifact['trained_through'], 'model_month': month,
        'period_start': first, 'data_through': str(source.date.max().date()),
        'assumed_reporting_lag_days': lag,
        'eligible_transactions': total, 'valued_transactions': len(features),
        'unavailable_transactions': total - len(features), 'available_types': len(rows),
        'mean_absolute_error_oku': round(float((error * features.observed).mean()), 4),
        'median_absolute_pct_error': round(float(error.median() * 100), 3),
        'note': ('모델 사양은 2026-09-09에 선택했습니다. 과거 월의 입력으로 다시 계산한 소급 분석이며 당시 공개했던 예측이 아닙니다. ' if spec.get('policy_selected_at') else '') + '각 계약월 시작일보다 31일 앞선 계약까지 사용하고 실제 거래 층을 입력했습니다. 1~2월과 학습 연도는 제외합니다. 최종 공개 자료의 해제 상태를 사용하므로 당시 공개 원본의 완전한 재현은 아닙니다.',
        'aggregation_note': '연간·월별 점수는 계약별 점수의 중앙값입니다. 중앙 거래가와 중앙 기준가를 재입력한 점수와는 일반적으로 다릅니다. 최근 계약 표는 개별 가격으로 정확히 재현됩니다.',
    }
    return {'metadata': meta, 'by_key': rows}


def build_transaction_replay(source, output=DEFAULT_REPLAY, month='2026-09',
                             artifact_path=Path(ARTIFACT),
                             manifest_path=Path(MANIFEST)):
    import joblib
    from estate_nowcast import load_transactions
    source, output = Path(source), Path(output)
    manifest_body = Path(manifest_path).read_bytes()
    manifest_hash = hashlib.sha256(manifest_body).hexdigest()
    spec = json.loads(manifest_body)
    artifact_bytes = Path(artifact_path).read_bytes()
    if hashlib.sha256(artifact_bytes).hexdigest() != spec['sha256']:
        raise ValueError('Monthly valuation artifact checksum mismatch')
    raw_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    if output.exists():
        cached = json.loads(gzip.decompress(output.read_bytes()))
        meta = cached.get('metadata', {})
        if (meta.get('version') == REPLAY_VERSION and meta.get('score_version') == SCORE_VERSION
                and meta.get('source_sha256') == raw_hash and meta.get('model_month') == month
                and meta.get('manifest_sha256') == manifest_hash
                and meta.get('artifact_sha256') == spec['sha256']):
            return cached
    artifact = joblib.load(artifact_path)
    if artifact['trained_through'] != spec['trained_through']:
        raise ValueError('Monthly valuation training date mismatch')
    d, quality = load_transactions(source)
    result = replay_transactions(d, artifact, spec, month)
    result['metadata']['source_sha256'] = raw_hash
    result['metadata']['manifest_sha256'] = manifest_hash
    result['metadata']['source_quality'] = quality
    body = json.dumps(result, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.tmp')
    temporary.write_bytes(gzip.compress(body, mtime=0))
    temporary.replace(output)
    return result


def attach_transaction_replay(model, replay):
    model['transaction_valuation'] = replay['metadata']
    for rec in model['recommendations']:
        rec['transaction_valuation'] = replay['by_key'].get(
            rec['building_key'], {'status': 'no_supported_contracts'})
    return model


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=Path('data/capital_area_apt_trade_transactions.csv'))
    parser.add_argument('--output', type=Path, default=DEFAULT_REPLAY)
    parser.add_argument('--month', default='2026-09')
    args = parser.parse_args()
    result = build_transaction_replay(args.source, args.output, args.month)
    print(json.dumps(result['metadata'], ensure_ascii=False, indent=2))
