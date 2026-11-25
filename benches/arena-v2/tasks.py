"""arena-v2: build a Victris Arena bot, then improve it from its own fights, 10 rounds.

Asked for on 2026-11-24: the model first reads the rules and writes its bot; the harness then makes that bot
fight, the model learns from how the fights went, and this repeats 10 times, to see how good the model's own
bot can get. (arena-v1 gave the model ./eval, ./tune and ./replays and let it run its own games: Qwen 3.8 27B
spent its 3 rounds exploring and never edited the bot.)

Each round, the workspace holds strategy.py (Victris's Python starter in round 1, then the model's own),
NOTES.md, the rules and skills, ./check (loads the bot, 10 quick games against random) and, from round 2,
FIGHTS.md: the previous round's bot against random, Victris's TS starter and the Python starter, 100 games each
on seeds new every round, with how each loss went, and a table of every round so far. No other tools: the
harness runs the fights between rounds (in write_files, before the model starts).

Hidden score every round (arena/grade.py, seeds 9000+, never used for FIGHTS.md): the same 10 tests as
arena-v1. The round-by-round scores are the learning curve.

Round time: 30 minutes for round 1 (reading the rules), 20 minutes for rounds 2 to 10.

    .venv/bin/python benches/arena-v2/tasks.py     # validate (plays real games: a few minutes)
"""
import json, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from arena import workspace  # noqa: E402

PY = str(workspace.PY)
SANDBOX = REPO / 'arena' / 'sandbox.sb'
ROUNDS = 10
FIGHT_GAMES = 100   # 40 was too noisy: the same starter bot won 21/40 then 12/40 against ts-starter
OPPONENTS = [('random', 'random'), ('ts-starter', 'ts-starter'), ('py-starter', None)]   # None: our champion 000, the Python starter
# what the harness owns in a workspace; everything else the model wrote carries over between rounds
HARNESS_FILES = {'AGENT.md', 'check', 'docs', 'skills', 'FIGHTS.md', 'fights-history.json', '__pycache__',
                 'eval', 'eval_tool.py', 'tune', 'tune_tool.py', 'replays', 'replays_tool.py'}
CHECK = '''#!/bin/sh
# Load strategy.py and play 10 quick games against random, to catch errors (not the real fight).
cd "{repo}" && exec "{py}" -m arena.grade "{ws}/strategy.py" --quick
'''

# 2026-11-24: qwen3.8 27b mtp drafted its whole bot in its reasoning (two turns of ~24k characters), never wrote it,
# and the round ended: code only in the reasoning is lost
STEPS = ("Write your code into strategy.py with the write and edit tools as you go, one change at a time with ./check "
         "after each: code you only draft in your reasoning is lost when the round ends.")
FIRST = ("Read AGENT.md and follow it. This is round 1 of {n}: read the rules, then write your Victris Arena bot "
         "in strategy.py, run ./check, and write in NOTES.md what you built and the rule numbers your bot relies on "
         "(later rounds work from NOTES.md, not the docs). When you stop, your bot fights. "
         "Don't build your own test games or study the simulator: the harness runs the fights and reports them to you. "
         "Have a first version of your bot in strategy.py early, then improve it. " + STEPS)
# 2026-11-24: in the first runs every later round re-read all the docs (~25k tokens), filled a 32k window or the
# 12 minutes, and timed out with no change; later rounds now start from FIGHTS.md, NOTES.md and strategy.py
LATER = ("Read FIGHTS.md (how your bot's fights went after round {p}), NOTES.md and strategy.py; don't re-read the docs or "
         "AGENT.md: what you need from them is in NOTES.md, and grep a doc only to check one fact. Learn from the fights, improve "
         "strategy.py, run ./check, and update NOTES.md. This is round {r} of {n}. When you stop, your bot fights again. "
         "Don't build your own test games: the next fights are run for you. " + STEPS)

TASKS = {}
for r in range(1, ROUNDS + 1):
    TASKS[f'round-{r}'] = dict(
        area='building a game bot, then learning from its fights',
        instruction=(FIRST if r == 1 else LATER).format(n=ROUNDS, p=r - 1, r=r),
        previous=f'round-{r - 1}' if r > 1 else None, round=r,
        timeout=1800 if r == 1 else 1200,   # 30 + 9 x 20 minutes: at ~50 s a step a 27B needs ~10 steps to study FIGHTS.md before editing (12 minutes: no edit)
        files={'strategy.py': (REPO / 'arena' / 'template' / 'strategy.py').read_text(),
               'NOTES.md': (HERE / 'NOTES.md').read_text()})


def fight(bot, rnd):
    """The previous round's bot against the three opponents, on seeds new every round (never the hidden 9000+)."""
    from arena import runner, league
    rows, reports = {}, []
    for name, opp in OPPONENTS:
        res = runner.play(str(bot), opp or str(league.champions()['000-py-starter']), FIGHT_GAMES, 20000 + 1000 * rnd)
        rows[name] = dict(won=res['wins'], games=res['games'], errors=res['timing']['errors'], max_ms=res['timing']['max_ms'])
        reports.append(runner.loss_report(res))
    return rows, reports


def fights_md(history, reports, rnd):
    names = [n for n, _ in OPPONENTS]
    out = [f'# How your bot fought after round {rnd}', '',
           f'Your strategy.py from round {rnd}, {FIGHT_GAMES} games against each opponent, on seeds it had not played before.', '',
           '## Every round so far', '', f'Each round\'s bot is kept in previous-bots/ (strategy-round-N.py): copy one back over strategy.py to undo a change that lost.', '',
           '| after round | ' + ' | '.join(names) + ' | errors |', '|---|' + '---|' * (len(names) + 1)]
    for h in history:
        out.append(f"| {h['round']} | " + ' | '.join(f"{h['rows'][n]['won']}/{h['rows'][n]['games']}" for n in names)
                   + f" | {sum(h['rows'][n]['errors'] for n in names)} |")
    out += ['', f'## The fights after round {rnd}, opponent by opponent', '']
    for (name, _), rep in zip(OPPONENTS, reports):
        out += [f'### {name}', '', '```', rep, '```', '']
    return '\n'.join(out)


def write_files(task, dest):
    """A fresh workspace (dest is RUN/round-N/workspace); later rounds carry the previous round's files and get
    FIGHTS.md, from fights played here with the previous round's strategy.py."""
    dest = Path(dest)
    prev = dest.parent.parent / task['previous'] / 'workspace' if task.get('previous') else None
    if prev and (prev / 'strategy.py').exists():
        keep = [p for p in prev.iterdir() if p.name not in HARNESS_FILES | {'strategy.py', 'NOTES.md'}]
        workspace.build(dest, strategy=prev / 'strategy.py', notes=prev / 'NOTES.md' if (prev / 'NOTES.md').exists() else None, keep=keep)
    else:
        workspace.build(dest, notes=HERE / 'NOTES.md')
    # the harness runs the fights: no eval/tune/replay tools, and not Victris's simulator and match-review skills
    # (a model given them spent round 1 writing its own fight harness instead of its bot)
    for d in ('skills/arena-simulator', 'skills/arena-match-review'):
        shutil.rmtree(dest / d, ignore_errors=True)
    for name in ('eval', 'eval_tool.py', 'tune', 'tune_tool.py', 'replays', 'replays_tool.py'):   # the harness runs the fights
        p = dest / name
        if p.exists():
            p.unlink()
    shutil.copy(HERE / 'AGENT.md', dest / 'AGENT.md')
    ck = dest / 'check'
    ck.write_text(CHECK.format(repo=REPO, py=PY, ws=dest))
    ck.chmod(ck.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    if prev and (prev / 'strategy.py').exists():
        history = json.loads((prev / 'fights-history.json').read_text()) if (prev / 'fights-history.json').exists() else []
        with tempfile.TemporaryDirectory() as d:   # fight a copy, so nothing the bot writes lands in the workspace
            bot = Path(d) / 'strategy.py'
            shutil.copy(prev / 'strategy.py', bot)
            rows, reports = fight(bot, task['round'] - 1)
        history.append(dict(round=task['round'] - 1, rows=rows))
        # every earlier round's bot, so a change that lost can be undone (copied over from the previous workspace too)
        (dest / 'previous-bots').mkdir(exist_ok=True)
        shutil.copy(prev / 'strategy.py', dest / 'previous-bots' / f"strategy-round-{task['round'] - 1}.py")
        (dest / 'fights-history.json').write_text(json.dumps(history, indent=1))
        (dest / 'FIGHTS.md').write_text(fights_md(history, reports, task['round'] - 1))


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
        w1 = run / 'round-1' / 'workspace'
        assert not (w1 / 'eval').exists() and (w1 / 'check').exists() and not (w1 / 'FIGHTS.md').exists()
        (w1 / 'mine.py').write_text('# a file the model wrote\n')
        write_files(TASKS['round-2'], run / 'round-2' / 'workspace')
        w2 = run / 'round-2' / 'workspace'
        assert (w2 / 'mine.py').exists() and (w2 / 'FIGHTS.md').exists()
        write_files(TASKS['round-3'], run / 'round-3' / 'workspace')
        print((run / 'round-3' / 'workspace' / 'FIGHTS.md').read_text()[:1500])
        ok, out = run_visible(w2)
        print('visible:', ok, out.strip())
        return ok


if __name__ == '__main__':
    sys.exit(0 if validate() else 1)
