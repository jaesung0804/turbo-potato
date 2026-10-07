"""Keep publication time separate from transaction collection provenance."""
import os
from datetime import datetime, timezone


def attach_release_status(manifest, *, ui_only=False, environ=None, now=None):
    environ = os.environ if environ is None else environ
    now = now or datetime.now(timezone.utc).isoformat()
    previous = manifest.get('release_status', {}).get('transaction_refresh')
    if ui_only:
        refresh = dict(previous or {'status': 'unknown', 'attempted_at': None})
    else:
        status = environ.get('ESTATE_REFRESH_STATUS', 'unknown')
        if status not in {'completed', 'skipped_missing_key', 'not_requested', 'unknown'}:
            raise ValueError('Unknown transaction refresh status')
        attempted = environ.get('ESTATE_REFRESH_ATTEMPTED_AT')
        if status in {'completed', 'skipped_missing_key'}:
            if not attempted:
                raise ValueError('A refresh attempt requires an observation time')
            stamp = datetime.fromisoformat(attempted.replace('Z', '+00:00'))
            if stamp.tzinfo is None:
                raise ValueError('Refresh attempt time requires a timezone')
        refresh = {'status': status, 'attempted_at': attempted}
        if status == 'completed':
            collection = manifest.get('collection', {})
            collected = collection.get('fetched_at')
            try:
                observed = datetime.fromisoformat(collected.replace('Z', '+00:00'))
                valid = collection.get('complete') is True and observed.tzinfo is not None and observed >= stamp
            except (AttributeError, TypeError, ValueError):
                valid = False
            if not valid:
                raise ValueError('Completed refresh is not backed by a fresh complete collection')
            refresh['collection_sha256'] = collection.get('sha256')
    manifest['release_status'] = {
        'schema_version': 1, 'built_at': now, 'mode': 'ui_only' if ui_only else 'data_release',
        'transaction_refresh': refresh,
    }
    return manifest
