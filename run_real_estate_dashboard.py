"""Build and serve the same complete release used on GitHub Pages."""
import argparse
import functools
import webbrowser
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from build_public_site import build

if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, default=Path('data/capital_area_apt_trade_transactions.csv'))
    p.add_argument('--site-dir', type=Path, default=Path('.work/site'))
    p.add_argument('--skip-build', action='store_true')
    p.add_argument('--port', type=int, default=8000)
    p.add_argument('--no-browser', action='store_true')
    a = p.parse_args()
    if not a.skip_build:
        build(a.input, a.site_dir)
    if not (a.site_dir/'data/dashboard_manifest.json').exists():
        raise FileNotFoundError('Build the complete release first')
    server = ThreadingHTTPServer(('127.0.0.1', a.port), functools.partial(SimpleHTTPRequestHandler, directory=str(a.site_dir)))
    url = f'http://127.0.0.1:{a.port}/'
    print(url, flush=True)
    if not a.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.server_close()
