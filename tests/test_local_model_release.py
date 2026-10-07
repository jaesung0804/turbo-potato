import gzip
import hashlib
import io
import json
import zipfile

import pytest

from dashboard_bundle import asset
from estate_io import write_json
from refresh_saved_model import update
from local_site_release import unpack


def test_sorted_model_output_is_joined_by_identity_not_position(tmp_path, monkeypatch):
    source = tmp_path/'raw.csv'; source.write_text('verified source')
    write_json(source.with_suffix('.manifest.json'), {'complete': True, 'rows': 2,
        'sha256': hashlib.sha256(source.read_bytes()).hexdigest()})
    fields = ['current_valuation']
    (tmp_path/'data/bundle').mkdir(parents=True)
    packed = {'fields': fields, 'metadata': {'model_month': '2026-10'},
              'rows': [[0, '111', {'price_billion': 10}], [1, '222', {'price_billion': 20}]]}
    manifest = {'catalog': asset(tmp_path/'data/bundle', 'catalog', {'addresses': [{'key': 'A'}, {'key': 'B'}]}),
        'recommendations': asset(tmp_path/'data/bundle', 'recommendations', packed), 'release_status': {}, 'ui_assets': {}}
    write_json(tmp_path/'data/dashboard_manifest.json', manifest)
    def attach(model, _):
        for r in model['recommendations']:
            r['current_valuation']['price_billion'] += 1
        model['recommendations'].reverse()
        model['nowcast'] = {'version': 'tested', 'available_types': 2}
    monkeypatch.setattr('refresh_saved_model.attach_nowcast', attach)
    result = update(tmp_path, source, 'code')
    out = json.loads(gzip.decompress((tmp_path/result['recommendations']['url']).read_bytes()))
    assert [r[2]['price_billion'] for r in out['rows']] == [11, 21]
    assert result['release_status']['model_refresh']['changed_point_prices'] == 2


def test_local_release_rejects_bad_hash_and_path_before_extracting(tmp_path):
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('../escape', 'not allowed')
    body = b.getvalue()
    with pytest.raises(ValueError, match='체크섬'):
        unpack(body, '0'*64, tmp_path/'site', 'commit')
    with pytest.raises(ValueError, match='경로'):
        unpack(body, hashlib.sha256(body).hexdigest(), tmp_path/'site', 'commit')
    assert not (tmp_path/'escape').exists()


def test_model_cache_requires_hash_before_loading(tmp_path, monkeypatch):
    from estate_model_artifact import active_nowcast
    monkeypatch.chdir(tmp_path)
    path = tmp_path/'models/nowcast/frozen.joblib'
    path.parent.mkdir(parents=True); path.write_bytes(b'wrong')
    write_json(tmp_path/'metadata/nowcast_quantiles_2026.json',
        {'artifact': 'models/nowcast/frozen.joblib', 'artifact_bytes': 5, 'sha256': '0'*64})
    with pytest.raises(ValueError, match='checksum'):
        active_nowcast()
