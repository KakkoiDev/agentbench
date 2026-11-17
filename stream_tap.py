#!/usr/bin/env python3
"""Record Ollama's raw response streams, to see why pi sometimes reports
"Stream ended without finish_reason".

Sits between pi and an Ollama server, passes everything through unchanged, and saves each
/v1/chat/completions response as it arrived (plus the tool names and last message of the request)
under results/stream-tap/<run>/. Streams that end without a finish_reason are listed in bad.txt.

    .venv/bin/python stream_tap.py --listen 11510 --target http://127.0.0.1:11500 --run v3-gpt-oss
"""
import argparse, http.client, json, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--listen', type=int, default=11510)
    ap.add_argument('--target', default='http://127.0.0.1:11500')
    ap.add_argument('--run', default=time.strftime('%Y%m%dT%H%M%S'))
    a = ap.parse_args()
    out = ROOT / 'results' / 'stream-tap' / a.run
    out.mkdir(parents=True, exist_ok=True)
    target = urlsplit(a.target)
    count = [0]
    lock = threading.Lock()

    class Tap(BaseHTTPRequestHandler):
        protocol_version = 'HTTP/1.1'

        def log_message(self, *args):
            pass

        def handle(self):
            try:
                super().handle()
            except (ConnectionResetError, BrokenPipeError):  # pi closed an idle keep-alive connection
                pass

        def relay(self):
            body = self.rfile.read(int(self.headers.get('Content-Length') or 0))
            conn = http.client.HTTPConnection(target.hostname, target.port, timeout=1800)
            headers = {k: v for k, v in self.headers.items() if k.lower() not in ('host', 'content-length', 'connection')}
            conn.request(self.command, self.path, body=body or None, headers=headers)
            resp = conn.getresponse()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in ('transfer-encoding', 'connection', 'content-length'):
                    self.send_header(k, v)
            self.send_header('Transfer-Encoding', 'chunked')
            self.end_headers()
            raw = bytearray()
            while True:
                chunk = resp.read1(65536)
                if not chunk:
                    break
                raw += chunk
                self.wfile.write(b'%x\r\n%s\r\n' % (len(chunk), chunk))
                self.wfile.flush()
            self.wfile.write(b'0\r\n\r\n')
            conn.close()
            if self.path.startswith('/v1/chat/completions'):
                with lock:
                    count[0] += 1
                    n = count[0]
                try:
                    req = json.loads(body)
                    info = dict(tools=[t.get('function', {}).get('name') for t in req.get('tools', [])],
                                last=str(req.get('messages', [{}])[-1].get('content'))[:500], status=resp.status)
                except ValueError:
                    info = dict(status=resp.status)
                (out / f'{n:05}.sse').write_bytes(bytes(raw))
                (out / f'{n:05}.json').write_text(json.dumps(info, ensure_ascii=False))
                if b'"finish_reason":"' not in raw.replace(b'": "', b'":"'):
                    with lock, (out / 'bad.txt').open('a') as f:
                        f.write(f'{n:05} {time.strftime("%H:%M:%S")} status={resp.status} bytes={len(raw)}\n')

        do_GET = do_POST = do_DELETE = do_HEAD = relay

    print(f'stream tap on 127.0.0.1:{a.listen} -> {a.target}, saving to {out}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', a.listen), Tap).serve_forever()


if __name__ == '__main__':
    main()
