"""./eval: play strategy.py against the opponent pool and report how the losses went.

    ./eval                     # every opponent in the pool, 40 games each
    ./eval --games 30          # quicker
    ./eval --vs ts-starter     # one opponent (random, ts-starter, or a champion's name)

The pool is Victris's random and starter bots plus our earlier champions (arena/champions). Seeds
here are 1000-1999; the final score uses other seeds, so do not tune to these games one by one.
Every run is appended to eval-log.jsonl next to strategy.py.
"""
import argparse, json, sys, time
from pathlib import Path

HERE = Path(__file__).resolve().parent   # the workspace: ./eval sits next to strategy.py
sys.path.insert(0, sys.argv.pop(1))   # the repository root, passed by ./eval
from arena import runner, league  # noqa: E402


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--games', type=int, default=40)
    p.add_argument('--vs', action='append', help='an opponent name from the pool (repeatable)')
    a = p.parse_args()
    pool = league.pool()
    names = a.vs or list(pool)
    unknown = [n for n in names if n not in pool]
    if unknown:
        sys.exit(f'unknown opponent {unknown}; the pool is: {", ".join(pool)}')
    bot = HERE / 'strategy.py'
    results = []
    for n in names:
        r = runner.play(bot, pool[n], a.games, seed0=1000)
        r['opponent'] = n
        results.append(r)
        print(runner.loss_report(r, limit=5), end='\n\n', flush=True)
    total = sum(r['wins'] for r in results) / sum(r['games'] for r in results)
    print(f'overall: {100 * total:.1f}% won across {len(results)} opponents')
    with open(HERE / 'eval-log.jsonl', 'a') as f:
        f.write(json.dumps(dict(when=time.strftime('%F %T'), overall=round(total, 3),
                                by_opponent={r['opponent']: r['win_rate'] for r in results})) + '\n')


if __name__ == '__main__':
    main()
