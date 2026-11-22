"""./tune: search the values of strategy.py's tunable constants for the most wins against the pool.

    ./tune                      # 10 minutes against every opponent in the pool
    ./tune --minutes 5 --vs ts-starter --vs 000-py-starter
    ./tune --games 30           # games per opponent for each candidate (more: slower, less noise)

The constants are the ones between "# TUNE-BEGIN" and "# TUNE-END" in strategy.py that are named in
TUNE_RANGES = {"NAME": (low, high), ...} (whole numbers when both bounds are whole). Add any number
your bot uses (distances, thresholds, weights) to that block to let ./tune adjust it.

The search keeps one best set and tries changes around it (bigger steps while they keep failing, smaller
when they win). A candidate is scored on its worst opponent first, then the mean, on seeds 2000+.
At the end the best set must also beat the starting values on fresh seeds (4000+); only then is it
written into strategy.py (the old file is kept as strategy.before-tune.py). Every run is logged in tune-log.jsonl.
"""
import argparse, json, random, re, sys, tempfile, time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, sys.argv.pop(1))   # the repository root, passed by ./tune
from arena import runner, league  # noqa: E402

BLOCK = re.compile(r'(# TUNE-BEGIN.*?\n)(.*?)(# TUNE-END)', re.S)


def read(src):
    m = BLOCK.search(src)
    if not m:
        sys.exit('strategy.py has no "# TUNE-BEGIN" ... "# TUNE-END" block: put the constants to tune there, with TUNE_RANGES')
    scope = {}
    exec(m.group(2), scope)
    ranges = scope.get('TUNE_RANGES') or {}
    if not ranges:
        sys.exit('the TUNE block has no TUNE_RANGES = {"NAME": (low, high)}')
    missing = [k for k in ranges if k not in scope]
    if missing:
        sys.exit(f'TUNE_RANGES names constants not set in the block: {missing}')
    return {k: scope[k] for k in ranges}, {k: tuple(v) for k, v in ranges.items()}


def write(src, values):
    def sub(m):
        body = m.group(2)
        for k, v in values.items():
            body = re.sub(rf'^{k}\s*=.*$', f'{k} = {v!r}', body, count=1, flags=re.M)
        return m.group(1) + body + m.group(3)
    return BLOCK.sub(sub, src, count=1)


def score(src, values, opps, games, seed0):
    with tempfile.NamedTemporaryFile('w', suffix='.py', delete=False) as f:
        f.write(write(src, values))
    by = {n: runner.play(f.name, o, games, seed0)['win_rate'] for n, o in opps.items()}
    Path(f.name).unlink()
    return (min(by.values()), sum(by.values()) / len(by)), by


def mutate(values, ranges, scale, rng):
    out = dict(values)
    keys = rng.sample(list(ranges), k=max(1, min(len(ranges), rng.choice([1, 1, 2, 3]))))
    for k in keys:
        lo, hi = ranges[k]
        if isinstance(lo, int) and isinstance(hi, int):
            step = max(1, round(scale * (hi - lo) * rng.gauss(0, 1)))
            out[k] = min(hi, max(lo, int(values[k]) + (step if step else rng.choice([-1, 1]))))
        else:
            out[k] = round(min(hi, max(lo, values[k] + scale * (hi - lo) * rng.gauss(0, 1))), 4)
    return out


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('--minutes', type=float, default=10)
    p.add_argument('--games', type=int, default=24)
    p.add_argument('--vs', action='append')
    a = p.parse_args()
    pool = league.pool()
    opps = {n: pool[n] for n in (a.vs or pool)}
    path = HERE / 'strategy.py'
    src = path.read_text()
    start, ranges = read(src)
    rng = random.Random(len(src))
    best, best_by = score(src, start, opps, a.games, 2000)
    cur, scale, tried, t_end = dict(start), 0.25, 0, time.time() + 60 * a.minutes
    print(f'start {start}: worst {100 * best[0]:.0f}%, mean {100 * best[1]:.0f}%  {best_by}', flush=True)
    while time.time() < t_end:
        cand = mutate(cur, ranges, scale, rng)
        if cand == cur:
            continue
        tried += 1
        sc, by = score(src, cand, opps, a.games, 2000)
        if sc > best:
            cur, best, best_by, scale = cand, sc, by, min(0.5, scale * 1.5)
            print(f'better {cur}: worst {100 * sc[0]:.0f}%, mean {100 * sc[1]:.0f}%  {by}', flush=True)
        else:
            scale = max(0.03, scale * 0.9)
    if cur == start:
        print(f'no better values in {tried} tries; strategy.py unchanged')
        verdict = 'unchanged'
    else:
        old, _ = score(src, start, opps, 2 * a.games, 4000)
        new, new_by = score(src, cur, opps, 2 * a.games, 4000)
        print(f'fresh seeds: start worst {100 * old[0]:.0f}% mean {100 * old[1]:.0f}%, tuned worst {100 * new[0]:.0f}% mean {100 * new[1]:.0f}%')
        if new > old:
            (HERE / 'strategy.before-tune.py').write_text(src)
            path.write_text(write(src, cur))
            print(f'written to strategy.py: {cur}')
            verdict = 'written'
        else:
            print('the gain did not hold on fresh seeds (noise or overfitting); strategy.py unchanged')
            verdict = 'did not hold'
    with open(HERE / 'tune-log.jsonl', 'a') as f:
        f.write(json.dumps(dict(when=time.strftime('%F %T'), start=start, best=cur, tried=tried, verdict=verdict,
                                score=dict(worst=best[0], mean=round(best[1], 3)), by=best_by)) + '\n')


if __name__ == '__main__':
    main()
