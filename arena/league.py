"""The Arena league: every generation of our bots (the best bot of each bench run and each lab session
that improved), the opponent pool every new bot faces, and a round-robin table with Elo ratings.

    python -m arena.league table                  # round-robin of every champion (+ house bots), Elo, saved to arena/league.json
    python -m arena.league challenge BOT.py --label qwen3.8-27b --model ollama/qwen3.8:27b-mlx --run agentic-arena-...
                                                   # play BOT against the pool; crowned a champion if it earns it

Every game here uses the ladder's time rules (1 s a turn, 300 s a match), whatever the bot's origin.
Every bot that enters is kept (a byte-identical copy of a kept bot is not added twice) and plays every
other kept bot and Victris's random and starter bots; the table's top Elo is the reigning champion.
"Crowned" in the history means the newcomer beat the champion of the moment head to head (at least
55% over the challenge games), matched its score against the rest of the pool, and beat every earlier generation
and Victris's starter at least 55% (2026-11-25: no regressions against the hall of fame). Bots are never
removed: the goal is one bot that beats every earlier generation. History: arena/league-history.jsonl.
"""
import argparse, json, math, shutil, sys, time
from pathlib import Path

from arena import runner

HERE = Path(__file__).resolve().parent
CHAMPS = HERE / 'champions'
HOUSE = {'random': 'random', 'ts-starter': 'ts-starter'}
TABLE = HERE / 'league.json'
HISTORY = HERE / 'league-history.jsonl'
CHALLENGE_SEEDS, CHALLENGE_GAMES = 5000, 120
CROWN_EVERY = 0.55   # a champion beats every earlier generation at least this often


def champions():
    """Champion name -> its strategy.py, oldest first (000 is Victris's Python starter)."""
    if not (CHAMPS / '000-py-starter' / 'strategy.py').exists():
        (CHAMPS / '000-py-starter').mkdir(parents=True, exist_ok=True)
        shutil.copy(HERE / 'template' / 'strategy.py', CHAMPS / '000-py-starter' / 'strategy.py')
        (CHAMPS / '000-py-starter' / 'meta.json').write_text(json.dumps(dict(
            label='py-starter', origin="Victris Arena's Python starter bot, strategy only", crowned='baseline'), indent=1))
    return {d.name: d / 'strategy.py' for d in sorted(CHAMPS.iterdir()) if (d / 'strategy.py').exists()}


def pool():
    """Every opponent a new bot faces: Victris's bots and all our champions."""
    return {**HOUSE, **{k: str(v) for k, v in champions().items()}}


def reigning():
    t = json.loads(TABLE.read_text()) if TABLE.exists() else {}
    champs = champions()
    best = t.get('reigning')
    return best if best in champs else list(champs)[-1]


def score(bot, games=CHALLENGE_GAMES, seed0=CHALLENGE_SEEDS, exclude=()):
    """Win rate against each opponent in the pool (minus `exclude`), and the mean."""
    by = {n: runner.play(bot, o, games, seed0, budget=runner.LADDER_S)['win_rate'] for n, o in pool().items() if n not in exclude}
    return dict(by_opponent=by, mean=round(sum(by.values()) / len(by), 3))


def elo(names, wins, games):
    """Bradley-Terry ratings on the Elo scale from pairwise wins (wins[a][b] = a's wins over b)."""
    r = {n: 0.0 for n in names}
    for _ in range(300):
        for a in names:
            num = sum(wins[a].get(b, 0) for b in names if b != a)
            den = sum(games[a].get(b, 0) / (math.exp(r[a]) + math.exp(r[b])) for b in names if b != a and games[a].get(b))
            if den:
                r[a] = math.log(max(num, 0.5) / den)
        m = sum(r.values()) / len(r)
        r = {n: v - m for n, v in r.items()}
    return {n: round(1500 + 400 / math.log(10) * v) for n, v in r.items()}


def table(games=60):
    """Every champion against every other champion and the house bots; Elo over the champions."""
    champs = champions()
    names = list(champs)
    wins = {n: {} for n in names}
    played = {n: {} for n in names}
    rows = {}
    for i, a in enumerate(names):
        rows[a] = {h: runner.play(champs[a], h, games, 7000, budget=runner.LADDER_S)['win_rate'] for h in HOUSE}
        for b in names[i + 1:]:
            r = runner.play(champs[a], champs[b], games, 7000, budget=runner.LADDER_S)
            wins[a][b], wins[b][a] = r['wins'] + r['draws'] / 2, r['losses'] + r['draws'] / 2
            played[a][b] = played[b][a] = games
    ratings = elo(names, wins, played) if len(names) > 1 else {names[0]: 1500}
    out = dict(updated=time.strftime('%F %T'), games=games, reigning=max(ratings, key=ratings.get),
               champions=[dict(name=n, elo=ratings[n], vs_house=rows[n], vs=({b: round(wins[n][b] / played[n][b], 3) for b in wins[n]}),
                               meta=json.loads((CHAMPS / n / 'meta.json').read_text()) if (CHAMPS / n / 'meta.json').exists() else {})
                          for n in sorted(names, key=lambda n: -ratings[n])])
    TABLE.write_text(json.dumps(out, indent=1))
    return out


def challenge(bot, label, model='', run='', notes='', keep=True):
    """Play `bot` against the pool and the champion of the moment; keep it as a new generation (keep=False:
    only if crowned)."""
    champ = reigning()
    champs = champions()
    mine = score(bot)
    head = runner.play(bot, champs[champ], CHALLENGE_GAMES, CHALLENGE_SEEDS, budget=runner.LADDER_S)
    theirs = score(champs[champ], exclude=(champ,))
    mine_wo = {k: v for k, v in mine['by_opponent'].items() if k != champ}
    mine_mean = sum(mine_wo.values()) / max(1, len(mine_wo))
    # 2026-11-25 (hall of fame): bots can beat each other in a circle, so a new champion must beat every earlier
    # generation and Victris's starter at least 55%, not only the champion of the moment; the ones it doesn't are kept
    # in the history as regressions
    regressions = sorted(k for k, v in mine['by_opponent'].items() if k != 'random' and k != champ and v < CROWN_EVERY)
    crowned = head['win_rate'] >= 0.55 and mine_mean >= theirs['mean'] and not regressions
    entry = dict(when=time.strftime('%F %T'), label=label, model=model, run=run, vs_reigning=champ, head_to_head=head['win_rate'],
                 pool=mine['by_opponent'], pool_mean=mine['mean'], reigning_pool_mean=theirs['mean'], crowned=crowned,
                 regressions=regressions)
    text = Path(bot).read_bytes()
    twin = next((n for n, f in champs.items() if f.read_bytes() == text), None)
    if twin:
        entry['same_as'] = twin
    elif keep or crowned:
        n = len(champs)
        d = CHAMPS / f'{n:03d}-{label.replace("/", "-").replace(":", "-")}'
        d.mkdir(parents=True, exist_ok=False)
        shutil.copy(bot, d / 'strategy.py')
        (d / 'meta.json').write_text(json.dumps(dict(label=label, model=model, run=run, added=entry['when'], crowned=crowned,
                                                     vs=champ, head_to_head=head['win_rate'], pool=mine['by_opponent'], notes=notes), indent=1))
        entry['kept_as'] = d.name
    with open(HISTORY, 'a') as f:
        f.write(json.dumps(entry) + '\n')
    return entry


def challenge_run(run_dir, label, model=''):
    """After an arena bench run (v1 to v4, v4-open): the best round's bot challenges (most hidden tests passed, then the higher
    mean hidden win rate, then the later round)."""
    run_dir = Path(run_dir)
    best = None
    for r in sorted(run_dir.glob('round-*/result.json'), key=lambda p: int(p.parent.name.split('-')[1])):   # round-10 after round-9
        rec = json.loads(r.read_text())
        bot = r.parent / 'workspace' / 'strategy.py'
        hid = r.parent / 'hidden.txt'
        line = next((l for l in hid.read_text().splitlines() if l.startswith('ARENA_JSON ')), None) if hid.exists() else None
        rates = json.loads(line[len('ARENA_JSON '):])['win_rates'] if line else {}
        key = (rec.get('hidden_passed', 0), sum(rates.values()) / max(1, len(rates)))
        if bot.exists() and (best is None or key >= best[0]):
            best = (key, bot, r.parent.name, rec.get('hidden_total'))
    if not best:
        return dict(error=f'no graded round with a strategy.py in {run_dir}')
    entry = challenge(best[1], label, model, run=f'{run_dir.name}/{best[2]}',
                      notes=f'best round of the {run_dir.name.split("-deepseek")[0].split("-ollama")[0]} run {run_dir.name} ({best[0][0]}/{best[3]} hidden tests)')
    if entry.get('kept_as'):
        table()
    return entry


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('table'); t.add_argument('--games', type=int, default=60)
    c = sub.add_parser('challenge'); c.add_argument('bot'); c.add_argument('--label', required=True)
    c.add_argument('--model', default=''); c.add_argument('--run', default=''); c.add_argument('--notes', default='')
    cr = sub.add_parser('challenge-run'); cr.add_argument('run_dir'); cr.add_argument('--label', required=True); cr.add_argument('--model', default='')
    a = p.parse_args()
    out = (table(a.games) if a.cmd == 'table' else challenge_run(a.run_dir, a.label, a.model) if a.cmd == 'challenge-run'
           else challenge(a.bot, a.label, a.model, a.run, a.notes))
    print(json.dumps(out, indent=1))


if __name__ == '__main__':
    sys.exit(main())
