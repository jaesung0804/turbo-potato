import gzip
import hashlib
import json
import pytest
from estate.models.potential.v2.local_release import choose
from estate.models.potential.quantile_v3.train import mature_train
import pandas as pd

def test_calibration_cannot_train_on_labels_from_its_future():
    f=pd.DataFrame({'origin':['2017-01-01','2018-01-01'],'label_available':['2019-02-01','2020-02-01'],'target':[.1,.2],'complex':['a','b']})
    assert mature_train(f,'2020-01-01').origin.tolist()==['2017-01-01']

def test_fixed_local_cohort_is_checked_and_future_monthly_cohort_wins(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path)
    payload={'origin':'2026-10-01','model':'v2','latest_training_label_available':'2026-08-01','rows':[{'key':'a','research_rank':1}],'cohort_size':1}
    body=gzip.compress(json.dumps(payload).encode());spec=tmp_path/'spec.json'
    spec.write_text(json.dumps({'origin':payload['origin'],'model':'v2','url':'https://github.com/jaesung0804/turbo-potato/releases/download/estate-quantiles-test/potential-public.json.gz','sha256':hashlib.sha256(body).hexdigest(),'bytes':len(body)}))
    assert choose({'origin':'2026-09-01'},spec,lambda url:body)==payload
    later={'origin':'2026-11-01'}
    assert choose(later,spec,lambda url:pytest.fail('Do not replace a newer monthly forecast')) is later
    next((tmp_path/'models/potential').glob('*.gz')).write_bytes(b'corrupt')
    with pytest.raises(ValueError,match='체크섬'):
        choose({'origin':'2026-09-01'},spec)
