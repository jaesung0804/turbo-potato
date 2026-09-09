"""Array-based reproduction of monthly nowcast features for long histories.

The equations and inclusive/exclusive windows match estate_nowcast. Only the
execution is changed; historical contracts additionally respect the declared
61/31-day availability policy. No full-sample price standardization is used.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from estate_nowcast import HALF_LIVES, load_transactions, weighted_median
from normalize_molit_capital_history import CompleteWriter

LAW_DAY = int(np.datetime64('2020-02-21', 'D').astype(int))


def save_parquet(frame, path):
    """Avoid partial native filesystem writes, and verify the Parquet footer."""
    import pyarrow.parquet as pq
    path = Path(path)
    with CompleteWriter(path.open('wb', buffering=0)) as stream:
        frame.to_parquet(stream, index=False, compression='zstd')
    if pq.ParquetFile(path).metadata.num_rows != len(frame):
        raise OSError('Incomplete Parquet output')


def array_history(values, origin_day, year, lag_days, policy=False):
    days, prices, floors = values
    end = np.searchsorted(days, origin_day - lag_days, side='right')
    days, prices, floors = days[:end], prices[:end], floors[:end]
    if policy:
        available = days + np.where(days < LAW_DAY, 61, 31) <= origin_day
        days, prices, floors = days[available], prices[available], floors[available]
    ages = origin_day - days
    result = {}
    for n in (30, 90, 180, 365):
        recent = prices[ages <= lag_days + n]
        result[f'n{n}'] = len(recent)
        result[f'median{n}'] = np.median(recent) if len(recent) else np.nan
    recent = ages <= lag_days + 365
    p, a = prices[recent], ages[recent]
    for h in HALF_LIVES:
        result[f'ew{h}'] = weighted_median(p, np.exp2(-a / h))
    w = np.exp2(-a / 90)
    near = ages <= lag_days + 90
    f = floors[near]
    finite_floor = f[np.isfinite(f)]
    result.update(eff90=float(w.sum() ** 2 / (w @ w)) if len(w) else 0.,
                  mass90=float(w.sum()), last=float(prices[-1]) if len(prices) else np.nan,
                  last_age=float(ages[-1]) if len(ages) else np.nan,
                  spread90=float(np.diff(np.quantile(prices[near], [.25, .75]))[0]) if near.sum() >= 2 else np.nan,
                  active_days90=len(np.unique(days[near])),
                  hist_floor=float(np.median(finite_floor)) if len(finite_floor) else np.nan)
    start = int(np.datetime64(f'{year-1}-01-01', 'D').astype(int))
    end_year = int(np.datetime64(f'{year}-01-01', 'D').astype(int))
    annual = prices[(days >= start) & (days < end_year)]
    result['prior_annual'] = float(np.median(annual)) if len(annual) else np.nan
    result['concentration'] = result['n30'] / max(result['n90'], 1)
    result['activity_ratio'] = ((result['n90'] + 1) / 90) / ((result['n365'] - result['n90'] + 1) / 275)
    return result


def monthly_features(d, first, last, *, policy=True, uniform_lag=None, months=None):
    ordered = d.sort_values(['key', 'day'], kind='stable')
    groups = {key: (g.day.to_numpy(), g.log_price.to_numpy(), g.floor.to_numpy())
              for key, g in ordered.groupby('key', sort=False)}
    del ordered
    periods = pd.period_range(first, last, freq='M')
    parts = []
    for period in periods:
        if period.month < 3 or (months is not None and period.month not in months):
            continue
        origin = period.start_time
        day = int(origin.to_datetime64().astype('datetime64[D]').astype(int))
        lag = uniform_lag if uniform_lag is not None else (61 if day - 31 < LAW_DAY else 31)
        target = d[d.month.eq(str(period))]
        if target.empty:
            continue
        mask = (d.day <= day-lag) & (d.day > day-lag-365)
        if policy:
            mask &= d.day + np.where(d.day < LAW_DAY, 61, 31) <= day
        past = d[mask]
        if past.empty:
            continue
        pools = {(col, n): past[past.day > day-lag-n].groupby(col).log_price.agg(['median','count'])
                 for col in ('complex','peer') for n in (90,365)}
        records = []
        metadata = target.drop_duplicates('key')[['key','complex','peer']]
        for row in metadata.itertuples(index=False):
            f = array_history(groups[row.key], day, period.year, lag, policy)
            for col in ('complex', 'peer'):
                for n in (90,365):
                    pool, identity = pools[(col,n)], getattr(row,col)
                    if identity in pool.index:
                        value = pool.loc[identity]
                        f[f'{col}{n}'],f[f'{col}_n{n}'] = value['median'],value['count']
                    else:
                        f[f'{col}{n}'],f[f'{col}_n{n}'] = np.nan,0
            f['anchor'] = next((f[c] for c in ('ew90','complex90','complex365','peer90','peer365')
                                if np.isfinite(f[c])), np.nan)
            for c in [*[f'ew{h}' for h in HALF_LIVES],'last','median90','median365','prior_annual']:
                if not np.isfinite(f[c]): f[c] = f['anchor']
            f['key'] = row.key
            records.append(f)
        block = target[['key','complex','region','gu','area','built','floor','date','log_price','price_oku']].merge(
            pd.DataFrame(records), on='key', how='left', validate='many_to_one')
        block['age'] = period.year - block.built
        block['floor_delta'] = block.floor - block.hist_floor
        block['low_floor'] = np.where(block.floor.notna(), (block.floor <= 2).astype(float), np.nan)
        block['actual'] = block.pop('log_price')
        block['contract_date'] = block.pop('date').dt.strftime('%Y-%m-%d')
        block['year'],block['month'],block['lag_days'] = period.year,str(period),lag
        block['feature_cutoff'] = str((origin-pd.Timedelta(days=lag)).date())
        parts.append(block.drop(columns=['built','hist_floor']))
        print('monthly features',period,'transactions',len(block),flush=True)
    return pd.concat(parts,ignore_index=True) if parts else pd.DataFrame()


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source',required=True)
    p.add_argument('--output',required=True)
    p.add_argument('--first',default='2007-03')
    p.add_argument('--last',default='2026-07')
    a=p.parse_args()
    target=Path(a.output)
    if target.exists(): raise FileExistsError(target)
    d,quality=load_transactions(a.source)
    keep=['key','complex','region','gu','peer','area','built','floor','date','day','month','year',
          'log_price','price_oku','STDG_NM','CGG_NM']
    d=d[keep]
    target.parent.mkdir(parents=True,exist_ok=True)
    save_parquet(d, target.parent/'transactions.parquet')
    features=monthly_features(d,a.first,a.last)
    save_parquet(features, target)
    manifest={'source':quality,'rows':len(features),'first':a.first,'last':a.last,
              'feature_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),
              'implementation_sha256':hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              'availability_policy':'historical_61_31','normalization':'log nominal 10000 KRW per sqm; no fitted scaler',
              'january_february':'excluded to preserve original validation design'}
    target.with_suffix('.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2))
    print(json.dumps(manifest,ensure_ascii=False),flush=True)


if __name__=='__main__': main()
