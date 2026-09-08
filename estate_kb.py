"""Parse public KB apartment indices by explicit geography and calendar month."""
import re
import numpy as np
import pandas as pd
from openpyxl import load_workbook
from get_molit_apt_trade_data import CAPITAL_AREA_LAWD_CODES

def period_rows(rows, first):
    year = None
    for row in rows[first:]:
        token = str(row[0]).strip().lstrip("'")
        m = re.fullmatch(r'(\d{2}|\d{4})\.(\d{1,2})', token)
        if m:
            year, month = map(int, m.groups())
            if year < 100:
                year += 1900 if year >= 80 else 2000
        elif token.isdigit() and 1 <= int(token) <= 12 and year:
            month = int(token)
        else:
            continue
        if not 1 <= month <= 12:
            raise ValueError('Invalid workbook month')
        yield pd.Period(year=year, month=month, freq='M'), row


def read_kb(path):
    workbook = load_workbook(path, read_only=True, data_only=True)
    seoul = {r.sgg:r.code for r in CAPITAL_AREA_LAWD_CODES if r.code.startswith('11')}
    records = []
    for kind, sheet in [('sale','2.매매APT'),('rent','6.전세APT')]:
        rows = list(workbook[sheet].values)
        columns = {i:seoul[rows[2][i]] for i in range(4,30) if rows[2][i] in seoul}
        if set(columns.values()) != set(seoul.values()):
            raise ValueError('KB Seoul district layout changed')
        for name, code in [('서울특별시','11'),('경기도','41'),('인천광역시','28'),('광명시','41210')]:
            matches = [i for i,v in enumerate(rows[1]) if v == name]
            if len(matches) != 1:
                raise ValueError('KB geography header mismatch: '+name)
            columns[matches[0]] = code
        for period,row in period_rows(rows,4):
            if period.year < 2015:
                continue
            for i,code in columns.items():
                v = row[i]
                if isinstance(v,(int,float)) and np.isfinite(v) and v > 0:
                    records.append({'period':str(period),'gu':code,'kind':kind,'value':float(v)})
    workbook.close()
    d = pd.DataFrame(records)
    if d.duplicated(['period','gu','kind']).any():
        raise ValueError('Duplicate index identity')
    return d
