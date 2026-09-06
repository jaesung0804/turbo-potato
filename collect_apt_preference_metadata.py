"""Checkpoint K-APT V5 inventory for future dated training; never log credentials.

Run with --codes pointing at the existing K-APT list. Successful responses are
retained individually. Missing/invalid area totals stay unknown, not estimated.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path
import ssl
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from estate_preference_features import read_csv, numeric, area_inventory

ENDPOINT='https://apis.data.go.kr/1613000/AptBasisInfoServiceV5/getAphusBassInfoV5'


def decode(body, expected_code):
    if body.lstrip().startswith(b'{'):
        response=json.loads(body).get('response',{})
        code=str(response.get('header',{}).get('resultCode',''))
        r=response.get('body',{}).get('item')
        if r is None:r=response.get('body',{}).get('items',{}).get('item')
        if isinstance(r,list):r=r[0] if len(r)==1 else None
    else:
        root=ET.fromstring(body);code=root.findtext('.//resultCode');item=root.find('.//item')
        r={e.tag:(e.text or '').strip() for e in item} if item is not None else None
    if code not in {'00','000','0000','0'}:raise ValueError('API response not successful')
    if not isinstance(r,dict):raise ValueError('Missing apartment metadata')
    if r.get('kaptCode')!=expected_code:raise ValueError('Apartment identity mismatch')
    counts=[numeric(r.get('kaptMparea'+suffix)) for suffix in ['60','85','135','136']]
    total=numeric(r.get('kaptdaCnt'))
    return {'kapt_code':expected_code,'building_name':r.get('kaptName'),'bjd_code':r.get('bjdCode'),
        'lot_address':r.get('kaptAddr'),'used_date':r.get('kaptUsedate'),'total_households':total,
        'area_bands_sqm':['<=60','60-85','85-135','>135'],'area_band_households':counts,
        'inventory_consistent':area_inventory(total,counts,84.91) is not None,
        'source_url':'https://www.data.go.kr/data/15058453/openapi.do',
        'observed_at':datetime.now(timezone.utc).isoformat(),'response_sha256':hashlib.sha256(body).hexdigest()}


def collect(codes_path, output, service_key):
    output.mkdir(parents=True,exist_ok=True)
    context=ssl.create_default_context();context.maximum_version=ssl.TLSVersion.TLSv1_2
    codes=sorted({r.get('kaptCode') or r.get('apt_cd') for r in read_csv(codes_path)}-{None,''})
    success=failed=consecutive=0
    for code in codes:
        if not code.isalnum():raise ValueError('Invalid apartment code')
        target=output/(code+'.json.gz')
        if target.exists():continue
        query=urllib.parse.urlencode({'serviceKey':urllib.parse.unquote(service_key),'kaptCode':code,'_type':'json'})
        try:
            body=urllib.request.urlopen(ENDPOINT+'?'+query,context=context,timeout=20).read()
            record=decode(body,code)
            target.write_bytes(gzip.compress(json.dumps(record,ensure_ascii=False).encode(),mtime=0))
            success+=1;consecutive=0
        except Exception as e:
            failed+=1;consecutive+=1
            print(json.dumps({'kapt_code':code,'error_type':type(e).__name__}),flush=True)
            if consecutive>=3:raise RuntimeError('Three requests failed; preserved checkpoints and stopped') from None
    print(json.dumps({'saved':success,'failed':failed,'requested_codes':len(codes)}))


if __name__=='__main__':
    import os
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--codes',type=Path,required=True)
    p.add_argument('--output',type=Path,default=Path('data/apt_preference_metadata'))
    p.add_argument('--use-existing-config',action='store_true')
    a=p.parse_args()
    key=os.getenv('MOLIT_API_KEY','')
    if a.use_existing_config:
        from collect_estate_transactions import existing_key
        key=existing_key()
    if not key:raise SystemExit('MOLIT_API_KEY is required')
    collect(a.codes,a.output,key)
