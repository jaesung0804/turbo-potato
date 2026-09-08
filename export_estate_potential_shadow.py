"""Freeze an unpromoted forecast cohort before its 18–24 month outcomes exist."""
from pathlib import Path
from datetime import datetime,timezone
import argparse
import gzip
import hashlib
import json
import numpy as np
import pandas as pd
from lightgbm import LGBMRegressor
from analyze_estate_long_horizon import inputs,snapshots,OUT
from analyze_estate_potential import CASES,COLS


def main(origin):
    origin=pd.Timestamp(origin)
    if origin.day!=1:raise ValueError('Forecast origin must be month start')
    cutoff=origin-pd.Timedelta(days=31)
    d=inputs();d=d[d.date<=cutoff].copy()
    leases=pd.read_parquet('.work/market-research/seoul_jeonse.parquet')
    indices=pd.read_parquet('.work/market-research/kb_indices.parquet')
    current=snapshots(d,leases,indices,180,origins=[origin],inference_only=True)
    history=pd.read_parquet(OUT/'features_180.parquet')
    train=history[history.horizon.eq(24)&(history.label_available<=str(origin.date()))
                  &history.target.notna()&~history.complex.isin(CASES)]
    m=LGBMRegressor(objective='regression_l1',n_estimators=120,num_leaves=7,
        min_child_samples=100,reg_lambda=20,learning_rate=.04,n_jobs=4,
        verbosity=-1,random_state=20260908)
    m.fit(train[COLS],train.target)
    current['predicted_relative_change_pct']=np.expm1(m.predict(current[COLS]))*100
    current=current.sort_values(['predicted_relative_change_pct','key'],ascending=[False,True])
    current['research_rank']=np.arange(1,len(current)+1)
    current['entry_reference_oku']=np.exp(current.entry)*current.area/10000
    records=json.loads(current[['key','gu','area','research_rank','entry_n','entry_reference_oku',
        'predicted_relative_change_pct']].to_json(orient='records',force_ascii=False))
    result={'schema_version':1,'status':'research_only_not_deployed_as_ranking',
        'created_at':datetime.now(timezone.utc).isoformat(),'origin':str(origin.date()),
        'feature_cutoff':str(cutoff.date()),'training_rows':len(train),
        'latest_training_label_available':str(train.label_available.max()),
        'outcome_start':str((origin+pd.DateOffset(months=18)).date()),
        'outcome_end':str((origin+pd.DateOffset(months=24)).date()),
        'label_maturity':str((origin+pd.DateOffset(months=24)+pd.Timedelta(days=31)).date()),
        'scope':'서울 25개 자치구·광명; 3–20층; 진입 전 180일에 동일 전용면적 매매 3건 이상',
        'features':COLS,'named_cases_excluded_from_training':CASES,
        'source_sha256':json.loads((OUT/'sources.json').read_text()),
        'note':'Exploratory forecast frozen before outcomes. Relative price change is not an executable net return or an appreciation probability.',
        'records':records}
    path=Path(f'metadata/potential_shadow_{origin:%Y-%m}.json.gz')
    if path.exists():raise FileExistsError('Frozen forecasts cannot be overwritten')
    body=json.dumps(result,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode()
    path.write_bytes(gzip.compress(body,mtime=0))
    print(json.dumps({'path':str(path),'cohort':len(records),'training_rows':len(train),'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}))


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--origin',required=True)
    main(p.parse_args().origin)
