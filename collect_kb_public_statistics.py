"""Download the public KB statistics export, preserving its observed vintage.

Only compact factual market changes enter the public dashboard. Individual
apartment KB appraisals and the original copyrighted workbook are not republished.
The regional indicators are explanatory context, not added price-model weights.
"""
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import argparse
import hashlib
import json
import pandas as pd
from estate_kb import read_kb
from get_molit_apt_trade_data import CAPITAL_AREA_LAWD_CODES

CATALOG='https://api.kbland.kr/land-extra/statistics/reference'
DOWNLOAD='https://api.kbland.kr/land-extra/statistics/getfiledown'
SOURCE='https://kbland.kr/pages/kbStatistics.html'


def fetch(url):
    with urlopen(Request(url,headers={'WebService':'1','Referer':'https://kbland.kr/'}),timeout=45) as response:
        return response.read()


def summarize(indices, record, workbook_hash, retrieved_at):
    period=str(indices.period.max())
    if pd.Period(period,'M')>pd.Timestamp(retrieved_at).tz_localize(None).to_period('M'):
        raise ValueError('Index file contains future reference months')
    names={r.code:r.sgg for r in CAPITAL_AREA_LAWD_CODES}
    names.update({'11':'서울특별시','41':'경기도','28':'인천광역시'})
    regions={}
    for gu,g in indices.groupby('gu'):
        p=g.pivot(index='period',columns='kind',values='value')
        if period not in p.index:continue
        out={'name':names[gu]}
        for kind in ['sale','rent']:
            for n in [1,3,6,12]:
                prior=str(pd.Period(period,'M')-n)
                if prior not in p.index:raise ValueError('Missing comparison month')
                out[f'{kind}_{n}m_pct']=round(float((p.loc[period,kind]/p.loc[prior,kind]-1)*100),3)
        regions[gu]=out
    registered=datetime.fromisoformat(record['등록일시']).replace(tzinfo=ZoneInfo('Asia/Seoul')).astimezone(timezone.utc).isoformat()
    return {'schema_version':1,'source':'KB부동산 월간 아파트 가격지수','source_url':SOURCE,
        'reference_month':period,'source_registered_at':registered,'retrieved_at':retrieved_at,
        'workbook_sha256':workbook_hash,'source_filename':record['원본파일명'],
        'source_file':record['파일경로']+'/'+record['파일명'],'regions':regions,
        'price_weight_applied':False,'availability_note':'Current downloaded vintage. Registration time is source metadata, not a reconstructed historical first-publication archive.'}


def collect(output, cache, previous=None):
    now=datetime.now(timezone.utc);cache.mkdir(parents=True,exist_ok=True)
    # Reuse a newer durable context snapshot from the existing data state.
    if previous and previous.exists():
        old=json.loads(previous.read_text())
        local=json.loads(output.read_text()) if output.exists() else None
        if old.get('schema_version')==1 and (not local or old['source_registered_at']>local['source_registered_at']):
            output.write_text(json.dumps(old,ensure_ascii=False,indent=2,allow_nan=False))
    params={'주월간구분':1,'기준년월시작일':f'{now.year}-01-01','기준년월종료일':f'{now.year}-12-31'}
    body=fetch(CATALOG+'?'+urlencode(params));data=json.loads(body)
    if data['dataHeader']['resultCode']!='10000':raise ValueError('KB catalog returned an application error')
    choices=[x for x in data['dataBody']['data']['시계열'] if '주택' in x['원본파일명'] and x['파일명'].endswith('.xlsx')]
    if len(choices)!=1:raise ValueError('Expected one public monthly housing workbook')
    record=choices[0]
    registered=datetime.fromisoformat(record['등록일시']).replace(tzinfo=ZoneInfo('Asia/Seoul')).astimezone(timezone.utc)
    if registered>now:raise ValueError('Future source registration')
    if output.exists():
        old=json.loads(output.read_text())
        if old.get('source_file')==record['파일경로']+'/'+record['파일명'] and old.get('source_registered_at')==registered.isoformat():
            print('KB context unchanged:',old['reference_month']);return old
    raw=fetch(DOWNLOAD+'?'+urlencode({'urlpath':record['파일경로']+'/'+record['파일명']}))
    path=cache/'kb-monthly.xlsx';path.write_bytes(raw)
    result=summarize(read_kb(path),record,hashlib.sha256(raw).hexdigest(),now.isoformat())
    temporary=output.with_suffix('.tmp');output.parent.mkdir(parents=True,exist_ok=True)
    temporary.write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False));temporary.replace(output)
    (cache/'kb-catalog.json').write_bytes(body)
    print('KB context updated:',result['reference_month'],len(result['regions']),'geographies')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--output',type=Path,default=Path('metadata/market_context.json'))
    p.add_argument('--cache',type=Path,default=Path('.work/kb-public'))
    p.add_argument('--previous',type=Path)
    args=p.parse_args()
    try:collect(args.output,args.cache,args.previous)
    except Exception as error:
        # This optional explanatory panel cannot block sale-data deployment.
        if not args.output.exists():raise
        print('KB refresh unavailable; dated previous context retained:',type(error).__name__)
