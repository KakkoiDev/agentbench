"""A loopback proxy to DeepSeek's own API, so the benches can run DeepSeek's hosted model (asked for on 2026-11-24:
a frozen reference to compare future local models against).

Why a proxy rather than a pi provider pointing at api.deepseek.com:
- the bench sandbox (arena/sandbox.sb and mini's) allows network only to localhost; the agent never reaches the
  internet, only this proxy does, and only for chat completions;
- the API key stays here (read from ~/.config/deepseek/api-key, never written into a bench's pi config, never logged);
- DeepSeek's thinking mode with tools needs every earlier turn's `reasoning_content` sent back (else HTTP 400);
  the proxy remembers it by tool-call id / reply text and puts it back if the client dropped it;
- every request's usage, cost and the model version DeepSeek actually served go to results/deepseek-usage.jsonl,
  and a spending cap (results/deepseek-budget.json, default $25) stops requests once it is reached.

Model ids the benches use (`--model deepseek/<id>`), each mapped to deepseek-flash (DeepSeek-V4.1-Flash-1017):
    flash-max   thinking, reasoning_effort max   (the ceiling runs)
    flash-high  thinking, reasoning_effort high  (DeepSeek's default)
    flash-off   thinking disabled
    flash       the client's own setting (2026-11-25): pi sends `thinking` and `reasoning_effort` from its --thinking
                (models.json: reasoning true, thinkingFormat deepseek), so a run's meta.json records the real effort

    .venv/bin/python benches/deepseek_proxy.py            # serves 127.0.0.1:8096 until stopped
    .venv/bin/python benches/deepseek_proxy.py --spent    # what has been spent so far
"""
import argparse, hashlib, json, sys, threading, time, urllib.error, urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = 8096
UPSTREAM = 'https://api.deepseek.com/chat/completions'
KEY_FILE = Path.home() / '.config' / 'deepseek' / 'api-key'
USAGE = ROOT / 'results' / 'deepseek-usage.jsonl'
BUDGET = ROOT / 'results' / 'deepseek-budget.json'
MODELS = {'flash': ('deepseek-flash', 'client'), 'flash-max': ('deepseek-flash', 'max'), 'flash-high': ('deepseek-flash', 'high'), 'flash-off': ('deepseek-flash', None)}
# $ per 1M tokens (api-docs.deepseek.com/quick_start/pricing, read 2026-11-24): off-peak; peak (01-04 and 06-10 UTC, Mon-Fri) is double
PRICE = dict(hit=0.003, miss=0.15, out=0.6)
LOCK = threading.Lock()
REASONING = {}   # tool-call id or hash of reply text -> reasoning_content DeepSeek sent with it


def peak(t=None):
    g = time.gmtime(t)
    return g.tm_wday < 5 and (1 <= g.tm_hour < 4 or 6 <= g.tm_hour < 10)


def wait_offpeak(need_s, log=print):
    """Asked for on 2026-11-24: DeepSeek benches run only off-peak. Sleep until a stretch of `need_s` seconds with no peak
    minute in it starts now (checked minute by minute), so a task never crosses into peak hours."""
    def clear(t0):
        return not any(peak(t0 + m * 60) for m in range(0, int(need_s // 60) + 2))
    now = time.time()
    t = now
    while not clear(t):
        t += 60
    if t > now + 1:
        log(f'off-peak wait: {round((t - now) / 60)} min, until {time.strftime("%H:%M UTC", time.gmtime(t))} '
            f'(DeepSeek peak 01-04 and 06-10 UTC Mon-Fri, this step needs {round(need_s / 60)} min)')
        time.sleep(t - now)


def cost(u, t=None):
    hit = u.get('prompt_cache_hit_tokens', 0)
    miss = u.get('prompt_cache_miss_tokens', max(0, u.get('prompt_tokens', 0) - hit))
    c = (hit * PRICE['hit'] + miss * PRICE['miss'] + u.get('completion_tokens', 0) * PRICE['out']) / 1e6
    return c * (2 if peak(t) else 1)


def spent():
    if not USAGE.exists():
        return 0.0
    return sum(json.loads(l).get('cost', 0) for l in USAGE.read_text().splitlines() if l.strip())


def cap():
    return json.loads(BUDGET.read_text()).get('usd', 25) if BUDGET.exists() else 25


def text_key(content):
    return 'c:' + hashlib.sha256((content or '').strip().encode()).hexdigest()


def remember(msg):
    rc = msg.get('reasoning_content')
    if not rc:
        return
    with LOCK:
        for tc in msg.get('tool_calls') or []:
            if tc.get('id'):
                REASONING[tc['id']] = rc
        if (msg.get('content') or '').strip():
            REASONING[text_key(msg['content'])] = rc


def restore(messages):
    """Put back the reasoning_content of earlier assistant turns the client dropped (thinking mode + tools needs it)."""
    n = 0
    for m in messages:
        if m.get('role') != 'assistant' or m.get('reasoning_content'):
            continue
        ids = [tc.get('id') for tc in m.get('tool_calls') or []]
        rc = next((REASONING[i] for i in ids if i in REASONING), None) or REASONING.get(text_key(m.get('content') if isinstance(m.get('content'), str) else ''))
        m['reasoning_content'] = rc or ''
        n += bool(rc)
    return n


class Handler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *a):
        pass

    def reply(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def do_GET(self):
        if self.path.rstrip('/').endswith('/models'):
            return self.reply(200, {'object': 'list', 'data': [{'id': k, 'object': 'model'} for k in MODELS]})
        self.reply(404, {'error': 'not found'})

    def do_POST(self):
        if not self.path.rstrip('/').endswith('/chat/completions'):
            return self.reply(404, {'error': {'message': 'only /v1/chat/completions'}})
        body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))) or b'{}')
        if spent() >= cap():
            return self.reply(429, {'error': {'message': f'DeepSeek bench budget reached (${cap()}); raise it in {BUDGET}'}})
        alias = body.get('model', '')
        if alias not in MODELS:
            return self.reply(400, {'error': {'message': f'unknown model {alias}; one of {list(MODELS)}'}})
        model, effort = MODELS[alias]
        body['model'] = model
        if effort == 'client':   # pi's own --thinking (thinkingFormat deepseek); DeepSeek has two efforts, high and max
            body.pop('store', None)
            if (body.get('thinking') or {}).get('type') == 'enabled':
                effort = 'max' if body.get('reasoning_effort') in ('xhigh', 'max') else 'high'
            else:
                effort = None
        else:   # the fixed aliases ignore what the client asked for
            for k in ('reasoning_effort', 'reasoning', 'thinking', 'store', 'temperature', 'top_p'):
                body.pop(k, None)
        if effort:
            body['thinking'] = {'type': 'enabled'}
            body['reasoning_effort'] = effort
        else:
            body['thinking'] = {'type': 'disabled'}
            body.pop('reasoning_effort', None)
            if alias != 'flash':
                body['temperature'] = 0
        restored = restore(body.get('messages', []))
        stream = bool(body.get('stream'))
        if stream:
            body['stream_options'] = {'include_usage': True}
        req = urllib.request.Request(UPSTREAM, json.dumps(body).encode(), method='POST', headers={
            'Content-Type': 'application/json', 'Authorization': 'Bearer ' + KEY_FILE.read_text().strip()})
        t0 = time.time()
        for attempt in range(4):
            try:
                up = urllib.request.urlopen(req, timeout=900)
                break
            except urllib.error.HTTPError as e:
                if e.code in (429, 500, 502, 503) and attempt < 3:
                    time.sleep(10 * (attempt + 1))
                    continue
                return self.reply(e.code, json.loads(e.read() or b'{}') if e.headers.get_content_type() == 'application/json' else {'error': {'message': f'upstream {e.code}'}})
            except Exception as e:
                if attempt < 3:
                    time.sleep(10 * (attempt + 1))
                    continue
                return self.reply(502, {'error': {'message': f'upstream: {e}'}})
        usage, served = {}, None
        if not stream:
            data = json.loads(up.read())
            usage, served = data.get('usage') or {}, data.get('model')
            for ch in data.get('choices', []):
                remember(ch.get('message') or {})
            self.reply(200, data)
        else:
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.end_headers()
            acc = {}   # choice index -> assembled message
            for line in up:
                try:
                    self.wfile.write(line)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                s = line.decode('utf-8', 'replace').strip()
                if not s.startswith('data:') or s == 'data: [DONE]':
                    continue
                try:
                    ev = json.loads(s[5:])
                except ValueError:
                    continue
                served = ev.get('model') or served
                if ev.get('usage'):
                    usage = ev['usage']
                for ch in ev.get('choices') or []:
                    m = acc.setdefault(ch.get('index', 0), {'content': '', 'reasoning_content': '', 'tool_calls': {}})
                    d = ch.get('delta') or {}
                    m['content'] += d.get('content') or ''
                    m['reasoning_content'] += d.get('reasoning_content') or ''
                    for tc in d.get('tool_calls') or []:
                        if tc.get('id'):
                            m['tool_calls'][tc.get('index', 0)] = tc['id']
            for m in acc.values():
                remember(dict(content=m['content'], reasoning_content=m['reasoning_content'],
                              tool_calls=[{'id': i} for i in m['tool_calls'].values()]))
            self.close_connection = True
        rec = dict(t=time.strftime('%FT%TZ', time.gmtime()), alias=alias, effort=effort or 'off', served=served, seconds=round(time.time() - t0, 1),
                   usage=usage, cost=round(cost(usage), 6), peak=peak(), restored_reasoning=restored)
        with LOCK, open(USAGE, 'a') as f:
            f.write(json.dumps(rec) + '\n')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--spent', action='store_true')
    p.add_argument('--wait-offpeak', type=int, metavar='MINUTES', help='sleep until MINUTES of off-peak time start now, then exit')
    a = p.parse_args()
    if a.wait_offpeak:
        return wait_offpeak(a.wait_offpeak * 60, log=lambda m: print(m, flush=True))
    if a.spent:
        print(f'${spent():.4f} of ${cap()}')
        return
    if not KEY_FILE.exists():
        sys.exit(f'no API key: save it in {KEY_FILE} (chmod 600)')
    print(f'DeepSeek proxy on 127.0.0.1:{PORT}; spent ${spent():.4f} of ${cap()}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', PORT), Handler).serve_forever()


if __name__ == '__main__':
    main()
