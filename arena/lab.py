"""The Arena lab: one bot improved session after session, to beat every generation of our bots.

Outside the benches. A persistent workspace (arena/lab/workspace: strategy.py, NOTES.md and any tools
the models wrote) carries over between sessions, whichever model runs them. Each session:

  1. copies the persistent workspace into arena/lab/sessions/<time>-<model>/workspace;
  2. a model (pi, in the bench sandbox) works on it with ./eval against the whole pool: Victris's
     random and starter bots and every bot generation kept in arena/champions;
  3. the result is graded (arena/grade.py) and enters the league (arena/league.py): kept as a new
     generation if it beats the lab's previous best on the league score, then the table is redone;
  4. NOTES.md always goes back to the persistent workspace (lessons from failed sessions count too);
     strategy.py and tools go back only when the session improved the bot.

    python -m arena.lab session --model ollama/qwen3.8:27b-mlx [--minutes 30]
    python -m arena.lab status

It takes the lab's .bench.lock, so it never runs at the same time as a bench.
"""
import argparse, fcntl, json, os, shutil, subprocess, sys, time
from pathlib import Path

os.environ['ARENA_BUDGET_MS'] = '1000'   # the lab plays by the ladder's time rules: 1 s a turn (before arena.runner loads)

ARENA = Path(__file__).resolve().parent
REPO = ARENA.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'benches'))   # local_engines
from arena import league, workspace  # noqa: E402

LAB = ARENA / 'lab'
WS = LAB / 'workspace'
STATE = LAB / 'state.json'
HISTORY = LAB / 'history.jsonl'
HARNESS_FILES = {'AGENT.md', 'eval', 'eval_tool.py', 'tune', 'tune_tool.py', 'replays', 'replays_tool.py', 'docs', 'skills', '__pycache__', 'eval-log.jsonl', 'strategy.before-tune.py'}

INSTRUCTION = ("You are continuing a long-running effort to build the strongest Victris Arena bot possible: one that dominates "
               "every other bot. Read NOTES.md first: it holds what every earlier session learned. strategy.py is the best bot so far; "
               "its worst matchup right now is {worst}. Then follow AGENT.md. The pool has every earlier generation of our bots plus "
               "Victris's random and starter bots; the strongest of them is {champ}. The goal is to win at least 80% against every "
               "one of them: work on the worst matchup first (./eval shows each one and how the losses went). Study the best ladder "
               "bots with ./replays (scout, show, and mine to compare your bot with them), and use ./tune to search the numbers "
               "in strategy.py's TUNE block once the logic is right. You may think up to 1 s a turn (the ladder's limit, 300 s a match): "
               "the best ladder bots search ahead, and skills/arena-search/SKILL.md explains how. Keep only changes that win more, and update NOTES.md with "
               "what you tried and the ./eval numbers, for the next session.")


def state():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def league_score(bot):
    """Win rates against the whole pool, with the worst one: the lab improves the worst matchup first, then the mean."""
    sc = league.score(bot, games=60, seed0=8000)
    sc['worst'] = min(sc['by_opponent'].values())
    return sc


def better(sc, st):
    return (sc['worst'], sc['mean']) > (st.get('best_worst', -1), st.get('best') or 0)


def setup():
    """First use: the persistent workspace starts from the strongest bot in the league."""
    if (WS / 'strategy.py').exists():
        return
    champ = league.reigning()
    # the starter champion is the template without its TUNE block: start from the template then
    workspace.build(WS, strategy=None if champ == '000-py-starter' else league.champions()[champ])
    sc = league_score(WS / 'strategy.py')
    STATE.write_text(json.dumps(dict(best=sc['mean'], best_worst=sc['worst'], best_by=sc['by_opponent'], started_from=champ, sessions=0), indent=1))


def _worst(st):
    by = st.get('best_by') or {}
    if not by:
        return 'unknown'
    k = min(by, key=by.get)
    return f'{k} ({100 * by[k]:.0f}% won)'


def session(model, minutes=30, thinking='off'):
    import agent_bench, local_engines
    lock = (REPO / '.bench.lock').open('w')
    print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)
    LAB.mkdir(exist_ok=True)
    setup()
    st = state()
    sdir = LAB / 'sessions' / f"{time.strftime('%Y%m%d-%H%M%S')}-{model.replace('/', '-').replace(':', '-')}"
    ws = sdir / 'workspace'
    keep = [p for p in WS.iterdir() if p.name not in HARNESS_FILES | {'strategy.py', 'NOTES.md'}]
    workspace.build(ws, strategy=WS / 'strategy.py', notes=WS / 'NOTES.md', keep=keep)
    home = agent_bench.pi_home()
    local_engines.add_providers(home / 'models.json')
    champ = league.reigning()
    cmd = ['/usr/bin/sandbox-exec', '-f', str(ARENA / 'sandbox.sb'), '-D', f'WORKSPACE={ws}', '-D', f'SESSIONS={home}',
           '-D', f'HOME={Path.home()}', '-D', f'REPO={REPO}', 'pi', '-p', '--model', model, '--thinking', thinking, '--offline', '--no-session', '--no-approve',
           '--no-context-files', '--no-extensions', '--no-skills', '--no-prompt-templates', '--mode', 'json',
           INSTRUCTION.format(champ=champ, worst=_worst(st)) + agent_bench.SUFFIX]
    env = dict(os.environ, PI_OFFLINE='1', PI_CODING_AGENT_DIR=str(home), ARENA_BUDGET_MS='1000')
    for start in range(4):   # retry pi's node dying at start-up under memory pressure (see agent_bench.attempt)
        t0 = time.time()
        with open(sdir / 'pi.jsonl', 'w') as so, open(sdir / 'pi.stderr', 'w') as se:
            try:
                rc = subprocess.run(cmd, cwd=ws, env=env, stdin=subprocess.DEVNULL, stdout=so, stderr=se, timeout=minutes * 60).returncode
                timed_out = False
            except subprocess.TimeoutExpired:
                rc, timed_out = None, True
        if not (rc not in (0, None) and time.time() - t0 < 10 and (sdir / 'pi.jsonl').stat().st_size == 0
                and 'Native stack trace' in (sdir / 'pi.stderr').read_text(errors='replace')):
            break
        print(f'pi crashed at start-up (start {start + 1}); retrying in 30 s', flush=True)
        time.sleep(30)
    stats = agent_bench.summarize(sdir / 'pi.jsonl')
    rec = dict(when=time.strftime('%F %T'), model=model, minutes=round((time.time() - t0) / 60, 1), timed_out=timed_out,
               tool_calls=stats['tool_calls'], output_tokens=stats['output_tokens'], before=st.get('best'), session=sdir.name)
    bot = ws / 'strategy.py'
    g = subprocess.run([str(workspace.PY), '-m', 'arena.grade', str(bot)], cwd=REPO, capture_output=True, text=True, timeout=3600,
                       env=dict(os.environ, ARENA_BUDGET_MS='1000'))
    (sdir / 'grade.txt').write_text(g.stdout + g.stderr)
    line = next((l for l in g.stdout.splitlines() if l.startswith('ARENA_JSON ')), None)
    rec['grade'] = json.loads(line[11:]) if line else None
    ok = bool(rec['grade']) and rec['grade']['timing']['errors'] == 0
    sc = league_score(bot) if ok else dict(mean=0, worst=0, by_opponent={})
    rec['after'], rec['after_worst'], rec['by_opponent'] = sc['mean'], sc['worst'], sc['by_opponent']
    rec['before_worst'] = st.get('best_worst')
    rec['improved'] = ok and better(sc, st)
    shutil.copy(ws / 'NOTES.md', WS / 'NOTES.md') if (ws / 'NOTES.md').exists() else None
    if rec['improved']:
        shutil.copy(bot, WS / 'strategy.py')
        for p in ws.iterdir():
            if p.name not in HARNESS_FILES | {'strategy.py', 'NOTES.md'}:
                (shutil.copytree(p, WS / p.name, dirs_exist_ok=True) if p.is_dir() else shutil.copy(p, WS / p.name))
        entry = league.challenge(bot, f'lab-{model.split("/")[-1]}', model, run=f'lab/{sdir.name}',
                                 notes=f'Arena lab session; league score {sc["mean"]}')
        rec['league'] = {k: entry.get(k) for k in ('kept_as', 'same_as', 'crowned', 'head_to_head', 'vs_reigning')}
        if entry.get('kept_as'):
            league.table()
        st.update(best=sc['mean'], best_worst=sc['worst'], best_by=sc['by_opponent'])
    st['sessions'] = st.get('sessions', 0) + 1
    STATE.write_text(json.dumps(st, indent=1))
    (sdir / 'result.json').write_text(json.dumps(rec, indent=1))
    with open(HISTORY, 'a') as f:
        f.write(json.dumps(rec) + '\n')
    return rec


def status():
    t = json.loads(league.TABLE.read_text()) if league.TABLE.exists() else {}
    hist = [json.loads(l) for l in HISTORY.read_text().splitlines()] if HISTORY.exists() else []
    return dict(lab=state(), sessions=hist[-20:], league=t)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    s = sub.add_parser('session'); s.add_argument('--model', required=True); s.add_argument('--minutes', type=int, default=30)
    s.add_argument('--thinking', default='off')
    sub.add_parser('status')
    a = p.parse_args()
    print(json.dumps(session(a.model, a.minutes, a.thinking) if a.cmd == 'session' else status(), indent=1))


if __name__ == '__main__':
    sys.exit(main())
