"""Atomic streaming output for large JSON and binary data."""
import json
from pathlib import Path


def write_json(path, value, indent=None):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp')
    with temp.open('w', encoding='utf-8', newline='\n') as stream:
        json.dump(value, stream, ensure_ascii=False, allow_nan=False,
                  indent=indent, separators=(',', ':') if indent is None else None)
    temp.replace(path)


def write_binary(path, body):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name+'.tmp')
    with temp.open('wb') as stream:
        for offset in range(0, len(body), 8*1024*1024):
            chunk = memoryview(body)[offset:offset+8*1024*1024]
            while chunk:
                written = stream.write(chunk)
                if not written:
                    raise OSError('Incomplete binary write')
                chunk = chunk[written:]
    if temp.stat().st_size != len(body):
        raise OSError('Incomplete binary file')
    temp.replace(path)
