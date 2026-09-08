"""Predeclared lag and repeated-row checks for the external-data ablations."""
from pathlib import Path
import json
import pandas as pd
from analyze_estate_market_extension import KB,LEASE,BASE,index_features,lease_features,model,metric,paired_gain


def main():
    root=Path('.work/market-research')
    f=pd.read_parquet(root/'ablation/features.parquet')
    previous=pd.read_parquet(root/'ablation/predictions.parquet')
    leases=pd.read_parquet(root/'seoul_jeonse.parquet')
    indices=pd.read_parquet(root/'kb_indices.parquet')
    bare=f.drop(columns=KB+LEASE+['lease_log90','lease_log180'],errors='ignore')
    report={}
    for name,cols in [('kb_lag3',BASE+KB),('lease_repeated_rows',BASE+LEASE),('lease_annual_file',BASE+LEASE)]:
        if name=='kb_lag3':data=index_features(bare,indices,3)
        else:data=lease_features(bare,leases,availability='annual_file' if name=='lease_annual_file' else 'lag31',unique=name!='lease_repeated_rows')
        parts=[]
        for year in [2025,2026]:
            train=data[data.year<year]
            test=data[(data.year==year)&(data.month<='2026-07')].copy()
            m=model(cols,train);test['candidate']=test.anchor+m.predict(test[cols])
            control=previous[previous.year.eq(year)]
            if not test[['key','month','floor','actual']].reset_index(drop=True).equals(control[['key','month','floor','actual']].reset_index(drop=True)):
                raise ValueError('Sensitivity and baseline cohort/order mismatch')
            test['base']=control.base.to_numpy()
            report[f'{name}_{year}']={scope:{method:metric(g,g[method].to_numpy()) for method in ['base','candidate']}
                for scope,g in [('capital',test),('seoul',test[test.gu.str.startswith('11')])]}
            parts.append(test)
        report[name+'_gain']=paired_gain(pd.concat(parts),'base','candidate')
        print('sensitivity',name,report[name+'_gain'],flush=True)
    (root/'sensitivity.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False))


if __name__=='__main__':main()
