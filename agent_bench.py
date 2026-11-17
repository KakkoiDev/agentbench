"""Agentic coding bench: a model works on small repositories with its own tools (pi).

Each task (benches/agentic-v1/tasks.py, or another bench with --bench agentic-v2 or agentic-v3) is copied to a fresh workspace, pi runs once
with the task's instruction inside a macOS sandbox (writes only in the workspace,
no network except localhost), then hidden tests decide the grade.

    .venv/bin/python agent_bench.py run --name NAME --model ollama/qwen3.8:27b-mlx [--thinking off]
    .venv/bin/python agent_bench.py report --names A,B --out OUT

Kept per run, under results/BENCH-NAME/TASK/ (BENCH defaults to agentic-v1): pi.jsonl (every pi event),
pi.stderr, workspace/ (final files), changes.diff, visible.txt, hidden.txt and
result.json. Runs are resumable and take the lab's .bench.lock (one GPU job at a time).
"""
import argparse, concurrent.futures, difflib, re, fcntl, importlib.util, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def load_bench(name):
    """The task module of a bench folder (benches/<name>/tasks.py): agentic-v1 or the held-out agentic-v2 and agentic-v3."""
    spec = importlib.util.spec_from_file_location(f'{name.replace("-", "_")}_tasks', ROOT / 'benches' / name / 'tasks.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = load_bench('agentic-v1')  # replaced by --bench in main()

SANDBOX = ROOT / 'benches' / 'agentic-v1' / 'sandbox.sb'
PI_HOME = ROOT / 'results' / 'agentic-pi-home'
TERSE = ("Be terse. Keep your reasoning short: decide and act with a tool call instead of deliberating. "
         "When you are done, reply in one or two lines.")
SUFFIX = ("\n\nWork only in the current folder; it is a fresh copy made for you. "
          "When you are finished, reply with a short summary of what you changed.")


def pi_home(ollama_url=''):
    """A private pi config for the bench, so the user's own pi settings and sessions are not used or changed.

    With ollama_url (an isolated Ollama, e.g. http://127.0.0.1:11500), a separate home whose ollama
    provider points there is used instead, so the default home keeps pointing at the system Ollama."""
    home = PI_HOME
    src = Path.home() / '.pi' / 'agent' / 'models.json'
    if ollama_url:
        home = PI_HOME.with_name(PI_HOME.name + '-' + ollama_url.rsplit(':', 1)[-1].strip('/'))
    home.mkdir(parents=True, exist_ok=True)
    if not (home / 'models.json').exists():
        shutil.copy(src, home / 'models.json')
    if ollama_url:
        cfg = json.loads((home / 'models.json').read_text())
        cfg['providers']['ollama']['baseUrl'] = ollama_url.rstrip('/') + '/v1'
        (home / 'models.json').write_text(json.dumps(cfg, indent=2))
    (home / 'settings.json').write_text(json.dumps({'quietStartup': 'header'}))
    return home


def summarize(events_path):
    """Count steps, tool calls, tokens and model time from pi's JSON events."""
    s = dict(assistant_messages=0, tool_calls=0, output_tokens=0, input_tokens=0, cache_read_tokens=0,
             model_ms=0, tools=[], stop_reasons=[], final_text='', errors=[])
    for line in Path(events_path).read_text(errors='replace').splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get('type')
        if t == 'tool_execution_start':
            s['tool_calls'] += 1
            s['tools'].append(e.get('toolName') or e.get('name') or (e.get('tool') or {}).get('name') or '?')
        elif t == 'message_end' and (e.get('message') or {}).get('role') == 'assistant':
            m = e['message']
            s['assistant_messages'] += 1
            u = m.get('usage') or {}
            s['output_tokens'] += u.get('output') or 0
            s['input_tokens'] += u.get('input') or 0
            s['cache_read_tokens'] += u.get('cacheRead') or 0
            s['model_ms'] += m.get('durationMs') or 0
            s['stop_reasons'].append(m.get('stopReason'))
            if m.get('errorMessage'):
                s['errors'].append(m['errorMessage'][:300])
            text = ''.join(c.get('text', '') for c in m.get('content') or [] if isinstance(c, dict) and c.get('type') == 'text')
            if text.strip():
                s['final_text'] = text.strip()[:2000]
    return s


def diff(task, workspace):
    out = []
    names = set(task['files']) | {str(p.relative_to(workspace)) for p in Path(workspace).rglob('*')
                                  if p.is_file() and '__pycache__' not in p.parts}
    for rel in sorted(names):
        before = task['files'].get(rel, '').splitlines(keepends=True)
        p = Path(workspace, rel)
        after = p.read_text(errors='replace').splitlines(keepends=True) if p.exists() else []
        out += difflib.unified_diff(before, after, 'a/' + rel, 'b/' + rel)
    return ''.join(out)


def attempt(args, task, name, tdir, home, exts, extra_env=None, model=None, start_from=None, instruction=None):
    """One pi run of one task in tdir/workspace; returns its result record."""
    key = f'{args.name}-{name}-{time.time_ns()}'  # log names stay apart when attempts run at once
    model = model or args.model
    ws = tdir / 'workspace'
    if start_from:  # carry on from an earlier run's files
        tdir.mkdir(parents=True)
        shutil.copytree(start_from, ws)
    else:
        ws.mkdir(parents=True)
        T.write_files(task, ws)
    cmd = ['/usr/bin/sandbox-exec', '-f', str(SANDBOX), '-D', f'WORKSPACE={ws}', '-D', f'SESSIONS={home}',
           '-D', f'HOME={Path.home()}', 'pi', '-p', '--model', model, '--thinking', args.thinking, '--offline',
           '--no-session', '--no-approve', '--no-context-files', '--no-extensions', '--no-skills',
           '--no-prompt-templates', *[x for e in exts for x in ('-e', e)], *(['--append-system-prompt', TERSE] if args.terse else []), '--mode', 'json', (instruction or task['instruction']) + SUFFIX]
    env = dict(os.environ, PI_OFFLINE='1', PI_CODING_AGENT_DIR=str(home), **(extra_env or {}))
    if exts:  # the extensions' own logs of what they did, kept with the run (written where the sandbox allows)
        env['PI_GUARD_LOG'] = str(home / f'guard-{key}.jsonl')
        env['PI_CODING_LOG'] = str(home / f'boost-{key}.jsonl')
    t0 = time.time()
    with open(tdir / 'pi.jsonl', 'w') as so, open(tdir / 'pi.stderr', 'w') as se:
        try:
            p = subprocess.run(cmd, cwd=ws, env=env, stdin=subprocess.DEVNULL, stdout=so, stderr=se, timeout=args.timeout)
            exit_code, timed_out = p.returncode, False
        except subprocess.TimeoutExpired:
            exit_code, timed_out = None, True
    wall = time.time() - t0
    vis_ok, vis_out = T.run_visible(ws)
    passed, total, hid_out = T.run_hidden(task, ws)
    (tdir / 'visible.txt').write_text(vis_out)
    (tdir / 'hidden.txt').write_text(hid_out)
    (tdir / 'changes.diff').write_text(diff(task, ws))
    stats = summarize(tdir / 'pi.jsonl')
    guard_log = home / f'guard-{key}.jsonl'
    if guard_log.exists():
        guard_log.replace(tdir / 'guard.jsonl')
        events = [json.loads(l) for l in (tdir / 'guard.jsonl').read_text().splitlines() if l.strip()]
        stats['guard'] = {k: sum(e['event'] == k for e in events) for k in ('adjusted', 'repaired', 'nudged', 'stall', 'out-of-nudges')}
    boost_log = home / f'boost-{key}.jsonl'
    if boost_log.exists():
        boost_log.replace(tdir / 'boost.jsonl')
        events = [json.loads(l) for l in (tdir / 'boost.jsonl').read_text().splitlines() if l.strip()]
        stats['boost'] = {k: sum(e['event'] == k for e in events) for k in sorted({e['event'] for e in events})}
    tests_changed = any(rel.startswith('test_') and Path(ws, rel).read_text() != text
                        for rel, text in task['files'].items() if Path(ws, rel).exists())
    rec = dict(task=name, area=task['area'], model=model, thinking=args.thinking, exit=exit_code,
               timed_out=timed_out, wall_s=round(wall, 1), visible_pass=vis_ok, hidden_passed=passed,
               hidden_total=total, solved=bool(total) and passed == total, original_tests_changed=tests_changed, **stats)
    return rec


def discussion(task, other, ok):
    """Round-2 request for --pair: the task again, plus the colleague's changes and visible test result."""
    d = (other / 'changes.diff').read_text(errors='replace')
    v = (other / 'visible.txt').read_text(errors='replace')
    return (task['instruction'] + "\n\nYou already worked on this task: the files are as you left them. A colleague worked on the same "
            "task at the same time, separately. Their changes:\n```diff\n" + (d[:6000] or '(no changes)') + "\n```\n"
            "Their run of the existing tests: " + ('passed' if ok else 'FAILED') + "\n```\n" + v[-1500:] + "\n```\n"
            "Compare the two solutions: where do they differ, and which handles the request and its edge cases better? "
            "Then improve your own files: keep what is right, take what is better from your colleague, and fix anything "
            "either of you missed. Run the tests before you finish.")


def by_generated_tests(args, task, tries, suites=2):
    """--pick tests: the model writes extra tests from the request and the starting code (no reference
    solution, no hidden tests), and the try passing the most of them is kept; ties keep the earlier try.
    Only tries that pass their visible tests compete. pick_bench.py measured this on saved tries."""
    import pick_bench as PB
    PB.OLLAMA = args.ollama_url or PB.OLLAMA  # the server already holding the model, never a second copy
    model = (args.model or '').removeprefix('ollama/')
    score = {id(r): 0 for r, _ in tries}
    for k in range(suites):
        fname = f'test_gen_extra{k}.py'
        try:
            text = PB.chat(model, PB.GEN_PROMPT.format(instruction=task['instruction'], files=PB.start_files(task), fname=fname),
                           PB.think_for(model, 'medium'), seed=k)
        except Exception as e:
            print(f'  pick: test writing failed: {e}', flush=True)
            continue
        m = re.findall(r'```(?:python)?\n(.*?)```', text, re.S)
        code = max(m, key=len) if m else text
        for r, ws in tries:
            if r['visible_pass']:
                score[id(r)] += len(PB.run_suite(ws, code, fname).get('passed', []))
    ok = [(r, ws) for r, ws in tries if r['visible_pass']]
    best = max(ok, key=lambda t: score[id(t[0])])[0]  # max keeps the first of equals
    print('  pick by generated tests: ' + ', '.join(f"try{i} {score[id(r)]}" for i, (r, _) in enumerate(tries, 1) if r['visible_pass']), flush=True)
    return best, 'generated tests ' + '/'.join(str(score[id(r)]) for r, _ in ok)


def several(args, task, name, tdir, home, exts):
    """--attempts N: N runs at once, keep the first whose visible tests pass (hidden tests are never used to pick).
    --escalate EFFORT: when no kept run passes its visible tests, run once more at that gpt-oss reasoning effort.
    --escalate-model MODEL: the same, handing that one more run to another model.
    --pair: after the 2 runs, each reads the other's changes and test result and revises its own, at once.
    Time is the wall time of the whole thing; tool calls and tokens are summed over every run."""
    tdir.mkdir(parents=True)
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(args.attempts) as ex:
        recs = list(ex.map(lambda k: attempt(args, task, name, tdir / f'try{k}', home, exts), range(1, args.attempts + 1)))
    pick = next((r for r in recs if r['visible_pass']), recs[0])
    picked_by = 'first visible pass'
    if args.pick == 'tests' and sum(r['visible_pass'] for r in recs) > 1:
        pick, picked_by = by_generated_tests(args, task, [(r, tdir / f'try{k}' / 'workspace') for k, r in enumerate(recs, 1)])
    discussed = False
    if args.pair:
        discussed = True
        with concurrent.futures.ThreadPoolExecutor(2) as ex:
            second = list(ex.map(lambda k: attempt(args, task, name, tdir / f'discuss{k}', home, exts,
                                                   start_from=tdir / f'try{k}' / 'workspace',
                                                   instruction=discussion(task, tdir / f'try{3 - k}', recs[2 - k]['visible_pass'])), (1, 2)))
        recs += second
        pick = next((r for r in second if r['visible_pass']), pick if pick['visible_pass'] else second[0])
    escalated = False
    if (args.escalate or args.escalate_model) and not pick['visible_pass']:
        r = attempt(args, task, name, tdir / 'escalated', home, exts,
                    {'PI_GUARD_GPT_OSS_EFFORT': args.escalate} if args.escalate else None, args.escalate_model or None)
        recs.append(r)
        escalated = True
        if r['visible_pass']:
            pick = r
    rec = dict(pick, wall_s=round(time.time() - t0, 1), tool_calls=sum(r['tool_calls'] for r in recs),
               output_tokens=sum(r['output_tokens'] for r in recs), picked=next(i for i, r in enumerate(recs) if r is pick) + 1, picked_by=picked_by, escalated=escalated, discussed=discussed,
               runs=[dict(model=r['model'], solved=r['solved'], visible_pass=r['visible_pass'], wall_s=r['wall_s']) for r in recs])
    return rec


def run(args):
    out_dir = ROOT / 'results' / f'{args.bench}-{args.name}'
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = (ROOT / '.bench.lock').open('w')
    if not args.no_lock:  # --no-lock is only for plumbing tests with a stand-in pi
        print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
    home = pi_home(args.ollama_url)
    meta = out_dir / 'meta.json'
    if not meta.exists():
        meta.write_text(json.dumps({
            'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'bench': args.bench, 'model': args.model, 'escalate_model': args.escalate_model or None, 'thinking': args.thinking,
            'pi_version': subprocess.run(['pi', '--version'], capture_output=True, text=True).stdout.strip(),
            'ollama_version': subprocess.run(['ollama', '--version'], capture_output=True, text=True).stdout.strip(),
            'ollama_url': args.ollama_url or 'http://127.0.0.1:11434',
            'terse': TERSE if args.terse else None,
            'timeout_s': args.timeout, 'extension': args.extension or None, 'sandbox': SANDBOX.read_text(), 'suffix': SUFFIX}, indent=1))
    exts = [e for e in args.extension.split(',') if e]
    names = [n for n in T.TASKS if not args.only or n in args.only.split(',')]
    if args.rerun:  # measure again: earlier task runs move to attempts/rerun-<time>/ (kept, never deleted)
        dest = out_dir / 'attempts' / f'rerun-{time.strftime("%Y%m%dT%H%M%S")}'
        for name in names:
            if (out_dir / name).exists():
                dest.mkdir(parents=True, exist_ok=True)
                (out_dir / name).replace(dest / name)
    for i, name in enumerate(names, 1):
        task = T.TASKS[name]
        tdir = out_dir / name
        if (tdir / 'result.json').exists():
            print(f'[{i}/{len(names)}] {name}: done before, skipped', flush=True)
            continue
        if tdir.exists():  # an interrupted attempt: keep it under attempts/
            (out_dir / 'attempts').mkdir(exist_ok=True)
            tdir.replace(out_dir / 'attempts' / f'{name}-{time.time_ns()}')
        if args.pair:
            args.attempts = 2
        if args.attempts > 1 or args.escalate or args.escalate_model or args.pair:
            rec = several(args, task, name, tdir, home, exts)
        else:
            rec = attempt(args, task, name, tdir, home, exts)
        passed, total, stats, timed_out = rec['hidden_passed'], rec['hidden_total'], rec, rec['timed_out']
        vis_ok = rec['visible_pass']
        (tdir / 'result.json').write_text(json.dumps(rec, indent=1))
        print(f"[{i}/{len(names)}] {name}: solved={rec['solved']} hidden={passed}/{total} visible={vis_ok} "
              f"wall={rec['wall_s']}s tools={stats['tool_calls']} out_tokens={stats['output_tokens']} "
              f"timeout={timed_out}", flush=True)


def report(args):
    rows, lines = [], []
    names = args.names.split(',')
    per = {}
    for n in names:
        d = ROOT / 'results' / f'{args.bench}-{n}'
        meta = json.loads((d / 'meta.json').read_text())
        rs = [json.loads(p.read_text()) for p in sorted(d.glob('*/result.json'))]
        per[n] = {r['task']: r for r in rs}
        solved = sum(r['solved'] for r in rs)
        tests = sum(r['hidden_passed'] for r in rs), sum(r['hidden_total'] or 0 for r in rs)
        wall = sum(r['wall_s'] for r in rs)
        rows.append(dict(name=n, model=meta['model'], thinking=meta['thinking'], tasks=len(rs), solved=solved,
                         hidden_tests=tests, total_wall_s=wall, tool_calls=sum(r['tool_calls'] for r in rs),
                         output_tokens=sum(r['output_tokens'] for r in rs), timeouts=sum(r['timed_out'] for r in rs),
                         tests_changed=sum(r['original_tests_changed'] for r in rs)))
    lines.append('| Run | Model | Thinking | Solved | Hidden tests | Total time | Tool calls | Output tokens | Timeouts | Changed given tests |')
    lines.append('|---|---|---|---:|---:|---:|---:|---:|---:|---:|')
    for r in rows:
        lines.append(f"| {r['name']} | {r['model']} | {r['thinking']} | {r['solved']}/{r['tasks']} | {r['hidden_tests'][0]}/{r['hidden_tests'][1]} | "
                     f"{r['total_wall_s'] / 60:.1f} min | {r['tool_calls']} | {r['output_tokens']} | {r['timeouts']} | {r['tests_changed']} |")
    lines += ['', '| Task | ' + ' | '.join(names) + ' |', '|---|' + '---|' * len(names)]
    for task in T.TASKS:
        cells = []
        for n in names:
            r = per[n].get(task)
            cells.append('—' if not r else f"{'✔' if r['solved'] else '✘'} {r['hidden_passed']}/{r['hidden_total']} · {r['wall_s']:.0f}s · {r['tool_calls']} tools")
        lines.append(f'| {task} | ' + ' | '.join(cells) + ' |')
    text = '\n'.join(lines) + '\n'
    out = ROOT / 'results' / f'compare-{args.out}'
    out.mkdir(parents=True, exist_ok=True)
    stem = 'agentic' if args.bench == 'agentic-v1' else args.bench
    (out / f'{stem}.md').write_text(text)
    (out / f'{stem}.json').write_text(json.dumps(rows, indent=1))
    print(text)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--name', required=True)
    r.add_argument('--model', required=True, help='pi model, e.g. ollama/qwen3.8:27b-mlx')
    r.add_argument('--thinking', default='off')
    r.add_argument('--only', default='')
    r.add_argument('--timeout', type=int, default=1200)
    r.add_argument('--no-lock', action='store_true', help='do not take .bench.lock: only when the caller already holds it '
                   '(parallel_requests_bench.py agents) or for plumbing tests with a stand-in pi')
    r.add_argument('--terse', action='store_true', help="append a terse instruction to pi's system prompt (short reasoning, act, one- or two-line reply)")
    r.add_argument('--ollama-url', default='', help='point pi at another Ollama, e.g. an isolated one on http://127.0.0.1:11500')
    r.add_argument('--rerun', action='store_true', help='measure again: earlier task runs move to attempts/rerun-<time>/')
    r.add_argument('--attempts', type=int, default=1, help='runs per task at once; keep the first that passes its visible tests')
    r.add_argument('--pick', choices=['first', 'tests'], default='first', help='with --attempts: tests = keep the try passing the most model-written tests')
    r.add_argument('--escalate', default='', help='gpt-oss effort (e.g. high) for one more run when visible tests still fail')
    r.add_argument('--escalate-model', default='', help='another pi model (e.g. ollama/qwen3.8:27b-mlx) for one more run when visible tests still fail')
    r.add_argument('--pair', action='store_true', help='two runs at once, then each sees the other\'s changes and test result and revises its own (2 rounds)')
    r.add_argument('--extension', default='', help='pi extensions to load for the run, comma-separated, e.g. ~/Code/pi-core/local-model-guard/index.ts')
    p = sub.add_parser('report')
    p.add_argument('--names', required=True)
    p.add_argument('--out', required=True)
    for sp in (r, p):
        sp.add_argument('--bench', default='agentic-v1', help='task folder under benches/: agentic-v1, or agentic-v2 or agentic-v3 (held out)')
    a = ap.parse_args()
    global T
    T = load_bench(a.bench)
    {'run': run, 'report': report}[a.cmd](a)


if __name__ == '__main__':
    main()
