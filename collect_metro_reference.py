"""Collect Seoul's public station master and a conservatively verified event subset."""
import hashlib,json,re,urllib.parse,urllib.request
from pathlib import Path
from estate_io import write_json
from estate_calendar import today

# Entire routes were operating BY these dates. These are conservative availability
# bounds, not invented individual station opening dates. Only named master routes match.
VERIFIED = {
    '3호선': ('2010-02-18','2010-02-19','https://mediahub.seoul.go.kr/archives/164275'),
    '6호선': ('2019-12-21','2020-01-31','https://mediahub.seoul.go.kr/archives/1265711'),
    '9호선': ('2018-12-01','2018-12-31','https://mediahub.seoul.go.kr/archives/1196358'),
    '9호선(연장)': ('2018-12-01','2018-12-31','https://mediahub.seoul.go.kr/archives/1196358'),
    '신림선': ('2022-05-28','2022-05-28','https://www.molit.go.kr/mta/USR/N0201/m_36770/dtl.jsp?id=95086737&lcmspage=3'),
    '진접선': ('2022-03-19','2022-03-19','https://m.korea.kr/news/policyNewsView.do?newsId=148899977'),
    '별내선': ('2024-08-10','2024-08-10','https://mediahub.seoul.go.kr/archives/2011685'),
}


def parse_sheet(text):
    # The official public preview is a JS object literal. Parse its restricted
    # unquoted-key/trailing-comma syntax without ever evaluating downloaded code.
    text=re.sub(r'([{,])\s*([A-Za-z_][A-Za-z_0-9]*):',r'\1"\2":',text)
    text=re.sub(r',\s*([}\]])',r'\1',text)
    return json.loads(text)


def collect(output=Path('metadata/metro_verified_events.json')):
    query=urllib.parse.urlencode({'onepagerow':1000,'srvType':'S','infId':'OA-21232','serviceKind':0,
        'pageNo':1,'ssUserId':'SAMPLE_VIEW','strOrderby':'BLDN_ID DESC'})
    body=urllib.request.urlopen('https://data.seoul.go.kr/dataList/dataView.do?'+query,timeout=30).read()
    result=parse_sheet(body.decode('utf-8'))
    rows=result['list']
    if len(rows)!=int(result['page']['totalCount']):raise ValueError('Incomplete station master; no truncated output')
    events=[]
    for r in rows:
        if r['ROUTE'] not in VERIFIED:continue
        effective,known,source=VERIFIED[r['ROUTE']]
        events.append({'station_id':r['BLDN_ID'],'name':r['BLDN_NM'],'line':r['ROUTE'],
            'latitude':float(r['LAT']),'longitude':float(r['LOT']),'status':'operating',
            'effective_at':effective,'known_at':known,'source_url':source,'date_semantics':'verified operating-by bound'})
        if r['ROUTE']=='별내선':
            for status,date,url in [
                ('planned','2014-12-18','https://gnews.gg.go.kr/briefing/brief_gongbo_view.do?BS_CODE=S017&number=25669'),
                ('construction','2015-12-18','https://gnews.gg.go.kr/news/news_detail_m.do?number=201512181817288001C048&s_code=daily')]:
                events.append({**events[-1],'status':status,'effective_at':date,'known_at':date,
                    'source_url':url,'date_semantics':'verified project status; current final coordinates are retrospective approximations'})
    manifest={'observed_at':today().isoformat(),'coordinate_source':'https://data.seoul.go.kr/dataList/OA-21232/A/1/datasetView.do',
        'attribution':'서울특별시 서울열린데이터광장 · 서울시 역사마스터 정보 · 공공누리 제1유형',
        'raw_sha256':hashlib.sha256(body).hexdigest(),'master_rows':len(rows),'verified_rows':len({e['station_id'] for e in events}),
        'coordinate_limitation':'Current station-point coordinates projected backwards; historic entrance positions and routed walking times unavailable.',
        'unverified_rows':len(rows)-len({e['station_id'] for e in events}),'events':events}
    write_json(output,manifest,indent=2)
    print('Station master:',len(rows),'verified stations:',manifest['verified_rows'],'status events:',len(events))


if __name__=='__main__':collect()
