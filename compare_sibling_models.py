"""Compare the same-complex fallback against v4 on full chronological cohorts."""
import argparse
import hashlib
import json
from pathlib import Path
import numpy as np
import estate_model as model
from estate_io import write_json


def inversion_counts(frame, log_prices):
    values=frame[['group','area']].copy()
    values['total']=np.exp(log_prices)*frame.area.to_numpy()/10000
    pairs=inverted=0
    for _,group in values.groupby('group'):
        records=group.groupby('area').total.median().sort_index().items()
        previous=[]
        for area,total in records:
            for old_area,old_total in previous:
                if 1.1<=area/old_area<=1.5:
                    pairs+=1;inverted+=total<old_total*.95
            previous.append((area,total))
    return {'comparable_pairs':pairs,'larger_total_at_least_5pct_lower':int(inverted)}


def compare(summary_path,output):
    body=summary_path.read_bytes();summary=json.loads(body)
    before,payload=model.dataset(summary,enhanced=True)
    after=model.sibling_history(before,payload)
    results={};current_prices={}
    for name,frame in [('v4',before),('v5',after)]:
        folds=model.evaluate(frame,2025)
        for fold in folds:
            year=fold['test_year'];test=frame[frame.year==year];training=frame[frame.year<year]
            weight,_=model.tune(training,year-1)
            predicted=model.predict(model.fit(training),test,weight)
            eligible=after.loc[test.index].prior_price.isna() & after.loc[test.index].last_price.isna() & after.loc[test.index].sibling_price.notna()
            fold['sibling_fallback_segment']=model.metrics(test.loc[eligible].target.to_numpy(),predicted[eligible.to_numpy()]) if eligible.any() else None
            fold['area_inversions']=inversion_counts(test,predicted)
        training=frame[frame.year<2026];current=frame[frame.year==2026]
        weight,_=model.tune(training,2025)
        current_prices[name]=model.predict(model.fit(training),current,weight)
        results[name]={'folds':folds,'current_inversions':inversion_counts(current,current_prices[name]),
            'pooled_mae':sum(f['model']['rows']*f['model']['mae_price_per_pyeong'] for f in folds)/sum(f['model']['rows'] for f in folds)}
        print(name,json.dumps(results[name],ensure_ascii=False),flush=True)
    current_payload=[p for p in payload if int(p['year'])==2026]
    example=[{'area':p['area_type'],'observed_total':p['price_billion'],
        **{v:round(float(np.exp(prices[i])*p['area_pyeong']/10000),4) for v,prices in current_prices.items()}}
        for i,p in enumerate(current_payload) if p['building_name'].replace(' ','')=='강동리엔파크14단지']
    report={'source_sha256':hashlib.sha256(body).hexdigest(),'data_through':summary['data_through'],
        'baseline':model.CANDIDATE_VERSION,'candidate':model.SIBLING_VERSION,'results':results,'reported_complex':example,
        'rule':'Fixed 17-input architecture. Same-complex, same construction year, earlier qualified year within 3 years; minimum 3 trades and area ratio within 1/1.5..1.5. Exact-type history stays first. No current-year prices, no post-hoc monotonic clamp.',
        'limitations':['Retrospective repeated holdouts, not prospective return validation.','Different sizes can have genuinely different total prices; inversion counts are diagnostics, not a hard price-order rule.']}
    write_json(output,report,indent=2)
    print('Reported complex',json.dumps(example,ensure_ascii=False),flush=True)
    return report


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--summary',type=Path,default=Path('.work/build/summary.json'))
    p.add_argument('--output',type=Path,default=Path('.work/sibling_comparison.json'))
    a=p.parse_args();compare(a.summary,a.output)
