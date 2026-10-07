"""Reject large research payloads in a code change before Git publication."""
import argparse
import json
import re
import subprocess
import difflib
from pathlib import Path

MAX_FILE = 256 * 1024
MAX_CHANGE = 1024 * 1024
RAW_SUFFIXES = {'.csv', '.parquet', '.joblib', '.pkl', '.pickle', '.zip', '.tar', '.db', '.sqlite', '.sqlite3', '.wallet'}


def verify(paths, renamed_from=None):
    total = 0
    errors = []
    for name in paths:
        path = Path(name)
        if not path.is_file():
            continue
        size = path.stat().st_size
        body = path.read_bytes()
        original = (renamed_from or {}).get(name)
        if original is None:
            total += size
        else:
            # Existing code moved into a package is not a new payload. Scan the
            # whole destination below, but count newly written lines once.
            old, new = original.splitlines(keepends=True), body.splitlines(keepends=True)
            total += sum(sum(map(len,new[b0:b1])) for op,a0,a1,b0,b1 in
                         difflib.SequenceMatcher(None,old,new,autojunk=False).get_opcodes()
                         if op in {'insert','replace'})
        if size > MAX_FILE:
            errors.append(f'{name}: exceeds {MAX_FILE} byte code-file limit')
            continue
        if path.suffix.lower() in RAW_SUFFIXES or (path.suffix.lower() == '.gz' and not name.startswith('web/data/')):
            errors.append(f'{name}: research originals/checkpoints/models belong in DB/OCI')
            continue
        if re.search(rb'(?:ghp_|github_pat_)[A-Za-z0-9_]{24,}', body):
            errors.append(f'{name}: possible literal credential; value suppressed')
        if name.endswith('.py') and re.search(rb'(?im)^\s*(?:DEFAULT_KEY|[A-Z_]*(?:API_KEY|TOKEN|SECRET))\s*=\s*[\"\'][A-Za-z0-9_+/%=.-]{40,}[\"\']', body):
            errors.append(f'{name}: possible literal credential; value suppressed')
    if total > MAX_CHANGE:
        errors.append(f'change exceeds {MAX_CHANGE} byte total code/report budget')
    return {'files': len(paths), 'bytes': total, 'ready': not errors, 'errors': errors}


def changed_paths(base):
    records = subprocess.check_output(['git','diff','--find-renames','--name-status','-z',
                                       '--diff-filter=ACMRT',base,'HEAD','--']).decode('utf-8').split('\0')
    paths, renamed = [], {}
    i=0
    while i<len(records) and records[i]:
        status,name=records[i:i+2];i+=2
        if status.startswith(('R','C')):
            previous,name=name,records[i];i+=1
            if status.startswith('R'):
                renamed[name]=subprocess.check_output(['git','show',f'{base}:{previous}'])
        paths.append(name)
    return paths,renamed


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base')
    parser.add_argument('--paths-file', type=Path)
    args = parser.parse_args()
    if bool(args.base) == bool(args.paths_file):
        parser.error('Specify one of --base or --paths-file')
    if args.base:
        paths, renamed = changed_paths(args.base)
    else:
        paths = json.loads(args.paths_file.read_text(encoding='utf-8'))
        renamed = {}
    result = verify(paths, renamed)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result['ready'] else 2)
