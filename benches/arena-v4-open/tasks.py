"""arena-v4-open: arena-v4 with the book open (asked for on 2026-11-25: "authorize the agents to look at our 10 best
of all time bots and improve on them. maybe something interesting will come out of it").

The same 10 rounds, opponents and hidden grade as arena-v4 (the frozen hall of fame), but the workspace holds the
code of the ten best bots of all time (hall-of-fame/, by Elo when the hall was frozen): the agent reads them,
./fight's them, takes what works, combines and improves on it. arena-v4 asks how good a bot a model builds alone;
this asks how far past the best of our history it can go. Scores of the two modes are not comparable.

    .venv/bin/python benches/arena-v4-open/tasks.py      # validate
"""
import importlib.util, json, shutil, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
_spec = importlib.util.spec_from_file_location('arena_v4_closed', HERE.parent / 'arena-v4' / 'tasks.py')
V4 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(V4)

SANDBOX = V4.SANDBOX
TOP = 10
OPEN = ("hall-of-fame/ holds the code of the ten best bots of all time (the strongest first in hall-of-fame/README.md): "
        "they are your opponents too. Read them, ./fight them, take what works, combine and improve on it: the goal is a bot "
        "that beats every one of them.")


def best():
    m = json.loads(V4.MANIFEST.read_text())['bots']
    return [b for b in sorted(m, key=lambda b: -(b.get('elo') or 0))][:TOP]


TASKS = {}
for name, t in V4.TASKS.items():
    TASKS[name] = dict(t, area='improving on the best game bots of all time, testing each change',
                       instruction=OPEN + ' ' + t['instruction'])


def write_files(task, dest):
    V4.write_files(task, dest)
    hof = Path(dest) / 'hall-of-fame'
    hof.mkdir(exist_ok=True)
    lines = ['# The ten best bots of all time', '', 'By Elo in our league when the hall of fame was frozen; each is also an opponent.', '']
    for b in best():
        shutil.copy(V4.OPP / f"{b['name']}.py", hof / f"{b['name']}.py")
        lines.append(f"- `{b['name']}.py`: Elo {b['elo']}, written by {b.get('model') or 'Victris (the Python starter)'}")
    (hof / 'README.md').write_text('\n'.join(lines) + '\n')


run_hidden, run_visible = V4.run_hidden, V4.run_visible
HARNESS_FILES = V4.HARNESS_FILES | {'hall-of-fame'}


if __name__ == '__main__':
    import tempfile, subprocess
    with tempfile.TemporaryDirectory() as d:
        w = Path(d) / 'round-1' / 'workspace'
        write_files(TASKS['round-1'], w)
        print((w / 'hall-of-fame' / 'README.md').read_text())
        top = best()[0]['name']
        r = subprocess.run([str(w / 'fight'), '--vs', f'hall-of-fame/{top}.py', '--games', '4'], capture_output=True, text=True)
        print('fight vs', top, '->', r.returncode, r.stdout.splitlines()[0] if r.stdout else r.stderr[-300:])
        sys.exit(r.returncode)
