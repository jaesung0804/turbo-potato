"""검증된 로컬 특징으로 기존 v2를 월별 재학습하고 고정 예측·모델을 보존한다."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import pandas as pd
from estate.models.potential.v1.experiment import COLS
from estate.models.potential.v2.horizons import evaluate

def run(folder, current, history, output, origin):
    folder,output=Path(folder),Path(output)
    if output.exists():
        raise FileExistsError('고정 결과는 덮어쓰지 않습니다. 새 경로를 사용하세요.')
    output.mkdir(parents=True);cache=output/'features';cache.mkdir()
    audit=json.loads((folder/'results.json').read_text(encoding='utf-8'))
    sources={'current':audit['sources']['current'],'older':audit['sources']['2016_2020']}
    manifest={**sources,'horizons':[24],'regimes':['historical_policy','uniform61']}
    frames=[]
    for regime in manifest['regimes']:
        f=pd.read_parquet(folder/f'features_{regime}.parquet')
        # Match the operational v2 design, not the full-history experimental fit.
        f=f[f.origin>='2017-01-01'].copy();f.to_parquet(cache/f'features_{regime}.parquet',index=False);frames.append(f)
    (cache/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False),encoding='utf-8')
    report=evaluate(pd.concat(frames,ignore_index=True),pd.Timestamp(audit['protocol']['asof']))
    report.update({'sources':sources,'features':COLS,'entry_window_days':180,'scope':'서울·광명 / 기존 v2 사양 유지','status':'retrospective_reused_evaluation'})
    (output/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    subprocess.run([sys.executable,'-m','estate.models.potential.v2.export','--origin',origin,'--current',str(current),'--history',str(history),'--cache',str(cache),'--output',str(output/'snapshot.json.gz'),'--artifacts-dir',str(output/'training')],check=True)
    subprocess.run([sys.executable,'-m','estate.models.potential.v2.publish','--snapshot',str(output/'snapshot.json.gz'),'--report',str(output/'validation.json'),'--output',str(output/'public.json.gz')],check=True)

if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--experiment',required=True);p.add_argument('--current',required=True);p.add_argument('--history',required=True);p.add_argument('--output',required=True);p.add_argument('--origin',required=True);a=p.parse_args();run(a.experiment,a.current,a.history,a.output,a.origin)
