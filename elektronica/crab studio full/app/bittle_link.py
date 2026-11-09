"""Bittle Link: serve the standalone robot console without the simulator.

Uses only the Python standard library. Web Bluetooth and Web Serial need a secure context,
which the browser grants to http://127.0.0.1, so the console must be served rather than opened as a file.
Packs in the repository's saved-motions/ folder are listed and saved through /saved-motions/.

    python app/bittle_link.py [--port 8770] [--no-browser]
"""
import argparse
import functools
import http.server
import json
import webbrowser
from pathlib import Path

from packs import list_packs, pack_path, save_pack

APP = Path(__file__).resolve().parent
PAGE = APP / 'web' / 'bittle-link'
SAVED_MOTIONS = APP.parent / 'saved-motions'


class Handler(http.server.SimpleHTTPRequestHandler):
    # Windows' registry can map .js to text/plain, which browsers refuse for ES modules.
    extensions_map = {**http.server.SimpleHTTPRequestHandler.extensions_map,
                      '.js': 'text/javascript', '.css': 'text/css', '.html': 'text/html', '.json': 'application/json'}

    def end_headers(self):
        self.send_header('Cache-Control', 'no-store')
        super().end_headers()

    def log_message(self, format, *args):
        pass

    def send_json(self, data, status=200):
        body = json.dumps(data).encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = self.path.split('?', 1)[0]
        if path == '/saved-motions/index.json':
            return self.send_json({'packs': list_packs(SAVED_MOTIONS)})
        if path.startswith('/saved-motions/'):
            try:
                file = pack_path(SAVED_MOTIONS, path.removeprefix('/saved-motions/'))
            except ValueError as error:
                return self.send_json({'detail': str(error)}, 400)
            if not file.is_file():
                return self.send_json({'detail': 'Pack not found'}, 404)
            return self.send_json(json.loads(file.read_text(encoding='utf-8')))
        return super().do_GET()

    def do_POST(self):
        if self.path != '/saved-motions/save':
            return self.send_json({'detail': 'Not found'}, 404)
        # Only this machine's pages may write packs.
        if self.headers.get('Origin') not in (None, f'http://127.0.0.1:{self.server.server_port}', f'http://localhost:{self.server.server_port}'):
            return self.send_json({'detail': 'Forbidden origin'}, 403)
        try:
            length = int(self.headers.get('Content-Length', 0))
            if length > 3_000_000:
                raise ValueError('The pack is too large')
            body = json.loads(self.rfile.read(length) or b'{}')
            return self.send_json(save_pack(SAVED_MOTIONS, body.get('name', ''), body.get('pack')))
        except (ValueError, TypeError, AttributeError) as error:
            return self.send_json({'detail': str(error)}, 400)


def main():
    parser = argparse.ArgumentParser(description='Serve the standalone Bittle Link console.')
    parser.add_argument('--port', type=int, default=8770)
    parser.add_argument('--no-browser', action='store_true')
    args = parser.parse_args()
    SAVED_MOTIONS.mkdir(exist_ok=True)
    server = http.server.ThreadingHTTPServer(('127.0.0.1', args.port), functools.partial(Handler, directory=str(PAGE)))
    url = f'http://127.0.0.1:{args.port}/'
    print(f'Bittle Link console at {url}\nPacks folder: {SAVED_MOTIONS}\nKeep this window open. Press Ctrl+C to stop.')
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
