"""Bounded deployment verification; never collects source data or trains models."""
import argparse
import hashlib
import json
from pathlib import Path
import tempfile
from research_backend_client import Client, BackendError

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--project', required=True, choices=('estate', 'investment'))
    args = parser.parse_args()
    try:
        client = Client(project=args.project)
        ready = client.json('GET', '/ready')
        assert ready['database'] == 'oracle' and ready['blob_store'] == 'oci'
        snapshot = 'estate-raw-state' if args.project == 'estate' else 'pipeline-state'
        assert client.json('GET', '/snapshot-heads/' + snapshot)['snapshot_id']
        assert client.json('GET', '/datasets?limit=2')['items']
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'deployment-verification.txt'
            restored = Path(folder) / 'restored.txt'
            body = ('GitHub Actions backend verification v1: ' + args.project + '\n').encode()
            source.write_bytes(body)
            info = client.upload(source)
            client.download(info['sha256'], restored, len(body))
            assert restored.read_bytes() == body
            assert hashlib.sha256(body).hexdigest() == info['sha256']
        print(json.dumps({'project': args.project, 'database': 'oracle', 'files': 'oci',
            'snapshot_present': True, 'datasets_present': True, 'small_file_roundtrip': True}))
        return 0
    except Exception as error:
        detail = {'error_type': type(error).__name__}
        if isinstance(error, BackendError): detail['http_status'] = error.status
        print(json.dumps(detail))
        return 1

if __name__ == '__main__':
    raise SystemExit(main())
