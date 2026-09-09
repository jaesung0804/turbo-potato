"""Publish the completed full-history adaptation without changing production."""
import argparse
import json
from pathlib import Path
import shutil

from estate_io import write_json

LABELS={'all_history':'전체 과거 · 기존 입력 · 동일 가중치',
        'recency_four_years':'전체 과거 · 기존 입력 · 시기 가중치',
        'relative_uniform':'전체 과거 · 상대 가격 · 동일 가중치',
        'relative_recency_four_years':'전체 과거 · 상대 가격 · 시기 가중치'}


def attach_full_history(summary, reports):
    path=Path(reports)/'estate_full_history_adaptation_20260909.json'
    if not path.exists(): return summary
    result=json.loads(path.read_text())
    if result['production_changed']: raise ValueError('Exploratory report cannot change production')
    method=result['selection']['method']
    rows=[{'period':r['period'],'original':r['original'],'uniform':r['all_history'],
           'adapted':result['evaluation'][str(year)]['mae_oku'],
           'complex_mape':result['evaluation'][str(year)]['complex_balanced_mape_pct']}
          for year,r in zip((2025,2026),summary['price_rows'])]
    development=[{'year':int(y),'method':LABELS[m],'mae':v['mae_oku'],'complex_mape':v['complex_balanced_mape_pct']}
                 for y,methods in result['development'].items() for m,v in methods.items()]
    outcome='기존 수치 기준을 충족했습니다.' if result['numerical_gate_passed'] else '기존 수치 기준을 모두 충족하지는 못했습니다.'
    gains=[100*(1-r['adapted']/r['original']) for r in rows]
    summary['headline']=(f'전체 과거 활용 개선: 평균 가격 오차가 2025년 {gains[0]:.1f}%, 2026년 3~7월 {gains[1]:.1f}% 줄었습니다. 지역·단지별 조건이 남아 운영 모델은 유지합니다.'
        if all(v>0 for v in gains) else '단순 과거 통합과 전체 과거 활용 개선을 비교했습니다. 현재 운영 모델을 유지합니다.')
    summary['full_history_followup']={
        'headline':'과거 자료를 버리지 않고 학습 방식을 바꿨습니다.',
        'detail':'현재 모델 유지는 과거 자료의 폐기 결정이 아닙니다. 모든 적격 과거 학습 행을 유지하고, 당시 시세 대비 가격 입력과 시기별 가중치를 비교했습니다. 동일 가중치로 모든 과거를 쓰는 상대 가격 사양도 포함했습니다.',
        'selected_method':LABELS[method],
        'selection_note':'2023·2024년의 MAE와 단지 균등 MAPE를 함께 비교해 한 후보를 선택한 뒤, 선택을 고정하고 기존 2025·2026년 평가 거래에 적용했습니다.',
        'price_rows':rows,'development_rows':development,
        'conclusion':f"선택 사양: {LABELS[method]}. {outcome} 운영 모델은 유지하고 결과와 연구 모델을 보존했습니다.",
        'data_note':'2007년부터 평가 전년까지의 적격 학습 행을 모두 사용했습니다. 2006년은 이력 준비에 사용하며, 기존 3~12월 평가 설계를 유지합니다. 1~2월 거래도 과거 입력 이력에 들어갑니다. 시기 가중치 사양은 4년의 반감기를 적용하며 가장 오래된 학습 행도 양수 가중치를 갖습니다.',
        'limitations':'앞선 2025·2026년 결과를 본 뒤 설계한 탐색적 후속 비교입니다. 개발 구간에서만 후보를 선택해도 새로운 독립 검증이 되는 것은 아닙니다. 수치 개선만으로 운영 모델을 자동 교체하지 않습니다.',
        'report_url':'https://github.com/jaesung0804/turbo-potato/blob/main/reports/estate_full_history_adaptation_20260909.md',
        'production_changed':False}
    return summary


def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','| '+' | '.join(['---']*len(headers))+' |']+
                     ['| '+' | '.join(str(x) for x in r)+' |' for r in rows])


def publish(source,reports):
    source,reports=Path(source),Path(reports)
    result=json.loads((source/'results.json').read_text())
    shutil.copyfile(source/'results.json',reports/'estate_full_history_adaptation_20260909.json')
    summary_path=reports/'estate_retraining_summary_20260909.json'
    summary=attach_full_history(json.loads(summary_path.read_text()),reports)
    write_json(summary_path,summary,indent=2)
    f=summary['full_history_followup']
    # A post-evaluation error description; it never enters candidate selection.
    import numpy as np
    import pandas as pd
    pred=pd.read_parquet(source/'evaluation_predictions.parquet')
    selected=result['selection']['method']
    pred['old_ape']=np.abs(np.expm1(pred.frozen_original-pred.actual))*100
    pred['new_ape']=np.abs(np.expm1(pred[selected]-pred.actual))*100
    complex_errors=pred.groupby(['year','complex']).agg(n=('actual','size'),old_ape=('old_ape','mean'),new_ape=('new_ape','mean')).reset_index()
    segments=[]
    for year,g in complex_errors.groupby('year'):
        for label,mask in [('평가 거래 1~2건',g.n.le(2)),('평가 거래 3~9건',g.n.between(3,9)),('평가 거래 10건 이상',g.n.ge(10))]:
            v=g[mask]
            segments.append({'year':int(year),'segment':label,'complexes':len(v),'trades':int(v.n.sum()),
                'original_complex_mape_pct':float(v.old_ape.mean()),'adapted_complex_mape_pct':float(v.new_ape.mean())})
    write_json(reports/'estate_full_history_error_segments_20260909.json',{'status':'post_evaluation_descriptive_audit',
        'segments':segments,'limitation':'Groups use counts in the evaluated transactions, not pre-forecast known liquidity or a proven causal mechanism.'},indent=2)
    report=['# 전체 과거를 유지한 개선 비교 — 2026-09-09',f['headline'],f['detail'],
        f['conclusion'],'## 사전 고정과 입력',
        '[후속 계산 전에 공개한 코드·기준](https://github.com/jaesung0804/turbo-potato/blob/387280bebf1a0249842e194be84709bc440c394a/reports/estate_full_history_protocol_20260909.md). 1차 결과를 본 뒤 설계한 탐색이라는 점은 그대로 남는다.',
        '[이후 기간 평가 결과 전에 공개한 개발 선택 기록](https://github.com/jaesung0804/turbo-potato/blob/933b100aad75e2a6bc85ca1e581453b1e075b10a/reports/estate_full_history_selection_20260909.json).',
        f['data_note'],'기존 타깃도 이미 실제 로그 가격에서 당시 기준 시세를 뺀 잔차다. 이번 상대 가격 사양은 입력 가격들에서도 해당 행의 당시 기준 시세를 빼고 절대 기준 시세 입력을 제거한다. 학습 또는 평가 기간 전체의 평균으로 표준화하지 않는다.',
        'LightGBM 사양은 1차 비교와 동일하며 가중치는 평균 1로 맞춘다. 시기 가중치가 없는 상대 가격 사양도 비교하여 과거 영향 감소와 가격 표현 변경을 구분한다.',
        '## 개발 구간의 모든 결과',f['selection_note'],
        table(['연도','사양','MAE 억','단지 균등 MAPE %'],
            [[r['year'],r['method'],f"{r['mae']:.4f}",f"{r['complex_mape']:.3f}"] for r in f['development_rows']]),
        '선택 점수는 두 연도의 MAE와 단지 균등 MAPE를 각 연도 동일 가중치 전체 과거 값으로 나눈 네 비율의 평균이다. 1보다 작으면 이 개발 기준에서 개선이다.',
        table(['후보','선택 점수'],[[LABELS[m],f'{v:.6f}'] for m,v in result['selection']['scores'].items()]),
        '## 선택 고정 후 기존 평가 구간에 적용',
        table(['기간','기존 고정 MAE 억','단순 전체 과거 MAE 억','개선 사양 MAE 억','개선 사양 단지 균등 MAPE %'],
            [[r['period'],f"{r['original']:.4f}",f"{r['uniform']:.4f}",f"{r['adapted']:.4f}",f"{r['complex_mape']:.3f}"] for r in f['price_rows']]),
        table(['연도','기존 대비 MAE 개선 %','단순 전체 대비 개선 %','3% 조건','단지 균등 조건','지역 조건'],
            [[g['year'],f"{g['gain_vs_frozen_pct']:+.2f}",f"{g['gain_vs_uniform_full_history_pct']:+.2f}",
              g['three_percent_vs_frozen_and_recent'],g['complex_balanced_not_worse'],g['region_deterioration_within_two_percent']] for g in result['gates']]),
        '## 지역별 결과',table(['연도','지역','거래 수','MAE 억','단지 균등 MAPE %','평균 편향 %'],
            [[r['year'],r['region'],r['transactions'],f"{r['mae_oku']:.4f}",f"{r['complex_balanced_mape_pct']:.3f}",f"{r['bias_pct']:+.3f}"] for r in result['regions']]),
        '## 평가 거래 수별 단지 오차 — 사후 설명용',
        table(['연도','평가 표본 구분','단지 수','거래 수','기존 단지 균등 MAPE %','개선 사양 단지 균등 MAPE %'],
            [[r['year'],r['segment'],r['complexes'],r['trades'],f"{r['original_complex_mape_pct']:.3f}",f"{r['adapted_complex_mape_pct']:.3f}"] for r in segments]),
        '후보 선택 이후 결과를 설명하기 위한 구분이다. 평가 거래 건수로 나눴으므로 사전에 알려진 유동성 집단이나 오차의 인과 원인으로 해석하지 않는다.',
        '## 전체 과거 행의 사용 확인',table(['단계','연도','사양','학습 행','양수 가중치 행','첫 연도','끝 연도','가중치 유효 표본 수'],
            [[r['stage'],r['year'],LABELS[r['method']],r['rows'],r['positive_weight_rows'],r['first_year'],r['last_year'],round(r['effective_sample_size'])] for r in result['training']]),
        '유효 표본 수는 가중치 집중도를 나타내며 삭제한 행 수가 아니다. 취소·지번 불명확·과거 기준 시세 부재 등 1차 비교의 제외 사유는 유지한다.',
        '## 해석과 남은 개선',f['limitations'],
        '이 비교로 모든 활용 방법을 소진한 것은 아니다. 지역별 과대·과소 추정의 원인, 당시 행정 경계와 동일 단지 연결, 가격 변화와 장기 입지 특성의 역할 분리 등을 추가로 검토할 수 있다. 한 번의 단순 통합 실패를 근거로 과거 자료를 폐기하지 않는다. 각 방법이 실제로 개선하는지는 별도 고정 검증으로 판단한다.',
        '현재 운영 가격 모델, 서울·광명 잠재력 모델, 9월 고정 예측, 기존 미통과 연구는 보존한다. 이번 변경은 결과 설명과 연구 기록이다.',
        '## 재현',
        '```bash\npython analyze_estate_full_history_adaptation.py\npython publish_estate_full_history_report.py\n```',
        '1차 연구 입력 복원 방법은 estate_retraining_results_20260909.md에 있다. 후속 입력 지문·개발 선택·지역 결과·단지 대응 부트스트랩은 같은 이름의 JSON에 저장했다. 행별 예측과 연구 모델은 후속 체크포인트에 보존한다.']
    (reports/'estate_full_history_adaptation_20260909.md').write_text('\n\n'.join(report)+'\n')
    print(json.dumps({'headline':summary['headline'],'selected':f['selected_method'],'numerical_gate_passed':result['numerical_gate_passed']},ensure_ascii=False))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',default='.work/full-history-adaptation-20260909')
    p.add_argument('--reports',default='reports')
    a=p.parse_args();publish(a.source,a.reports)
