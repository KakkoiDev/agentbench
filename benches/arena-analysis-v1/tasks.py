"""arena-analysis-v1: can a model understand Victris Arena strategy? Diagnose bots and read ladder games.

Diagnosis (4 tasks): a bot with exactly one serious flaw, its code hidden (bot.bin); the model may only
watch and measure it (./study: results and loss reports, a profile of its habits, the same profile for a
sound reference bot, any game tick by tick, ladder bots' profiles) and must name the flaw. Each flaw
was checked to show in the measures and to cost games (benches/arena-analysis-v1/flaws.py).
Tests: names the flaw (one sentence, graded by what it says; listing several flaws fails), cites
numbers as evidence, and did not decode the hidden code.

Ladder reading (2 tasks): questions on a frozen set of 55 ladder matches (replays/), answered with
./replays: one across bots (scouting numbers), one inside a match (reading it tick by tick).

    .venv/bin/python benches/arena-analysis-v1/tasks.py     # validate: reference answers pass, empty ones fail
"""
import base64, json, os, re, sys, zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
sys.path.insert(0, str(REPO)); sys.path.insert(0, str(HERE))
os.environ['ARENA_REPLAYS_DIR'] = str(HERE / 'replays')
import flaws  # noqa: E402
from arena import workspace, replays  # noqa: E402

PY = str(workspace.PY)
SANDBOX = REPO / 'arena' / 'sandbox.sb'   # reads kept to the task (agent_bench uses it for this bench)
DOCS = REPO / 'arena' / 'harness' / 'docs'
SCRIPT = '#!/bin/sh\n# {doc}\nARENA_REPLAYS_DIR="{rep}" exec "{py}" "$(dirname "$0")/{tool}" "{repo}" "$@"\n'

DIAG = ("A Victris Arena bot (bot.bin) has exactly one serious flaw in how it plays. Its code is hidden on purpose: do not try "
        "to decode or read bot.bin. Find the flaw from its behaviour only, with ./study: `./study eval` (results and how the "
        "losses went), `./study profile` (its habits), `./study reference` (the same profile for a sound bot built from the "
        "same code), `./study watch --seed N` (one game tick by tick) and `./study scout` (ladder bots). The rules are in "
        "docs/rules.md. Then write ANSWER.md with exactly these three lines:\n"
        "FLAW: <the one flaw, in one sentence>\nEVIDENCE: <the numbers from ./study that show it>\nFIX: <what the bot should do instead>")


def _scout_questions():
    prof = json.loads(replays.PROFILES.read_text())['bots']
    q = {n: b for n, b in prof.items() if b['matches'] >= 5}
    best = lambda f: max(q, key=lambda n: f(q[n]))
    return dict(
        a=('which bot gets the largest share of the damage it deals from wall slams?', best(lambda b: b['dealt_mix'].get('wall_slam', 0))),
        b=('which bot keeps the longest average range to its enemy?', best(lambda b: b['mean_range'] or 0)),
        c=('which bot casts the most frostbursts per 100 ticks?', best(lambda b: b['shots_per100']['frostburst'] or 0)))


def _match_questions():
    for m in replays.matches():
        tr = replays.trajectories(m['id'])
        end = tr[1]['end']
        stats = {f['id']: f for f in end['stats']['fighters']}
        win = replays.winner_name(m)
        if win == 'draw' or end['final_tick'] < 100:
            continue
        loser = next(b for b in m['bots'] if b['name'] != win)
        taken = sorted(stats[loser['fighter']]['taken_by_source'].items(), key=lambda kv: -kv[1])
        if len(taken) > 1 and taken[0][1] - taken[1][1] >= 15:
            return m['id'], dict(a=('who won?', win), b=('on which tick did it end?', end['final_tick']),
                                 c=('which kind of damage (ranged, frostburst, repulsor, wall_slam, zone...) did the loser take the most of, in total?', taken[0][0]))


SCOUT, (MATCH, MATCHQ) = _scout_questions(), _match_questions()

TASKS = {}
for f in flaws.FLAWS:
    TASKS[f'diagnose-{f}'] = dict(area='diagnosing a bot from its behaviour', kind='diagnosis', flaw=f, instruction=DIAG,
                                  files={'ANSWER.md': 'FLAW: \nEVIDENCE: \nFIX: \n'})
TASKS['ladder-scouting'] = dict(area='reading ladder games: across bots', kind='reading', answers={k: v[1] for k, v in SCOUT.items()},
    instruction=("The folder's ./replays tool studies 55 recent Victris Arena ladder matches (./replays scout, ./replays list, "
                 "./replays show MATCH). Considering only bots with at least 5 matches in this set:\n" +
                 '\n'.join(f'{k}) {v[0]}' for k, v in SCOUT.items()) +
                 "\nWrite ANSWER.md with one line per question: 'a: NAME', 'b: NAME', 'c: NAME'."),
    files={'ANSWER.md': 'a: \nb: \nc: \n'})
TASKS['ladder-match'] = dict(area='reading ladder games: one match', kind='reading', answers={k: v[1] for k, v in MATCHQ.items()},
    instruction=(f"The folder's ./replays tool studies recent Victris Arena ladder matches (./replays show MATCH --fighter NAME "
                 f"prints one tick by tick, with every hit). About match {MATCH}:\n" + '\n'.join(f'{k}) {v[0]}' for k, v in MATCHQ.items()) +
                 "\nWrite ANSWER.md with one line per question: 'a: ...', 'b: ...', 'c: ...'."),
    files={'ANSWER.md': 'a: \nb: \nc: \n'})


def write_files(task, dest):
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    (dest / 'docs').mkdir(exist_ok=True)
    for d in ('rules.md', 'rules.json', 'bot-guide.md'):
        (dest / 'docs' / d).write_text((DOCS / d).read_text())
    for rel, text in task['files'].items():
        (dest / rel).write_text(text)
    tools = {'replays': ('replays_tool.py', REPO / 'arena' / 'harness' / 'replays_tool.py', 'Study the ladder matches: ./replays scout | list | show MATCH')}
    if task['kind'] == 'diagnosis':
        (dest / 'bot.bin').write_text(base64.b64encode(zlib.compress(flaws.source(task['flaw']).encode())).decode())
        tools['study'] = ('study_tool.py', HERE / 'study_tool.py', 'Watch and measure the hidden bot: ./study eval | profile | reference | watch | scout')
    for name, (tool, src, doc) in tools.items():
        (dest / tool).write_text(Path(src).read_text())
        sh = dest / name
        sh.write_text(SCRIPT.format(doc=doc, rep=HERE / 'replays', py=PY, tool=tool, repo=REPO))
        sh.chmod(0o755)


def _peeked(ws):
    """Did the agent try to read the hidden bot's code (bot.bin decoded, or the flaw list)?"""
    log = Path(ws).parent / 'pi.jsonl'
    if not log.exists():
        return False
    text = log.read_text(errors='ignore')
    return bool(re.search(r'(b64decode|base64 -d|base64 --decode|zlib|decompress)[^"\n]{0,200}bot\.bin|bot\.bin[^"\n]{0,200}(b64decode|base64 -d|zlib|decompress)|flaws\.py', text))


def _line(answer, key):
    m = re.search(rf'^\s*\**{key}\**\s*[:)]\s*(.+)$', answer, re.I | re.M)
    return m.group(1).strip().strip('*`"\' ') if m else ''


def run_hidden(task, ws, timeout=60):
    ans = (Path(ws) / 'ANSWER.md').read_text(errors='ignore') if (Path(ws) / 'ANSWER.md').exists() else ''
    tests = []
    if task['kind'] == 'diagnosis':
        peek = _peeked(ws)
        tests.append(('test_names_the_flaw', flaws.named(ans, task['flaw']) and not peek, _line(ans, 'FLAW')[:200]))
        tests.append(('test_cites_numbers', bool(re.search(r'\d', _line(ans, 'EVIDENCE'))), _line(ans, 'EVIDENCE')[:200]))
        tests.append(('test_did_not_read_hidden_code', not peek, 'decoded bot.bin' if peek else 'ok'))
    else:
        for k, want in task['answers'].items():
            got = _line(ans, k)
            if isinstance(want, int):
                n = re.search(r'\d+', got)
                ok = bool(n) and abs(int(n.group()) - want) <= 1
            else:
                ok = bool(re.fullmatch(rf'.{{0,12}}\b{re.escape(str(want).lower())}\b.{{0,40}}', got.lower()))
            tests.append((f'test_{k}', ok, f'answered {got[:60]!r}'))
    out = '\n'.join(f"{n} ... {'ok' if ok else 'FAIL'} ({why})" for n, ok, why in tests) + f'\nRan {len(tests)} tests\n'
    return sum(ok for _, ok, _ in tests), len(tests), out


def run_visible(ws, timeout=60):
    ans = Path(ws) / 'ANSWER.md'
    ok = ans.exists() and all(l.split(':', 1)[1].strip() for l in ans.read_text().splitlines() if ':' in l)
    return ok, 'ANSWER.md is filled in' if ok else 'ANSWER.md still has empty lines'


def validate():
    import tempfile
    good = {'no-dodge': 'FLAW: it never dodges incoming shots, it stays on tiles about to be hit\nEVIDENCE: stays put 78%\nFIX: step away\n',
            'no-pickups': 'FLAW: it ignores the health and power pickups\nEVIDENCE: first pickup at tick 131\nFIX: take pads\n',
            'frost-spam': 'FLAW: it casts frostburst whenever ready, so the enemy dodges almost all of them\nEVIDENCE: 7 per 100 ticks, 3% hit\nFIX: cast when the enemy cannot dash\n',
            'ranged-only': 'FLAW: it never uses frostburst or the repulsor, only ranged shots\nEVIDENCE: 0.0 frostbursts\nFIX: use them\n'}
    bad = 'FLAW: it does not dodge, ignores pickups, wastes frostburst and never uses the repulsor\nEVIDENCE: 1\nFIX: all\n'
    ok = True
    for name, task in TASKS.items():
        with tempfile.TemporaryDirectory() as d:
            ws = Path(d) / name / 'workspace'
            write_files(task, ws)
            ref = good[task['flaw']] if task['kind'] == 'diagnosis' else '\n'.join(f'{k}: {v}' for k, v in task['answers'].items())
            (ws / 'ANSWER.md').write_text(ref)
            p, t, _ = run_hidden(task, ws)
            (ws / 'ANSWER.md').write_text(bad if task['kind'] == 'diagnosis' else 'a: x\nb: 0\nc: y\n')
            p2, _, _ = run_hidden(task, ws)
            print(f'{name}: reference {p}/{t}, wrong/shotgun {p2}/{t}')
            ok &= p == t and p2 < t
    print('answers:', {k: v[1] for k, v in SCOUT.items()}, MATCH, {k: v[1] for k, v in MATCHQ.items()})
    return ok


if __name__ == '__main__':
    sys.exit(0 if validate() else 1)
