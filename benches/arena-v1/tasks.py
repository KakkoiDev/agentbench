"""arena-v1: build the strongest Victris Arena bot you can, in 3 rounds with memory.

Victris Arena (victris.ai) is a deterministic 1v1 game for programmed bots. The model works in an Arena
workspace (arena/workspace.py): strategy.py to improve (Victris's Python starter at first), the rules
and skills, NOTES.md for what it learns, and ./eval, which plays strategy.py against the pool
(Victris's random and starter bots and our champion bots) and explains every loss.

Rounds: round-1 starts from the starter bot and empty notes; round-2 and round-3 start from the
previous round's workspace (its strategy.py, NOTES.md and any tools the model wrote), so the model
keeps what it learned. Each round is graded on its own.

Hidden tests (arena/grade.py, seeds 9000+ that ./eval never uses, 120 games each): no errors, fast
enough, and win-rate bars against random (95%), Victris's starter (50/65/80/90%) and our first
champion, Victris's Python starter (55/70/85%). The starter itself passes 3 of 10.

    .venv/bin/python benches/arena-v1/tasks.py     # validate
"""
import json, shutil, subprocess, sys, tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from arena import workspace  # noqa: E402

PY = str(workspace.PY)
HARNESS_FILES = {'AGENT.md', 'eval', 'eval_tool.py', 'tune', 'tune_tool.py', 'replays', 'replays_tool.py', 'docs', 'skills', '__pycache__'}
ROUNDS = 3

FIRST = ("Read AGENT.md and follow it: make strategy.py win as many Victris Arena matches as you can against every "
         "opponent in the pool. Use ./eval to measure each change, and write what you learn in NOTES.md for the next round. "
         "This is round 1 of {n}.")
LATER = ("Read NOTES.md first: strategy.py and NOTES.md are where round {p} left off. Then follow AGENT.md: make "
         "strategy.py win even more Victris Arena matches against every opponent in the pool, measuring each change with "
         "./eval, and update NOTES.md for the next round. This is round {r} of {n}.")

TASKS = {}
for r in range(1, ROUNDS + 1):
    TASKS[f'round-{r}'] = dict(
        area='building a game bot, with memory between rounds' if r > 1 else 'building a game bot',
        instruction=(FIRST if r == 1 else LATER).format(n=ROUNDS, p=r - 1, r=r),
        previous=f'round-{r - 1}' if r > 1 else None,
        files={'strategy.py': (REPO / 'arena' / 'template' / 'strategy.py').read_text(),
               'NOTES.md': (REPO / 'arena' / 'harness' / 'NOTES.md').read_text()})


def write_files(task, dest):
    """A fresh workspace; later rounds carry over the previous round's files (dest is RUN/round-N/workspace)."""
    dest = Path(dest)
    prev = dest.parent.parent / task['previous'] / 'workspace' if task.get('previous') else None
    if prev and (prev / 'strategy.py').exists():
        keep = [p for p in prev.iterdir() if p.name not in HARNESS_FILES | {'strategy.py', 'NOTES.md'}]
        workspace.build(dest, strategy=prev / 'strategy.py', notes=prev / 'NOTES.md' if (prev / 'NOTES.md').exists() else None, keep=keep)
    else:
        workspace.build(dest)


def run_hidden(task, ws, timeout=900):
    with tempfile.TemporaryDirectory() as d:   # grade a copy, so nothing the bot writes lands in the workspace
        shutil.copy(Path(ws) / 'strategy.py', Path(d) / 'strategy.py') if (Path(ws) / 'strategy.py').exists() else None
        try:
            r = subprocess.run([PY, '-m', 'arena.grade', str(Path(d) / 'strategy.py')], cwd=REPO, capture_output=True, text=True, timeout=timeout)
            out = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return 0, None, f'timeout after {timeout}s'
    line = next((l for l in out.splitlines() if l.startswith('ARENA_JSON ')), None)
    if not line:
        return 0, 10, out
    j = json.loads(line[len('ARENA_JSON '):])
    return j['passed'], j['total'], out


def run_visible(ws, timeout=300):
    try:
        r = subprocess.run([PY, '-m', 'arena.grade', str(Path(ws) / 'strategy.py'), '--quick'], cwd=REPO, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return False, 'timeout'


def validate():
    with tempfile.TemporaryDirectory() as d:
        run = Path(d)
        write_files(TASKS['round-1'], run / 'round-1' / 'workspace')
        (run / 'round-1' / 'workspace' / 'tools').mkdir()
        (run / 'round-1' / 'workspace' / 'tools' / 'x.py').write_text('# a tool the model wrote\n')
        write_files(TASKS['round-2'], run / 'round-2' / 'workspace')
        assert (run / 'round-2' / 'workspace' / 'tools' / 'x.py').exists(), 'round 2 must keep round 1 tools'
        ok, out = run_visible(run / 'round-2' / 'workspace')
        p, t, _ = run_hidden(TASKS['round-2'], run / 'round-2' / 'workspace')
        print('visible:', ok, out.strip()); print(f'starter hidden: {p}/{t}')
        return ok and t == 10


if __name__ == '__main__':
    sys.exit(0 if validate() else 1)
