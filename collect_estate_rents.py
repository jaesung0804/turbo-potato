"""Collect authorized MOLIT apartment rent pages for a bounded research cohort.

Preserves original fields and retrieval timestamps. Historical contracts fetched
now are NOT treated as records known historically. No model is promoted here.
"""
import argparse,gzip,json,os,hashlib
from pathlib import Path
import collect_estate_transactions as api
from get_molit_apt_trade_data import month_range

URL='https://apis.data.go.kr/1613000/RTMSDataSvcAptRent/getRTMSDataSvcAptRent'


def collect(codes,months,out,key):
    if not key:raise RuntimeError('MOLIT_API_KEY is required; no key is printed')
    api.URL=URL
    out.mkdir(parents=True,exist_ok=True);manifest=[]
    for code in codes:
        if code not in api.REGIONS:raise ValueError('Unknown legal district code')
        for month in months:
            path=out/f'{month}-{code}.json.gz'
            if path.exists():
                data=json.loads(gzip.decompress(path.read_bytes()))
                if data.get('complete') and data.get('source')==URL:
                    manifest.append({'code':code,'month':month,'rows':len(data['rows']),'fetched_at':data['fetched_at']});continue
            rows=[];seen=set();total=None
            for page in range(1,10001):
                root=api.request(key,code,month,page)
                current=api.validate(root)
                if total is None:total=current
                if current!=total:raise ValueError('Changing pagination total; incomplete rent data rejected')
                items=[{c.tag:(c.text or '').strip() for c in r} for r in root.findall('.//item')]
                signature=hashlib.sha256(json.dumps(items,sort_keys=True).encode()).hexdigest()
                if items and signature in seen:raise ValueError('Repeated rent API page')
                seen.add(signature);rows.extend(items)
                if len(rows)==total:break
                if not items or len(rows)>total:raise ValueError('Incomplete rent pagination')
            else:raise ValueError('Rent pagination limit reached')
            timestamp=api.utc_now()
            data={'source':URL,'complete':True,'code':code,'month':month,'fetched_at':timestamp,'rows':rows}
            temp=path.with_suffix('.tmp');temp.write_bytes(gzip.compress(json.dumps(data,ensure_ascii=False,separators=(',',':')).encode(),mtime=0));temp.replace(path)
            manifest.append({'code':code,'month':month,'rows':len(rows),'fetched_at':timestamp})
            print('rent',code,month,len(rows),flush=True)
    (out/'manifest.json').write_text(json.dumps({'source':URL,'complete':True,'scope':'requested districts and months only','partitions':manifest},ensure_ascii=False,indent=2))
    return manifest

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--codes',default='11740,41210,11620');p.add_argument('--start',default='202307');p.add_argument('--end',default='202412');p.add_argument('--output',type=Path,default=Path('.work/market-data/rents'))
    p.add_argument('--use-existing-config',action='store_true')
    a=p.parse_args();collect(a.codes.split(','),month_range(a.start,a.end),a.output,api.existing_key() if a.use_existing_config else os.getenv('MOLIT_API_KEY',''))
