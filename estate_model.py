"""A frozen monthly, prior-year reference-price model with honest temporal validation."""
from __future__ import annotations
import hashlib,json,math
from datetime import date
from pathlib import Path
import joblib
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor

VERSION='estate-reference-v3'
NUMERIC=['year','area','age','prior_price','prior_peer','momentum','prior_count','peer_count']
CATEGORICAL=['sido','gu','dong','area_band']

def number(value):
    try:
        value=float(value)
        return value if math.isfinite(value) else None
    except (ValueError,TypeError):return None

def price(b,metric='price_per_pyeong'):
    m=(b or {}).get('metrics',{}).get(metric,{})
    return number(m.get('median')) or number(m.get('avg'))

def dataset(summary):
    history={};peers={}
    for r in summary['regions']:
        loc=(r.get('sido_name',''),r.get('gu_name',''),r.get('dong_name',''))
        for y,bucket in r['years'].items():
            for b in bucket['addresses']:
                key=(loc,b['key'],int(y))
                if key not in history or b['count']>history[key]['count']:history[key]=b
    for (loc,key,y),b in history.items():
        value=price(b)
        if value and value>0:peers.setdefault((loc,y),[]).append((value,b['count']))
    stats={key:(float(np.median([v for v,n in values])),sum(n for v,n in values)) for key,values in peers.items()}
    rows=[];payload=[]
    for r in summary['regions']:
        loc=(r.get('sido_name',''),r.get('gu_name',''),r.get('dong_name',''))
        for y,bucket in r['years'].items():
            year=int(y)
            for b in bucket['addresses']:
                pp,area=price(b),price(b,'area_pyeong')
                if not pp or pp<=0 or not area or area<=0:continue
                prior=history.get((loc,b['key'],year-1),{})
                p1=price(prior);p2=price(history.get((loc,b['key'],year-2),{}))
                peer,peer_n=stats.get((loc,year-1),(None,None));built=number(b.get('built_year'))
                rows.append({'year':year,'area':area,'age':year-built if built and built<=year else np.nan,
                    'prior_price':math.log(p1) if p1 else np.nan,'prior_peer':math.log(peer) if peer else np.nan,
                    'momentum':math.log(p1/p2) if p1 and p2 else np.nan,
                    'prior_count':prior.get('count',np.nan),'peer_count':peer_n,
                    'sido':loc[0],'gu':'|'.join(loc[:2]),'dong':'|'.join(loc),'area_band':str(int(area//5)),
                    'target':math.log(pp),'count':b['count'],
                    'group':'|'.join(loc)+':'+(b.get('complex_key') or b['key'].split(' | ')[0])})
                payload.append({'year':y,'region_code':r['code'],'sido_name':loc[0],'gu_code':r['gu_code'],
                    'gu_name':loc[1],'dong_name':loc[2],'building_key':b['key'],'building_name':b.get('building_name'),
                    'area_type':b.get('area_type'),'price_billion':price(b,'price_billion'),'price_per_pyeong':pp,
                    'area_pyeong':area,'trade_count':b['count'],'prior_price_per_pyeong':p1,
                    **{k:b.get(k) for k in ['households','built_year','elementary_500m','subway_lines','subway_station','subway_distance_m','latitude','longitude']}})
    return pd.DataFrame(rows),payload

def matrix(frame,categories):
    x=frame[NUMERIC+CATEGORICAL].copy()
    for name in NUMERIC:x[name]=pd.to_numeric(x[name],errors='coerce')
    for name in CATEGORICAL:x[name]=pd.Categorical(x[name],categories=categories[name])
    return x

def fit(frame):
    if len(frame)<100:raise ValueError('Need at least 100 earlier-year observations; no overlapping fallback')
    categories={name:sorted(frame[name].dropna().unique()) for name in CATEGORICAL}
    model=LGBMRegressor(objective='regression_l1',n_estimators=180,learning_rate=.045,num_leaves=15,
        min_child_samples=45,reg_lambda=8,colsample_bytree=.9,random_state=202609,n_jobs=2,verbosity=-1)
    model.fit(matrix(frame,categories),frame.target)
    return {'model':model,'categories':categories,'fallback':float(frame.target.median())}

def baseline(frame,fallback):return frame.prior_price.fillna(frame.prior_peer).fillna(fallback).to_numpy(float)
def predict(fitted,frame,weight):
    return weight*fitted['model'].predict(matrix(frame,fitted['categories']))+(1-weight)*baseline(frame,fitted['fallback'])

def metrics(actual,predicted):
    a,p=np.exp(actual),np.exp(predicted);error=np.abs(a-p);ape=error/a
    return {'rows':len(a),'mae_price_per_pyeong':round(float(error.mean()),2),
        'median_absolute_pct_error':round(float(np.median(ape)*100),2),'within_20pct':round(float((ape<=.2).mean()),4)}

def quantile(errors):
    if not len(errors):raise ValueError('Missing calibration observations')
    q=min(1,math.ceil((len(errors)+1)*.8)/len(errors))
    return float(np.quantile(errors,q,method='higher'))

def tune(frame,year):
    training=frame[frame.year<year];validation=frame[frame.year==year]
    if min(len(training),len(validation))<100:raise ValueError('Insufficient earlier-year training or calibration')
    mask=validation.group.map(lambda x:int(hashlib.sha256(x.encode()).hexdigest()[:8],16)%2==0)
    tuning,calibration=validation[mask],validation[~mask]
    if min(len(tuning),len(calibration))<20:raise ValueError('Insufficient independent calibration groups')
    fitted=fit(training)
    choices=[(metrics(tuning.target.to_numpy(),predict(fitted,tuning,w))['mae_price_per_pyeong'],w) for w in [0,.25,.5,.75,1]]
    weight=min(choices)[1];errors=np.abs(calibration.target.to_numpy()-predict(fitted,calibration,weight))
    interval={'global':quantile(errors),'by_sido':{s:quantile(errors[calibration.sido.to_numpy()==s])
        for s in calibration.sido.unique() if (calibration.sido==s).sum()>=100},
        'tuning_rows':len(tuning),'calibration_rows':len(calibration)}
    return weight,interval

def evaluate(frame,closed_year):
    folds=[]
    for year in sorted(y for y in frame.year.unique() if y<=closed_year)[2:]:
        training=frame[frame.year<year];test=frame[frame.year==year]
        if min(len(training),len(test))<100:continue
        weight,interval=tune(training,year-1);fitted=fit(training)
        predicted=predict(fitted,test,weight);actual=test.target.to_numpy()
        width=np.array([interval['by_sido'].get(s,interval['global']) for s in test.sido])*np.sqrt(1+2/np.maximum(test['count'].to_numpy(),1))
        folds.append({'test_year':int(year),'trained_through':int(year-1),'weight_selected_on':int(year-1),'ml_weight':weight,
            'model':metrics(actual,predicted),'baseline':metrics(actual,baseline(test,fitted['fallback'])),
            'interval_target_coverage':.8,'interval_actual_coverage':round(float((np.abs(actual-predicted)<=width).mean()),4),
            'tuning_rows':interval['tuning_rows'],'calibration_rows':interval['calibration_rows'],
            'calibration_split':'disjoint apartment complexes in the preceding year',
            'regions':{s:metrics(actual[test.sido.to_numpy()==s],predicted[test.sido.to_numpy()==s]) for s in sorted(test.sido.unique())}})
    return folds

def run(summary_path,output_path,model_dir,month=None,mode='auto'):
    summary=json.loads(Path(summary_path).read_text());frame,payload=dataset(summary)
    month=month or date.today().strftime('%Y-%m')
    if date.fromisoformat(month+'-01').strftime('%Y-%m')!=month:raise ValueError('Invalid model month')
    artifact_path=Path(model_dir)/VERSION/(month+'.joblib')
    if artifact_path.exists():
        if mode=='train':raise FileExistsError('Monthly models cannot be overwritten')
        artifact=joblib.load(artifact_path)
        if artifact.get('version')!=VERSION or artifact['model_month']!=month:raise ValueError('Frozen model identity mismatch')
    else:
        if mode=='infer':raise FileNotFoundError('Train the current model month first')
        cutoff=min(int(month[:4])-1,int(frame.year.max())-1)
        training=frame[frame.year<=cutoff];folds=evaluate(frame,cutoff)
        if not folds:raise ValueError('At least three completed years are required for independent validation')
        weight,interval=tune(training,cutoff)
        artifact={**fit(training),'version':VERSION,'model_month':month,'created_at':date.today().isoformat(),
            'trained_through':f'{cutoff}-12-31','training_rows':len(training),'data_snapshot':summary['generated_at'],
            'training_sha256':hashlib.sha256(training.to_json().encode()).hexdigest(),'ml_weight':weight,'interval':interval,'validation':folds}
        artifact_path.parent.mkdir(parents=True,exist_ok=True);joblib.dump(artifact,artifact_path,compress=3)
    latest=int(frame.year.max());mask=frame.year==latest;current=frame[mask]
    if int(artifact['trained_through'][:4])>=latest:raise ValueError('Inference must follow training')
    prices=np.exp(predict(artifact,current,artifact['ml_weight']));results=[]
    current_payload=[p for p,keep in zip(payload,mask) if keep]
    for p,fair in zip(current_payload,prices):
        n=p['trade_count'];confidence=n/(n+5);error=artifact['interval']['by_sido'].get(p['sido_name'],artifact['interval']['global'])
        width=error*math.sqrt(1+2/max(n,1));gap=math.log(fair/p['price_per_pyeong'])
        score=50+40*math.tanh(gap/max(error,.05))*confidence;flags=[]
        if n<3:flags.append('거래 표본 3건 미만')
        if p['prior_price_per_pyeong'] is None:flags.append('동일 평형 전년도 비교 없음')
        if abs(gap)>2*error:flags.append('가격 차이 큼: 층·상태·권리관계 확인')
        results.append({**p,'fair_price_per_pyeong':round(float(fair),1),'reference_low':round(fair*math.exp(-width),1),
            'reference_high':round(fair*math.exp(width),1),'house_match_score':round(score,1),'sample_confidence':round(confidence,3),
            'undervalue_pct':round((fair/p['price_per_pyeong']-1)*100,1),'quality_flags':flags,'expected_growth_pct':None})
    results.sort(key=lambda p:(-p['house_match_score'],p['region_code'],p['building_key']))
    result={'schema_version':2,'model_version':VERSION,'generated_at':date.today().isoformat(),
        'data_as_of':summary.get('data_through',summary['generated_at']),'target_year':str(latest),
        **{k:artifact[k] for k in ['model_month','created_at','trained_through','training_rows','ml_weight','validation']},
        'score_note':'검토점수는 기준가격과 관측가격의 차이를 거래수·과거 오차로 조정한 순서이며 수익률이나 상승 확률이 아닙니다.',
        'interval_note':'과거 검증 오차의 80% 목표 구간이며 실제 포함률은 연도별로 확인합니다.',
        'forecast_status':'보류: 미완결 연도를 다음 해 연간 수익률 정답으로 사용하지 않습니다.',
        'features_used':NUMERIC+CATEGORICAL,'data_limitations':['과거 신고·해제 전 원본이 없어 당시 정보만의 완전한 재현은 아닙니다.',
            '연간 집계에는 거래된 층·세대 구성 차이가 남습니다. 매도 호가나 개별 세대 감정가가 아닙니다.',
            '시점이 없는 역·학교·세대수 자료는 과거 학습 입력에서 제외합니다.'],
        'recommendations':results}
    output_path=Path(output_path);output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False))
    return result
