"""Shared offline fixtures; production modules remain self-contained in the skill."""
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / 'skills' / 'camofox-client'
SCRIPTS = SKILL / 'scripts'
sys.path.insert(0, str(SCRIPTS))


def clean_env():
    return {key: value for key, value in os.environ.items()
            if not key.startswith('CAMOFOX_') and not key.lower().endswith('_proxy')}


@contextmanager
def http_service(respond=None, *, write_response=None):
    """Record requests and serve fixtures on an OS-assigned loopback port."""
    records = []

    class Handler(BaseHTTPRequestHandler):
        def handle_request(self):
            raw = self.rfile.read(int(self.headers.get('Content-Length', 0)))
            record = {'method': self.command, 'path': self.path, 'headers': dict(self.headers),
                      'body': json.loads(raw) if raw else None}
            records.append(record)
            if write_response is not None:
                try:
                    write_response(record, self)
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
                    pass
                return
            status, headers, body = respond(record) if respond else (200, {}, {'ok': True})
            if not isinstance(body, bytes):
                body = json.dumps(body, ensure_ascii=False).encode('utf-8')
            self.send_response(status)
            self.send_header('Content-Type', headers.get('Content-Type', 'application/json'))
            self.send_header('Content-Length', str(len(body)))
            for name, value in headers.items():
                if name != 'Content-Type':
                    self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        do_GET = do_POST = do_DELETE = do_PUT = do_PATCH = do_CONNECT = handle_request

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}', records
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += seconds
