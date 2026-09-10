"""Bounded agent memory access. Write individual records, not full repository histories."""
import argparse
import json
from pathlib import Path
import urllib.parse
from research_backend_client import Client


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('command', choices=['list', 'get', 'put'])
    p.add_argument('--kind', required=True)
    p.add_argument('--key')
    p.add_argument('--input', type=Path)
    p.add_argument('--expected-version', type=int)
    p.add_argument('--summary', default='')
    p.add_argument('--after', default='')
    p.add_argument('--limit', type=int, default=20)
    args = p.parse_args()
    client = Client(project='estate')
    base = '/records/' + urllib.parse.quote(args.kind, safe='')
    if args.command == 'list':
        result = client.json('GET', base + '?' + urllib.parse.urlencode({'after': args.after, 'limit': args.limit}))
    else:
        if not args.key:
            p.error('--key required')
        base += '/' + urllib.parse.quote(args.key, safe='')
        if args.command == 'get':
            result = client.json('GET', base)
        else:
            if args.input is None or args.expected_version is None:
                p.error('--input and --expected-version required')
            result = client.json('PUT', base, {'payload': json.loads(args.input.read_text(encoding='utf-8-sig')),
                'expected_version': args.expected_version, 'summary': args.summary})
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    main()
