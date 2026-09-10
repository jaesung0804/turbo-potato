"""Serve a saved release; rebuild only when explicitly requested."""
import argparse
import functools
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, default=Path('data/capital_area_apt_trade_transactions.csv'))
    p.add_argument('--site-dir', type=Path, default=Path('.work/site'))
    p.add_argument('--skip-build', action='store_true')
    p.add_argument('--rebuild', action='store_true', help='Explicitly rebuild data/models before serving')
    p.add_argument('--port', type=int, default=8000)
    p.add_argument('--no-browser', action='store_true')
    a = p.parse_args()
    if a.rebuild and a.skip_build:
        p.error('--rebuild and --skip-build cannot be combined')
    if a.rebuild:
        from build_public_site import build
        build(a.input, a.site_dir)
    if not (a.site_dir/'data/dashboard_manifest.json').exists():
        raise FileNotFoundError('No saved release. Run with --rebuild once; ordinary serving never rebuilds or trains.')
    server = ThreadingHTTPServer(('127.0.0.1', a.port), functools.partial(SimpleHTTPRequestHandler, directory=str(a.site_dir)))
    url = f'http://127.0.0.1:{a.port}/'
    print(url, flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
