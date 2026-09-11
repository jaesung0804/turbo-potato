"""Reject large research payloads in a code change before Git publication."""
import argparse
import json
import re
import subprocess
from pathlib import Path

MAX_FILE = 256 * 1024
MAX_CHANGE = 1024 * 1024
RAW_SUFFIXES = {'.csv', '.parquet', '.joblib', '.pkl', '.pickle', '.zip', '.tar', '.db', '.sqlite', '.sqlite3', '.wallet'}


def verify(paths):
    total = 0
    errors = []
    for name in paths:
        path = Path(name)
        if not path.is_file():
            continue
        size = path.stat().st_size
        total += size
        if size > MAX_FILE:
            errors.append(f'{name}: exceeds {MAX_FILE} byte code-file limit')
            continue
        if path.suffix.lower() in RAW_SUFFIXES or (path.suffix.lower() == '.gz' and not name.startswith('web/data/')):
            errors.append(f'{name}: research originals/checkpoints/models belong in DB/OCI')
            continue
        body = path.read_bytes()
        if re.search(rb'(?:ghp_|github_pat_)[A-Za-z0-9_]{24,}', body):
            errors.append(f'{name}: possible literal credential; value suppressed')
        if name.endswith('.py') and re.search(rb'(?im)^\s*(?:DEFAULT_KEY|[A-Z_]*(?:API_KEY|TOKEN|SECRET))\s*=\s*[\"\'][A-Za-z0-9_+/%=.-]{40,}[\"\']', body):
            errors.append(f'{name}: possible literal credential; value suppressed')
    if total > MAX_CHANGE:
        errors.append(f'change exceeds {MAX_CHANGE} byte total code/report budget')
    return {'files': len(paths), 'bytes': total, 'ready': not errors, 'errors': errors}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--paths-file', type=Path)
    args = parser.parse_args()
    if bool(args.base) == bool(args.paths_file):
        parser.error('Specify one of --base or --paths-file')
    if args.base:
        paths = subprocess.check_output(['git', 'diff', '--no-renames', '--name-only', '--diff-filter=ACMRT', args.base, 'HEAD', '--'], text=True).splitlines()
    else:
        paths = json.loads(args.paths_file.read_text())
    result = verify(paths)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['ready'] else 2)
