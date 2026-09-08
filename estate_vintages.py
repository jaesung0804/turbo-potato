"""As-observed transaction vintages; never pretend collection time is publication time."""
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json


def row_id(row):
    return hashlib.sha256(json.dumps(row,sort_keys=True,ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def utc_timestamp(value):
    at=datetime.fromisoformat(value)
    if at.tzinfo is None:raise ValueError('Observation timestamps require a timezone')
    return at.astimezone(timezone.utc).isoformat()


def advance(previous, rows, observed_at):
    """Keep count changes for indistinguishable public rows, including removals.

    The source has no stable transaction ID. A correction is a removed row plus
    a new row; we do not invent a physical-unit link. Every completed poll keeps
    its precise timestamp, even when the public rows did not change.
    """
    observed_at=utc_timestamp(observed_at)
    ledger=json.loads(json.dumps(previous)) if previous else {'schema_version':2,'baseline_at':observed_at,'records':{},
        'observation_times':[observed_at],'observation_times_complete_since':observed_at}
    latest=max([ledger['baseline_at']]+ledger.get('observation_times',[])+
        [r['changes'][-1][0] for r in ledger['records'].values()],key=datetime.fromisoformat)
    if datetime.fromisoformat(observed_at)<datetime.fromisoformat(latest):raise ValueError('Observation revisions must advance in time')
    if 'observation_times' not in ledger:
        # Old ledgers omitted unchanged polls. Preserve only known complete polls;
        # never invent daily polling history while upgrading a schema-1 archive.
        ledger['observation_times']=[ledger['baseline_at']]
        ledger['observation_times_complete_since']=observed_at
    ledger['schema_version']=2
    if observed_at not in ledger['observation_times']:
        ledger['observation_times'].append(observed_at)
    counts=Counter(row_id(r) for r in rows)
    values={row_id(r):r for r in rows}
    for key in sorted(set(ledger['records'])|set(counts)):
        rec=ledger['records'].get(key)
        count=counts.get(key,0)
        if rec is None:
            ledger['records'][key]={'row':values[key],'changes':[[observed_at,count]]}
        elif rec['changes'][-1][1]!=count:
            if datetime.fromisoformat(observed_at)<=datetime.fromisoformat(rec['changes'][-1][0]):raise ValueError('Observation revisions must advance in time')
            rec['changes'].append([observed_at,count])
    return ledger


def observe_partition(previous,rows,observed_at):
    ledger=previous.get('observation_ledger') if previous else None
    if not ledger and previous and previous.get('fetched_at'):
        ledger=advance(None,previous['rows'],previous['fetched_at'])
    return advance(ledger,rows,observed_at)


def rows_as_observed(ledger,as_of):
    """Return exactly the row multiplicity known at our last poll by as_of."""
    as_of=utc_timestamp(as_of)
    if as_of<ledger['baseline_at']:raise ValueError('No archived observation exists at this time')
    result=[]
    for key,rec in ledger['records'].items():
        if row_id(rec['row'])!=key:raise ValueError('Observation identity checksum mismatch')
        known=[n for at,n in rec['changes'] if at<=as_of]
        if known:result.extend([dict(rec['row'])]*known[-1])
    return result
