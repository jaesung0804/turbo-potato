"""임장 비교표 자동 채우기. 인증키 없이 공개 페이지를 조회하며 원본은 보존합니다."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta
from concurrent.futures import ProcessPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import time
from urllib.parse import urlparse, urlencode

from bs4 import BeautifulSoup
import openpyxl
from openpyxl.comments import Comment
import requests

ALLOWED = {'search.naver.com', 'm.richgo.ai', 'realty.daangn.com'}
AUTO = '임장 자동 채우기'


def number(value):
    try:
        v = float(value)
        return v if 0 < v < 1e9 else None
    except (TypeError, ValueError):
        return None


def address(value):
    s = str(value or '').replace('서울특별시', '서울').replace('서울시', '서울').replace('인천광역시', '인천').replace('경기도', '경기')
    s = re.sub(r'(?<=\d)번지(?=\s|$)', '', s)
    # Old public pages omit Hwaseong's 2026 wards. City, dong and exact lot remain.
    s = re.sub(r'(화성시)\s+(만세구|효행구|병점구|동탄구)\s+',r'\1 ',s)
    return re.sub(r'\s+', ' ', s).strip()


class PublicPages:
    def __init__(self, cache, refresh=False):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.refresh = refresh
        self.session = requests.Session()
        self.last = 0.

    def get(self, url):
        if urlparse(url).scheme != 'https' or urlparse(url).hostname not in ALLOWED:
            raise ValueError('지원하는 공개 정보 사이트가 아닙니다.')
        cache = self.cache/(hashlib.sha256(url.encode()).hexdigest()+'.html')
        if not self.refresh and cache.exists() and time.time()-cache.stat().st_mtime < 86400:
            return cache.read_text(encoding='utf-8')
        time.sleep(max(0., 1.2-(time.monotonic()-self.last)))
        response = self.session.get(url, timeout=30, allow_redirects=False)
        self.last = time.monotonic()
        response.raise_for_status()
        if response.is_redirect:
            raise ValueError('로그인 또는 변경된 주소로 이동되어 조회를 중단했습니다.')
        if len(response.content) > 8_000_000:
            raise ValueError('페이지 크기 제한을 초과했습니다.')
        body = response.content.decode('utf-8')
        if 'captcha' in body[:2000].lower():
            raise ValueError('사이트의 확인 절차가 필요합니다. 우회하지 않고 건너뜁니다.')
        cache.write_text(body, encoding='utf-8')
        return body

    def search(self, query, provider):
        sites = {'richgo': 'm.richgo.ai/realty/danji', 'daangn': 'realty.daangn.com/complexes'}
        prefix = 'https://'+sites[provider]+'/'
        html = self.get('https://search.naver.com/search.naver?'+urlencode({'query': query+' site:'+sites[provider]}))
        urls = []
        for a in BeautifulSoup(html, 'html.parser').select('a[href]'):
            href = a['href']
            if href.startswith(prefix):
                match = re.match(re.escape(prefix)+r'[a-zA-Z0-9]+', href)
                if match and match[0] not in urls:
                    urls.append(match[0])
        return urls[:6]


def richgo(html):
    node = BeautifulSoup(html, 'html.parser').find('script', id='__NEXT_DATA__')
    if not node:
        raise ValueError('단지 자료를 읽을 수 없습니다.')
    page = json.loads(node.string)['props']['pageProps']
    data = page.get('data') or {}
    if not data.get('basic', {}).get('address', {}).get('jibun'):
        raise ValueError('주소가 있는 단지 자료가 아닙니다.')
    return data


def richgo_facts(data, area, today=None):
    today = today or datetime.now().date()
    basic = data['basic']
    f = {'세대수': number(basic.get('households')), '준공년도': int(basic['construction']['date'][:4]),
         '주차대수': number(basic.get('parking', {}).get('count')),
         '세대당 주차': round(number(basic.get('parking', {}).get('households')), 3) if number(basic.get('parking', {}).get('households')) else None,
         '용적률(%)': number(basic.get('floorArea')), '건폐율(%)': number(basic.get('buildingRatio'))}
    # 공급평형을 전용면적의 단일 타입 호가로 오인하지 않도록 범위를 함께 기록한다.
    matches = [v for v in data.get('pyeongInfos', {}).values()
               if number(v.get('minPrivateArea')) and number(v.get('maxPrivateArea'))
               and area is not None and float(v['minPrivateArea'])-.005 <= area <= float(v['maxPrivateArea'])+.005]
    # The photograph groups asks by SUPPLY-area pyeong, not private square metres.
    dated_asks = []
    for p in data.get('pyeongInfos', {}).values():
        supply = number(p.get('pyeongType'))
        offer = p.get('danjiPriceInfo', {}).get('memePriceDict', {}).get('OFFER', {})
        try:
            dated = datetime.strptime(offer.get('yyyymmdd') or '', '%Y.%m.%d').date()
        except ValueError:
            continue
        if supply and 0 <= (today-dated).days <= 30 and number(offer.get('minPrice')):
            bucket = int(supply)//10*10
            if bucket in (20, 30, 40):
                label = f'{bucket}평대 호가'
                price = float(offer['minPrice'])/10000
                f[label] = min(f.get(label, price), price)
                dated_asks.append(f"공급 {supply:g}평 · {price:g}억 · {dated}")
    if dated_asks:
        f['_asking_details'] = '\n'.join(dated_asks)
    if len(matches) == 1:
        p = matches[0]
        offer = p.get('danjiPriceInfo', {}).get('memePriceDict', {}).get('OFFER', {})
        date_text = offer.get('yyyymmdd') or ''
        try:
            dated = datetime.strptime(date_text, '%Y.%m.%d').date()
        except ValueError:
            dated = None
        if dated and 0 <= (today-dated).days <= 30 and number(offer.get('minPrice')):
            f['매물 최저호가(억)'] = float(offer['minPrice'])/10000
            f['호가 평형 범위'] = f"공급 {p['pyeongType']}평 · 전용 {p['minPrivateArea']}~{p['maxPrivateArea']}㎡"
            f['_asking_date'] = date_text
    return {k: v for k, v in f.items() if v is not None}


def daangn(html, road_address):
    soup = BeautifulSoup(html, 'html.parser')
    script = next((s.string for s in soup.find_all('script') if (s.string or '').startswith('window.RELAY_STORE = ')), None)
    if not script:
        return {}
    # Decode public server-rendered page data as JSON; never execute site scripts.
    encoded, _ = json.JSONDecoder().raw_decode(script.split(' = ', 1)[1])
    data = json.loads(encoded)
    complex_ = next((v for v in data.values() if v.get('__typename') == 'PropComplex' and 'proptierDanji' in v), None)
    if not complex_:
        return {}
    def ref(obj, name):
        return data.get((obj.get(name) or {}).get('__ref'), {})
    def refs(obj, name):
        return [data[k] for k in (obj.get(name) or {}).get('__refs', [])]
    buildings = ref(complex_, 'buildings(first:10)') or ref(complex_, 'buildings(first:1)')
    # Only buildings attached to this complex, never unrelated recommendations.
    linked = [ref(data[e], 'node') for e in (buildings.get('edges') or {}).get('__refs', [])]
    if not any(address(b.get('roadAddress')) == address(road_address) for b in linked):
        return {}
    p = ref(complex_, 'proptierDanji')
    f = {}
    connection = complex_.get('parkingConnection')
    if isinstance(connection, bool):
        f['승강기 연결'] = 'O' if connection else 'X'
        if connection:
            f['지하주차장'] = 'O'
    stations = [s for s in refs(p, 'subways') if number(s.get('walkingTimeMin'))]
    if stations:
        nearest = min(stations, key=lambda s: float(s['walkingTimeMin']))
        f.update({'근접역': nearest['stationNm']+'역', '역 도보(분)': float(nearest['walkingTimeMin'])})
    communities = complex_.get('communityFacilities')
    if isinstance(communities, list) and communities and all(isinstance(c, str) for c in communities):
        labels = {'DAYCARE': '어린이집', 'SENIOR_CENTER': '경로당', 'GYM': '헬스장',
                  'FITNESS': '헬스장', 'GOLF': '골프연습장', 'LIBRARY': '도서관',
                  'SWIMMING_POOL': '수영장', 'SAUNA': '사우나', 'GUEST_HOUSE': '게스트하우스',
                  'PLAYGROUND': '놀이터'}
        translated = [labels.get(c, c if re.search('[가-힣]', c) else '') for c in communities]
        if any(translated):
            f['커뮤니티'] = ', '.join(c for c in translated if c)
    return f


def collect(pages, name, location, area, built):
    locality = location.rsplit(' ', 1)[0]
    short_name = name.removesuffix('아파트')
    dong = locality.split()[-1].removesuffix('동')
    if dong and short_name.startswith(dong) and len(short_name) > len(dong):
        short_name = short_name[len(dong):]
    query = locality+' '+short_name
    errors = []
    urls = pages.search(query, 'richgo')
    for url in urls:
        try:
            data = richgo(pages.get(url))
            basic = data['basic']
            if address(basic['address']['jibun']) != address(location):
                continue
            if built and int(basic['construction']['date'][:4]) != int(built):
                continue
            facts = richgo_facts(data, area)
            sources = [url]
            # Confirm the secondary source against the verified road address.
            for other in pages.search(query, 'daangn')[:3]:
                try:
                    additional = daangn(pages.get(other), basic['address']['road'])
                    if additional:
                        facts.update(additional)
                        sources.append(other)
                        break
                except (ValueError, KeyError, requests.RequestException) as e:
                    errors.append(type(e).__name__)
            return facts, sources, errors
        except (ValueError, KeyError, requests.RequestException) as e:
            errors.append(type(e).__name__)
    # Apartment display names change; a single address lookup can recover an
    # exact parcel without fuzzy-matching a different same-name property.
    for url in pages.search(location, 'richgo'):
        if url in urls:
            continue
        try:
            data=richgo(pages.get(url));basic=data['basic']
            if address(basic['address']['jibun'])!=address(location):
                continue
            if built and int(basic['construction']['date'][:4])!=int(built):
                continue
            facts=richgo_facts(data,area);sources=[url]
            for other in pages.search(location,'daangn')[:3]:
                additional=daangn(pages.get(other),basic['address']['road'])
                if additional:
                    facts.update(additional);sources.append(other);break
            return facts,sources,errors
        except (ValueError,KeyError,requests.RequestException) as e:
            errors.append(type(e).__name__)
    return {}, [], errors+['주소·준공년도가 일치하는 공개 단지를 찾지 못했습니다.']


def fetch_row(identity, templates, cache, refresh, routes):
    """One isolated worker reads public facts and five routes; Excel writes stay serial."""
    pages = PublicPages(cache, refresh)
    saved = pages.cache/(hashlib.sha256(json.dumps([identity,templates,routes],ensure_ascii=False).encode()).hexdigest()+'.result.json')
    if not refresh and saved.exists() and time.time()-saved.stat().st_mtime < 86400:
        previous=json.loads(saved.read_text(encoding='utf-8'))
        if not previous.get('errors'):
            return previous
    try:
        facts, sources, errors = collect(pages, *identity)
    except (requests.RequestException, ValueError, KeyError) as e:
        facts, sources, errors = {}, [], [str(e)[:200]]
    route_notes = []
    if routes and templates:
        try:
            from .naver_routes import NaverRoutes
        except ImportError:
            from naver_routes import NaverRoutes
        reader=NaverRoutes(pages.cache/'이동시간')
        try:
            for label, url in templates.items():
                try:
                    record=reader.read(identity[1],url)
                    facts[label+'(분)']=record['minutes']
                    route_notes.append(f"{label}: {record['route_date']} 08:00 출발 · {record['minutes']}분 · {record['source_url']}")
                except Exception as e:
                    message=str(e).splitlines()[0][:160]
                    errors.append(label+' 이동 조회 실패: '+message)
                    if '정확한 출발 주소' in message or '주변 주소' in message:
                        errors.append('정확한 출발점이 없어 나머지 목적지 조회도 보류했습니다.')
                        break
        finally:
            reader.close()
    result={'identity':identity,'facts':facts,'sources':sources,'errors':errors,'route_notes':route_notes,'observed_at':datetime.now().astimezone().isoformat(timespec='seconds')}
    saved.write_text(json.dumps(result,ensure_ascii=False),encoding='utf-8')
    return result


def row_results(tasks,templates,cache,refresh,routes,workers):
    if workers==1:
        for row,identity in tasks:
            yield row,identity,fetch_row(identity,templates,cache,refresh,routes)
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        pending={pool.submit(fetch_row,identity,templates,cache,refresh,routes):(row,identity) for row,identity in tasks}
        for future in as_completed(pending):
            row,identity=pending[future]
            yield row,identity,future.result()


def fill(path, output=None, cache=None, refresh=False, limit=None, routes=True, workers=1):
    path = Path(path).resolve()
    output = Path(output).resolve() if output else path.with_name(path.stem+'-채움'+path.suffix)
    if output == path or output.exists():
        raise ValueError('원본이나 기존 결과를 덮어쓰지 않습니다. 새 출력 이름을 지정하세요.')
    wb = openpyxl.load_workbook(path, keep_vba=path.suffix.lower()=='.xlsm')
    if wb.sheetnames != ['임장 비교', '기준 및 출처']:
        raise ValueError('대시보드에서 최신 임장 비교 엑셀을 내려받아 주세요.')
    ws, notes = wb.worksheets
    headers = {c.value: c.column for c in ws[1]}
    template = '이름' in headers and '20평대 호가' in headers
    for required in (['이름', '모델 비교 전용(㎡)', '임장', '메모'] if template else ['단지', '주소', '전용(㎡)', '임장', '메모']):
        if required not in headers:
            raise ValueError('비교표의 필수 열이 없습니다: '+required)
    pages = PublicPages(cache or path.parent/'.임장조회캐시', refresh)
    locations = {str(r[0].value).removeprefix('단지 주소 · '): r[1].value
                 for r in notes.iter_rows(min_row=2, max_col=2) if str(r[0].value).startswith('단지 주소 · ')}
    templates = {str(r[0].value).removeprefix('네이버 목적지 · '): r[1].value
                 for r in notes.iter_rows(min_row=2, max_col=2)
                 if str(r[0].value).startswith('네이버 목적지 · ')}
    if not 1 <= workers <= 4:
        raise ValueError('동시 조회 수는 1~4입니다.')
    today = datetime.now().astimezone().isoformat(timespec='seconds')
    changed = processed = 0
    tasks=[]
    for row in range(2, ws.max_row+1):
        if limit is not None and len(tasks) >= limit:
            break
        values = {h: ws.cell(row, col).value for h, col in headers.items()}
        identity = (values.get('이름', values.get('단지')), locations.get(str(values.get('번호')),values.get('주소')), number(values.get('모델 비교 전용(㎡)',values.get('전용(㎡)'))), values.get('준공년도'))
        if not identity[0] or not identity[1] or not identity[2]:
            continue
        tasks.append((row,identity))
    print(f'{len(tasks)}개 단지 조회 시작 · 동시 {workers}개',flush=True)
    for row,identity,result in row_results(tasks,templates,str(pages.cache),refresh,routes,workers):
        facts,sources,errors=result['facts'],result['sources'],result['errors']
        if template:
            aliases = {'역 도보(분)': '역까지거리(도보)', '정비사업': '조합여부','신랑 직주(분)':'신랑직주','신부 직주(분)':'아내직주','신랑 본가(분)':'신랑 본가','신부 본가(분)':'신부 본가','강남역(분)':'강남역'}
            facts = {aliases.get(k,k):v for k,v in facts.items()}
        route_notes=result['route_notes']
        provenance = f"{result['observed_at']}\n"+'\n'.join(sources)
        if route_notes:
            provenance += '\n'+'\n'.join(route_notes)
        if facts.get('_asking_date'):
            provenance += '\n호가 자료일: '+facts['_asking_date']
        if facts.get('_asking_details'):
            provenance += '\n'+facts['_asking_details']
        for h, value in facts.items():
            if h.startswith('_') or h not in headers or h in {'임장', '메모'}:
                continue
            cell = ws.cell(row, headers[h])
            # Manual changes to a previously populated result are respected.
            if cell.comment and cell.comment.author == AUTO:
                old = cell.comment.text.split('\n', 1)[0].removeprefix('저장값: ')
                if str(cell.value) != old:
                    continue
            # Existing downloaded dated snapshot values may be refreshed. Values
            # entered in the manual fields are outside this write allowlist.
            cell.value = value
            if template and h == '세대당 주차' and '주차대수' in facts and '세대수' in facts:
                value = f'=IF(OR(G{row}="",D{row}="",D{row}=0),"",G{row}/D{row})'
                cell.value = value
            if isinstance(value, str) and not value.startswith('=') and len(value) > 16:
                cell.alignment = openpyxl.styles.Alignment(wrap_text=True, vertical='center')
                ws.row_dimensions[row].height = max(ws.row_dimensions[row].height or 30, 48)
            if isinstance(value, str):
                cell.data_type = 'f' if template and h == '세대당 주차' else 's'
            cell.comment = Comment('저장값: '+str(value)+'\n'+provenance, AUTO)
            changed += 1
        if sources and '단지 정보' in headers:
            cell = ws.cell(row, headers['단지 정보'])
            cell.value, cell.hyperlink = '정보 보기', sources[0]
        note = provenance
        if errors:
            note += '\n조회 메모: '+' / '.join(dict.fromkeys(errors))
        missing = [h for h in ('주차대수','20평대 호가','30평대 호가','40평대 호가','용적률(%)','대지지분(㎡)','지하주차장','승강기 연결','커뮤니티','조합여부') if h in headers and ws.cell(row,headers[h]).value is None]
        if missing:
            note += '\n공개 출처에서 확정하지 못한 항목: '+', '.join(missing)
        notes.append([f'자동 조회 {row-1}. {identity[0]} {identity[2]}㎡', note])
        notes.cell(notes.max_row, 2).alignment = openpyxl.styles.Alignment(wrap_text=True, vertical='top')
        notes.row_dimensions[notes.max_row].height = min(240, 18*(note.count('\n')+2))
        processed += 1
        print(f"[{processed}/{len(tasks)}] {identity[0]} · 시설 출처 {len(sources)}개 · 이동 {len(route_notes)}곳",flush=True)
        # A bounded progress file allows recovery without corrupting the source.
        tmp = output.with_name(output.stem+'.진행중'+output.suffix)
        if processed % 10 == 0:
            wb.save(tmp)
    wb.save(output)
    progress = output.with_name(output.stem+'.진행중'+output.suffix)
    if progress.exists():
        progress.unlink()
    print(f'완료: {processed}행, {changed}개 항목 → {output}', flush=True)
    return {'rows': processed, 'cells': changed, 'output': str(output)}


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('workbook', nargs='?')
    p.add_argument('--output')
    p.add_argument('--cache')
    p.add_argument('--refresh', action='store_true')
    p.add_argument('--limit', type=int)
    p.add_argument('--workers', type=int, default=2, choices=range(1,5), help='동시에 조회할 단지 수 (기본 2)')
    p.add_argument('--no-routes', action='store_true', help='시설·호가만 조회')
    a = p.parse_args()
    if not a.workbook:
        import tkinter as tk
        from tkinter.filedialog import askopenfilename
        root = tk.Tk(); root.withdraw()
        a.workbook = askopenfilename(title='채울 임장 비교표 선택', filetypes=[('엑셀', '*.xlsx *.xlsm')])
        root.destroy()
        if not a.workbook:
            return
    fill(a.workbook, a.output, a.cache, a.refresh, a.limit, not a.no_routes,a.workers)


if __name__ == '__main__':
    main()
