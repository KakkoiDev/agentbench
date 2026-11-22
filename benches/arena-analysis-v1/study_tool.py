"""./study: watch and measure the bot in bot.bin (its code is hidden on purpose) - and compare it.

    ./study eval [--vs NAME] [--games N]   its results against random, ts-starter and py-starter, and how the losses went
    ./study profile [--vs NAME]            its habits: range, attacks per 100 ticks, accuracy, dodging, pickups...
    ./study reference [--vs NAME]          the same profile for a sound bot built from the same code, to compare
    ./study watch --seed N [--vs NAME] [--from T] [--to T]
                                           one of its games, tick by tick, from its view
    ./study scout [BOT]                    ladder bots' profiles (victrisarena.com), for comparison
"""
import argparse, base64, os, sys, tempfile, zlib
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = sys.argv.pop(1)
sys.path.insert(0, ROOT)
os.environ.setdefault('ARENA_REPLAYS_DIR', str(Path(ROOT) / 'benches' / 'arena-analysis-v1' / 'replays'))
from arena import runner, replays, league  # noqa: E402

OPPS = {'random': 'random', 'ts-starter': 'ts-starter', 'py-starter': str(league.champions()['000-py-starter'])}


def hidden_bot():
    f = tempfile.NamedTemporaryFile('w', suffix='.py', delete=False)
    f.write(zlib.decompress(base64.b64decode((HERE / 'bot.bin').read_text())).decode())
    f.close()
    return f.name


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd', choices=['eval', 'profile', 'reference', 'watch', 'scout'])
    p.add_argument('bot', nargs='?', help='for scout: one ladder bot')
    p.add_argument('--vs', default=None); p.add_argument('--games', type=int, default=30)
    p.add_argument('--seed', type=int, default=0); p.add_argument('--from', dest='t0', type=int, default=0); p.add_argument('--to', dest='t1', type=int)
    a = p.parse_args()
    if a.cmd == 'scout':
        import json
        print(replays.scouting_md(json.loads(replays.PROFILES.read_text()), a.bot))
        return
    if a.vs and a.vs not in OPPS:
        sys.exit(f'--vs is one of: {", ".join(OPPS)}')
    bot = str(Path(ROOT) / 'arena' / 'template' / 'strategy.py') if a.cmd == 'reference' else hidden_bot()
    try:
        if a.cmd == 'eval':
            for n in ([a.vs] if a.vs else OPPS):
                r = runner.play(bot, OPPS[n], a.games, 5000)
                r['opponent'] = n
                print(runner.loss_report(r, limit=4), end='\n\n')
        elif a.cmd in ('profile', 'reference'):
            vs = a.vs or 'ts-starter'
            name = ('the hidden bot' if a.cmd == 'profile' else 'the reference bot') + f' vs {vs}'
            print(replays.describe(name, dict(replays.mine(bot, OPPS[vs], a.games, 6000), rating=None)))
        elif a.cmd == 'watch':
            from arena_sim import replay_trajectories
            r = runner.play(bot, OPPS[a.vs or 'ts-starter'], 1, a.seed, replays=True, workers=1)
            rep = r['replays'][0]
            tr = replay_trajectories(rep['text'])
            print(replays.show_trajectory(tr, rep['me'], {rep['me']: 'bot', 3 - rep['me']: a.vs or 'ts-starter'}, a.t0, a.t1))
    finally:
        if a.cmd != 'reference':
            os.unlink(bot)


if __name__ == '__main__':
    main()
