"""Lossless static data: a common catalog, compact history, and lazy period files."""
import gzip,hashlib,json,os
from pathlib import Path
METRICS=['price_billion','area_pyeong','price_per_pyeong'];STATS=['avg','median','min','max']
def compact(metrics):return [metrics.get(k,{}).get(s) for k in METRICS for s in STATS]
def asset(root,name,value):
    body=gzip.compress(json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode(),mtime=0)
    h=hashlib.sha256(body).hexdigest();filename=f'{name}.{h[:16]}.json.gz'
    (root/filename).write_bytes(body)
    return {'url':'data/bundle/'+filename,'bytes':len(body),'sha256':h}
def build_bundle(summary,model,root,map_data):
    root.mkdir(parents=True,exist_ok=True);catalog=[];identities={};regions=[];periods={};history={};recommendation_catalog={}
    for r in summary['regions']:
        regions.append({k:v for k,v in r.items() if k not in {'all','years'}})
        for year,bucket in [('all',r['all']),*r['years'].items()]:
            if sum(a['count'] for a in bucket['addresses'])>bucket['count']:raise ValueError('Inconsistent source trade counts')
            rows=[]
            for a in bucket['addresses']:
                info={k:v for k,v in a.items() if k not in {'count','metrics','observed_floor','floor_sample_count'}}
                identity=json.dumps([r['code'],info],sort_keys=True,ensure_ascii=False)
                if identity not in identities:identities[identity]=len(catalog);catalog.append(info)
                idx=identities[identity];rows.append([idx,a['count'],compact(a['metrics']),a.get('observed_floor'),a.get('floor_sample_count',0)])
                if year==model.get('target_year'):recommendation_catalog[(r['code'],a['key'])]=idx
                if year!='all':
                    m=a['metrics'];history.setdefault(year,{}).setdefault(r['code'],[]).append([idx,a['count'],
                        m['price_billion'].get('avg'),m['price_billion'].get('median'),m['price_per_pyeong'].get('avg'),
                        m['price_per_pyeong'].get('median'),m['area_pyeong'].get('avg')])
            periods.setdefault(year,[]).append([r['code'],bucket['count'],compact(bucket['metrics']),rows,[]])
    coverage={}
    for year,rs in periods.items():
        source=sum(r[1] for r in rs);represented=sum(row[1] for r in rs for row in r[3])
        coverage[year]={'source_trades':source,'represented_trades':represented,'available_types':sum(len(r[3]) for r in rs),
            'unrepresented_trades':source-represented,'complete':source==represented}
    recommendation_fields=['price_billion','price_per_pyeong','area_pyeong','trade_count','prior_price_per_pyeong',
        'fair_price_per_pyeong','reference_low','reference_high','house_match_score','sample_confidence','undervalue_pct','quality_flags','expected_growth_pct',
        'neutral_price_billion','score_error_scale']
    packed_model={'encoding':'catalog-v1','metadata':{k:v for k,v in model.items() if k!='recommendations'},'fields':recommendation_fields,
        'rows':[[recommendation_catalog[(r['region_code'],r['building_key'])],r['region_code'],*[r.get(k) for k in recommendation_fields]] for r in model['recommendations']]}
    manifest={'schema_version':1,'generated_at':summary.get('data_through',summary['generated_at']),
        'built_at':summary['generated_at'],'code_commit':os.getenv('GITHUB_SHA'),'source':summary['source'],
        'years':summary['years'],'default_year':max(summary['years'],key=int),'coverage':coverage,
        'filters':summary.get('filters',{}),'data_quality':summary.get('data_quality',{}),'model':{k:v for k,v in model.items() if k!='recommendations'},
        'catalog':asset(root,'catalog',{'regions':regions,'addresses':catalog}),'history':asset(root,'history',history),
        'periods':{year:asset(root,'period-'+year,rows) for year,rows in periods.items()},
        'recommendations':asset(root,'recommendations',packed_model),'map':asset(root,'map',map_data)}
    (root.parent/'dashboard_manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8',newline='\n')
    return manifest
