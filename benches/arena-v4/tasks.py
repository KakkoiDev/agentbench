"""arena-v4: the hall-of-fame gauntlet (asked for on 2026-11-25).

Every winner our benches and the Arena lab produced is kept in the league (arena/champions). arena-v4 freezes
that hall of fame (opponents/, `--freeze`) and asks for a bot that beats every member, not just the latest
champion: bots can beat each other in a circle, so a new bot must be checked against all of history.

Also from 2026-11-25, the benches aim at each model's best possible result, not its speed:
- no time limit to speak of: each round may run up to 4 hours (a safety cap for a stuck run, not a target);
- self-testing is encouraged, not forbidden: ./fight plays the bot against Victris's bots, its own earlier rounds or
  any sparring bot it writes, as often as it likes (DeepSeek built its own fight batches on arena-v2 and made the
  best bots: the instructions should help that, not forbid it);
- between rounds the harness fights it against the whole hall of fame (FIGHTS.md); the hall's code stays outside
  the sandbox, so a bot can't be copied from it.

Hidden score (seeds 9000+, 120 games per opponent, never used for FIGHTS.md): no errors, fast enough, then for
each opponent "wins 55%" and "wins 70%". The score says how much of our history the bot beats.

    .venv/bin/python benches/arena-v4/tasks.py --freeze          # snapshot arena/champions into opponents/ (once)
    .venv/bin/python benches/arena-v4/tasks.py                   # validate (plays real games: several minutes)
    .venv/bin/python benches/arena-v4/tasks.py --grade BOT       # the hidden grade of one bot
"""
import hashlib, json, shutil, stat, subprocess, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from arena import workspace  # noqa: E402

PY = str(workspace.PY)
SANDBOX = HERE / 'sandbox.sb'   # arena/sandbox.sb: the league's bots and this folder are not readable
ROUNDS = 10
ROUND_CAP = 4 * 3600   # a safety cap, not a target
FIGHT_GAMES = 100
OPP = HERE / 'opponents'          # frozen copies of the hall of fame (kept local: they include Victris's starter)
MANIFEST = HERE / 'opponents.json'   # what was frozen when, with each bot's hash: committed, so a later freeze is visible
SEED0, GRADE_GAMES = 9000, 120
BARS = (55, 70)
HARNESS_FILES = {'AGENT.md', 'check', 'fight', 'fight_tool.py', 'docs', 'skills', 'FIGHTS.md', 'fights-history.json', '__pycache__',
                 'eval', 'eval_tool.py', 'tune', 'tune_tool.py', 'replays', 'replays_tool.py'}
CHECK = '''#!/bin/sh
# Load strategy.py and play 10 quick games against random, to catch errors (not the real fight).
cd "{repo}" && exec "{py}" -m arena.grade "{ws}/strategy.py" --quick
'''
FIGHT = '''#!/bin/sh
# Play a bot against opponents and see how the losses went. As often as you like: test every change.
#   ./fight                                   strategy.py vs random and ts-starter, 40 games each
#   ./fight --vs previous-bots/strategy-round-3.py --games 100
#   ./fight --vs sparring.py --vs ts-starter  any bot file in this folder
#   ./fight other.py --vs strategy.py         any two bots
cd "{repo}" && exec "{py}" "{ws}/fight_tool.py" "{repo}" "{ws}" "$@"
'''
FIGHT_TOOL = '''"""./fight: arena.runner for the agent (files in the workspace only; seeds 30000+, never the hidden ones)."""
import argparse, sys
from pathlib import Path
sys.path.insert(0, sys.argv.pop(1))
WS = Path(sys.argv.pop(1))
from arena import runner  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument('bot', nargs='?', default='strategy.py')
p.add_argument('--vs', action='append')
p.add_argument('--games', type=int, default=40)
p.add_argument('--seed', type=int, default=30000)
a = p.parse_args()
def path(x):
    if x in runner.HOUSE:
        return x
    q = (WS / x).resolve()
    if WS.resolve() not in q.parents and q != WS.resolve():
        sys.exit(f'{x}: only bots in this folder (or random, ts-starter)')
    return str(q)
for o in a.vs or ['random', 'ts-starter']:
    r = runner.play(path(a.bot), path(o), a.games, max(a.seed, 30000))
    print(runner.loss_report(r, limit=5), end='\\n\\n', flush=True)
'''

STEPS = ("Write your code into strategy.py with the write and edit tools as you go: code you only draft in your reasoning "
         "is lost when the round ends.")
TEST = ("Test every change before you keep it: ./fight plays your bot against Victris's bots, your earlier rounds' bots "
        "(previous-bots/) or any sparring bot you write, as often as you like, and shows how the losses went. Keep a "
        "change only if it wins more; undo it if it loses.")
TIME = ("There is no time pressure: take as long as you need, and stop only when you believe your bot is the best you can "
        "make this round.")
FIRST = ("Read AGENT.md and follow it. This is round 1 of {n}: read the rules, then write your Victris Arena bot in strategy.py, "
         "and write in NOTES.md what you built and the rule numbers your bot relies on (later rounds work from NOTES.md). "
         + TEST + " " + TIME + " " + STEPS + " When you stop, your bot fights the hall of fame.")
LATER = ("Read FIGHTS.md (how your bot did against the hall of fame after round {p}), NOTES.md and strategy.py; grep a doc only "
         "to check one fact. Find what loses, improve strategy.py, and update NOTES.md. " + TEST + " " + TIME + " " + STEPS
         + " This is round {r} of {n}. When you stop, your bot fights the hall of fame again.")


def opponents():
    """[(name, opponent)]: Victris's starter, then every frozen hall-of-fame bot, oldest first."""
    if not MANIFEST.exists():
        return []
    return [('ts-starter', 'ts-starter')] + [(o['name'], str(OPP / f"{o['name']}.py")) for o in json.loads(MANIFEST.read_text())['bots']]


TASKS = {}
for r in range(1, ROUNDS + 1):
    TASKS[f'round-{r}'] = dict(
        area='building a game bot that beats every earlier winner, testing each change',
        instruction=(FIRST if r == 1 else LATER).format(n=ROUNDS, p=r - 1, r=r),
        previous=f'round-{r - 1}' if r > 1 else None, round=r, timeout=ROUND_CAP,
        files={'strategy.py': (REPO / 'arena' / 'template' / 'strategy.py').read_text(), 'NOTES.md': (HERE / 'NOTES.md').read_text()})


def freeze():
    """Snapshot arena/champions as the hall of fame (Elo from arena/league.json), and record it in opponents.json."""
    from arena import league
    table = {c['name']: c for c in json.loads((REPO / 'arena' / 'league.json').read_text())['champions']}
    OPP.mkdir(exist_ok=True)
    bots = []
    for name, src in league.champions().items():
        shutil.copy(src, OPP / f'{name}.py')
        c = table.get(name, {})
        bots.append(dict(name=name, model=(c.get('meta') or {}).get('model'), elo=c.get('elo'),
                         sha256=hashlib.sha256(Path(src).read_bytes()).hexdigest()[:16]))
    MANIFEST.write_text(json.dumps(dict(frozen=time.strftime('%F'), bots=bots), indent=1) + '\n')
    print(f'froze {len(bots)} bots into {OPP}')


def fight(bot, rnd):
    from arena import runner
    rows, reports = {}, []
    for name, opp in opponents():
        res = runner.play(str(bot), opp, FIGHT_GAMES, 20000 + 1000 * rnd)
        rows[name] = dict(won=res['wins'], games=res['games'], errors=res['timing']['errors'], max_ms=res['timing']['max_ms'])
        reports.append(runner.loss_report(res))
    return rows, reports


def fights_md(history, reports, rnd):
    names = [n for n, _ in opponents()]
    out = [f'# How your bot fought the hall of fame after round {rnd}', '',
           f'Your strategy.py from round {rnd}, {FIGHT_GAMES} games against each bot that ever won here, on seeds it had not '
           'played before. The hidden score asks for 55% and 70% against each.', '',
           '## Every round so far', '', 'Each round\'s bot is kept in previous-bots/ (strategy-round-N.py): ./fight them, or copy one back.', '',
           '| after round | ' + ' | '.join(names) + ' | errors |', '|---|' + '---|' * (len(names) + 1)]
    for h in history:
        out.append(f"| {h['round']} | " + ' | '.join(f"{h['rows'][n]['won']}/{h['rows'][n]['games']}" for n in names)
                   + f" | {sum(h['rows'][n]['errors'] for n in names)} |")
    out += ['', f'## The fights after round {rnd}, opponent by opponent', '']
    for (name, _), rep in zip(opponents(), reports):
        out += [f'### {name}', '', '```', rep, '```', '']
    return '\n'.join(out)


def write_files(task, dest):
    dest = Path(dest)
    prev = dest.parent.parent / task['previous'] / 'workspace' if task.get('previous') else None
    if prev and (prev / 'strategy.py').exists():
        keep = [p for p in prev.iterdir() if p.name not in HARNESS_FILES | {'strategy.py', 'NOTES.md'}]
        workspace.build(dest, strategy=prev / 'strategy.py', notes=prev / 'NOTES.md' if (prev / 'NOTES.md').exists() else None, keep=keep)
    else:
        workspace.build(dest, notes=HERE / 'NOTES.md')
    for name in ('eval', 'eval_tool.py', 'tune', 'tune_tool.py', 'replays', 'replays_tool.py'):   # the pool tools read the league
        p = dest / name
        if p.exists():
            p.unlink()
    shutil.copy(HERE / 'AGENT.md', dest / 'AGENT.md')
    for name, text in (('check', CHECK), ('fight', FIGHT)):
        f = dest / name
        f.write_text(text.format(repo=REPO, py=PY, ws=dest))
        f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    (dest / 'fight_tool.py').write_text(FIGHT_TOOL)
    if prev and (prev / 'strategy.py').exists():
        history = json.loads((prev / 'fights-history.json').read_text()) if (prev / 'fights-history.json').exists() else []
        with tempfile.TemporaryDirectory() as d:
            bot = Path(d) / 'strategy.py'
            shutil.copy(prev / 'strategy.py', bot)
            rows, reports = fight(bot, task['round'] - 1)
        history.append(dict(round=task['round'] - 1, rows=rows))
        (dest / 'previous-bots').mkdir(exist_ok=True)
        shutil.copy(prev / 'strategy.py', dest / 'previous-bots' / f"strategy-round-{task['round'] - 1}.py")
        (dest / 'fights-history.json').write_text(json.dumps(history, indent=1))
        (dest / 'FIGHTS.md').write_text(fights_md(history, reports, task['round'] - 1))


def grade(bot):
    from arena import runner
    tests, rates, t_all = [], {}, dict(calls=0, total_ms=0.0, max_ms=0.0, errors=0, over=0, first_error=None)
    for name, opp in opponents():
        r = runner.play(bot, opp, GRADE_GAMES, SEED0)
        rates[name] = r['win_rate']
        t = r['timing']
        t_all['calls'] += t['calls']; t_all['total_ms'] += t['mean_ms'] * t['calls']; t_all['max_ms'] = max(t_all['max_ms'], t['max_ms'])
        t_all['errors'] += t['errors']; t_all['over'] += t['over_budget']; t_all['first_error'] = t_all['first_error'] or t['first_error']
        tests += [(f'test_beats_{name}_{b}', 100 * r['win_rate'] >= b, f"{100 * r['win_rate']:.1f}% won") for b in BARS]
    mean = t_all['total_ms'] / max(1, t_all['calls'])
    tests = [('test_no_errors', t_all['errors'] == 0, f"{t_all['errors']} errors"),
             ('test_fast_enough', t_all['over'] == 0 and mean < 20, f"mean {mean:.2f} ms, max {t_all['max_ms']} ms, {t_all['over']} over budget")] + tests
    for name, ok, why in tests:
        print(f"{name} ... {'ok' if ok else 'FAIL'} ({why})")
    print('-' * 70)
    print(f'Ran {len(tests)} tests')
    print('OK' if all(ok for _, ok, _ in tests) else f'FAILED (failures={sum(not ok for _, ok, _ in tests)})')
    print('ARENA_JSON ' + json.dumps(dict(win_rates=rates, timing=dict(mean_ms=round(mean, 2), max_ms=t_all['max_ms'], errors=t_all['errors'],
                                                                      over_budget=t_all['over'], first_error=t_all['first_error']),
                                          passed=sum(ok for _, ok, _ in tests), total=len(tests))), flush=True)


def run_hidden(task, ws, timeout=3600):
    with tempfile.TemporaryDirectory() as d:
        shutil.copy(Path(ws) / 'strategy.py', Path(d) / 'strategy.py') if (Path(ws) / 'strategy.py').exists() else None
        try:
            r = subprocess.run([PY, str(HERE / 'tasks.py'), '--grade', str(Path(d) / 'strategy.py')], cwd=REPO, capture_output=True, text=True, timeout=timeout)
            out = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return 0, None, f'timeout after {timeout}s'
    line = next((l for l in out.splitlines() if l.startswith('ARENA_JSON ')), None)
    if not line:
        return 0, 2 + len(BARS) * len(opponents()), out
    j = json.loads(line[len('ARENA_JSON '):])
    return j['passed'], j['total'], out


def run_visible(ws, timeout=300):
    try:
        r = subprocess.run([PY, '-m', 'arena.grade', str(Path(ws) / 'strategy.py'), '--quick'], cwd=REPO, capture_output=True, text=True, timeout=timeout)
        return r.returncode == 0, r.stdout + r.stderr
    except subprocess.TimeoutExpired:
        return False, 'timeout'


def validate():
    assert opponents(), 'no hall of fame frozen yet: run with --freeze first'
    with tempfile.TemporaryDirectory() as d:
        run = Path(d)
        write_files(TASKS['round-1'], run / 'round-1' / 'workspace')
        w1 = run / 'round-1' / 'workspace'
        assert (w1 / 'check').exists() and (w1 / 'fight').exists() and not (w1 / 'FIGHTS.md').exists() and not (w1 / 'eval').exists()
        r = subprocess.run([str(w1 / 'fight'), '--games', '4'], capture_output=True, text=True)
        print('fight:', r.returncode, r.stdout[:300], r.stderr[-300:])
        write_files(TASKS['round-2'], run / 'round-2' / 'workspace')
        print((run / 'round-2' / 'workspace' / 'FIGHTS.md').read_text()[:800])
        ok, out = run_visible(w1)
        print('visible:', ok, out.strip()[-200:])
        return ok and r.returncode == 0


if __name__ == '__main__':
    if '--freeze' in sys.argv:
        freeze()
        sys.exit(0)
    if '--grade' in sys.argv:
        grade(sys.argv[sys.argv.index('--grade') + 1])
        sys.exit(0)
    sys.exit(0 if validate() else 1)
