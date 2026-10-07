"""24개월 상대 상승 모델의 로컬 분위수 실험. 미래 결과를 입력에 사용하지 않는다."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from sklearn.metrics import mean_pinball_loss
from estate.models.potential.v1.experiment import CASES, COLS
from estate.models.potential.v2.horizons import price_snapshot, labels, measure, finite, LAW_CHANGE
from estate.models.nowcast.v1.model import load_transactions

EXTRA = ['lower_spread', 'upper_spread', 'active_days', 'median_floor']
QUANTILES = [.1, .5, .9]
PARAMS = dict(n_estimators=120, num_leaves=7, min_child_samples=100, reg_lambda=20,
              learning_rate=.04, n_jobs=4, verbosity=-1, random_state=20260908)

def model(q=.5):
    return LGBMRegressor(objective='quantile', alpha=q, **PARAMS)

def mature_train(frame, origin):
    return frame[(frame.label_available <= origin) & frame.target.notna() & ~frame.complex.isin(CASES)].copy()

def snapshot(d, origin, regime):
    f = price_snapshot(d, origin, regime)
    cutoff = pd.Timestamp(f.cutoff.iloc[0])
    past = d[(d.available_date <= origin) & d.date.between(cutoff-pd.Timedelta(days=179), cutoff)]
    grouped = past.groupby('key')
    f['lower_spread'] = f.entry-grouped.log_price.quantile(.1)
    f['upper_spread'] = grouped.log_price.quantile(.9)-f.entry
    f['active_days'] = grouped.date.nunique()
    f['median_floor'] = grouped.floor.median()
    return f

def fit(train, test):
    # Calibration uses the last mature training origin, never this test's outcomes.
    # Thin market origins must not force calibration on fewer than 100 labels.
    # Pool the most recent mature origins until the predeclared minimum is met.
    last = train.origin.max()
    for boundary in sorted(train.origin.unique(), reverse=True):
        learn, cal = mature_train(train, str(boundary)), train[train.origin >= boundary]
        if len(cal) >= 100:
            break
    if learn.origin.nunique() < 3 or len(learn) < 500 or len(cal) < 100:
        raise ValueError('학습·보정에 필요한 과거 관측량이 부족합니다.')
    baseline = LGBMRegressor(objective='regression_l1', **PARAMS).fit(train[COLS], train.target)
    calibration_model = LGBMRegressor(objective='regression_l1', **PARAMS).fit(learn[COLS], learn.target)
    offsets = np.quantile(cal.target-calibration_model.predict(cal[COLS]), [.1,.9])
    median = baseline.predict(test[COLS])
    base = np.column_stack([median+min(0,offsets[0]), median, median+max(0,offsets[1])])
    features = COLS+EXTRA
    fitted, parts = [], []
    for q in QUANTILES:
        earlier = model(q).fit(learn[features], learn.target)
        shift = float(np.quantile(cal.target-earlier.predict(cal[features]), q))
        final = model(q).fit(train[features], train.target)
        fitted.append(final); parts.append(final.predict(test[features])+(shift if q != .5 else 0))
    direct = np.column_stack(parts)
    retained = direct.copy(); retained[:,1] = median
    for p in (direct,retained):
        p[:,0] = np.minimum(p[:,0],p[:,1]); p[:,2] = np.maximum(p[:,2],p[:,1])
    return {'v2_calibrated':base,'quantile_v3':direct,'retained_v2_quantiles':retained}, {'median':baseline,'quantiles':fitted,'features':features}, {'latest_training_label':train.label_available.max(),'calibration_origin':last,'training_rows':len(train)}

def diagnostics(test, p, name):
    known = test.target.notna().to_numpy(); y=test.loc[known,'target'].to_numpy(); pred=p[known]
    coverage=(y>=pred[:,0]) & (y<=pred[:,2])
    balanced=pd.DataFrame({'complex':test.loc[known,'complex'].to_numpy(),'covered':coverage}).groupby('complex').covered.mean().mean()*100
    result=measure(test,p[:,1],name)
    result.update({'labeled_rows':len(y),'pinball':float(np.mean([mean_pinball_loss(y,pred[:,i],alpha=q) for i,q in enumerate(QUANTILES)])),
        'median_mae_log':float(np.abs(y-pred[:,1]).mean()),'coverage_pct':float(coverage.mean()*100),
        'complex_coverage_pct':float(balanced),'mean_width_log':float((pred[:,2]-pred[:,0]).mean()),
        'p10_cdf_pct':float((y<=pred[:,0]).mean()*100),'p90_cdf_pct':float((y<=pred[:,2]).mean()*100)})
    return finite(result)

def run(args):
    out=Path(args.output);out.mkdir(parents=True,exist_ok=True)
    protocol={'asof':args.asof,'horizon_months':24,'scope':'서울·광명 / 3~20층 / 180일 3건 이상',
        'train_origins':'2007-01부터 반기. 비교 모델도 동일 이력으로 재학습. 보정 모형은 보정 판단일 당시 성숙한 라벨만 학습.','test_origins':['2021-07-01','2022-01-01','2022-07-01','2023-01-01','2023-07-01','2024-01-01','2024-07-01'],
        'regimes':['historical_policy','uniform61'],'candidates':['v2_calibrated','quantile_v3','retained_v2_quantiles'],
        'fixed_parameters':PARAMS,'extra_features':EXTRA,
        'gate':'각 지연 가정에서 공통 평가 5시점 이상·분위수 손실 개선·중앙값 오차 2% 이내·시점 동일 가중 포함률 75~85%·단지 균형 포함률 75~85%. 순위 교체는 평균 순위상관·상위10% 단지균형 상대변화도 악화하지 않을 때만.',
        'limitations':'과거 정정 자료와 가정한 공시 지연. 재사용 평가이며 독립 홀드아웃이 아니다. 결과 미관측을 사전 선정 분모에서 제거하지 않는다. 낮은 거래도 가격으로 제거·감량하지 않는다.'}
    (out/'protocol.json').write_text(json.dumps(protocol,ensure_ascii=False,indent=2),encoding='utf-8')
    oldpath=Path(args.history);meta=json.loads(oldpath.with_suffix('').with_suffix('.json').read_text(encoding='utf-8'))
    raw=gzip.decompress(oldpath.read_bytes())
    if not meta['complete'] or hashlib.sha256(raw).hexdigest()!=meta['sha256']:raise ValueError('과거 원본 검증 실패')
    older=out/'history.csv';older.write_bytes(raw)
    current=Path(args.current);cm=json.loads(current.with_suffix('.manifest.json').read_text(encoding='utf-8'))
    if not cm['complete'] or hashlib.sha256(current.read_bytes()).hexdigest()!=cm['sha256']:raise ValueError('현재 원본 검증 실패')
    old,oq=load_transactions(older);new,nq=load_transactions(current)
    if oq['raw_rows']!=meta['rows'] or nq['raw_rows']!=cm['rows'] or old.date.max()>=new.date.min():raise ValueError('원본 범위·행 검증 실패')
    earlypath=Path(args.older_history);em=json.loads(earlypath.with_suffix('').with_suffix('.json').read_text(encoding='utf-8'))
    earlyraw=gzip.decompress(earlypath.read_bytes())
    if not em['complete'] or hashlib.sha256(earlyraw).hexdigest()!=em['sha256']:raise ValueError('2006~2015 원본 검증 실패')
    earlycsv=out/'older-history.csv';earlycsv.write_bytes(earlyraw)
    early,eq=load_transactions(earlycsv)
    if eq['raw_rows']!=em['rows'] or early.date.max()>=old.date.min():raise ValueError('이전 이력 범위 검증 실패')
    old=pd.concat([early,old],ignore_index=True)
    new=new[new.gu.str.startswith('11')|new.gu.eq('41210')]
    d=pd.concat([old,new],ignore_index=True);d=d[d.floor.between(3,20)&(d.date<=pd.Timestamp(args.asof))].copy()
    results=[]; predictions={}; current_models={}
    for regime in protocol['regimes']:
        d['available_date']=d.date+pd.to_timedelta(np.where((d.date<LAW_CHANGE)|(regime=='uniform61'),61,31),unit='D')
        cache=out/f'features_{regime}.parquet'
        frames=[]
        for origin in pd.date_range('2007-01-01','2024-07-01',freq='2QS'):
            f=snapshot(d,origin,regime);frames.append(labels(d,f,origin,24,regime))
            print('features',regime,str(origin.date()),len(f),flush=True)
        data=pd.concat(frames,ignore_index=True);data.to_parquet(cache,index=False)
        for origin in protocol['test_origins']:
            test=data[data.origin==origin].copy()
            if test.label_available.iloc[0]>args.asof:continue
            train=mature_train(data,origin)
            preds,_,audit=fit(train,test)
            for name,p in preds.items():
                row={'origin':origin,'regime':regime,**audit,**diagnostics(test,p,name)};results.append(row)
            print('evaluated',regime,origin,len(train),len(test),flush=True)
        now=pd.Timestamp('2026-10-01');test=snapshot(d,now,regime).reset_index();train=mature_train(data,str(now.date()))
        preds,models,audit=fit(train,test)
        current_models[regime]={**models,'audit':audit};predictions[regime]=test.assign(**{name+'_'+str(int(q*100)):p[:,i] for name,p in preds.items() for i,q in enumerate(QUANTILES)})
        predictions[regime].to_parquet(out/f'current_{regime}.parquet',index=False)
    table=pd.DataFrame(results);summary=table.groupby(['regime','method'])[['pinball','median_mae_log','coverage_pct','complex_coverage_pct','mean_width_log','spearman','complex_median_excess_pct']].mean().reset_index().to_dict('records')
    gates={}
    for candidate in ['quantile_v3','retained_v2_quantiles']:
        conditions=[]
        for regime in protocol['regimes']:
            base=next(r for r in summary if r['regime']==regime and r['method']=='v2_calibrated')
            c=next(r for r in summary if r['regime']==regime and r['method']==candidate)
            conditions.append({'regime':regime,'enough_origins':int(((table.regime==regime)&(table.method==candidate)).sum())>=5,
                'pinball_improved':c['pinball']<base['pinball'],'median_error':c['median_mae_log']<=base['median_mae_log']*1.02,
                'coverage':75<=c['coverage_pct']<=85,'complex_coverage':75<=c['complex_coverage_pct']<=85,
                'rank_correlation':c['spearman']>=base['spearman']-1e-12,'selected_outcome':c['complex_median_excess_pct']>=base['complex_median_excess_pct']-1e-12})
        gates[candidate]={'checks':conditions,'passed':all(all(v for k,v in r.items() if k!='regime') for r in conditions)}
    chosen=next((k for k,v in gates.items() if v['passed']),'retain_v2')
    result={'created_at':datetime.now(timezone.utc).isoformat(),'protocol':protocol,'sources':{'2006_2015':eq,'2016_2020':oq,'current':nq},'results':results,'summary':finite(summary),'gates':gates,'decision':chosen}
    (out/'results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    joblib.dump(current_models,out/'candidates.joblib',compress=3)
    print(json.dumps({'decision':chosen,'summary':summary,'gates':gates},ensure_ascii=False,allow_nan=False),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--history',required=True);p.add_argument('--older-history',required=True);p.add_argument('--current',required=True);p.add_argument('--asof',default='2026-10-08');p.add_argument('--output',default='.work/potential-quantiles-20261008');run(p.parse_args())
