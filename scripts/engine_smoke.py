"""Smoke test of the local engines (benches/local_engines.py): does each model load and answer, and how fast?

    .venv/bin/python scripts/engine_smoke.py ds4/glm-5.3-flash-q2 slotstream/qwen3.8-flash-next:4bit ...

For each model: start its server, then two requests like the benches send: a short one (about 60 prompt
tokens, 64 out) and a long one (about 2,500 prompt tokens, the size of an agent bench's first turn, 16 out;
SlotStream failed exactly these with insufficient_memory on 2026-11-21). Records whether each completed,
time to first token, prefill and decode speed, the server's peak memory and the swap used; then stops the
server. Takes the lab's .bench.lock (so the bench queue waits) and unloads Ollama's models first.
Results: results/engine-smoke/smoke-<time>.json.
"""
import fcntl, json, subprocess, sys, threading, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'benches'))
import local_engines as le  # noqa: E402

OUT = ROOT / 'results' / 'engine-smoke'
# about 2,600 tokens (rules.md alone is ~1,200): the size of an agent bench's first turn
LONG = ((ROOT / 'arena' / 'harness' / 'docs' / 'rules.md').read_text() + '\n\n' + (ROOT / 'arena' / 'harness' / 'docs' / 'spec.md').read_text())[:15000]
REQS = [('short', 'In two sentences, why does a cache make programs faster?', 64),
        ('long ~2.5k tokens', LONG + '\n\nIn one sentence: what is this document about?', 16)]
TIMEOUT = 900


def server_pid(port):
    out = subprocess.run(['lsof', '-ti', f'tcp:{port}', '-sTCP:LISTEN'], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def rss_gb(pid):
    """Resident memory of the server and its children, in GB."""
    out = subprocess.run(['ps', '-A', '-o', 'pid=,ppid=,rss='], capture_output=True, text=True).stdout.split('\n')
    rows = [tuple(map(int, l.split())) for l in out if l.strip()]
    pids, total = {pid}, 0
    for _ in range(3):
        pids |= {p for p, pp, _ in rows if pp in pids}
    return sum(r for p, _, r in rows if p in pids) / 1e6


def swap_gb():
    s = subprocess.run(['sysctl', '-n', 'vm.swapusage'], capture_output=True, text=True).stdout
    used = s.split('used = ')[1].split()[0]
    return float(used.rstrip('M')) / 1024 if used.endswith('M') else float(used.rstrip('G'))


def ask(engine, model_id, prompt, max_tokens):
    port = le.ENGINES[engine]['port']
    body = dict(model=model_id, messages=[{'role': 'user', 'content': prompt}], max_tokens=max_tokens, stream=True,
                stream_options={'include_usage': True}, temperature=0)
    req = urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', data=json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json', 'Authorization': 'Bearer ' + le.api_key(engine)})
    t0, first, chunks, usage, text, err = time.time(), None, 0, None, '', None
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as r:
            for raw in r:
                line = raw.decode(errors='replace').strip()
                if not line.startswith('data:'):
                    continue
                data = line[5:].strip()
                if data == '[DONE]':
                    break
                j = json.loads(data)
                if j.get('error'):
                    err = str(j['error'])[:300]
                    break
                for c in j.get('choices') or []:
                    d = c.get('delta') or {}
                    piece = (d.get('content') or '') + (d.get('reasoning_content') or d.get('reasoning') or '')
                    if piece:
                        first = first or time.time()
                        chunks += 1
                        text += d.get('content') or ''
                usage = j.get('usage') or usage
                if time.time() - t0 > TIMEOUT:
                    err = f'over {TIMEOUT}s'
                    break
    except Exception as ex:
        err = f'{type(ex).__name__}: {ex}'[:300]
    t1 = time.time()
    out_tok = (usage or {}).get('completion_tokens') or chunks
    in_tok = (usage or {}).get('prompt_tokens')
    ttft = (first - t0) if first else None
    decode = (out_tok - 1) / (t1 - first) if first and out_tok > 1 and t1 > first else None
    return dict(ok=err is None and out_tok > 0, error=err, prompt_tokens=in_tok, output_tokens=out_tok, ttft_s=round(ttft, 1) if ttft else None,
                prefill_tok_s=round(in_tok / ttft, 1) if in_tok and ttft else None, decode_tok_s=round(decode, 2) if decode else None,
                total_s=round(t1 - t0, 1), answer=text.strip()[:160])


def unload_ollama():
    try:
        ps = json.load(urllib.request.urlopen('http://127.0.0.1:11434/api/ps', timeout=10))
        for m in ps.get('models', []):
            subprocess.run(['ollama', 'stop', m['name']], capture_output=True)
    except Exception:
        pass
    time.sleep(3)


def smoke(model):
    engine, model_id = model.split('/', 1)
    rec = dict(model=model, when=time.strftime('%F %T'), swap_before_gb=round(swap_gb(), 2))
    t0 = time.time()
    try:
        stop = le.serve(model)
    except Exception as ex:
        rec.update(loaded=False, error=str(ex)[:400])
        return rec
    rec['load_s'] = round(time.time() - t0, 1)
    rec['loaded'] = True
    pid = server_pid(le.ENGINES[engine]['port'])
    peak, done = [0.0], threading.Event()

    def watch():
        while not done.is_set():
            if pid:
                peak[0] = max(peak[0], rss_gb(pid))
            done.wait(2)
    threading.Thread(target=watch, daemon=True).start()
    try:
        rec['requests'] = {}
        for name, prompt, n in REQS:
            print(f'  {model}: {name} request...', flush=True)
            r = ask(engine, model_id, prompt, n)
            rec['requests'][name] = r
            print(f'    {json.dumps(r)}', flush=True)
    finally:
        done.set()
        stop()
    rec['peak_rss_gb'] = round(peak[0], 2)
    rec['swap_after_gb'] = round(swap_gb(), 2)
    rec['works'] = all(r['ok'] for r in rec['requests'].values())
    return rec


def main():
    models = sys.argv[1:]
    unknown = [m for m in models if m not in le.MODELS]
    if not models or unknown:
        sys.exit(f'usage: engine_smoke.py MODEL...; unknown: {unknown}; known: {", ".join(le.MODELS)}')
    lock = (ROOT / '.bench.lock').open('w')
    print(time.strftime('%T'), 'waiting for .bench.lock (the bench queue waits for us once we have it)', flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)
    print(time.strftime('%T'), 'lock taken; unloading Ollama', flush=True)
    unload_ollama()
    OUT.mkdir(parents=True, exist_ok=True)
    out = OUT / f"smoke-{time.strftime('%Y%m%d-%H%M%S')}.json"
    results = []
    for m in models:
        print(time.strftime('%T'), 'start', m, flush=True)
        results.append(smoke(m))
        out.write_text(json.dumps(results, indent=1))
        r = results[-1]
        print(time.strftime('%T'), m, 'WORKS' if r.get('works') else 'FAILED', f"load {r.get('load_s')}s, peak {r.get('peak_rss_gb')} GB, swap {r.get('swap_before_gb')} -> {r.get('swap_after_gb')} GB",
              r.get('error') or '', flush=True)
    print(time.strftime('%T'), 'done; lock released:', out, flush=True)


if __name__ == '__main__':
    main()
