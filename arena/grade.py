"""Grade an Arena bot with fixed, named threshold tests on seeds the model never sees (9000+).

    python -m arena.grade path/to/strategy.py          # the tests, unittest-style, then a JSON line
    python -m arena.grade path/to/strategy.py --quick  # the bench's visible check: loads, 10 games vs random, no errors

The test list is fixed (not the growing champions pool), so scores stay comparable across runs:
Victris's random and starter bots, and our first champion (Victris's Python starter), at rising bars.
"""
import json, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from arena import runner, league  # noqa: E402

SEED0, GAMES = 9000, 120
BARS = [('random', 'random', (95,)), ('ts_starter', 'ts-starter', (50, 65, 80, 90)), ('py_starter', None, (55, 70, 85))]


def grade(bot):
    py_starter = str(league.champions()['000-py-starter'])
    results, tests = {}, []
    timing = dict(calls=0, max_ms=0.0, total_ms=0.0, errors=0, over=0)
    for key, opp, bars in BARS:
        r = runner.play(bot, opp or py_starter, GAMES, SEED0)
        results[key] = r['win_rate']
        t = r['timing']
        timing['calls'] += t['calls']; timing['total_ms'] += t['mean_ms'] * t['calls']; timing['max_ms'] = max(timing['max_ms'], t['max_ms'])
        timing['errors'] += t['errors']; timing['over'] += t['over_budget']
        timing.setdefault('first_error', t['first_error'])
        tests += [(f'test_beats_{key}_{b}', 100 * r['win_rate'] >= b, f"{100 * r['win_rate']:.1f}% won") for b in bars]
    mean = timing['total_ms'] / max(1, timing['calls'])
    tests.insert(0, ('test_no_errors', timing['errors'] == 0, f"{timing['errors']} errors"))
    tests.insert(1, ('test_fast_enough', timing['over'] == 0 and mean < 20, f"mean {mean:.2f} ms, max {timing['max_ms']} ms, {timing['over']} over the {runner.BUDGET_S * 1000:.0f} ms budget"))
    return tests, results, dict(mean_ms=round(mean, 2), max_ms=timing['max_ms'], errors=timing['errors'], over_budget=timing['over'],
                                first_error=timing.get('first_error'))


def main():
    bot = sys.argv[1]
    if not Path(bot).exists():
        print('strategy.py is missing'); print('Ran 0 tests'); return 1
    if '--quick' in sys.argv:
        r = runner.play(bot, 'random', 10, 0)
        print(f"10 games vs random: {r['wins']} won; {r['timing']['errors']} errors, max {r['timing']['max_ms']} ms per decide")
        if r['timing']['first_error']:
            print(r['timing']['first_error'])
        return 0 if r['timing']['errors'] == 0 and r['wins'] >= 1 else 1
    tests, results, timing = grade(bot)
    for name, ok, why in tests:
        print(f"{name} ... {'ok' if ok else 'FAIL'} ({why})")
    print('-' * 70)
    print(f'Ran {len(tests)} tests')
    print('OK' if all(ok for _, ok, _ in tests) else f'FAILED (failures={sum(not ok for _, ok, _ in tests)})')
    print('ARENA_JSON ' + json.dumps(dict(win_rates=results, timing=timing, passed=sum(ok for _, ok, _ in tests), total=len(tests))))


if __name__ == '__main__':
    sys.exit(main())
