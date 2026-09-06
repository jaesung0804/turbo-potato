"""Compare fixed v3/v4 designs on identical chronological holdouts, without deployment."""
import argparse
import hashlib
import json
import platform
from pathlib import Path

import lightgbm
import numpy
import pandas
import sklearn

from estate_calendar import today
from estate_io import write_json
from estate_model import dataset, evaluate, EXTRA_NUMERIC, VERSION, CANDIDATE_VERSION


def compare(summary_path, output_path):
    body=Path(summary_path).read_bytes()
    summary=json.loads(body)
    frame,_=dataset(summary,enhanced=True)
    closed_year=min(today().year-1,int(frame.year.max())-1)
    baseline=evaluate(frame.drop(columns=EXTRA_NUMERIC),closed_year)
    candidate=evaluate(frame,closed_year)
    if not baseline or [f['test_year'] for f in baseline]!=[f['test_year'] for f in candidate]:
        raise ValueError('Matching independent holdouts are required')
    rows=[]
    for before,after in zip(baseline,candidate):
        if before['model']['rows']!=after['model']['rows']:
            raise ValueError('Candidate dropped evaluation rows')
        rows.append({'test_year':before['test_year'],'rows':before['model']['rows'],
            'mae_change_pct':round((after['model']['mae_price_per_pyeong']/before['model']['mae_price_per_pyeong']-1)*100,2),
            'v3':before,'v4':after})
    total=sum(r['rows'] for r in rows)
    pooled={name:round(sum(r['rows']*r[name]['model']['mae_price_per_pyeong'] for r in rows)/total,2)
        for name in ['v3','v4']}
    report={'schema_version':1,'generated_at':today().isoformat(),'source_sha256':hashlib.sha256(body).hexdigest(),
        'runtime':{'python':platform.python_version(),'platform':platform.system(),'lightgbm':lightgbm.__version__,
            'numpy':numpy.__version__,'pandas':pandas.__version__,'sklearn':sklearn.__version__},
        'data_through':summary.get('data_through'),'baseline_version':VERSION,'candidate_version':CANDIDATE_VERSION,
        'status':'research_only','selection_note':'No automatic replacement. One fixed candidate; weights use only the preceding year. Repeated comparison of these years is retrospective research, not new prospective evidence.',
        'pooled_mae':pooled,'folds':rows,
        'limitations':['Annual observed-price comparison, not a return forecast.',
            'Historical records include later corrections; archived as-of observations are unavailable.',
            'Past-year intervals do not guarantee future coverage.']}
    write_json(output_path,report,indent=2)
    print(json.dumps({'pooled_mae':pooled,'folds':[{k:r[k] for k in ['test_year','rows','mae_change_pct']} for r in rows]},ensure_ascii=False),flush=True)
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--summary',type=Path,default=Path('.work/build/summary.json'))
    parser.add_argument('--output',type=Path,default=Path('.work/model_comparison.json'))
    args=parser.parse_args()
    compare(args.summary,args.output)
