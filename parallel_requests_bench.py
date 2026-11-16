"""Parallel-requests bench: can one loaded model serve several chat requests at once?

The system Ollama (127.0.0.1:11434) runs with OLLAMA_NUM_PARALLEL=1, so concurrent
requests queue. This bench starts its OWN Ollama on another port (default 11500) with
OLLAMA_NUM_PARALLEL set, the same model store, and stops it afterwards. The system
service is never touched.

    # throughput sweep: 1, 2 and 4 concurrent requests per model
    .venv/bin/python parallel_requests_bench.py throughput [--models gpt-oss:20b,...]
    # realistic check: 2-3 agentic-v1 tasks through pi at once vs one after another
    .venv/bin/python parallel_requests_bench.py agents --model gpt-oss:20b
    # rebuild report.md from a run folder
    .venv/bin/python parallel_requests_bench.py report results/parallel-requests-<time>

A model "parallelizes" when its aggregate tokens/s at 4 concurrent requests is at least
1.5x its single-request figure. DEFAULT_MODELS keeps only the models that passed that
check on this Mac (see the report under results/); the others are listed in
NOT_PARALLEL with the measured numbers. Pass --models to measure any model.

Results: results/parallel-requests-<time>/ with summary.json, report.md and server.log.
Takes the lab's .bench.lock (one GPU job at a time) for the whole run.
"""
import argparse, concurrent.futures, fcntl, json, os, re, shutil, signal, statistics, subprocess, sys, threading, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SYSTEM_URL = 'http://127.0.0.1:11434'
SPEEDUP_BAR = 1.5
# Measured 2026-11-15 on the 32 GB M6 (Ollama 0.40.16), see results/parallel-requests-*/report.md.
DEFAULT_MODELS = ['gpt-oss:20b']
NOT_PARALLEL = {  # aggregate tok/s at 1 / 2 / 4 concurrent on the isolated server with OLLAMA_NUM_PARALLEL=4
    'qwen3.8:27b-mlx': 'MLX runner: 22.4 / 22.0 / 21.5 tok/s (0.96x); requests queue, TTFT p50 at 4 = 18.5 s',
    'gemma4:26b-mlx': 'MLX runner: 46.8 / 47.1 / 44.9 tok/s (0.96x); requests queue, TTFT p50 at 4 = 8.5 s',
}
# gpt-oss:20b (GGUF, llama runner): 36.1 / 57.3 / 83.2 tok/s (2.3x), per-stream 37.6 -> 21.7 tok/s at 4.
AGENT_TASKS = ['bugfix-inventory', 'refactor-dates', 'feature-todo']

TOPICS = [
    'a Python function that merges overlapping time intervals',
    'a small LRU cache class in Python with get/put and O(1) operations',
    'a CSV parser in Python that handles quoted fields with embedded commas',
    'a TypeScript debounce helper with cancel and flush',
    'a Go function that walks a directory tree and sums file sizes by extension',
    'a Python retry decorator with exponential backoff and jitter',
    'a SQL query that finds the top 3 customers per region by revenue',
    'a Rust function that parses "key=value" lines into a HashMap',
]


def api(url, path, body=None, timeout=180):
    req = urllib.request.Request(url + '/api/' + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def swap_mib():
    m = re.search(r'used = ([0-9.]+)M', subprocess.check_output(['sysctl', 'vm.swapusage'], text=True))
    return float(m[1]) if m else None


def available_mib():
    """Free + inactive + speculative pages: memory the OS can hand out without swapping."""
    text = subprocess.check_output(['vm_stat'], text=True)
    page = int(re.search(r'page size of (\d+)', text)[1])
    pages = sum(int(re.search(rf'{k}:\s+(\d+)', text)[1]) for k in ('Pages free', 'Pages inactive', 'Pages speculative'))
    return pages * page / 2**20


def tree_rss_mib(pgid):
    """Resident memory of the isolated server and its runner (same process group)."""
    out = subprocess.run(['ps', '-axo', 'pgid=,rss='], capture_output=True, text=True).stdout
    return sum(int(r) for g, r in (l.split() for l in out.splitlines() if l.strip()) if int(g) == pgid) / 1024


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, round(q * (len(xs) - 1)))] if xs else None


class IsolatedOllama:
    """`ollama serve` on its own port with OLLAMA_NUM_PARALLEL, sharing the model store; stopped on exit."""

    def __init__(self, port, parallel, out_dir, context_length=None, extra_env=None):
        self.url = f'http://127.0.0.1:{port}'
        self.port, self.parallel, self.out_dir, self.ctx = port, parallel, out_dir, context_length
        self.extra_env = extra_env or {}  # e.g. OLLAMA_KV_CACHE_TYPE, OLLAMA_MAX_LOADED_MODELS
        self.proc = None

    def __enter__(self):
        try:
            api(self.url, 'version', timeout=2)
            raise RuntimeError(f'Something already listens on {self.url}; pick another --port. Nothing changed.')
        except OSError:
            pass
        binary = shutil.which('ollama') or '/Applications/Ollama.app/Contents/Resources/ollama'
        env = dict(os.environ, OLLAMA_HOST=f'127.0.0.1:{self.port}', OLLAMA_NUM_PARALLEL=str(self.parallel),
                   OLLAMA_MAX_LOADED_MODELS='1', OLLAMA_NOPRUNE='1', OLLAMA_KEEP_ALIVE='5m')
        if self.ctx:
            env['OLLAMA_CONTEXT_LENGTH'] = str(self.ctx)
        env.update({k: str(v) for k, v in self.extra_env.items()})
        env.setdefault('OLLAMA_MODELS', str(Path.home() / 'models' / 'ollama'))  # same store as the system service, no downloads
        self.env = {k: env[k] for k in env if k.startswith('OLLAMA_')}
        self.log = (self.out_dir / f'server-{self.port}.log').open('a')
        self.proc = subprocess.Popen([binary, 'serve'], env=env, stdout=self.log, stderr=self.log, start_new_session=True)
        for _ in range(200):
            if self.proc.poll() is not None:
                raise RuntimeError('Isolated Ollama exited at startup; see ' + self.log.name)
            try:
                self.version = api(self.url, 'version', timeout=2)['version']
                return self
            except OSError:
                time.sleep(.1)
        raise RuntimeError('Isolated Ollama startup timed out')

    def unload(self, model):
        try:
            api(self.url, 'generate', dict(model=model, keep_alive=0), timeout=30)
        except Exception:
            pass

    def __exit__(self, *exc):
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
                self.proc.wait(timeout=15)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                if self.proc.poll() is None:
                    os.killpg(self.proc.pid, signal.SIGKILL)
        self.log.close()


def check_room(model_name, need_extra_mib=4096):
    """Refuse to load when the system Ollama holds a model or memory is short (no swapping the Mac)."""
    loaded = api(SYSTEM_URL, 'ps').get('models', [])
    if loaded:
        raise RuntimeError(f"System Ollama has {[m['name'] for m in loaded]} loaded; wait until it unloads. Nothing changed.")
    size = next(m['size'] for m in api(SYSTEM_URL, 'tags')['models'] if m['name'] == model_name) / 2**20
    avail = available_mib()
    if avail < size + need_extra_mib:
        raise RuntimeError(f'{model_name}: needs ~{size + need_extra_mib:.0f} MiB, {avail:.0f} MiB available. Nothing loaded.')
    return dict(model_file_mib=round(size), available_before_mib=round(avail))


def chat(url, model, client, tokens, ctx):
    topic = TOPICS[client % len(TOPICS)]
    prompt = (f'Request {client}. You are helping a developer in a chat. Write {topic}. Explain the approach briefly, '
              'then give the code, then two edge cases and how the code handles them.')
    body = dict(model=model, messages=[dict(role='user', content=prompt)], think=False, stream=True, keep_alive='5m',
                options=dict(temperature=0, num_ctx=ctx, num_predict=tokens, seed=client))
    req = urllib.request.Request(url + '/api/chat', data=json.dumps(body).encode(), headers={'Content-Type': 'application/json'})
    start = time.monotonic(); first = None; final = {}
    with urllib.request.urlopen(req, timeout=600) as resp:
        for line in resp:
            ev = json.loads(line)
            if ev.get('error'):
                raise RuntimeError(ev['error'])
            msg = ev.get('message', {})
            if first is None and (msg.get('content') or msg.get('thinking')):
                first = time.monotonic()
            if ev.get('done'):
                final = ev
    end = time.monotonic()
    if not final or first is None:
        raise RuntimeError('missing streamed output or final telemetry')
    n = final.get('eval_count', 0)
    return dict(client=client, start=start, end=end, latency_s=end - start, ttft_s=first - start, tokens=n,
                prompt_tokens=final.get('prompt_eval_count'), stream_tok_s=n / max(1e-9, end - first),
                server_eval_tok_s=n / (final['eval_duration'] / 1e9) if final.get('eval_duration') else None,
                finish=final.get('done_reason'))


def level(url, model, n, rounds, tokens, ctx, pgid):
    """n concurrent requests, `rounds` times. Aggregate tok/s = all generated tokens / wall time of the rounds."""
    samples, stop = [], threading.Event()

    def sample():
        while not stop.is_set():
            row = dict(t=time.monotonic(), avail_mib=available_mib(), rss_mib=tree_rss_mib(pgid), swap_mib=swap_mib())
            try:
                ps = api(url, 'ps', timeout=5).get('models', [])
                row['ollama_size_mib'] = max((m.get('size', 0) for m in ps), default=0) / 2**20
                row['system_ollama_loaded'] = bool(api(SYSTEM_URL, 'ps', timeout=5).get('models'))
            except Exception:
                pass
            samples.append(row)
            stop.wait(.5)

    th = threading.Thread(target=sample, daemon=True); th.start()
    swap0, reqs, wall = swap_mib(), [], 0.0
    try:
        for r in range(rounds):
            t0 = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(n) as pool:
                reqs += list(pool.map(lambda i: chat(url, model, r * n + i, tokens, ctx), range(n)))
            wall += time.monotonic() - t0
    finally:
        stop.set(); th.join(timeout=10)
    lat, ttft = [q['latency_s'] for q in reqs], [q['ttft_s'] for q in reqs]
    return dict(
        concurrent=n, rounds=rounds, requests=len(reqs), wall_s=round(wall, 2),
        aggregate_tok_s=round(sum(q['tokens'] for q in reqs) / wall, 2),
        per_stream_tok_s_p50=round(statistics.median(q['stream_tok_s'] for q in reqs), 2),
        latency_p50_s=round(statistics.median(lat), 2), latency_max_s=round(max(lat), 2),
        ttft_p50_s=round(statistics.median(ttft), 2), ttft_max_s=round(max(ttft), 2),
        tokens_mean=round(statistics.mean(q['tokens'] for q in reqs)),
        rss_peak_mib=round(max((s['rss_mib'] for s in samples), default=0)),
        ollama_reported_mib=round(max((s.get('ollama_size_mib', 0) for s in samples), default=0)),
        available_min_mib=round(min((s['avail_mib'] for s in samples), default=0)),
        swap_delta_mib=round(swap_mib() - swap0, 1), system_ollama_busy=any(s.get('system_ollama_loaded') for s in samples),
        raw=reqs)


def throughput(args, out, summary, save):
    summary.update(kind='throughput', parallel=args.parallel, levels=args.levels, rounds=args.rounds, tokens=args.tokens,
                   num_ctx=args.ctx, speedup_bar=SPEEDUP_BAR, models={})
    with IsolatedOllama(args.port, args.parallel, out) as srv:
        summary.update(engine=srv.version, server_env=srv.env); save()
        for model in args.models.split(','):
            rec = summary['models'][model] = dict(levels=[], status='running')
            try:
                rec['room'] = check_room(model)
                t0 = time.monotonic()
                chat(srv.url, model, 99, 16, args.ctx)  # load + warm
                rec['load_s'] = round(time.monotonic() - t0, 1)
                for n in args.levels:
                    row = level(srv.url, model, n, args.rounds, args.tokens, args.ctx, srv.proc.pid)
                    rec['levels'].append(row); save()
                    print(f"{model}: {n} concurrent -> {row['aggregate_tok_s']} tok/s aggregate, latency p50 {row['latency_p50_s']}s "
                          f"max {row['latency_max_s']}s, TTFT p50 {row['ttft_p50_s']}s, RSS {row['rss_peak_mib']} MiB, "
                          f"swap {row['swap_delta_mib']:+} MiB", flush=True)
                    if row['swap_delta_mib'] > 256:
                        rec['stopped'] = 'swap grew by more than 256 MiB'; break
                one = next(r['aggregate_tok_s'] for r in rec['levels'] if r['concurrent'] == 1)
                four = next((r['aggregate_tok_s'] for r in rec['levels'] if r['concurrent'] == max(args.levels)), None)
                rec['speedup'] = round(four / one, 2) if four else None
                rec['parallelizes'] = bool(four) and four / one >= SPEEDUP_BAR
                rec['status'] = 'done'
            except Exception as e:
                rec.update(status='error', error=str(e)); print(f'{model}: ERROR {e}', flush=True)
            finally:
                srv.unload(model); save()
                time.sleep(3)


def agents(args, out, summary, save):
    """Same pi + agentic-v1 tasks against the isolated server: one after another, then all at once."""
    tasks = args.tasks.split(',')
    stamp = time.strftime('%Y%m%dT%H%M%S')
    slug = args.model.replace(':', '-').replace('.', '-').replace('/', '-')
    summary.update(kind='agents', model=args.model, tasks=tasks, parallel=args.parallel, context_length=args.ctx,
                   extension=args.extension or None, modes={})
    with IsolatedOllama(args.port, args.parallel, out, context_length=args.ctx) as srv:
        summary.update(engine=srv.version, server_env=srv.env); save()
        summary['room'] = check_room(args.model, need_extra_mib=6144)
        chat(srv.url, args.model, 99, 16, args.ctx)

        def bench(name, only):
            cmd = [sys.executable, str(ROOT / 'agent_bench.py'), 'run', '--name', name, '--model', 'ollama/' + args.model,
                   '--thinking', args.thinking, '--timeout', str(args.timeout), '--only', only, '--no-lock', '--ollama-url', srv.url]
            if args.extension:
                cmd += ['--extension', args.extension]
            return subprocess.Popen(cmd, cwd=ROOT, stdout=(out / f'{name}-{only}.log').open('w'), stderr=subprocess.STDOUT)

        order = ['sequential', 'concurrent'] if not args.concurrent_first else ['concurrent', 'sequential']
        for mode in order:
            name = f'{slug}-iso{args.port}-{mode}-{stamp}'
            swap0, avail = swap_mib(), []
            t0 = time.monotonic()
            if mode == 'sequential':
                for t in tasks:
                    bench(name, t).wait()
                    avail.append(available_mib())
            else:
                procs = [bench(name, t) for t in tasks]
                while any(p.poll() is None for p in procs):
                    avail.append(available_mib()); time.sleep(1)
            wall = time.monotonic() - t0
            rs = [json.loads(p.read_text()) for p in sorted((ROOT / 'results' / f'agentic-v1-{name}').glob('*/result.json'))]
            summary['modes'][mode] = dict(
                run=f'agentic-v1-{name}', wall_s=round(wall, 1), solved=sum(r['solved'] for r in rs), tasks=len(rs),
                hidden=[sum(r['hidden_passed'] for r in rs), sum(r['hidden_total'] or 0 for r in rs)],
                per_task={r['task']: dict(solved=r['solved'], hidden=f"{r['hidden_passed']}/{r['hidden_total']}", wall_s=r['wall_s'],
                                          tools=r['tool_calls'], out_tokens=r['output_tokens']) for r in rs},
                swap_delta_mib=round(swap_mib() - swap0, 1), available_min_mib=round(min(avail, default=0)))
            save()
            print(f"{mode}: {summary['modes'][mode]['solved']}/{len(rs)} solved in {wall / 60:.1f} min", flush=True)
        srv.unload(args.model)


def report_text(s):
    L = []
    if s['kind'] == 'throughput':
        L += [f"# Parallel requests on an isolated Ollama ({s['started']})", '',
              f"Ollama {s.get('engine')} on port {s['port']} with OLLAMA_NUM_PARALLEL={s['parallel']} and the system model store; "
              f"the system service was not touched. Each level runs {s['rounds']} rounds of N concurrent chat requests "
              f"(distinct coding prompts, {s['tokens']} tokens max, num_ctx {s['num_ctx']}, temperature 0, thinking off).", '',
              f"A model parallelizes when aggregate tok/s at 4 concurrent is at least {s['speedup_bar']}x the single-request figure.", '',
              '| Model | Verdict | Speedup @4 | Concurrent | Aggregate tok/s | Per-stream tok/s (p50) | Latency p50 / max | TTFT p50 / max | Server RSS peak | Ollama-reported | Min available | Swap Δ |',
              '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
        for m, rec in s['models'].items():
            verdict = 'error: ' + rec.get('error', '')[:60] if rec.get('status') == 'error' else (
                'parallel' if rec.get('parallelizes') else 'not parallel')
            for i, r in enumerate(rec['levels']):
                L.append(f"| {m if i == 0 else ''} | {verdict if i == 0 else ''} | {(str(rec.get('speedup')) + 'x') if i == 0 else ''} | "
                         f"{r['concurrent']} | {r['aggregate_tok_s']} | {r['per_stream_tok_s_p50']} | {r['latency_p50_s']} / {r['latency_max_s']} s | "
                         f"{r['ttft_p50_s']} / {r['ttft_max_s']} s | {r['rss_peak_mib'] / 1024:.1f} GiB | {r['ollama_reported_mib'] / 1024:.1f} GiB | "
                         f"{r['available_min_mib'] / 1024:.1f} GiB | {r['swap_delta_mib']:+.0f} MiB |")
            if not rec['levels']:
                L.append(f'| {m} | {verdict} | | | | | | | | | | |')
        L += ['', 'RSS is the isolated server and runner processes; Metal buffers of the MLX runner may not all show in RSS, '
              'so "Min available" (free + inactive pages during the level) is the safer memory signal.']
        busy = [m for m, rec in s['models'].items() if any(r['system_ollama_busy'] for r in rec['levels'])]
        if busy:
            L.append(f'Warning: the system Ollama had a model loaded during {busy}; those numbers share the GPU.')
    else:
        L += [f"# Concurrent agents on an isolated Ollama ({s['started']})", '',
              f"{s['model']} via pi (thinking {s.get('thinking', 'off')}{', extension ' + s['extension'] if s.get('extension') else ''}), "
              f"agentic-v1 tasks {', '.join(s['tasks'])}; Ollama {s.get('engine')} with OLLAMA_NUM_PARALLEL={s['parallel']}, "
              f"OLLAMA_CONTEXT_LENGTH={s['context_length']}. Same server for both modes.", '',
              '| Mode | Wall time | Solved | Hidden tests | Min available | Swap Δ | Per task (hidden · time · tools) |', '|---|---:|---:|---:|---:|---:|---|']
        for mode, r in s['modes'].items():
            per = '; '.join(f"{t}: {'✔' if v['solved'] else '✘'} {v['hidden']} · {v['wall_s']:.0f}s · {v['tools']}" for t, v in r['per_task'].items())
            L.append(f"| {mode} | {r['wall_s'] / 60:.1f} min | {r['solved']}/{r['tasks']} | {r['hidden'][0]}/{r['hidden'][1]} | "
                     f"{r['available_min_mib'] / 1024:.1f} GiB | {r['swap_delta_mib']:+.0f} MiB | {per} |")
        if {'sequential', 'concurrent'} <= set(s['modes']):
            a, b = s['modes']['sequential']['wall_s'], s['modes']['concurrent']['wall_s']
            L += ['', f'Concurrent wall time is {b / a:.2f}x sequential ({a / b:.2f}x faster overall).']
    if s.get('error'):
        L += ['', 'Run error: ' + s['error']]
    return '\n'.join(L) + '\n'


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('throughput')
    t.add_argument('--models', default=','.join(DEFAULT_MODELS))
    t.add_argument('--levels', default='1,2,4')
    t.add_argument('--rounds', type=int, default=2)
    t.add_argument('--tokens', type=int, default=256)
    t.add_argument('--ctx', type=int, default=8192, help='num_ctx per request')
    a = sub.add_parser('agents')
    a.add_argument('--model', default=DEFAULT_MODELS[0])
    a.add_argument('--tasks', default=','.join(AGENT_TASKS))
    a.add_argument('--thinking', default='off')
    a.add_argument('--extension', default='')
    a.add_argument('--timeout', type=int, default=1200)
    a.add_argument('--ctx', type=int, default=32768, help='OLLAMA_CONTEXT_LENGTH of the isolated server (the system default is 32768)')
    a.add_argument('--concurrent-first', action='store_true')
    for p in (t, a):
        p.add_argument('--port', type=int, default=11500)
        p.add_argument('--parallel', type=int, default=4, help='OLLAMA_NUM_PARALLEL of the isolated server')
    r = sub.add_parser('report')
    r.add_argument('folder')
    args = ap.parse_args()
    if args.cmd == 'report':
        s = json.loads((Path(args.folder) / 'summary.json').read_text())
        text = report_text(s); (Path(args.folder) / 'report.md').write_text(text); print(text); return
    if args.cmd == 'throughput':
        args.levels = [int(x) for x in args.levels.split(',')]
    out = ROOT / 'results' / f"parallel-requests-{args.cmd}-{time.strftime('%Y%m%dT%H%M%S')}"
    out.mkdir(parents=True)
    summary = dict(started=time.strftime('%Y-%m-%dT%H:%M:%S%z'), port=args.port, status='running', thinking=getattr(args, 'thinking', 'off'))

    def save():
        (out / 'summary.json').write_text(json.dumps(summary, indent=1))

    with (ROOT / '.bench.lock').open('a') as lock:
        print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
        signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))
        try:
            (throughput if args.cmd == 'throughput' else agents)(args, out, summary, save)
            summary['status'] = 'done'
        except (Exception, KeyboardInterrupt) as e:
            summary.update(status='error', error=repr(e)); raise
        finally:
            save()
            text = report_text(summary); (out / 'report.md').write_text(text)
            print(text); print(out, flush=True)


if __name__ == '__main__':
    main()
