"""The frozen DeepSeek reference (asked for on 2026-11-24): DeepSeek's hosted deepseek-flash, served by DeepSeek through
benches/deepseek_proxy.py, on every bench, so a future local model can be compared with DeepSeek as it was then.

Reads every DeepSeek result folder in results/ and writes docs/deepseek-reference.md: the score of each run at each
reasoning setting (max: thinking at DeepSeek's highest effort; off: no thinking), the runs that count, what went wrong,
and a fingerprint of each bench's files, so a later run can tell whether it is still graded by the same bench.
Nothing is typed in by hand: rerun this after any regrade.

    .venv/bin/python benches/deepseek_report.py            # docs/deepseek-reference.md
    .venv/bin/python benches/deepseek_report.py --public   # the same without the expected answers (for the public repo,
                                                           # where the benches are encrypted): failures by check count only
"""
import argparse, hashlib, json, os, re, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / 'results'
BENCHES = Path(os.environ.get('BENCHES_DIR') or ROOT / 'benches')
EFFORTS = ('max', 'off')
# the mini (an assistant bot) the assistant benches drive; MINI_PY overrides
MINI = Path(os.environ.get('MINI_PY') or Path.home() / 'Documents/ChatGPT/DropBox/dropbox/mini-v33.py')

# Runs that don't count, and why. Every run is still listed in the run details.
EXCLUDE = {
    ('agentic-v4', 'max', 'fix1'): 'read r1\'s hidden-test output (results/peek-audit.json)',
    ('agentic-v5', 'max', 'r1'): 'read the exercise\'s own tests in benches/agentic-v5 (forth 54/54 does not count)',
    ('agentic-v5', 'max', 'r2'): 'ran before the 2026-11-25 sandbox (home folder and shared /tmp closed)',
}
SKIP = re.compile(r'smoke')   # harness checks, not scores
BENCH_ORDER = ['vision-v1', 'vision-v2', 'vision-v3', 'family', 'family-hard', 'family-expert', 'family-master',
               'family-grandmaster', 'mini-en', 'mini-lang', 'agentic-v1', 'agentic-v2', 'agentic-v3', 'agentic-v4',
               'agentic-v5', 'agentic-v6', 'arena-analysis-v1', 'arena-v2', 'arena-v3']


def fingerprint(paths):
    h = hashlib.sha256()
    for p in sorted(map(Path, paths)):
        files = sorted(f for f in (p.rglob('*') if p.is_dir() else [p] if p.exists() else []) if f.is_file()
                       and '__pycache__' not in f.parts and 'sources' not in f.parts and not f.name.startswith('test_'))
        for f in files:
            h.update(str(f.relative_to(ROOT) if ROOT in f.parents else f.name).encode())
            h.update(f.read_bytes())
    return h.hexdigest()[:12]


def bench_files(bench):
    b = BENCHES
    if bench.startswith(('agentic-', 'arena-')):
        return [b / bench, ROOT / 'agent_bench.py'] + ([ROOT / 'arena'] if bench.startswith('arena-') else [])
    if bench.startswith('vision-'):
        return [b / bench, ROOT / 'vision_bench.py']
    if bench.startswith('family'):
        suite = {'family': 'family.py'}.get(bench, bench.replace('-', '_') + '.py')
        return [b / 'dropbox' / suite, b / 'dropbox' / 'cases.py', ROOT / 'dropbox_bench.py', MINI]
    return [b / 'dropbox' / 'cases.py', ROOT / 'dropbox_bench.py', MINI]


def natural(s):
    return [int(x) if x.isdigit() else x for x in re.split(r'(\d+)', s)]


def collect():
    """{(bench, effort): [run, ...]}; a run is dict(run, passed, total, unit, fails=[(case, [missed checks])], extra)."""
    runs = {}
    add = lambda b, e, r: runs.setdefault((b, e), []).append(r)
    for e in EFFORTS:
        for d in sorted(RES.glob(f'*-deepseek-flash-{e}-*'), key=lambda d: natural(d.name)):
            m = re.match(rf'((?:agentic|arena|arena-analysis)-v\d+)-deepseek-flash-{e}-(\w+)$', d.name)
            if m and (d / 'meta.json').exists() and not SKIP.search(d.name):
                tasks = [json.loads(p.read_text()) for p in sorted(d.glob('*/result.json'), key=lambda p: natural(p.parent.name))]
                if not tasks:
                    continue
                meta = json.loads((d / 'meta.json').read_text())
                by_round = m.group(1) in ('arena-v2', 'arena-v3')
                running = m.group(2).startswith('t') and not (d / 'DONE').exists()   # t runs: the suite marks a finished bench
                add(m.group(1), e, dict(run=m.group(2) + (', running' if running else ''), thinking=meta.get('thinking'), started=meta.get('started'),
                                        passed=sum(t['solved'] for t in tasks), total=len(tasks), unit='tasks',
                                        rounds=[t['hidden_passed'] for t in tasks] if by_round else None,
                                        round_of=tasks[0]['hidden_total'] if by_round else None,
                                        fails=[(t['task'], [f"{t['hidden_passed']}/{t['hidden_total']} hidden tests"
                                                            + (' (timed out)' if t.get('timed_out') else '')])
                                               for t in tasks if not t['solved']]))
                continue
            m = re.match(rf'dropbox-([\w-]+?)-deepseek-flash-{e}-(\w+?)-(\w+)-deepseek-flash-{e}$', d.name)
            if m and (d / 'results.json').exists() and not SKIP.search(d.name):
                j = json.loads((d / 'results.json').read_text())
                s = j['summary']
                bench = s['job'] if s['job'] != 'mini' else f'mini-{m.group(3)}'
                fails = []
                for r in j['rows']:
                    if r.get('ok'):
                        continue
                    steps = r.get('steps')
                    if isinstance(steps, str):
                        try:
                            steps = json.loads(steps.replace("'", '"'))
                        except ValueError:
                            steps = None
                    missed = [x[0] for x in steps if not x[1]] if isinstance(steps, list) else (r.get('why') or [])
                    fails.append((r['id'], missed))
                add(bench, e, dict(run=m.group(2), passed=s['passed'], total=s['total'], unit='points' if 'family-' in s['job'] else 'cases',
                                   regraded=s.get('regraded'), fails=fails))
                continue
            m = re.match(rf'(vision-v\d+)-deepseek-flash-{e}-think-(on|off)(?:-before-(\w+))?$', d.name)
            if m and (d / 'results.json').exists():
                done = list(RES.glob(f'deepseek-suite-flash-{e}-*/{m.group(1)}.done'))
                if m.group(3):
                    label = 'r1'   # the run before a rerun, copied aside by the suite
                elif done:
                    label = done[0].parent.name.rsplit('-', 1)[1]
                elif any(RES.glob(d.name + '-before-*')):
                    continue       # being rerun right now
                else:
                    label = 'r1'
                rows = json.loads((d / 'results.json').read_text())['rows']
                add(m.group(1), e, dict(run=label, passed=sum(bool(r['correct']) for r in rows), total=len(rows), unit='images',
                                        fails=[(r['id'], [f"expected {r['expected']!r}, said {str(r['answer'])[:60]!r}"]) for r in rows if not r['correct']]))
    for k in runs:
        runs[k].sort(key=lambda r: natural(r['run']))
    return runs


def usage():
    rows = [json.loads(l) for l in (RES / 'deepseek-usage.jsonl').read_text().splitlines() if l.strip()]
    tok = lambda k: sum((r.get('usage') or {}).get(k, 0) for r in rows)
    return dict(requests=len(rows), served=sorted({r.get('served') for r in rows if r.get('served')}),
                peak=sum(1 for r in rows if r.get('peak')), cost=sum(r.get('cost', 0) for r in rows),
                first=rows[0]['t'] if rows else None, last=rows[-1]['t'] if rows else None,
                prompt=tok('prompt_tokens'), completion=tok('completion_tokens'))


def score(r):
    if r.get('rounds'):
        return ' '.join(map(str, r['rounds'])) + f" (of {r['round_of']}, by round)"
    return f"{r['passed']}/{r['total']}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--public', action='store_true')
    ap.add_argument('--out', default=str(ROOT / 'docs' / 'deepseek-reference.md'))
    a = ap.parse_args()
    runs, u = collect(), usage()
    head = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    L = ['# DeepSeek reference: hosted deepseek-flash, November 2026', '',
         '*Written by `benches/deepseek_report.py` from the result files; rerun it rather than editing this page.*', '',
         'The model is DeepSeek\'s own hosted **deepseek-flash** (DeepSeek-V4.1-Flash-1017 in DeepSeek\'s docs on 2026-11-24), '
         'reached through `benches/deepseek_proxy.py`. Two settings: **max** (thinking on, DeepSeek\'s highest reasoning effort, '
         'the ceiling) and **off** (no thinking: the fair comparison for a local model run without thinking). '
         'The benches were made harder until max failed. This is kept as a fixed point, so a future local model can be '
         'compared with DeepSeek as it was then.', '',
         '## How it ran', '',
         f"- {u['requests']} requests from {u['first']} to {u['last']}, ${u['cost']:.2f} in all, "
         f"{u['peak']} of them in DeepSeek's peak hours (every bench waits for off-peak time).",
         f"- Model name DeepSeek returned: {', '.join(u['served'])}. The API reports no finer version, so a later "
         '`deepseek-flash` may be a different model.',
         f"- Tokens: {u['prompt']:,} prompt, {u['completion']:,} completion (thinking included).",
         '- Coding and arena benches run in the pi coding agent 1.1.0 inside a macOS sandbox (writes only in '
         'the task folder, network only to the model\'s port). Runs labelled t1 and later use pi\'s own `--thinking max|off` on '
         'model `deepseek/flash`, so their meta.json records the setting; the earlier r runs used the fixed alias `flash-max`, '
         'whose meta.json says `thinking: off` (pi\'s view) although DeepSeek thought at max.',
         '- The assistant benches (family to grandmaster, mini) drive an assistant bot, mini v3.3, with the model behind it.',
         f"- Each bench has a **fingerprint**: a hash of its tasks, graders and harness when this page was written (repo {head}). "
         'A later score compares with these only if the fingerprint is the same.', '',
         '## Scores', '',
         'Each cell is every counted run, in order. Coding and arena benches count tasks solved (every hidden test passed); '
         'family benches count graded steps; vision counts images.', '',
         '| bench | max | off | fingerprint |', '|---|---|---|---|']
    for b in BENCH_ORDER:
        cells = []
        for e in EFFORTS:
            rs = [r for r in runs.get((b, e), []) if (b, e, r['run']) not in EXCLUDE]
            cells.append(', '.join(f"{score(r)} ({r['run']})" for r in rs) or '-')
        if any(c != '-' for c in cells):
            L.append(f"| {b} | {cells[0]} | {cells[1]} | `{fingerprint(bench_files(b))}` |")
    L += ['', 'Runs left out: ' + '; '.join(f"{b} {e} {r}: {why}" for (b, e, r), why in EXCLUDE.items()) + '.', '']

    L += ['## What it got wrong', '']
    if a.public:
        L += ['The checks it missed name the expected answers, so they are left out here: the benches are encrypted to keep '
              'them out of training data. Counts of missed checks per case:', '']
    for e in EFFORTS:
        items = []
        for b in BENCH_ORDER:
            if b in ('arena-v2', 'arena-v3'):
                continue
            for r in runs.get((b, e), []):
                if (b, e, r['run']) in EXCLUDE:
                    continue
                for case, missed in r['fails']:
                    items.append(f"- {b} {r['run']} **{case}**: " + (f"{len(missed)} check(s) missed" if a.public else '; '.join(map(str, missed))))
        if items:
            L += [f'### {e}', ''] + items + ['']
    L += ['## Known limits', '',
          '- **arena-v2**: the model can run the hidden grader (`arena.grade`) from the repo, so its perfect rounds are an upper bound. '
          'arena-v3 is the arena bench to compare on. (The arena benches use the Victris Arena game, which is not in the public repo.)',
          '- **arena-v3** counts the opponents and tests the bot beats, out of 13; the hardest is winning 70% against '
          'league-deepseek, a bot DeepSeek wrote earlier.',
          '- A single run can move by a task or two; where there are several runs, compare with their spread.', '']
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text('\n'.join(L))
    print(f'wrote {a.out} ({len(L)} lines)')


if __name__ == '__main__':
    main()
