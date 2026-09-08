"""Audit observed public-row arrival intervals; do not estimate legal reporting lag.

Uses only locally archived, complete MOLIT partitions. Baseline rows are left
censored. A new row signature may be a correction or an indistinguishable set
of contracts, so neither its first-seen time nor its count identifies publication
time for an actual contract. Output deliberately has no fitted correction factor.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
from zoneinfo import ZoneInfo

from estate_vintages import row_id

KST = ZoneInfo('Asia/Seoul')
BINS = ('0–7', '8–14', '15–30', '31–60', '61–90', '91–180', '181+')
SOURCE_URL = 'https://rt.molit.go.kr/pt/bbs/faqList.do'


def timestamp(value):
    at = datetime.fromisoformat(value)
    if at.tzinfo is None:
        raise ValueError('Observation timestamps require a timezone')
    return at.astimezone(timezone.utc)


def contract_date(row):
    try:
        return datetime.strptime(str(row.get('CTRT_DAY', '')), '%Y%m%d').date()
    except ValueError:
        return None


def day_bin(days):
    if days < 0:
        return 'negative'
    for ceiling, name in zip((7, 14, 30, 60, 90, 180), BINS):
        if days <= ceiling:
            return name
    return BINS[-1]


def inspect_partition(data, fetched_at=None):
    """Return counts and anonymous arrival events for one complete partition.

    Legacy event times on other signatures are not promoted into a full polling
    history. The baseline is a valid, conservative absence bound for new rows.
    Explicit schema-2 polls make subsequent bounds narrower, including polls
    with no changes. These intervals concern row appearance, not a contract ID.
    """
    if not data.get('complete'):
        raise ValueError('Incomplete partitions cannot establish prior absence')
    ledger = data.get('observation_ledger')
    counts = Counter(current_rows=len(data['rows']))
    if ledger is None:
        counts['rows_without_observation_ledger'] = len(data['rows'])
        return {'counts': counts, 'events': [], 'baseline': None, 'latest': None,
                'known_poll_times': [], 'complete_since': None}
    baseline = timestamp(ledger['baseline_at'])
    polls = sorted({baseline, *(timestamp(x) for x in ledger.get('observation_times', []))})
    latest = max(polls)
    if fetched_at:
        latest = max(latest, timestamp(fetched_at))
    counts['ledger_partitions'] = 1
    if ledger.get('observation_times'):
        counts['partitions_with_explicit_poll_history'] = 1
    events = []
    for identity, record in ledger['records'].items():
        row = record['row']
        if row_id(row) != identity:
            raise ValueError('Observation identity checksum mismatch')
        changes = [(timestamp(at), n) for at, n in record['changes']]
        if not changes or any(not isinstance(n, int) or isinstance(n, bool) or n < 0 for _, n in changes):
            raise ValueError('Observation counts must be nonnegative integers')
        if changes[0][0] < baseline or any(b[0] <= a[0] for a, b in zip(changes, changes[1:])):
            raise ValueError('Observation revisions must advance in time')
        latest = max(latest, changes[-1][0])
        first_at, first_count = changes[0]
        if first_count <= 0:
            raise ValueError('A newly observed signature needs a positive count')
        counts['ledger_signatures'] += 1
        counts['latest_ledger_rows'] += changes[-1][1]
        if first_at == baseline:
            counts['baseline_left_censored_signatures'] += 1
            counts['baseline_left_censored_rows'] += first_count
        else:
            counts['post_baseline_new_signatures'] += 1
            counts['post_baseline_new_row_copies'] += first_count
            prior = max(p for p in polls if p < first_at)
            date = contract_date(row)
            cancelled = str(row.get('RTRCN_DAY', '')).strip() not in ('', '-', '--')
            event = {
                'first_observed_at': first_at.isoformat(),
                'prior_known_absent_at': prior.isoformat(),
                'absence_bound_kind': 'explicit_poll' if prior > baseline else 'baseline',
                'interval_width_days': (first_at - prior).total_seconds() / 86400,
                'contract_date': date.isoformat() if date else None,
                'first_observed_age_days': (first_at.astimezone(KST).date() - date).days if date else None,
                'row_copies': first_count, 'cancelled_at_first_observation': cancelled,
            }
            events.append(event)
            counts['new_cancelled_signatures' if cancelled else 'new_active_signatures'] += 1
        for (_, before), (_, after) in zip(changes, changes[1:]):
            counts['revision_events'] += 1
            if after > before:
                counts['count_increase_events'] += 1
                counts['additional_row_copies_on_existing_signatures'] += after - before
                if before == 0:
                    counts['reappearance_events'] += 1
            elif after < before:
                counts['count_decrease_events'] += 1
                counts['removed_row_copies'] += before - after
    if counts['latest_ledger_rows'] != len(data['rows']):
        raise ValueError('Latest ledger multiplicity disagrees with the complete partition')
    if Counter(row_id(row) for row in data['rows']) != Counter(
            {key: record['changes'][-1][1] for key, record in ledger['records'].items()
             if record['changes'][-1][1] > 0}):
        raise ValueError('Latest ledger identities disagree with the complete partition')
    return {'counts': counts, 'events': events, 'baseline': baseline, 'latest': latest,
            'known_poll_times': polls,
            'complete_since': ledger.get('observation_times_complete_since')}


def audit(raw_state):
    raw_state = Path(raw_state)
    manifest_bytes = (raw_state / 'raw_manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    totals = Counter()
    all_times, baseline_dates = [], Counter()
    by_first_date, by_contract_month, by_source = defaultdict(Counter), defaultdict(Counter), []
    ages = {name: Counter() for name in (*BINS, 'negative', 'invalid_date')}
    intervals = Counter()
    for item in manifest['files']:
        if not item['path'].startswith('partitions/'):
            continue
        path = (raw_state / item['path']).resolve()
        if not path.is_relative_to(raw_state.resolve()):
            raise ValueError('Source manifest path escapes its directory')
        body = path.read_bytes()
        if hashlib.sha256(body).hexdigest() != item['sha256'] or len(body) != item['size']:
            raise ValueError(f'Source partition checksum mismatch: {item["path"]}')
        data = json.loads(gzip.decompress(body))
        result = inspect_partition(data, item.get('fetched_at'))
        totals['source_partitions'] += 1
        totals.update(result['counts'])
        if result['baseline'] is None:
            continue
        all_times.extend((result['baseline'], result['latest']))
        baseline_dates[result['baseline'].astimezone(KST).date().isoformat()] += 1
        source_count = Counter()
        for event in result['events']:
            date = timestamp(event['first_observed_at']).astimezone(KST).date().isoformat()
            active = not event['cancelled_at_first_observation']
            labels = {'new_signatures': 1, 'new_row_copies': event['row_copies'],
                      'active_signatures': int(active), 'cancelled_signatures': int(not active)}
            by_first_date[date].update(labels)
            month = event['contract_date'][:7] if event['contract_date'] else 'invalid_date'
            by_contract_month[month].update(labels)
            source_count.update(labels)
            age = event['first_observed_age_days']
            bucket = day_bin(age) if age is not None else 'invalid_date'
            ages[bucket].update(labels)
            intervals[event['absence_bound_kind']] += 1
            intervals['over_7_days' if event['interval_width_days'] > 7 else 'within_7_days'] += 1
        if source_count:
            by_source.append({'path': item['path'], 'month': data['month'], 'region_code': data['code'],
                              'sha256': item['sha256'], **dict(source_count)})
    first, last = (min(all_times), max(all_times)) if all_times else (None, None)
    observed_days = (last - first).total_seconds() / 86400 if first else 0
    return {
        'schema_version': 1, 'status': 'observation_only_not_calibrated',
        'created_at': datetime.now(timezone.utc).isoformat(),
        'source': {'manifest_sha256': hashlib.sha256(manifest_bytes).hexdigest(),
                   'collection': {key: manifest['collection'].get(key) for key in (
                       'fetched_at', 'start', 'end', 'rows', 'partition_count', 'region_count', 'sha256', 'source')},
                   'all_partition_checksums_verified': True},
        'window': {'first_baseline_at': first.isoformat() if first else None,
                   'latest_observed_at': last.isoformat() if last else None,
                   'elapsed_days': observed_days,
                   'baseline_partitions_by_kst_date': dict(sorted(baseline_dates.items()))},
        'counts': dict(sorted(totals.items())),
        'first_observed_by_kst_date': {key: dict(value) for key, value in sorted(by_first_date.items())},
        'new_signatures_by_contract_month': {key: dict(value) for key, value in sorted(by_contract_month.items())},
        'contract_to_first_observed_age_days': [
            {'bin': key, **{name: value.get(name, 0) for name in (
                'new_signatures', 'new_row_copies', 'active_signatures', 'cancelled_signatures')}}
            for key, value in ages.items()],
        'row_appearance_interval_bounds': dict(intervals),
        'source_partitions_with_new_signatures': by_source,
        'interpretation': {
            'measured_quantity': 'contract_date_to_first_observation_of_a_public_row_signature',
            'is_true_publication_delay': False,
            'baseline_rows_excluded_from_arrival_ages': True,
            'stable_contract_identifiers_available': False,
            'historical_unchanged_polls_available': bool(totals['partitions_with_explicit_poll_history']),
            'correction_weight_fitted': False,
            'limitations': [
                '기준 관측에 이미 존재한 행은 최초 공개를 관측하지 못했으므로 도착 나이 분포에서 제외한다.',
                '신규 공개행에는 정정·해제 표시·동일 내용 복수 거래가 섞일 수 있어 신규 계약 수가 아니다.',
                '새로 보인 행의 등장 구간만 관측한다. 최초 관측 시각은 실제 신고·공개 시각이 아니다.',
                '아직 공개되지 않은 계약은 보이지 않는다. 짧은 관측창의 유입 행만으로 전체 지연 분포를 적합하지 않는다.',
                '과거 원장은 변화 없는 수집 시각을 남기지 않았다. 새 코드부터 완료된 수집 시각을 모두 보존한다.',
            ]},
        'next_calibration_design': {
            'state': 'collect_complete_poll_history_before_fitting',
            'proposed_minimum_observation_days': 180,
            'proposed_minimum_mature_contract_months': 6,
            'proposed_contract_cohort_maturity_days': 90,
            'thresholds_are_operational_gates_not_proof': True,
            'estimate': '월별 계약 코호트의 공개행 누적 도착률을 지역·거래종류·시기별로 추정하고 희소 지역은 상위 지역으로 축소한다.',
            'validation': '성숙한 계약월을 순서대로 보류해 미공개 거래량 추정과 가격 추세 오차를 확인한다. 실제 공개 빈티지만 사용한다.',
            'price_adjustment': '거래량의 미공개분 보정과 가격의 오래됨 보정을 분리한다. 신고 지연을 가격 상승률로 직접 치환하지 않는다.',
            'permit_handling': '허가 절차 전 의사결정 시각과 신고 계약일의 차이는 이 원장으로 측정할 수 없다. 지역별 허가 적용 이력이 확보되면 별도 구분한다.',
        },
        'official_context': {
            'url': SOURCE_URL, 'checked_on': '2026-09-08',
            'summary': '국토교통부 FAQ는 2020-02-21 전 계약의 신고 기한을 60일, 이후를 30일로 설명한다. 정상 공개 절차에서는 신고 다음 날 공개가 원칙이며 계약월로 귀속된다. 법정 기한은 관측된 지연 분포나 모든 건의 최대 지연을 의미하지 않는다.',
            'permit_delay_assumption_applied': False,
        },
    }


def markdown(report):
    counts, window = report['counts'], report['window']
    rows = ['# 실거래 공개 시차 관측 감사', '',
        '**현재 자료로 평균 공시 시차를 학습하거나 일률적인 가격 보정치를 적용하지 않는다.**', '',
        f"공식 원천의 {counts['source_partitions']:,}개 파티션·{counts['current_rows']:,}행을 검사했다. "
        f"관측 원장이 있는 파티션은 {counts.get('ledger_partitions', 0):,}개이며, "
        f"가장 이른 기준 관측부터 마지막 확인까지 {window['elapsed_days']:.2f}일이다.", '',
        '| 항목 | 값 |', '| --- | ---: |',
        f"| 기준 관측 시점에 이미 존재한 공개행 서명 — 지연 분석 제외 | {counts.get('baseline_left_censored_signatures', 0):,} |",
        f"| 기준 관측 이후 처음 나타난 공개행 서명 | {counts.get('post_baseline_new_signatures', 0):,} |",
        f"| 그중 처음 관측될 때 해제 표시가 없는 서명 | {counts.get('new_active_signatures', 0):,} |",
        f"| 기존 서명의 개수 변화 사건 | {counts.get('revision_events', 0):,} |",
        f"| 원장이 없는 행 — 최초 관측 정보 없음 | {counts.get('rows_without_observation_ledger', 0):,} |", '',
        '## 계약일부터 최초 관측일까지의 일수', '',
        '아래는 해당 기간에 유입된 공개행의 나이 분포다. 실제 계약의 신고 지연 분포가 아니며, 기준 관측 전에 이미 있던 행은 제외했다. 날짜 차이는 한국 시각의 달력 날짜로 계산한다.', '',
        '| 일수 | 신규 공개행 서명 | 해제 표시 없는 서명 |', '| --- | ---: | ---: |']
    rows += [f"| {row['bin']} | {row['new_signatures']:,} | {row['active_signatures']:,} |"
             for row in report['contract_to_first_observed_age_days']]
    rows += ['', '## 해석과 코드 반영', '',
        '기준 관측에 이미 존재한 행은 그보다 언제 먼저 공개됐는지 모르는 자료다. 과거 계약일과 이번 다운로드일의 차이를 신고 지연으로 계산하지 않았다. '
        '신규 공개행에도 정정, 해제 표시가 추가된 행, 공개 필드가 우연히 같은 복수 계약이 섞일 수 있다. 안정적인 계약 식별자가 없어 이들을 임의로 연결하지 않는다.', '',
        '기존 원장은 행별 개수 변화 시각만 저장했다. 따라서 변화가 없는 날의 수집 시각을 복원하지 않는다. '
        '이번 코드부터는 완료된 각 파티션 수집 시각을 정확히 보존한다. 이전 schema 1 원장을 읽을 수 있으며, 업그레이드 전의 누락된 관측시각을 만들어내지 않는다. '
        '새 행이 없었던 마지막 실제 관측과 처음 나타난 관측 사이의 등장 구간을 이후 더 좁힐 수 있다. '
        '이 구간 역시 원본 계약의 실제 공개 시각을 확인한 것은 아니다.', '',
        '## 충분한 관측이 쌓인 뒤 적용할 방법', '',
        '우선 180일 관측과 90일 이상 성숙한 계약월 6개를 운영상 검토 조건으로 둔다. 이는 통계적 충분성을 보증하는 숫자가 아니다. '
        '월별 계약 코호트의 누적 공개행 도착률을 추정하고 지역·거래종류·시기별로 비교한다. 표본이 적은 구간은 상위 지역으로 축소한다. '
        '연속한 과거 계약월을 보류해 나중에 관측된 거래량과 가격 추세로 검증한 후에만 보정치를 채택한다.', '',
        '거래량의 미공개분 보정과 가격의 오래됨 보정을 별도로 둔다. 지연이 길다는 이유로 가격을 일률적으로 올리지 않는다. '
        '허가 신청 전 의사결정 시점은 현재 계약일 필드에서 알 수 없으므로, 토지거래허가구역 전체에 2주~2개월을 일괄 적용하지 않는다.', '',
        report['official_context']['summary'] + f" [국토교통부 공식 FAQ]({SOURCE_URL})", '',
        '## 재현', '',
        '```sh',
        'python analyze_estate_reporting_lag.py --raw-state .work/raw-state',
        'python -m pytest tests/test_reporting_lag.py tests/test_estate_vintages.py',
        '```', '',
        f"원천 manifest SHA256: `{report['source']['manifest_sha256']}`. 모든 gzip 원천의 크기와 SHA256을 manifest와 대조했다. "
        '기준일·수집일별 집계와 새 행이 관측된 원천 파일 목록은 `estate_reporting_lag.json`에 저장했다.']
    return '\n'.join(rows) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--raw-state', type=Path, required=True)
    parser.add_argument('--json-path', type=Path, default=Path('reports/estate_reporting_lag.json'))
    parser.add_argument('--markdown-path', type=Path, default=Path('reports/estate_reporting_lag.md'))
    args = parser.parse_args()
    report = audit(args.raw_state)
    args.json_path.parent.mkdir(parents=True, exist_ok=True)
    args.markdown_path.parent.mkdir(parents=True, exist_ok=True)
    args.json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    args.markdown_path.write_text(markdown(report))
    print(json.dumps({'window': report['window'], 'counts': report['counts']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
