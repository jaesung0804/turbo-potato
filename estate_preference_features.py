"""Auditable preference features for a separate retrospective experiment.

Structural and coordinate backcasts are assumptions, never certified as-of data.
Unknown facilities remain missing. Counts are inventories, not transaction counts.
"""
import csv
import gzip
import hashlib
import json
import math
import re
from pathlib import Path
import numpy as np
from sklearn.neighbors import BallTree

BRANDS = {
    'raemian': ['래미안', '레미안'], 'lotte_castle': ['롯데캐슬'],
    'hillstate': ['힐스테이트'], 'ipark': ['아이파크', '아이파크'],
    'xi': ['자이'], 'prugio': ['푸르지오'], 'the_sharp': ['더샵', '더샾'],
    'elife': ['이편한세상', 'e편한세상'], 'dongmun': ['동문굿모닝힐', '동문디이스트', '동문'],
    'woomi': ['우미린'], 'hoban': ['호반써밋', '호반베르디움', '호반'],
    'desian': ['데시앙'], 'sujain': ['수자인'], 'harrington': ['해링턴'],
    'hyundai_legacy': ['현대'], 'samsung_legacy': ['삼성'], 'lotte_legacy': ['롯데'],
}


def normalized(value):
    return re.sub(r'[^0-9a-z가-힣]', '', str(value).lower())


def brand(name):
    text = normalized(name)
    matches = [key for key, tokens in BRANDS.items() if any(normalized(t) in text for t in tokens)]
    modern = [m for m in matches if not m.endswith('_legacy')]
    matches = modern or matches
    return '+'.join(sorted(matches)) if matches else 'unidentified'


def numeric(value):
    try:
        v = float(str(value).replace(',', ''))
        return v if math.isfinite(v) else None
    except (TypeError, ValueError):
        return None


def address(value):
    value=re.sub(r'\s+', ' ', value.replace('번지', '').strip())
    # The 2024 master mixes "성남분당구" and "성남시 분당구", and
    # sometimes appends the apartment name after the lot. Preserve the lot.
    value=re.sub(r'(수원|성남|안양|안산|고양|용인|부천)시\s+(\S+구)',r'\1\2',value)
    match=re.match(r'^(.*?(?:동|리|가)\s+(?:산\s*)?\d+(?:-\d+)?)(?:\s.*)?$',value)
    return match.group(1) if match else value


def read_csv(path):
    for encoding in ['utf-8-sig', 'cp949']:
        try:
            with path.open(encoding=encoding, newline='') as stream:
                return list(csv.DictReader(stream))
        except UnicodeDecodeError:
            continue
    raise ValueError('Unsupported CSV encoding: '+str(path))


def load_master(path):
    result = {}
    for r in read_csv(path):
        if r.get('rbld_yn') == '1':
            continue
        key = address(r.get('lnno_adres', ''))
        if key:
            result.setdefault(key, []).append(r)
    return result


def match_master(master, region, building):
    name = building['building_name']
    suffix = ' '+name
    key = building['complex_key']
    if not key.endswith(suffix):
        return None
    location = address(region['sido_name']+' '+key[:-len(suffix)])
    rows = [r for r in master.get(location, [])
            if numeric(r.get('use_aprv_yr')) == numeric(building.get('built_year'))
            and normalized(r.get('apt_nm', '')) == normalized(name)]
    identities = {r['apt_cd'] for r in rows}
    return rows[0] if len(identities) == 1 else None


def tree_nearest(coords, facilities):
    if not len(facilities):
        return np.full(len(coords), np.nan)
    tree = BallTree(np.radians(np.array(facilities, dtype=float)), metric='haversine')
    distances, _ = tree.query(np.radians(coords), k=1)
    return distances[:, 0]*6371000


def active_events(events, cutoff, status):
    """Both event occurrence and publication must precede prediction time."""
    latest = {}
    for e in events:
        if not e.get('source_url') or not e.get('known_at') or not e.get('effective_at'):
            continue
        if e['known_at'] > cutoff or e['effective_at'] > cutoff:
            continue
        key = e['station_id']
        if key not in latest or (e['effective_at'], e['known_at']) > (latest[key]['effective_at'], latest[key]['known_at']):
            latest[key] = e
    return [e for e in latest.values() if e['status'] == status]


def area_inventory(total, counts, area_sqm):
    """K-APT bands are not exact 84.91-square-metre inventories."""
    values = [numeric(v) for v in counts]
    total = numeric(total)
    if total is None or total <= 0 or any(v is None or v < 0 for v in values) or sum(values) != total:
        return None
    index = 0 if area_sqm <= 60 else 1 if area_sqm <= 85 else 2 if area_sqm <= 135 else 3
    return {'band_households': values[index], 'band_share': values[index]/total}


def prepare_features(summary, payload, source_dir, station_file=None):
    master_path = source_dir/'apt_mst_info_202410.csv'
    school_path = next(source_dir.glob('*학교*csv'))
    master = load_master(master_path)
    school_rows = read_csv(school_path)
    schools = [r for r in school_rows if r['학교급구분'] == '초등학교' and r['운영상태'] == '운영'
               and re.fullmatch(r'\d{4}-\d{2}-\d{2}', r['설립일자'])
               and numeric(r['위도']) and numeric(r['경도'])]
    by_type = {(r['code'], b['key']): (r, b) for r in summary['regions'] for b in r['all']['addresses']}
    match_cache = {}
    result = []
    for p in payload:
        r, b = by_type[p['region_code'], p['building_key']]
        key = (r['code'], b['complex_key'])
        if key not in match_cache:
            match_cache[key] = match_master(master, r, b)
        matched = match_cache[key]
        households = numeric(matched.get('nmhsh')) if matched else None
        lat = numeric(matched.get('la')) if matched else None
        lon = numeric(matched.get('lo')) if matched else None
        result.append({'brand_name': brand(p['building_name']),
            'log_households': math.log1p(households) if households and households>0 else np.nan,
            'latitude': lat if lat and lon else np.nan, 'longitude': lon if lat and lon else np.nan,
            'school_log_distance': np.nan, 'school_within_500m': np.nan,
            'transit_log_distance': np.nan})
    import pandas as pd
    frame = pd.DataFrame(result)
    years = np.array([int(p['year']) for p in payload])
    valid = frame.latitude.notna() & frame.longitude.notna()
    for year in sorted(set(years)):
        indices = frame.index[valid & (years==year)]
        coordinates = frame.loc[indices, ['latitude', 'longitude']].to_numpy()
        eligible = [(float(r['위도']), float(r['경도'])) for r in schools if r['설립일자'] < f'{year}-01-01']
        if len(indices):
            d = tree_nearest(coordinates, eligible)
            frame.loc[indices, 'school_log_distance'] = np.log1p(d)
            frame.loc[indices, 'school_within_500m'] = (d <= 500).astype(float)
    if station_file:
        station = json.loads(station_file.read_text(encoding='utf-8'))
        for year in sorted(set(years)):
            events = active_events(station['events'], f'{year}-01-01', 'operating')
            indices = frame.index[valid & (years==year)]
            if events and len(indices):
                d=tree_nearest(frame.loc[indices,['latitude','longitude']].to_numpy(),[(e['latitude'],e['longitude']) for e in events])
                # This is a verified subset of the network: far away is unknown, not no station.
                frame.loc[indices,'transit_log_distance']=np.where(d<=2000,np.log1p(d),np.nan)
    coverage = {str(year): {c: round(float(frame.loc[years==year,c].notna().mean()),4)
                for c in ['log_households','school_log_distance','transit_log_distance']}
                for year in sorted(set(years))}
    provenance = {'master_sha256': hashlib.sha256(master_path.read_bytes()).hexdigest(),
        'station_sha256': hashlib.sha256(station_file.read_bytes()).hexdigest() if station_file else None,
        'school_sha256': hashlib.sha256(school_path.read_bytes()).hexdigest(),
        'master_match': 'exact full lot address, normalized name and construction year; reject ambiguous IDs and reconstruction flags',
        'coverage': coverage, 'brand_counts': frame.brand_name.value_counts().to_dict(),
        'temporal_status': 'retrospective reconstruction: 2024-labelled structural snapshot and current school coordinates projected backwards; establishment dates gated; historical moves/closures/name changes are not fully known',
        'school_meaning': 'distance to an established current elementary-school point, not catchment or safe walking route',
        'transit_meaning': 'straight-line distance <=2km to a date-verified subset of the metro network; other coverage remains missing'}
    return frame.drop(columns=['latitude','longitude']), provenance
