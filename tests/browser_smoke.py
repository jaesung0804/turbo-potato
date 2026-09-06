"""Optional real-browser release check: pip install playwright, then pass a served URL."""
import argparse
import json
from pathlib import Path
from playwright.sync_api import sync_playwright


def check(url, chrome=None):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, **({'executable_path': chrome} if chrome else {}))
        page = browser.new_page(viewport={'width': 1440, 'height': 1000})
        errors, requests = [], []
        page.on('pageerror', lambda e: errors.append(str(e)))
        page.on('console', lambda m: errors.append(m.text) if m.type == 'error' else None)
        page.on('request', lambda r: requests.append(r.url))
        page.goto(url, wait_until='networkidle')
        page.wait_for_selector('.ai-row', timeout=60000)
        assert page.locator('.ai-row').count() == 50
        assert not any('/history.' in r for r in requests), 'History must load only on demand'
        assert page.evaluate("getComputedStyle(document.querySelector('.leaflet-pane')).position") == 'absolute', 'Leaflet CSS failed'
        assert page.evaluate("getComputedStyle(document.querySelector('.leaflet-control-zoom')).display") != 'none'
        assert page.evaluate("(()=>{const ids=[...document.querySelectorAll('[id]')].map(x=>x.id);return new Set(ids).size===ids.length})()")
        samples = page.evaluate("Array.from({length:5},()=>{let t=performance.now();refresh();return performance.now()-t})")
        for width, height in [(1440, 1000), (1280, 900), (768, 1024), (390, 844), (360, 800)]:
            page.set_viewport_size({'width': width, 'height': height})
            page.wait_for_timeout(150)
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), f'Overflow at {width}'
            if width<=600:
                assert page.locator('.ai-row').first.bounding_box()['y'] < height, 'Mobile results are buried below controls'
            assert page.evaluate("(()=>{let r=[...document.querySelectorAll('.ai-row')].map(e=>e.getBoundingClientRect());return r.every((x,i)=>x.height>80&&(!i||x.top>=r[i-1].bottom))})()"), 'Cards overlap'
            page.screenshot(path=f'.work/ui-final-{width}.png')
        page.set_viewport_size({'width': 1440, 'height': 1000})
        first = page.locator('.ai-row').first.inner_text()
        page.locator('#ai-list-pages button').last.click()
        assert page.locator('.ai-row').first.inner_text() != first
        assert page.locator('#ai-list-pages button').last.is_disabled()
        page.locator('#ai-list-pages button').first.click()
        name = page.locator('.ai-main strong').first.inner_text()
        page.locator('#search-input').fill(name)
        page.wait_for_timeout(500)
        assert page.locator('.ai-main strong').count() > 0
        assert all(name in s for s in page.locator('.ai-main strong').all_inner_texts())
        page.locator('.ai-row').first.click()
        page.wait_for_selector('#type-select')
        assert page.locator('#selected-region').is_visible()
        assert any('history' in r for r in requests)
        page.locator('#clear-filter').click()
        page.locator('#advanced-filters summary').click()
        assert page.locator('#advanced-filters').get_attribute('open') is not None
        with page.expect_download() as download:
            page.locator('#export-results').click()
        assert download.value.suggested_filename.endswith('.csv')
        page.locator('#analysis-panel summary').click()
        page.wait_for_timeout(1500)
        assert page.locator('#growth-summary').inner_text()
        page.goto(url.rstrip('/') + '/model.html', wait_until='networkidle')
        page.wait_for_selector('#validation-rows tr')
        assert page.locator('#importance-rows tr').count() == 6
        assert page.locator('#quality-rows tr').count() == 9
        assert not errors, errors
        browser.close()
        print(json.dumps({'browser_errors': errors, 'refresh_ms': samples, 'viewports': 5, 'paging_search_details_guide': 'passed'}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('url')
    parser.add_argument('--chrome')
    args = parser.parse_args()
    Path('.work').mkdir(exist_ok=True)
    check(args.url, args.chrome)
