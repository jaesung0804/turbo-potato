"""Real HTTP round trips verify the vendorable client against the server."""
import hashlib
import socket
import threading
import time
import pytest
import uvicorn
from research_backend.api import create_app
from research_backend.config import Settings
from research_backend.database import Databases
from research_backend.objects import LocalObjects
from research_backend.client import Client, BackendError


@pytest.fixture
def live_backend(tmp_path):
    settings = Settings(tmp_path / 'runtime')
    databases = Databases(settings)
    databases.initialize()
    tokens = {'estate': 'e' * 48, 'investment': 'i' * 48}
    app = create_app(settings, databases, LocalObjects(settings.data_dir / 'objects'), tokens)
    listener = socket.socket()
    listener.bind(('127.0.0.1', 0))
    port = listener.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, access_log=False, log_level='error'))
    thread = threading.Thread(target=server.run, kwargs={'sockets': [listener]}, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(.02)
    assert server.started
    try:
        yield f'http://127.0.0.1:{port}', tokens
    finally:
        server.should_exit = True
        thread.join(10)
        listener.close()
        assert not thread.is_alive()


def test_photo_snapshot_roundtrip_and_stale_writer(live_backend, tmp_path):
    url, tokens = live_backend
    client = Client(url, tokens['estate'], 'estate')
    a, b = tmp_path / 'writer-a', tmp_path / 'writer-b'
    a.mkdir()
    photo = a / 'photo.bin'
    photo.write_bytes(b'\x89PNG' + b'fixture' * 50000)
    original = hashlib.sha256(photo.read_bytes()).hexdigest()
    first = client.push('estate-photos', a, ['photo.bin'])
    assert first['uploaded'] == 1
    assert client.push('estate-photos', a, ['photo.bin'])['changed'] is False
    assert client.pull('estate-photos', b)['downloaded'] == 1
    assert hashlib.sha256((b / 'photo.bin').read_bytes()).hexdigest() == original
    photo.write_bytes(b'revised photo')
    client.push('estate-photos', a, ['photo.bin'])
    with pytest.raises(BackendError) as error:
        client.push('estate-photos', b, ['photo.bin'])
    assert error.value.status == 409


def test_document_requires_read_before_edit_and_conflict(live_backend):
    url, tokens = live_backend
    a = Client(url, tokens['investment'], 'investment')
    b = Client(url, tokens['investment'], 'investment')
    path = 'data/reference/example.json'
    assert a.write_json(path, {'count': 1})['version'] == 1
    with pytest.raises(BackendError) as error:
        b.write_json(path, {'count': 2})
    assert error.value.status == 409
    assert b.read_json(path) == {'count': 1}
    assert a.write_json(path, {'count': 2})['version'] == 2
    with pytest.raises(BackendError) as error:
        b.write_json(path, {'count': 3})
    assert error.value.status == 409
