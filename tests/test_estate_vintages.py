import pytest
from estate_vintages import advance,observe_partition,rows_as_observed
A='2026-09-01T00:00:00+00:00'
B='2026-09-02T00:00:00+00:00'
C='2026-09-03T00:00:00+00:00'

def test_later_cancellation_does_not_rewrite_past_and_duplicates_survive():
    row={'price':'100','cancelled':''};cancel={**row,'cancelled':'20260902'}
    old=advance(None,[row,row],A)
    new=advance(old,[row,cancel],B)
    assert rows_as_observed(new,A)==[row,row]
    assert sorted(rows_as_observed(new,B),key=str)==sorted([row,cancel],key=str)
    unchanged=advance(new,[row,cancel],C)
    assert unchanged['records']==new['records']
    assert unchanged['observation_times']==[A,B,C]
    assert unchanged['observation_times_complete_since']==A
    with pytest.raises(ValueError,match='No archived'):rows_as_observed(new,'2026-08-01T00:00:00+00:00')

def test_existing_cache_is_a_known_observation_not_a_contract_date():
    row={'contract':'2024-01-01','price':'10'}
    x=observe_partition({'rows':[row],'fetched_at':A},[],B)
    assert rows_as_observed(x,A)==[row]
    assert rows_as_observed(x,B)==[]
    assert x['baseline_at']==A


def test_observation_times_are_compared_in_utc():
    row={'price':'10'}
    x=advance(None,[row],'2026-09-01T09:00:00+09:00')
    assert x['baseline_at']==A
    assert rows_as_observed(x,A)==[row]
    with pytest.raises(ValueError,match='timezone'):advance(None,[row],'2026-09-01T09:00:00')
    with pytest.raises(ValueError,match='advance'):advance(x,[],'2026-09-01T08:00:00+09:00')


def test_unchanged_poll_preserves_exact_time_and_prevents_time_reversal():
    x=advance(None,[],A)
    x=advance(x,[],C)
    assert x['observation_times']==[A,C]
    with pytest.raises(ValueError,match='advance'):advance(x,[],B)
    assert advance(x,[],C)==x


def test_legacy_upgrade_does_not_fabricate_missing_unchanged_polls():
    row={'contract':'20260815','price':'10'}
    legacy=advance(None,[row],A)
    legacy['schema_version']=1
    del legacy['observation_times']
    del legacy['observation_times_complete_since']
    new=advance(legacy,[row],C)
    assert new['schema_version']==2
    assert new['observation_times']==[A,C]
    assert new['observation_times_complete_since']==C
    assert rows_as_observed(new,B)==[row]
