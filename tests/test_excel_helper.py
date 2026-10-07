from datetime import date
import json
import zipfile

import openpyxl
import pytest

from excel_helper.fill_workbook import address, richgo_facts, PublicPages, fill
from estate.site.ui import copy_ui


def test_asking_range_and_recency_are_not_transaction_or_predicted_prices():
    data = {'basic': {'construction': {'date': '1994.04.21'}, 'households': 498},
            'pyeongInfos': {'30': {'pyeongType': 30, 'minPrivateArea': '84.07', 'maxPrivateArea': '84.34',
                'danjiPriceInfo': {'memePriceDict': {'MOLIT': {'price': 74000},
                    'RICHGO_SISE': {'price': 120000},
                    'OFFER': {'minPrice': 133000, 'yyyymmdd': '2026.10.07'}}}}}}
    got = richgo_facts(data, 84.34, date(2026, 10, 8))
    assert got['매물 최저호가(억)'] == 13.3
    assert '84.07~84.34' in got['호가 평형 범위']
    assert '매물 최저호가(억)' not in richgo_facts(data, 84.77, date(2026, 10, 8))
    assert '매물 최저호가(억)' not in richgo_facts(data, 84.34, date(2026, 11, 8))
    assert address('서울특별시 강서구 염창동 288') == address('서울시 강서구 염창동 288')
    assert address('서울 강서구 염창동 288') != address('서울 강서구 염창동 291')


def test_autofill_preserves_manual_cells_original_and_source_trace(tmp_path, monkeypatch):
    import excel_helper.fill_workbook as helper
    src = tmp_path/'input.xlsx'
    w = openpyxl.Workbook(); s = w.active; s.title = '임장 비교'
    s.append(['단지', '주소', '전용(㎡)', '준공년도', '용적률(%)', '임장', '메모'])
    s.append(['현대1', '서울 강서구 염창동 288', 84.34, 1994, None, '방문함', '=이 문자는 메모'])
    w.create_sheet('기준 및 출처').append(['항목', '설명'])
    w.save(src); original = src.read_bytes()
    monkeypatch.setattr(helper, 'collect', lambda *a: ({'용적률(%)': 271, '임장': '덮어쓰기 금지'}, ['https://m.richgo.ai/realty/danji/example'], []))
    result = fill(src)
    out = openpyxl.load_workbook(result['output'])
    assert src.read_bytes() == original
    assert out.worksheets[0]['E2'].value == 271
    assert out.worksheets[0]['F2'].value == '방문함'
    assert out.worksheets[0]['G2'].value == '=이 문자는 메모'
    assert 'https://m.richgo.ai/' in out.worksheets[0]['E2'].comment.text
    assert out.sheetnames == ['임장 비교', '기준 및 출처']


def test_helper_zip_contains_only_program_and_instructions(tmp_path):
    copy_ui(tmp_path)
    with zipfile.ZipFile(tmp_path/'downloads/estate-excel-helper.zip') as z:
        assert {'fill.cmd', 'fill_workbook.py', 'requirements.txt', '사용법.md', 'FillEstate.bas'} <= set(z.namelist())
        assert all('/' not in n and not n.endswith(('.xlsx', '.html', '.json')) for n in z.namelist())


def test_naver_time_includes_wait_and_rejects_missing_destination():
    from excel_helper.naver_routes import arrival_minutes, destination_token
    assert arrival_minutes('최적 오전 8:05 출발 오전 8:27 도착') == 27
    assert arrival_minutes('오후 1:15 도착') == 315
    with pytest.raises(ValueError):
        arrival_minutes('오전 7:50 도착')
    with pytest.raises(ValueError):
        destination_token('https://map.naver.com/p/directions/-/-/-/transit')
    with pytest.raises(ValueError):
        destination_token('https://example.com/p/directions/-/destination/-/transit')


def test_photo_price_columns_use_fresh_supply_buckets_and_exact_address():
    def p(supply, price, dated='2026.10.07'):
        return {'pyeongType': supply, 'minPrivateArea': 84, 'maxPrivateArea': 85,
                'danjiPriceInfo': {'memePriceDict': {'OFFER': {'minPrice': price, 'yyyymmdd': dated}}}}
    data={'basic': {'construction': {'date': '1994.01.01'}, 'parking': {'count': 600}},
          'pyeongInfos': {'a':p(24,70000),'b':p(32,100000),'c':p(39,90000),
                         'd':p(44,140000,'2026.08.01'),'e':p(40,150000)}}
    facts=richgo_facts(data,84.5,date(2026,10,8))
    assert [facts[k] for k in ['20평대 호가','30평대 호가','40평대 호가']]==[7,9,15]
    assert facts['주차대수']==600
    assert '매물 최저호가(억)' not in facts  # Multiple supply groups: no false exact-type ask.
    assert address('경기도 화성시 동탄구 산척동 800번지')==address('경기 화성시 산척동 800')
    assert address('경기 화성시 산척동 800')!=address('경기 화성시 산척동 801')


def test_photo_autofill_routes_formulas_and_manual_edits(tmp_path,monkeypatch):
    import excel_helper.fill_workbook as helper
    from openpyxl.comments import Comment
    src=tmp_path/'photo.xlsx';w=openpyxl.Workbook();s=w.active;s.title='임장 비교'
    s.append(['번호','이름','위치','세대수','준공년도','연차','주차대수','세대당 주차',
              '20평대 호가','모델 비교 전용(㎡)','신랑직주','아내직주','강남역','신랑 본가','신부 본가','임장','메모','커뮤니티'])
    s.append([1,'현대1','염창동',498,1994,None,None,None,None,84.34,None,None,None,None,None,'방문','기록'])
    n=w.create_sheet('기준 및 출처');n.append(['항목','설명']);n.append(['단지 주소 · 1','서울 강서구 염창동 288'])
    w.save(src)
    def rows(tasks,*args):
        row,identity=tasks[0]
        assert identity==('현대1','서울 강서구 염창동 288',84.34,1994)
        return [(row,identity,{'facts':{'세대수':498,'주차대수':600,'세대당 주차':1.2,
                     '신랑 직주(분)':44,'신부 직주(분)':55,'강남역(분)':65,'신랑 본가(분)':90,'신부 본가(분)':85,
                     '20평대 호가':7.1,'커뮤니티':'=공개페이지의 문자'},
                     'sources':['https://m.richgo.ai/realty/danji/example'],'errors':[],
                     'route_notes':[],'observed_at':'2026-10-08'})]
    monkeypatch.setattr(helper,'row_results',rows)
    out=fill(src);w=openpyxl.load_workbook(out['output']);s=w.worksheets[0]
    assert s['H2'].data_type=='f' and 'G2/D2' in s['H2'].value
    assert [s.cell(2,c).value for c in range(11,16)]==[44,55,65,90,85]
    assert [s['P2'].value,s['Q2'].value]==['방문','기록']
    assert s['R2'].data_type=='s' and s['R2'].value.startswith('=')
    s['H2']=1.5;s['H2'].comment=Comment('저장값: 1.2\n출처',helper.AUTO)
    edited=tmp_path/'edited.xlsx';w.save(edited)
    again=fill(edited);assert openpyxl.load_workbook(again['output']).worksheets[0]['H2'].value==1.5


def test_photo_layout_also_wraps_preexisting_community_text():
    from excel_helper.fill_workbook import layout_photo_row
    w=openpyxl.Workbook();s=w.active;s.column_dimensions['T'].width=25
    text='관리사무소, 노인정, 주민공동시설, 어린이놀이터, 자전거보관소'
    s['T2']=text;layout_photo_row(s,2)
    assert s['T2'].value==text and s['T2'].alignment.wrap_text
    assert s.row_dimensions[2].height>=59
