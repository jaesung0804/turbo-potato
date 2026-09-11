import gzip
import hashlib
import json

import pytest

from collect_estate_transactions import existing_key
from estate_ui_release import UI_FILES
from refresh_saved_site import refresh, safe_data_path


def test_credentials_require_private_environment(monkeypatch):
    monkeypatch.delenv('MOLIT_API_KEY', raising=False)
    with pytest.raises(RuntimeError, match='private execution environment'):
        existing_key()
    monkeypatch.setenv('MOLIT_API_KEY', 'private-runtime-fixture')
    assert existing_key() == 'private-runtime-fixture'


def saved_fixture():
    body = gzip.compress(b'{"unchanged":true}')
    sha = hashlib.sha256(body).hexdigest()
    validation = b'{"fixture":true}'
    asset = {'url': 'data/bundle/fixture.json.gz', 'bytes': len(body), 'sha256': sha}
    m = {'collection': {'complete': True, 'normalizer_version': 2, 'fetched_at': '2026-09-10'},
         'coverage': {'2026': {'complete': True}}, 'periods': {'2026': asset},
         **{key: asset for key in ['catalog', 'history', 'recommendations', 'map']},
         'code_commit': 'data-commit', 'release_id': 'old-release', 'generated_at': '2026-09-10',
         'ui_assets': {'data/model_validation.json': hashlib.sha256(validation).hexdigest()}}
    files = {'data/dashboard_manifest.json': json.dumps(m).encode(), asset['url']: body,
             'data/model_validation.json': validation}
    return m, files


def test_ui_refresh_preserves_data_and_provenance(tmp_path):
    old, files = saved_fixture()
    result = refresh('https://example.test/', tmp_path / 'site', 'ui-commit', reader=lambda p, n: files[p])
    for key in ['catalog', 'history', 'recommendations', 'map', 'periods', 'coverage', 'collection', 'generated_at']:
        assert result[key] == old[key]
    assert result['data_code_commit'] == 'data-commit'
    assert result['code_commit'] == 'ui-commit'
    for name in UI_FILES:
        assert (tmp_path / 'site' / name).is_file()
    html = (tmp_path / 'site/research.html').read_text()
    assert 'research.css?v=' in html
    assert 'aria-current="page"' in html


def test_ui_refresh_rejects_corrupt_saved_data(tmp_path):
    _, files = saved_fixture()
    files['data/bundle/fixture.json.gz'] = b'corrupt'
    with pytest.raises(ValueError, match='checksum mismatch'):
        refresh('https://example.test/', tmp_path / 'site', 'ui-commit', reader=lambda p, n: files[p])
    assert not (tmp_path / 'site/data/dashboard_manifest.json').exists()


@pytest.mark.parametrize('path', ['../private.json', 'data/../../private', '/data/x', 'https://x.test/data/a', '//x.test/a', 'data/a?x=1', 'data\\x'])
def test_public_release_cannot_escape_data_path(path):
    with pytest.raises(ValueError):
        safe_data_path(path)
