#!/usr/bin/env python3
"""Choosing between tries: when an agent tries a task twice (or more), which try do we keep?

The existing tests can't tell good tries from bad ones: in agentic runs nearly every try passes the
visible tests, right or wrong. This bench measures better ways to choose, on the tries already saved
under results/agentic-*/ (every workspace a gpt-oss run left: single runs, try1/try2, discussions,
escalations), so no agent has to run again. Hidden tests grade each try and are never shown to a picker.

Pickers, for a group of k tries of the same task:
  random     keep any (what "keep the first" does when all pass the visible tests)
  visible    keep one that passes the visible tests
  gen-tests  a model writes extra tests from the request alone (3 suites, never seeing a try);
             keep the try passing most of them
  agree      CodeT-style: tries that pass exactly the same generated tests form a group; keep the
             group with the largest (size x tests passed): independent tries agreeing is evidence
  judge      a model reads the request and both diffs and picks one (k=2 only)
  oracle     a right try whenever there is one: the best any picker could do

    .venv/bin/python pick_bench.py collect --bench agentic-v1      # grade every saved try (no model)
    .venv/bin/python pick_bench.py gen --bench agentic-v1          # write extra tests, run every try on them
    .venv/bin/python pick_bench.py judge --bench agentic-v1        # judge pairs of tries that differ
    .venv/bin/python pick_bench.py report

Results: results/pick-bench/. gen and judge take the lab's .bench.lock and use the system Ollama.
"""
import argparse, fcntl, hashlib, itertools, json, random, re, shutil, subprocess, sys, tempfile, time
from pathlib import Path
import agent_bench as AB
from parallel_requests_bench import api

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results' / 'pick-bench'
OLLAMA = 'http://127.0.0.1:11434'
RUNNER = r'''
import json, sys, unittest
suite = unittest.defaultTestLoader.discover('.', pattern=sys.argv[1])
ids = []
def walk(s):
    for t in s:
        walk(t) if isinstance(t, unittest.TestSuite) else ids.append(t.id())
walk(suite)
res = unittest.TestResult()
suite.run(res)
bad = {t.id() for t, _ in res.failures + res.errors}
broken = [t.id() for t, _ in res.errors if 'ModuleImportFailure' in t.id() or '_FailedTest' in type(t).__name__]
print('@@' + json.dumps(dict(passed=[i for i in ids if i not in bad], total=len(ids), broken=broken)))
'''


def bench_mod(bench):
    return AB.load_bench(bench)


def start_files(task):
    return '\n\n'.join(f'--- {rel} ---\n{text}' for rel, text in task['files'].items())


def reference_ws(task, dest):
    AB.T.write_files(task, dest)
    for rel, change in task['fix'].items():
        p = Path(dest, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(change(p.read_text() if p.exists() else ''))  # a reference fix may create new files


# ---------- collect ----------

def collect(args):
    T = AB.T = bench_mod(args.bench)
    rows = []
    for ws in sorted(ROOT.glob(f'results/{args.bench}-*/*/workspace')) + sorted(ROOT.glob(f'results/{args.bench}-*/*/*/workspace')):
        run = ws.relative_to(ROOT / 'results').parts[0]
        meta = ROOT / 'results' / run / 'meta.json'
        if not meta.exists() or 'attempts' in ws.parts:
            continue
        model = json.loads(meta.read_text())['model'].split('/')[-1]
        if args.model and model != args.model:
            continue
        task_name = ws.relative_to(ROOT / 'results' / run).parts[0]
        task = T.TASKS.get(task_name)
        if not task:
            continue
        d = AB.diff(task, ws)
        if not d.strip():  # no change at all: a crashed run (server error), not a real try
            continue
        passed, total, _ = T.run_hidden(task, ws)
        vis, _ = T.run_visible(ws)
        rows.append(dict(task=task_name, path=str(ws.relative_to(ROOT)), run=run, model=model,
                         solved=bool(total) and passed == total, visible=vis,
                         digest=hashlib.sha1(d.encode()).hexdigest()[:12]))
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / f'tries-{args.bench}.json').write_text(json.dumps(rows, indent=1))
    by = {}
    for r in rows:
        by.setdefault(r['task'], []).append(r)
    for t, rs in by.items():
        print(f"{t:22} tries={len(rs):3} solved={sum(r['solved'] for r in rs):3} visible-pass={sum(r['visible'] for r in rs):3}")


# ---------- model calls ----------

def chat(model, prompt, think, seed=0):
    body = dict(model=model, stream=False, keep_alive='15m', messages=[dict(role='user', content=prompt)],
                options=dict(temperature=0.7 if seed else 0, seed=seed, num_ctx=32768, num_predict=12000))
    if think is not None:
        body['think'] = think
    r = api(OLLAMA, 'chat', body, timeout=1800)
    return r['message'].get('content', '')


def lock():
    f = (ROOT / '.bench.lock').open('w')
    print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
    fcntl.flock(f, fcntl.LOCK_EX)
    return f


def think_for(model, effort):
    return effort if model.startswith('gpt-oss') else (effort != 'off')


# ---------- generated tests ----------

GEN_PROMPT = '''You are reviewing a coding task before anyone works on it. Here is the request a developer received:

<request>
{instruction}
</request>

Here are the project files as they are now, before the change:

{files}

Write extra unit tests (Python unittest, one file) that a correct, complete solution of the request must pass,
and that a wrong or half-done solution would likely fail. Cover the edge cases the request mentions or implies.
Only use names that exist in the files or that the request explicitly asks for. Do not test anything the request
leaves open. Keep each test small and independent. The file will be saved as {fname} next to the project files.
Reply with the file only, in one ```python block.'''


def run_suite(ws, code, fname):
    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(ws, d, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__', '.pi*', 'test_*.py'))
        Path(d, fname).write_text(code)
        Path(d, '_pick_runner.py').write_text(RUNNER)
        try:
            r = subprocess.run([sys.executable, '-I', '_pick_runner.py', fname], cwd=d, capture_output=True, text=True, timeout=90)
        except subprocess.TimeoutExpired:
            return dict(passed=[], total=0, timeout=True)
        m = re.search(r'^@@(.*)$', r.stdout, re.M)
        return json.loads(m[1]) if m else dict(passed=[], total=0, error=(r.stderr or r.stdout)[-300:])


def gen(args):
    T = AB.T = bench_mod(args.bench)
    tries = json.loads((OUT / f'tries-{args.bench}.json').read_text())
    _l = lock()
    tag = args.model.replace(':', '-')
    out = OUT / f'gen-{args.bench}-{tag}.json'
    data = json.loads(out.read_text()) if out.exists() else {}
    for name, task in T.TASKS.items():
        for k in range(args.suites):
            key = f'{name}#{k}'
            if key in data:
                continue
            fname = f'test_gen_extra{k}.py'
            t0 = time.time()
            text = chat(args.model, GEN_PROMPT.format(instruction=task['instruction'], files=start_files(task), fname=fname),
                        think_for(args.model, args.effort), seed=k)
            m = re.findall(r'```(?:python)?\n(.*?)```', text, re.S)
            code = max(m, key=len) if m else text
            with tempfile.TemporaryDirectory() as ref:
                reference_ws(task, ref)
                ref_res = run_suite(ref, code, fname)
            with tempfile.TemporaryDirectory() as st:
                T.write_files(task, st)
                start_res = run_suite(st, code, fname)
            per = {}
            for r in tries:
                if r['task'] == name:
                    per[r['path']] = run_suite(ROOT / r['path'], code, fname)['passed']
            data[key] = dict(code=code, gen_s=round(time.time() - t0), total=ref_res.get('total', 0),
                             reference_passed=len(ref_res.get('passed', [])), start_passed=len(start_res.get('passed', [])),
                             reference_ok=ref_res.get('passed', []), tries=per)
            print(f"{key:26} tests={ref_res.get('total', 0):2} reference passes {len(ref_res.get('passed', []))}, "
                  f"starting code passes {len(start_res.get('passed', []))}  ({data[key]['gen_s']}s)", flush=True)
            out.write_text(json.dumps(data, indent=1))


# ---------- judge ----------

JUDGE_PROMPT = '''Two developers each worked separately on the same request. Decide which result is correct and complete.

<request>
{instruction}
</request>

Project files before the change:

{files}

Changes by developer A:
```diff
{a}
```

Changes by developer B:
```diff
{b}
```

Both say the existing tests pass. Check each change against the request: missed cases, wrong edge-case behaviour,
broken existing behaviour. Then answer on the last line with exactly "WINNER: A" or "WINNER: B".'''


def judge(args):
    T = AB.T = bench_mod(args.bench)
    tries = json.loads((OUT / f'tries-{args.bench}.json').read_text())
    _l = lock()
    tag = args.model.replace(':', '-')
    out = OUT / f'judge-{args.bench}-{tag}.json'
    data = [d for d in (json.loads(out.read_text()) if out.exists() else []) if d['pick']]  # no verdict: judge again
    done = {(d['a'], d['b']) for d in data}
    rng = random.Random(7)
    for name, task in T.TASKS.items():
        rs = [r for r in tries if r['task'] == name and r['visible']]
        good, bad = [r for r in rs if r['solved']], [r for r in rs if not r['solved']]
        pairs = [(g, b) for g in good for b in bad if g['digest'] != b['digest']]
        rng.shuffle(pairs)
        for g, b in pairs[:args.pairs]:
            a, bb = (g, b) if rng.random() < .5 else (b, g)  # which side is right is random
            if (a['path'], bb['path']) in done:
                continue
            da = AB.diff(task, ROOT / a['path'])[:9000]
            db = AB.diff(task, ROOT / bb['path'])[:9000]
            t0 = time.time()
            text = chat(args.model, JUDGE_PROMPT.format(instruction=task['instruction'], files=start_files(task), a=da, b=db),
                        think_for(args.model, args.effort))
            m = re.findall(r'WINNER:\s*\**\s*([AB])', text)
            if not m and args.model.startswith('gpt-oss'):  # reasoning used the whole budget: ask again with less
                text = chat(args.model, JUDGE_PROMPT.format(instruction=task['instruction'], files=start_files(task), a=da, b=db), 'low')
                m = re.findall(r'WINNER:\s*\**\s*([AB])', text)
            pick = m[-1] if m else None
            right = 'A' if a['solved'] else 'B'
            data.append(dict(task=name, a=a['path'], b=bb['path'], right=right, pick=pick, correct=pick == right, s=round(time.time() - t0)))
            print(f"{name:22} right={right} pick={pick} {'✓' if pick == right else '✗'} ({data[-1]['s']}s)", flush=True)
            out.write_text(json.dumps(data, indent=1))


# ---------- report ----------

def expected(group, score):
    """Solved rate of the kept try when the picker keeps a top-scoring try (ties: any of them)."""
    best = max(score(r) for r in group)
    top = [r for r in group if score(r) == best]
    return sum(r['solved'] for r in top) / len(top)


def report(args):
    lines = []
    for tf in sorted(OUT.glob('tries-*.json')):
        bench = tf.stem[len('tries-'):]
        tries = json.loads(tf.read_text())
        for gf in sorted(OUT.glob(f'gen-{bench}-*.json')) or [None]:
            gen = json.loads(gf.read_text()) if gf else {}
            gmodel = gf.stem[len(f'gen-{bench}-'):] if gf else '-'
            jf = OUT / f'judge-{bench}-{gmodel}.json'
            jud = json.loads(jf.read_text()) if jf.exists() else []
            lines += [f'## {bench}, tests/judge by {gmodel}', '',
                      '| Task | tries | right | gen tests (reference passes) | k | random | visible | gen-tests | agree | judge | oracle |',
                      '|---|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|']
            tot = {}
            for task in dict.fromkeys(r['task'] for r in tries):
                rs = [r for r in tries if r['task'] == task]
                suites = [v for k, v in gen.items() if k.split('#')[0] == task]
                # only generated tests the reference solution passes count (a wrong test says nothing)
                passed = {r['path']: frozenset(f'{i}:{t}' for i, s in enumerate(suites) for t in s['tries'].get(r['path'], []) if t in s['reference_ok'])
                          for r in rs}
                desc = ', '.join(f"{s['total']} ({s['reference_passed']})" for s in suites) or '-'
                jr = [d for d in jud if d['task'] == task]
                jrate = sum(d['correct'] for d in jr) / len(jr) if jr else None
                for k in (2, 4):
                    rng = random.Random(k)
                    groups = [list(c) for c in itertools.combinations(rs, k)] if k == 2 else [rng.sample(rs, k) for _ in range(3000)]
                    res = dict(random=0, visible=0, gen=0, agree=0, judge=0, oracle=0)
                    for g in groups:
                        res['random'] += sum(r['solved'] for r in g) / k
                        res['visible'] += expected(g, lambda r: r['visible'])
                        res['gen'] += expected(g, lambda r: (r['visible'], len(passed[r['path']])))
                        clusters = {}
                        for r in g:
                            clusters.setdefault(passed[r['path']], []).append(r)
                        best = max(clusters.items(), key=lambda kv: (len(kv[1]) * len(kv[0]), len(kv[0])))
                        res['agree'] += sum(r['solved'] for r in best[1]) / len(best[1])
                        res['oracle'] += any(r['solved'] for r in g)
                        if k == 2:  # judge on visible-passing pairs that differ; elsewhere it falls back to visible
                            a, b = g
                            if a['visible'] and b['visible'] and a['solved'] != b['solved'] and jrate is not None:
                                res['judge'] += jrate
                            else:
                                res['judge'] += expected(g, lambda r: r['visible'])
                    n = len(groups)
                    row = {x: res[x] / n for x in res}
                    for x in row:
                        tot.setdefault(k, {}).setdefault(x, []).append(row[x])
                    jcell = f"{row['judge']:.0%}" + (f" ({len(jr)}p)" if jr else '') if k == 2 else '—'
                    lines.append(f"| {task} | {len(rs)} | {sum(r['solved'] for r in rs)} | {desc if k == 2 else ''} | {k} | {row['random']:.0%} | "
                                 f"{row['visible']:.0%} | {row['gen']:.0%} | {row['agree']:.0%} | {jcell} | {row['oracle']:.0%} |")
            for k, t in tot.items():
                avg = {x: sum(v) / len(v) for x, v in t.items()}
                lines.append(f"| **average** | | | | {k} | {avg['random']:.0%} | {avg['visible']:.0%} | {avg['gen']:.0%} | {avg['agree']:.0%} | "
                             f"{(format(avg['judge'], '.0%') if k == 2 else '—')} | {avg['oracle']:.0%} |")
            lines.append('')
    text = '\n'.join(lines)
    (OUT / 'report.md').write_text(text)
    print(text)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd', choices=['collect', 'gen', 'judge', 'report'])
    p.add_argument('--bench', default='agentic-v1')
    p.add_argument('--model', default='', help='collect: only tries by this model (e.g. gpt-oss:20b); gen/judge: the model that writes tests or judges')
    p.add_argument('--effort', default='medium', help='gpt-oss reasoning effort for gen/judge (other models: off or on)')
    p.add_argument('--suites', type=int, default=3)
    p.add_argument('--pairs', type=int, default=12, help='judge: right/wrong pairs per task')
    a = p.parse_args()
    if a.cmd in ('gen', 'judge') and not a.model:
        a.model = 'gpt-oss:20b'
    {'collect': collect, 'gen': gen, 'judge': judge, 'report': report}[a.cmd](a)


if __name__ == '__main__':
    main()
