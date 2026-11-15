"""Agentic coding bench: a model works on small repositories with its own tools (pi).

Each task (benches/agentic-v1/tasks.py) is copied to a fresh workspace, pi runs once
with the task's instruction inside a macOS sandbox (writes only in the workspace,
no network except localhost), then hidden tests decide the grade.

    .venv/bin/python agent_bench.py run --name NAME --model ollama/qwen3.8:27b-mlx [--thinking off]
    .venv/bin/python agent_bench.py report --names A,B --out OUT

Kept per run, under results/agentic-v1-NAME/TASK/: pi.jsonl (every pi event),
pi.stderr, workspace/ (final files), changes.diff, visible.txt, hidden.txt and
result.json. Runs are resumable and take the lab's .bench.lock (one GPU job at a time).
"""
import argparse, difflib, fcntl, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'benches' / 'agentic-v1'))
import tasks as T  # noqa: E402

SANDBOX = ROOT / 'benches' / 'agentic-v1' / 'sandbox.sb'
PI_HOME = ROOT / 'results' / 'agentic-pi-home'
SUFFIX = ("\n\nWork only in the current folder; it is a fresh copy made for you. "
          "When you are finished, reply with a short summary of what you changed.")


def pi_home():
    """A private pi config for the bench, so the user's own pi settings and sessions are not used or changed."""
    PI_HOME.mkdir(parents=True, exist_ok=True)
    src = Path.home() / '.pi' / 'agent' / 'models.json'
    if not (PI_HOME / 'models.json').exists():
        shutil.copy(src, PI_HOME / 'models.json')
    (PI_HOME / 'settings.json').write_text(json.dumps({'quietStartup': 'header'}))
    return PI_HOME


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


def run(args):
    out_dir = ROOT / 'results' / f'agentic-v1-{args.name}'
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = (ROOT / '.bench.lock').open('w')
    if not args.no_lock:  # --no-lock is only for plumbing tests with a stand-in pi
        print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
    home = pi_home()
    meta = out_dir / 'meta.json'
    if not meta.exists():
        meta.write_text(json.dumps({
            'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'model': args.model, 'thinking': args.thinking,
            'pi_version': subprocess.run(['pi', '--version'], capture_output=True, text=True).stdout.strip(),
            'ollama_version': subprocess.run(['ollama', '--version'], capture_output=True, text=True).stdout.strip(),
            'timeout_s': args.timeout, 'extension': args.extension or None, 'sandbox': SANDBOX.read_text(), 'suffix': SUFFIX}, indent=1))
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
        ws = tdir / 'workspace'
        ws.mkdir(parents=True)
        T.write_files(task, ws)
        cmd = ['/usr/bin/sandbox-exec', '-f', str(SANDBOX), '-D', f'WORKSPACE={ws}', '-D', f'SESSIONS={home}',
               '-D', f'HOME={Path.home()}', 'pi', '-p', '--model', args.model, '--thinking', args.thinking, '--offline',
               '--no-session', '--no-approve', '--no-context-files', '--no-extensions', '--no-skills',
               '--no-prompt-templates', *(['-e', args.extension] if args.extension else []), '--mode', 'json', task['instruction'] + SUFFIX]
        env = dict(os.environ, PI_OFFLINE='1', PI_CODING_AGENT_DIR=str(home))
        if args.extension:  # the guard's own log of what it did, kept with the run (written where the sandbox allows)
            env['PI_GUARD_LOG'] = str(home / f'guard-{args.name}-{name}.jsonl')
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
        guard_log = home / f'guard-{args.name}-{name}.jsonl'
        if guard_log.exists():
            guard_log.replace(tdir / 'guard.jsonl')
            events = [json.loads(l) for l in (tdir / 'guard.jsonl').read_text().splitlines() if l.strip()]
            stats['guard'] = {k: sum(e['event'] == k for e in events) for k in ('adjusted', 'repaired', 'nudged', 'stall', 'out-of-nudges')}
        tests_changed = any(rel.startswith('test_') and Path(ws, rel).read_text() != text
                            for rel, text in task['files'].items() if Path(ws, rel).exists())
        rec = dict(task=name, area=task['area'], model=args.model, thinking=args.thinking, exit=exit_code,
                   timed_out=timed_out, wall_s=round(wall, 1), visible_pass=vis_ok, hidden_passed=passed,
                   hidden_total=total, solved=bool(total) and passed == total, original_tests_changed=tests_changed, **stats)
        (tdir / 'result.json').write_text(json.dumps(rec, indent=1))
        print(f"[{i}/{len(names)}] {name}: solved={rec['solved']} hidden={passed}/{total} visible={vis_ok} "
              f"wall={rec['wall_s']}s tools={stats['tool_calls']} out_tokens={stats['output_tokens']} "
              f"timeout={timed_out}", flush=True)


def report(args):
    rows, lines = [], []
    names = args.names.split(',')
    per = {}
    for n in names:
        d = ROOT / 'results' / f'agentic-v1-{n}'
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
    (out / 'agentic.md').write_text(text)
    (out / 'agentic.json').write_text(json.dumps(rows, indent=1))
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
    r.add_argument('--no-lock', action='store_true', help=argparse.SUPPRESS)
    r.add_argument('--rerun', action='store_true', help='measure again: earlier task runs move to attempts/rerun-<time>/')
    r.add_argument('--extension', default='', help='a pi extension to load for the run, e.g. ~/Code/pi-core/local-model-guard/index.ts')
    p = sub.add_parser('report')
    p.add_argument('--names', required=True)
    p.add_argument('--out', required=True)
    a = ap.parse_args()
    {'run': run, 'report': report}[a.cmd](a)


if __name__ == '__main__':
    main()
