"""agentic-v6: eight much harder coding tasks, same format and grading as agentic-v1..v5.

Written on 2026-11-24 because hosted DeepSeek V4.1 Flash (max reasoning) solved 29/30 tasks of v1..v5 in
1-3 minutes each (v4: 51/52 hidden tests, v5: 311/312), so v1..v5 no longer tell strong models apart.
v6 is meant to be failed often by strong models (target: 30-50% solved) and nearly always by local 27B models.
The tasks are hard for real reasons, never trick wording: long precise specs whose edge cases interact,
speed limits a naive solution misses by orders of magnitude, an existing multi-module codebase with a
bug report and regression tests, exact numbers, and ordering under real threads. A task counts as solved
only when every hidden test passes; the score per task is partial credit (hidden tests passed).

Layout (all of it unreadable for the agent, see sandbox.sb):
    tasks/<id>/instruction.md   what the agent is asked (the full spec; nothing hidden tests rely on is left out)
    tasks/<id>/files/           the starting workspace (stub or codebase, plus a small visible test file)
    tasks/<id>/hidden_test.py   the grading tests (20-60 per task; speed tests run in a subprocess with a budget)
    reference/<id>/             a reference solution, copied over the starting files only by the validation

SANDBOX below is this folder's sandbox.sb: agentic-v1's profile plus no reads of this folder, earlier
agentic-v6 runs, the repo's .git and ~/.claude, so the agent cannot read hidden tests or references.

    .venv/bin/python benches/agentic-v6/tasks.py            # validate every task (light: one process at a time)
    .venv/bin/python benches/agentic-v6/tasks.py regex-pike # validate some tasks
"""
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
_v1 = importlib.util.spec_from_file_location('agentic_v1_tasks', HERE.parent / 'agentic-v1' / 'tasks.py')
V1 = importlib.util.module_from_spec(_v1)
_v1.loader.exec_module(V1)
write_files, run_visible = V1.write_files, V1.run_visible

SANDBOX = HERE / 'sandbox.sb'
HIDDEN_TIMEOUT = 900   # the whole hidden suite (a timeout scores 0); each test also has its own limit, speed tests a budget

# id -> what makes it hard (the task's area), agent time limit in seconds
META = {
    'regex-engine': ('a regex engine matching Python re exactly (leftmost-first, captures, lazy quantifiers, $ before a final '
                     'newline, find_all empty-match rules) with no backtracking blow-up: pathological patterns within a time budget', 1500),
    'toml-errors': ('a TOML subset parser: table/array-of-tables/dotted-key/inline-table redefinition rules, multi-line strings, '
                    'number grammar, and the exact kind, line and column of the first error', 1500),
    'cron-dst': ('a cron engine in a real time zone: Vixie day-of-month/day-of-week rule, spring-forward gaps and repeated '
                 'fall-back hours, never-firing expressions, and a speed limit a minute-by-minute search misses', 1500),
    'mini-sql': ('a SQL SELECT engine: joins, GROUP BY/HAVING, aggregates, three-valued NULL logic, ORDER BY rules, exact '
                 'column names and row order, and a hash join fast enough for 40,000 x 40,000 rows', 1500),
    'mvcc-store': ('a transactional key-value store: snapshot isolation, first-committer-wins, serializable mode with range '
                   'reads, savepoints, exact garbage collection, thread safety and a speed limit on scans', 1200),
    'order-book': ('a limit order book: price-time priority, IOC/FOK, self-trade prevention, amends that keep or lose '
                   'priority, cascading stop orders, exact event lists, and 200,000 orders within a time budget', 1500),
    'task-scheduler': ('a dependency-aware thread pool: priority/readiness/submission ordering, per-group limits, skip '
                       'propagation, cancellation, tasks submitted from running tasks, cycle detection, no deadlocks', 1200),
    'sheet-bugfix': ('an existing 7-module spreadsheet engine with a 5-point bug report (recalc order, mixed references, '
                     'cycles through ranges, rounding, row deletion) and regression tests that must keep passing', 1200),
}


def run_hidden(task, workspace, timeout=HIDDEN_TIMEOUT):
    return V1.run_hidden(task, workspace, timeout)


def _read_tree(root):
    return {str(p.relative_to(root)): p.read_text() for p in sorted(Path(root).rglob('*'))
            if p.is_file() and '__pycache__' not in p.parts}


TASKS = {}
for _id, (_area, _timeout) in META.items():
    _dir = HERE / 'tasks' / _id
    if not _dir.exists():
        continue
    _ref = _read_tree(HERE / 'reference' / _id)
    TASKS[_id] = dict(
        area=_area,
        timeout=_timeout,
        instruction=(_dir / 'instruction.md').read_text().strip(),
        files=_read_tree(_dir / 'files'),
        hidden=(_dir / 'hidden_test.py').read_text(),
        fix={rel: (lambda text: lambda _old: text)(text) for rel, text in _ref.items()},
    )


def _timings(out):
    """TIMING lines the hidden speed tests print: (test, seconds taken, budget)."""
    return [(m[1], float(m[2]), float(m[3])) for m in re.finditer(r'TIMING (\S+) ([\d.]+) ([\d.]+)', out)]


def _check_sandbox():
    """The agent's sandbox: its own workspace (under a results/agentic-v6-*/ folder, as in a real run) is readable
    and usable; this bench's folder (references, hidden tests) and earlier runs are not."""
    if sys.platform != 'darwin' or not Path('/usr/bin/sandbox-exec').exists():
        print('sandbox: not on macOS, not checked')
        return 0
    repo = HERE.parents[1]
    with tempfile.TemporaryDirectory() as fake:
        fake = Path(fake).resolve()
        ws = fake / 'results' / 'agentic-v6-check' / 'some-task' / 'workspace'
        other = fake / 'results' / 'agentic-v6-other' / 'some-task' / 'workspace'
        sess = fake / 'sessions'
        for d in (ws, other, sess):
            d.mkdir(parents=True)
        (ws / 'mine.py').write_text('print("own workspace ok")\n')
        (other / 'theirs.py').write_text('SOLUTION\n')
        probes = [  # (what, command, should succeed)
            ('read own workspace', ['python3', 'mine.py'], True),
            ('write own workspace', ['/bin/sh', '-c', 'echo x > new.txt && cat new.txt'], True),
            ('node starts in the workspace', ['node', '-e', 'console.log(process.cwd())'], True),
            ('read a reference solution', ['/bin/cat', str(next((HERE / 'reference').rglob('*.py')))], False),
            ('read hidden tests', ['/bin/cat', str(HERE / 'tasks' / 'regex-engine' / 'hidden_test.py')], False),
            ('list this bench folder', ['/bin/ls', str(HERE)], False),
            ('read tasks.py', ['/bin/cat', str(HERE / 'tasks.py')], False),
            ("read another run's workspace", ['/bin/cat', str(other / 'theirs.py')], False),
            ('list earlier runs', ['/bin/ls', str(fake / 'results' / 'agentic-v6-other')], False),
        ]
        bad = 0
        for what, cmd, want in probes:
            # REPO is the real repo for bench-folder probes; the fake one for the results/ layout probes
            use_repo = fake if ('workspace' in what or 'run' in what or 'node' in what) else repo
            full = ['/usr/bin/sandbox-exec', '-f', str(SANDBOX), '-D', f'WORKSPACE={ws}', '-D', f'SESSIONS={sess}',
                    '-D', f'HOME={Path.home()}', '-D', f'REPO={use_repo}', *cmd]
            r = subprocess.run(full, cwd=ws, capture_output=True, text=True, timeout=60)
            ok = (r.returncode == 0) == want
            bad += not ok
            print(f"{'ok  ' if ok else 'FAIL'} sandbox: {what}: {'allowed' if r.returncode == 0 else 'denied'}"
                  + ('' if ok else f'  ({(r.stdout + r.stderr).strip()[:200]})'))
    return bad


def validate(only=()):
    bad = 0
    total_hidden = 0
    for name, task in TASKS.items():
        if only and name not in only:
            continue
        with tempfile.TemporaryDirectory() as start, tempfile.TemporaryDirectory() as fixed:
            write_files(task, start)
            t0 = time.time()
            passed, total, sout = run_hidden(task, start)
            t_start = time.time() - t0
            write_files(task, fixed)
            for rel, change in task['fix'].items():
                p = Path(fixed, rel)
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(change(p.read_text() if p.exists() else ''))
            t0 = time.time()
            fpassed, ftotal, out = run_hidden(task, fixed)
            t_ref = time.time() - t0
            vis_ok, vis_out = run_visible(fixed)
            svis_ok, _ = run_visible(start)
            m = re.search(r'Ran (\d+) test', vis_out)
            nvis = int(m[1]) if m else 0
            timings = _timings(out)
            slow = [t for t in timings if t[1] * 3 > t[2]]
            ok = bool(ftotal) and fpassed == ftotal and passed < (total or ftotal or 1) and vis_ok and not slow
            total_hidden += ftotal or 0
            print(f"{'ok  ' if ok else 'FAIL'} {name}: start {passed}/{total} (visible {'pass' if svis_ok else 'fail'}), "
                  f"reference {fpassed}/{ftotal} in {t_ref:.0f}s, visible tests ({nvis}) with reference {'pass' if vis_ok else 'FAIL'}")
            for test, took, budget in timings:
                print(f"       speed {test}: reference {took:.2f}s, budget {budget:.0f}s, margin {budget / max(took, 1e-3):.1f}x"
                      + ('  <-- under 3x' if took * 3 > budget else ''))
            if not ok:
                bad += 1
                print(out[-3000:], vis_out[-800:])
    print(f"{sum(1 for n in TASKS if not only or n in only) - bad} tasks valid, {total_hidden} hidden tests")
    if not only:
        bad += _check_sandbox()
    return bad


if __name__ == '__main__':
    sys.exit(1 if validate(set(sys.argv[1:])) else 0)
