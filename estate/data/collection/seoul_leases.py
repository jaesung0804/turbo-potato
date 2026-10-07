"""Normalize Seoul's licensed annual lease files without inventing publication dates.

The file year is receipt year, NOT contract year. No stable transaction ID is
provided: exact duplicate rows are retained; only repeated file ingestion is
prevented. This export is a retrospective research input, not an as-of archive.
"""
from pathlib import Path
from datetime import datetime, timezone
import argparse
import hashlib
import json
import zipfile
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import numpy as np
import pandas as pd

CATALOG = 'https://data.seoul.go.kr/dataList/OA-21276/A/1/datasetView.do'
DOWNLOAD = 'https://datafile.seoul.go.kr/bigfile/iot/inf/nio_download.do?&useCache=false'
# Observed public catalog entries on 2026-09-08; fail on unlisted years.
FILES = {2016: (29, '2022-05-18'), 2017: (30, '2022-05-18'),
         2018: (31, '2022-05-18'), 2019: (32, '2022-05-18'),
         2020: (33, '2022-05-18'), 2021: (34, '2022-05-18'),
         2022: (36, '2023-01-12'), 2023: (38, '2024-02-19'),
         2024: (39, '2025-04-04'), 2025: (40, '2026-02-04')}


def read_zip(path):
    with zipfile.ZipFile(path) as z:
        names = [n for n in z.namelist() if n.lower().endswith(('.csv', '.txt'))]
        if len(names) != 1:
            raise ValueError('Expected exactly one tabular lease file')
        with z.open(names[0]) as f:
            prefix = f.read(4)
        encoding = 'utf-8-sig' if prefix.startswith(b'\xef\xbb\xbf') else 'cp949'
        with z.open(names[0]) as f:
            return pd.read_csv(f, encoding=encoding, dtype=str, keep_default_na=False)


def normalize(raw):
    d = raw[raw['건물용도'].str.strip().eq('아파트')].copy()
    for c in d:
        d[c] = d[c].str.strip()
    d['date'] = pd.to_datetime(d['계약일'], format='%Y%m%d', errors='coerce')
    for src, dst in [('임대면적','area'), ('보증금(만원)','deposit'),
                     ('임대료(만원)','monthly_rent'), ('층','floor')]:
        d[dst] = pd.to_numeric(d[src].str.replace(',', '', regex=False), errors='coerce')
    d = d[d.date.notna() & d.area.gt(0) & d.deposit.gt(0)
          & d.monthly_rent.eq(0) & d['전월세구분'].eq('전세')
          & d['본번'].str.fullmatch(r'\d+') & d['부번'].str.fullmatch(r'\d+')].copy()
    lot = d['본번'].str.lstrip('0').replace('', '0')
    sub = d['부번'].str.lstrip('0')
    lot += np.where(sub.ne(''), '-' + sub, '')
    area = d['임대면적'].str.replace(',', '', regex=False).map(
        lambda v: v.rstrip('0').rstrip('.') if '.' in v else v)
    d['complex'] = d['자치구명']+' '+d['법정동명']+' '+lot+' '+d['건물명']
    d['key'] = d.complex+' | '+area+'㎡'
    d['lot_key'] = d['자치구코드']+'|'+d['법정동명']+'|'+lot+'|'+area
    d['gu'] = d['자치구코드']
    d['contract_type'] = d['신규계약구분'].where(d['신규계약구분'].isin(['신규','갱신']), '미상')
    d['receipt_year'] = pd.to_numeric(d['접수년도'], errors='raise').astype(int)
    d['log_rent'] = np.log(d.deposit/d.area)
    return d[['key','complex','lot_key','gu','date','area','floor','deposit',
              'monthly_rent','contract_type','receipt_year','log_rent']]


def collect(cache, out, years):
    cache.mkdir(parents=True, exist_ok=True)
    out.mkdir(parents=True, exist_ok=True)
    frames, sources = [], []
    for year in sorted(set(years)):
        seq, modified = FILES[year]
        path = cache/f'seoul-rent-{year}.zip'
        if not path.exists():
            req = Request(DOWNLOAD, data=urlencode({'infId':'OA-21276','seqNo':'',
                'seq':str(seq),'infSeq':'3'}).encode(), headers={'Referer': CATALOG})
            with urlopen(req, timeout=60) as response:
                body = response.read()
            temporary = path.with_suffix('.tmp')
            temporary.write_bytes(body)
            read_zip(temporary)  # Validate before making the cached file visible.
            temporary.replace(path)
        raw = read_zip(path)
        d = normalize(raw)
        d['source_file_year'] = year
        frames.append(d)
        sources.append({'file_year':year, 'sequence':seq, 'catalog_modified':modified,
            'sha256':hashlib.sha256(path.read_bytes()).hexdigest(), 'raw_rows':len(raw),
            'pure_apartment_jeonse_rows':len(d), 'contract_types':d.contract_type.value_counts().to_dict(),
            'contract_min':str(d.date.min().date()), 'contract_max':str(d.date.max().date())})
        print(json.dumps(sources[-1], ensure_ascii=False), flush=True)
    result = pd.concat(frames, ignore_index=True)
    result.to_parquet(out/'seoul_jeonse.parquet', index=False)
    meta = {'catalog':CATALOG,'license':'공공누리 제1유형 (출처표시)',
        'retrieved_at':datetime.now(timezone.utc).isoformat(), 'sources':sources,
        'availability':'Current downloadable vintage; contract date and receipt year are not first publication timestamps.',
        'rows':len(result),'identical_normalized_rows':int(result.duplicated().sum()),
        'deduplication':'No stable contract ID. Identical rows are retained; file years are ingested once.'}
    (out/'seoul_jeonse_sources.json').write_text(json.dumps(meta, ensure_ascii=False, indent=2))
    return result, meta


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--cache', type=Path, default=Path('.work/external-inspect'))
    p.add_argument('--out', type=Path, default=Path('.work/market-research'))
    p.add_argument('--start', type=int, default=2021)
    p.add_argument('--end', type=int, default=2025)
    args = p.parse_args()
    collect(args.cache,args.out,range(args.start,args.end+1))
