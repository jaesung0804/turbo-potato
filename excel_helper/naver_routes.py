"""네이버 지도 화면에서 평일 08:00 대중교통 경로를 읽는 로컬 매크로."""
from datetime import datetime, timedelta
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlparse


def destination_token(url):
    parts = urlparse(str(url))
    path = parts.path.split('/')
    if parts.scheme != 'https' or parts.hostname != 'map.naver.com' or len(path) != 7 or path[1:3] != ['p', 'directions'] or path[6] != 'transit':
        raise ValueError('네이버 대중교통 경로 주소가 아닙니다.')
    if path[4] == '-' or len(path[4]) > 1500:
        raise ValueError('목적지가 지정된 경로가 필요합니다.')
    return path[4]


def arrival_minutes(text):
    m = re.search(r'(오전|오후)\s*(\d{1,2}):(\d{2})\s*도착', text)
    if not m:
        raise ValueError('오전 8시 출발 경로의 도착 시각을 확인하지 못했습니다.')
    hour = int(m[2]) % 12 + (12 if m[1] == '오후' else 0)
    minutes = hour*60+int(m[3])-8*60
    if not 0 < minutes < 600:
        raise ValueError('이동 시간이 지원 범위를 벗어납니다.')
    return minutes


class NaverRoutes:
    def __init__(self, cache):
        self.cache = Path(cache)
        self.cache.mkdir(parents=True, exist_ok=True)
        self.runtime = self.browser = self.page = None
        self.origins = {}
        self.day = datetime.now().date()
        while self.day.weekday() >= 5:
            self.day += timedelta(days=1)

    def start(self):
        from playwright.sync_api import sync_playwright
        self.runtime = sync_playwright().start()
        # Isolated Edge session, never connect to the user's existing tabs/profile.
        self.browser = self.runtime.chromium.launch(channel='msedge', headless=True)
        self.page = self.browser.new_page(locale='ko-KR', timezone_id='Asia/Seoul')
        self.page.set_default_timeout(15000)

    def close(self):
        if self.browser:
            self.browser.close()
        if self.runtime:
            self.runtime.stop()

    def read(self, origin, target_url):
        token = destination_token(target_url)
        identity = json.dumps([origin, token, str(self.day), '08:00', 'transit'], ensure_ascii=False)
        cache = self.cache/(hashlib.sha256(identity.encode()).hexdigest()+'.json')
        if cache.exists():
            saved = json.loads(cache.read_text(encoding='utf-8'))
            if saved.get('identity') == identity:
                self.origins[origin] = urlparse(saved['source_url']).path.split('/')[3]
                return saved
        if not self.page:
            self.start()
        page = self.page
        page.goto('https://map.naver.com/p/directions/'+self.origins.get(origin, '-')+'/'+token+'/-/transit', wait_until='domcontentloaded')
        if origin not in self.origins:
            box = page.get_by_role('combobox', name='출발지 입력', exact=True)
            box.wait_for(state='visible')
            page.wait_for_timeout(1000)  # Wait for the search control's hydration.
            for attempt in range(2):
                box.click(); box.press('ControlOrMeta+A'); box.press('Backspace')
                box.press_sequentially(origin, delay=35)
                page.wait_for_timeout(600)
                if box.input_value() == origin:
                    break
            if box.input_value() != origin:
                raise ValueError('출발 주소 입력이 일치하지 않습니다.')
            box.press('Enter')
            select = page.get_by_role('button', name='출발', exact=True)
            select.wait_for(state='visible')
            text = page.locator('#section_content').inner_text()
            if '검색 결과가 없어' in text:
                raise ValueError('주소 대신 주변 주소가 검색되어 경로를 기록하지 않았습니다.')
            try:
                from .fill_workbook import address
            except ImportError:
                from fill_workbook import address
            if address(origin) not in address(text):
                raise ValueError('검색 결과에 정확한 출발 주소가 없습니다.')
            select.click()
        time_button = page.get_by_role('button', name=re.compile(r'출발 펼치기$'))
        time_button.wait_for(state='visible'); time_button.click()
        self.origins[origin] = urlparse(page.url).path.split('/')[3]
        if self.day != datetime.now().date():
            page.get_by_role('button', name='오늘', exact=True).click()
            dialog = page.get_by_role('dialog')
            month = datetime.now().month
            if self.day.month != month:
                dialog.get_by_role('button', name='다음달', exact=True).click()
            dialog.get_by_role('button', name=str(self.day.day), exact=True).filter(visible=True).click()
            page.wait_for_timeout(800)
        page.get_by_role('button', name=re.compile(r'^\d{2}시$')).click()
        page.get_by_role('option', name='08', exact=True).click()
        page.wait_for_timeout(800)  # Changing time rerenders route controls.
        page.get_by_role('button', name=re.compile(r'^\d{2}분$')).click()
        page.get_by_role('option', name='00', exact=True).click()
        page.get_by_role('button', name=re.compile(r'오전 08:00.*출발')).wait_for(state='visible')
        result = page.get_by_role('button', name=re.compile(r'^최적\s+\d'))
        result.wait_for(state='visible', timeout=30000)
        label = result.inner_text()
        record = {'identity': identity, 'minutes': arrival_minutes(label), 'route_date': str(self.day),
                  'departure': '08:00', 'mode': 'transit', 'source_url': page.url,
                  'observed_at': datetime.now().astimezone().isoformat(timespec='seconds'),
                  'origin_basis': '정확한 지번 주소의 네이버 출발점', 'route_text': label}
        cache.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
        return record
