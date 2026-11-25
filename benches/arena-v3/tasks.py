"""arena-v3: arena-v2 against the league's best bots (2026-11-24).

arena-v2 saturated: DeepSeek V4.1 Flash's round-1 bot already won 95-100% against random and both starters and
passed all 10 hidden tests every round. arena-v3 keeps v2's loop (build, the harness fights, learn; 10 rounds;
30 + 9 x 20 minutes) but the opponents are the strongest bots we have, frozen in opponents/ on 2026-11-24 so
scores stay comparable for future models: Victris's TS starter, the Python starter, the league champions of
Qwen 3.8 27B (MLX) and Muse Glimmer 30B, and DeepSeek V4.1 Flash's own arena-v2 bot (crowned at 99% vs Muse).

Hidden score (seeds 9000+, 120 games each, never used for FIGHTS.md): 13 threshold tests, the hardest being
beating DeepSeek's bot at 50 / 60 / 70%. The opponents' code sits outside the agent's sandbox.

    .venv/bin/python benches/arena-v3/tasks.py               # validate (plays real games: several minutes)
    .venv/bin/python benches/arena-v3/tasks.py --grade BOT   # the hidden grade of one bot
"""
import json, shutil, stat, subprocess, sys, tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO))
from arena import workspace  # noqa: E402

PY = str(workspace.PY)
SANDBOX = HERE / 'sandbox.sb'   # arena/sandbox.sb, minus the league's bots
ROUNDS = 10
FIGHT_GAMES = 100   # 40 was too noisy: the same starter bot won 21/40 then 12/40 against ts-starter
OPP = HERE / 'opponents'   # frozen copies (not readable from the sandbox: arena/sandbox.sb allows only arena/, .venv/ and the workspace)
OPPONENTS = [('ts-starter', 'ts-starter'), ('py-starter', str(OPP / 'py-starter.py')), ('league-qwen', str(OPP / 'league-qwen.py')),
             ('league-muse', str(OPP / 'league-muse.py')), ('league-deepseek', str(OPP / 'league-deepseek.py'))]
SEED0, GRADE_GAMES = 9000, 120
BARS = {'ts-starter': (90, 97), 'py-starter': (90, 97), 'league-qwen': (75, 90), 'league-muse': (75, 90), 'league-deepseek': (50, 60, 70)}
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
    """The previous round's bot against the five opponents, on seeds new every round (never the hidden 9000+)."""
    from arena import runner
    rows, reports = {}, []
    for name, opp in OPPONENTS:
        res = runner.play(str(bot), opp, FIGHT_GAMES, 20000 + 1000 * rnd)
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


def grade(bot):
    """The hidden tests: unittest-style lines, then ARENA_JSON (passed, total, win_rates, timing) like arena.grade."""
    from arena import runner
    tests, rates, t_all = [], {}, dict(calls=0, total_ms=0.0, max_ms=0.0, errors=0, over=0, first_error=None)
    for name, opp in OPPONENTS:
        r = runner.play(bot, opp, GRADE_GAMES, SEED0)
        rates[name] = r['win_rate']
        t = r['timing']
        t_all['calls'] += t['calls']; t_all['total_ms'] += t['mean_ms'] * t['calls']; t_all['max_ms'] = max(t_all['max_ms'], t['max_ms'])
        t_all['errors'] += t['errors']; t_all['over'] += t['over_budget']; t_all['first_error'] = t_all['first_error'] or t['first_error']
        tests += [(f'test_beats_{name}_{b}', 100 * r['win_rate'] >= b, f"{100 * r['win_rate']:.1f}% won") for b in BARS[name]]
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


def run_hidden(task, ws, timeout=1800):
    with tempfile.TemporaryDirectory() as d:   # grade a copy, so nothing the bot writes lands in the workspace
        shutil.copy(Path(ws) / 'strategy.py', Path(d) / 'strategy.py') if (Path(ws) / 'strategy.py').exists() else None
        try:
            r = subprocess.run([PY, str(HERE / 'tasks.py'), '--grade', str(Path(d) / 'strategy.py')], cwd=REPO, capture_output=True, text=True, timeout=timeout)
            out = r.stdout + r.stderr
        except subprocess.TimeoutExpired:
            return 0, None, f'timeout after {timeout}s'
    line = next((l for l in out.splitlines() if l.startswith('ARENA_JSON ')), None)
    if not line:
        return 0, sum(len(b) for b in BARS.values()) + 2, out
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
    if '--grade' in sys.argv:
        grade(sys.argv[sys.argv.index('--grade') + 1])
        sys.exit(0)
    sys.exit(0 if validate() else 1)
