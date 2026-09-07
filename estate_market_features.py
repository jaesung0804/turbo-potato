"""Observed-at market features. Inputs are normalized, source-identified records.

These diagnostics are not assigned arbitrary weights in production valuation.
Missing history stays missing. Renewals do not substitute for new lease prices.
"""
import numpy as np
import pandas as pd


def observable(frame,as_of):
    d=frame.copy()
    if 'available_at' not in d:raise ValueError('available_at is required; contract dates are not publication dates')
    at=pd.to_datetime(d.available_at,utc=True,errors='raise')
    if at.isna().any():raise ValueError('Missing availability timestamp')
    d['available_at']=at
    return d[at<=pd.to_datetime(as_of,utc=True)].copy()


def latest_index_vintage(frame,as_of):
    """series_id includes source, geography, asset class, frequency and unit."""
    d=observable(frame,as_of)
    if not (np.isfinite(pd.to_numeric(d.value,errors='coerce')) & pd.to_numeric(d.value,errors='coerce').gt(0)).all():raise ValueError('Index levels must be positive')
    return d.sort_values('available_at').drop_duplicates(['series_id','period'],keep='last')


def lease_features(leases,sales,key,as_of,days=180):
    end=pd.to_datetime(as_of,utc=True);start=end-pd.Timedelta(days=days)
    def recent(frame):
        d=observable(frame,as_of);dates=pd.to_datetime(d.contract_date,utc=True)
        return d[d.key.eq(key)&dates.between(start,end)]
    rent=recent(leases);sale=recent(sales)
    rent=rent[rent.monthly_rent.eq(0)&rent.deposit.gt(0)]
    new=rent[rent.contract_type.eq('신규')]
    renewal=rent[rent.contract_type.eq('갱신')]
    valid=sale[sale.price.gt(0)&~sale.cancelled]
    deposit=float(new.deposit.median()) if len(new)>=3 else None
    price=float(valid.price.median()) if len(valid)>=3 else None
    return {'new_jeonse_count':len(new),'renewal_count':len(renewal),'unknown_contract_type_count':int((~rent.contract_type.isin(['신규','갱신'])).sum()),'new_jeonse_median':deposit,'sale_median':price,'new_jeonse_ratio':deposit/price if deposit and price else None}


def asking_features(snapshots,key,as_of,fair_price,lookback_days=28):
    d=observable(snapshots,as_of);d=d[d.key.eq(key)&d.price.gt(0)]
    now=pd.to_datetime(as_of,utc=True);previous=now-pd.Timedelta(days=lookback_days)
    def at(t):
        x=d[pd.to_datetime(d.available_at,utc=True)<=t].sort_values('available_at').drop_duplicates('listing_id',keep='last')
        return x[x.status.eq('active')].set_index('listing_id')
    current,old=at(now),at(previous)
    # Compare identical listing IDs; entries/exits do not masquerade as repricing.
    pairs=current[['price']].join(old[['price']],lsuffix='_new',rsuffix='_old',how='inner')
    changes=np.log(pairs.price_new/pairs.price_old)
    q25=float(current.price.quantile(.25)) if len(current)>=3 else None
    return {'active_listing_ids':len(current),'matched_listing_ids':len(pairs),'matched_28d_change':float(np.expm1(changes.median())) if len(pairs)>=3 else None,'price_cut_share':float((changes<0).mean()) if len(pairs)>=3 else None,'asking_q25_premium':q25/fair_price-1 if q25 and fair_price>0 else None,'identity_note':'listing IDs, not verified distinct homes; disappearance is not a confirmed sale'}
