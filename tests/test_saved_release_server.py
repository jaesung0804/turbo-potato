"""A normal website view must not import or run the data/model build stack."""
import builtins
import http.server
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / 'run_real_estate_dashboard.py'


def run_server(monkeypatch, site, flags, events):
    original_import = builtins.__import__
    def build(source, output):
        events.append(('build', source, output))
        (output / 'data').mkdir(parents=True, exist_ok=True)
        (output / 'data/dashboard_manifest.json').write_text('{}')
    def importing(name, *args, **kwargs):
        if name == 'build_public_site':
            events.append(('import_build',))
            return SimpleNamespace(build=build)
        return original_import(name, *args, **kwargs)
    class Server:
        def __init__(self, address, handler):
            events.append(('serve', address, handler.keywords['directory']))
        def serve_forever(self):
            raise KeyboardInterrupt
        def server_close(self):
            events.append(('closed',))
    monkeypatch.setattr(builtins, '__import__', importing)
    monkeypatch.setattr(http.server, 'ThreadingHTTPServer', Server)
    monkeypatch.setattr('sys.argv', [str(SCRIPT), '--site-dir', str(site), '--no-browser', *flags])
    runpy.run_path(str(SCRIPT), run_name='__main__')


@pytest.mark.parametrize('flags', [[], ['--skip-build']])
def test_saved_release_serves_without_importing_or_running_any_builder(tmp_path, monkeypatch, flags):
    (tmp_path / 'data').mkdir()
    manifest = tmp_path / 'data/dashboard_manifest.json'
    manifest.write_text('{"saved_release":true}')
    events = []
    run_server(monkeypatch, tmp_path, flags, events)
    assert events == [('serve', ('127.0.0.1', 8000), str(tmp_path)), ('closed',)]
    assert manifest.read_text() == '{"saved_release":true}'


def test_missing_saved_release_fails_without_collecting_or_training(tmp_path, monkeypatch):
    events = []
    with pytest.raises(FileNotFoundError, match='--rebuild'):
        run_server(monkeypatch, tmp_path, [], events)
    assert events == []


def test_explicit_rebuild_is_the_only_building_path(tmp_path, monkeypatch):
    events = []
    run_server(monkeypatch, tmp_path, ['--rebuild'], events)
    assert events[0] == ('import_build',)
    assert events[1] == ('build', Path('data/capital_area_apt_trade_transactions.csv'), tmp_path)
    assert events[2:] == [('serve', ('127.0.0.1', 8000), str(tmp_path)), ('closed',)]


def test_conflicting_legacy_and_explicit_flags_cannot_start_work(tmp_path, monkeypatch):
    events = []
    with pytest.raises(SystemExit) as error:
        run_server(monkeypatch, tmp_path, ['--rebuild', '--skip-build'], events)
    assert error.value.code == 2 and events == []
