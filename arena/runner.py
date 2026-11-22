"""Play Victris Arena bots against each other offline, on the ladder's own match code (arena_sim).

    python -m arena.runner BOT.py --vs random --vs ts-starter --vs arena/champions/003/strategy.py --games 100

A bot is a Python file with decide(obs, match) -> turn (see arena/template/strategy.py). Opponents are
Victris's house bots by name ("random", "ts-starter") or other bot files, which play in self-play
mode with sides swapped every other game. Every decide() call is timed: one over the budget (200 ms
by default, ARENA_BUDGET_MS to change it; opponent bot files get the ladder's 1 s) is replaced by an
idle turn and counted, as is every turn after a bot spends the ladder's 300 s match budget, and an exception costs an
idle turn too, as on the ladder. The maps and spawns give only 6 distinct starts (3 maps x 2 sides), so between two
deterministic bots every seed would replay one of 6 games: by default each match opens with 0-8
ticks of seeded random steps for both fighters (decide() still sees those ticks, its turns are
replaced), so every seed is a different game and a bot cannot just memorise 6 of them. Results come back per opponent with a summary of every loss, built
from the match stats (who did the damage, with what; zone time; refused turns).
"""
import argparse, importlib.util, json, os, random, subprocess, sys, tempfile, time, traceback
from pathlib import Path

from arena_sim import Simulator

HOUSE = ('random', 'ts-starter')
# Per-turn budget for the bot under test: 200 ms in the benches; the Arena lab and league use the
# ladder's 1 s (ARENA_BUDGET_MS=1000). Opponent bot files always get the ladder's 1 s.
BUDGET_S = float(os.environ.get('ARENA_BUDGET_MS', 200)) / 1000
LADDER_S = 1.0
MATCH_BUDGET_S = 300.0   # the ladder's response-time budget for a whole match
# games run in this many processes at once (each with its own simulator); slow bots need it
WORKERS = int(os.environ.get('ARENA_WORKERS', 6))
OPENING = 8   # at most this many ticks of random opening steps (0 = off)


class Bot:
    """A bot file, loaded fresh for each match so module-level state never leaks between matches."""

    def __init__(self, path, budget=BUDGET_S):
        self.path, self.budget = str(path), budget
        self.calls = self.slow = self.errors = self.exhausted = 0
        self.total_s = self.max_s = 0.0
        self.first_error = None

    def new_match(self, hello):
        spec = importlib.util.spec_from_file_location(f'arena_bot_{id(self)}_{time.time_ns()}', self.path)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        self.spent = 0.0
        return mod, {'id': hello['match_id'], 'rules': hello['rules'], 'hello': hello}

    def turn(self, mod, obs, match):
        left = MATCH_BUDGET_S - self.spent
        if left <= 0:   # the match budget is spent: every turn is idle, as on the ladder
            self.exhausted += 1
            return {}
        if isinstance(obs.get('self'), dict):
            obs['self']['budget_ms_left'] = int(1000 * left)
        t = time.perf_counter()
        try:
            out = mod.decide(obs, match) or {}
            if not isinstance(out, dict):
                raise TypeError(f'decide returned {type(out).__name__}, not a dict')
        except Exception:
            self.errors += 1
            self.first_error = self.first_error or traceback.format_exc(limit=3)
            out = {}
        dt = time.perf_counter() - t
        self.spent += min(dt, self.budget)
        self.calls += 1
        self.total_s += dt
        self.max_s = max(self.max_s, dt)
        if dt > self.budget:
            self.slow += 1
            out = {}
        return out

    def timing(self):
        return dict(calls=self.calls, mean_ms=round(1000 * self.total_s / max(1, self.calls), 2), max_ms=round(1000 * self.max_s, 1),
                    over_budget=self.slow, errors=self.errors, first_error=self.first_error, budget_ms=round(1000 * self.budget),
                    match_budget_exhausted=self.exhausted, total_s=self.total_s)


def _merge_timing(ts):
    calls = sum(t['calls'] for t in ts)
    total = sum(t['total_s'] for t in ts)
    return dict(calls=calls, mean_ms=round(1000 * total / max(1, calls), 2), max_ms=max(t['max_ms'] for t in ts),
                over_budget=sum(t['over_budget'] for t in ts), errors=sum(t['errors'] for t in ts),
                first_error=next((t['first_error'] for t in ts if t['first_error']), None), budget_ms=ts[0]['budget_ms'],
                match_budget_exhausted=sum(t['match_budget_exhausted'] for t in ts), total_s=total)


def _summary(end, me):
    """What happened in one match, from the end message's stats, for fighter `me`."""
    st = end.get('stats') or {}
    fs = {f['id']: f for f in st.get('fighters', [])}
    a, b = fs.get(me, {}), fs.get(3 - me, {})
    return dict(result=end.get('result'), reason=end.get('reason'), tick=end.get('final_tick'), map=st.get('map'), seed=st.get('seed'),
                hp=a.get('hp'), enemy_hp=b.get('hp'), dealt=a.get('damage_dealt'), taken=a.get('damage_taken'),
                taken_by=a.get('taken_by_source'), dealt_by=a.get('dealt_by_source'), zone_ticks=a.get('zone_ticks'),
                pickups=a.get('pickups'), refused=a.get('refused_turns'), stunned=a.get('stunned'))


def play(bot_path, opponent, games=100, seed0=0, budget=None, keep=False, opening=OPENING, replays=False, workers=None):
    """Play `games` matches of bot_path against opponent (a house bot name or another bot file), split
    over `workers` processes (the game numbers, seeds and sides are the same however they are split)."""
    budget = BUDGET_S if budget is None else budget
    workers = max(1, min(WORKERS if workers is None else workers, games))
    args = (str(bot_path), str(opponent), seed0, budget, keep, opening, replays)
    if workers == 1:
        parts = [_play_some(*args, list(range(games)))]
    else:
        parts = _in_workers(args, [list(range(i, games, workers)) for i in range(workers)])
    out = dict(opponent=opponent if opponent in HOUSE else str(opponent), games=games, wins=0, losses=0, draws=0, matches=[])
    if replays:
        out['replays'] = []
    for p in parts:
        for k in ('wins', 'losses', 'draws'):
            out[k] += p[k]
        out['matches'] += p['matches']
        if replays:
            out['replays'] += p['replays']
    out['matches'].sort(key=lambda m: m['seed'])
    out['win_rate'] = round(out['wins'] / games, 3)
    out['timing'] = _merge_timing([p['timing'] for p in parts])
    return out


def _in_workers(args, chunks):
    """Each chunk of games in its own `python -m arena.runner --worker` process (no fork, no re-import of
    the caller's script); results come back through temporary files, so a bot's prints can't garble them."""
    root = str(Path(__file__).resolve().parents[1])
    env = dict(os.environ, PYTHONPATH=root + os.pathsep + os.environ.get('PYTHONPATH', ''))
    procs = []
    with tempfile.TemporaryDirectory() as d:
        for i, c in enumerate(chunks):
            job = Path(d) / f'{i}.json'
            job.write_text(json.dumps(dict(args=list(args), indices=c)))
            procs.append((subprocess.Popen([sys.executable, '-m', 'arena.runner', '--worker', str(job)], env=env, cwd=root,
                                           stdout=subprocess.DEVNULL, stderr=subprocess.PIPE), job))
        parts = []
        for p, job in procs:
            err = p.communicate()[1]
            out = job.with_suffix('.out')
            if p.returncode or not out.exists():
                raise RuntimeError(f'game worker failed: {err.decode(errors="replace")[-800:]}')
            parts.append(json.loads(out.read_text()))
    return parts


def _play_some(bot_path, opponent, seed0, budget, keep, opening, replays, indices):
    me_bot = Bot(bot_path, budget)
    opp_bot = None if opponent in HOUSE else Bot(opponent, LADDER_S)
    out = dict(wins=0, losses=0, draws=0, matches=[])
    if replays:
        out['replays'] = []
    with Simulator(1) as sim:
        for g in indices:
            seed = seed0 + g
            if opp_bot is None:
                hello, nxt = sim.start(0, seed=seed, opponent=opponent)
                me, them = 1, None
            else:
                hello, nxt = sim.start(0, seed=seed)
                me = 1 if g % 2 == 0 else 2   # swap sides every other game
                them = 3 - me
            rng = random.Random(seed * 7919 + 17)
            shuffle = rng.randint(0, opening) if opening else 0
            mod, match = me_bot.new_match(hello[me])
            omod, omatch = opp_bot.new_match(hello[them]) if opp_bot else (None, None)
            while True:
                msg = nxt[me]
                if msg.get('type') == 'end':
                    break
                replies = {me: me_bot.turn(mod, msg, match)}
                if opp_bot:
                    replies[them] = opp_bot.turn(omod, nxt[them], omatch)
                if msg.get('tick', 0) < shuffle:   # random opening: seeded steps replace both fighters' turns
                    for f in replies:
                        moves = nxt[f].get('moves') or []
                        replies[f] = {'move': rng.choice(moves)['dir']} if moves and rng.random() < 0.8 else {}
                nxt = sim.step([replies])[0]
            s = _summary(msg, me)
            if replays:
                out['replays'].append(dict(me=me, seed=seed, result=s['result'], text=sim.replay_text(0)))
            out['wins' if s['result'] == 'win' else 'losses' if s['result'] == 'loss' else 'draws'] += 1
            if keep or s['result'] != 'win':
                out['matches'].append(s)
    out['timing'] = me_bot.timing()
    return out


def loss_report(res, limit=8):
    """A short, readable account of how the losses went: for a model to read between rounds."""
    lines = [f"vs {res['opponent']}: {res['wins']}/{res['games']} won, {res['losses']} lost, {res['draws']} drawn "
             f"(decide: mean {res['timing']['mean_ms']} ms, max {res['timing']['max_ms']} ms, {res['timing']['over_budget']} over the {res['timing']['budget_ms']} ms budget, "
             f"{res['timing']['errors']} errors" + (f", {res['timing']['match_budget_exhausted']} turns idle after the 300 s match budget ran out" if res['timing']['match_budget_exhausted'] else '') + ")"]
    if res['timing']['first_error']:
        lines.append('first error:\n' + res['timing']['first_error'].strip())
    losses = [m for m in res['matches'] if m['result'] != 'win']
    by = {}
    for m in losses:
        for src, v in (m.get('taken_by') or {}).items():
            by[src] = by.get(src, 0) + v
    if losses:
        lines.append(f"damage taken in non-wins, by source: {json.dumps(dict(sorted(by.items(), key=lambda kv: -kv[1])))}")
        lines.append(f"reasons: {json.dumps({r: sum(1 for m in losses if m['reason'] == r) for r in {m['reason'] for m in losses}})}")
    for m in losses[:limit]:
        lines.append(f"- seed {m['seed']} {m['map']}: {m['result']} ({m['reason']}) at tick {m['tick']}, hp {m['hp']} vs {m['enemy_hp']}, "
                     f"dealt {m['dealt']} {json.dumps(m['dealt_by'])}, took {m['taken']} {json.dumps(m['taken_by'])}, zone ticks {m['zone_ticks']}, "
                     f"pickups {json.dumps(m['pickups'])}, stunned {m['stunned']}")
    return '\n'.join(lines)


def main():
    if len(sys.argv) == 3 and sys.argv[1] == '--worker':   # one chunk of play()'s games, in its own process
        job = Path(sys.argv[2])
        j = json.loads(job.read_text())
        job.with_suffix('.out').write_text(json.dumps(_play_some(*j['args'], j['indices'])))
        return 0
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('bot')
    p.add_argument('--vs', action='append', help='opponent: random, ts-starter or a bot file (repeatable)')
    p.add_argument('--games', type=int, default=100)
    p.add_argument('--seed', type=int, default=0, help='first seed (seeds are consecutive)')
    p.add_argument('--budget-ms', type=float, default=BUDGET_S * 1000)
    p.add_argument('--workers', type=int)
    p.add_argument('--json', action='store_true')
    a = p.parse_args()
    results = [play(a.bot, o, a.games, a.seed, a.budget_ms / 1000, workers=a.workers) for o in (a.vs or ['random', 'ts-starter'])]
    if a.json:
        print(json.dumps(results, indent=1))
    else:
        print('\n\n'.join(loss_report(r) for r in results))


if __name__ == '__main__':
    sys.exit(main())
