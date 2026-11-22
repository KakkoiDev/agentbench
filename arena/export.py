"""Package an Arena bot to play on victrisarena.com: Victris's own Python client (bots/py-starter/bot.py,
connection code unchanged) around our strategy.py.

    python -m arena.export                         # the lab's best bot -> arena/export/<name>/
    python -m arena.export path/to/strategy.py --name my-bot

The folder holds bot.py, strategy.py, requirements.txt, run.sh and README.md. Running it needs your bot's
token from victrisarena.com (My Bots), which stays with you: run.sh reads it from ARENA_TOKEN.
"""
import argparse, json, shutil, stat, sys
from pathlib import Path

ARENA = Path(__file__).resolve().parent
REPO = ARENA.parent
STARTER = REPO / 'engines' / 'victris-arena' / 'bots' / 'py-starter' / 'bot.py'

HEAD = '''"""Victris Arena bot built in the GLM lab's Arena lab ({origin}).

The strategy is strategy.py (decide(obs, match) -> turn). The connection code below is Victris Arena's
Python starter client, unchanged: it queues, answers every observation and reconnects by itself.

    ARENA_TOKEN=<your bot token> ./run.sh            # ranked ladder
    ARENA_TOKEN=... ARENA_LADDER=<league id> ./run.sh  # your group's league ladder
"""
import asyncio
import inspect
import json
import os
import traceback

import websockets

from strategy import decide

'''

RUN = '''#!/bin/sh
# Play on victrisarena.com. Needs ARENA_TOKEN (My Bots -> your bot -> token). Optional: ARENA_LADDER (a league
# ladder id, to play your group's bots), ARENA_VERSION (default {version}; use a new one each time the bot changes),
# ARENA_MATCHES (stop after that many matches).
cd "$(dirname "$0")"
[ -n "$ARENA_TOKEN" ] || {{ echo "Set ARENA_TOKEN to your bot's token from victrisarena.com (My Bots)"; exit 1; }}
[ -x .venv/bin/python ] || {{ python3 -m venv .venv && .venv/bin/pip install -q -r requirements.txt; }} || exit 1
ARENA_VERSION="${{ARENA_VERSION:-{version}}}" exec .venv/bin/python bot.py
'''

README = '''# {name}: a Victris Arena bot from the Arena lab

{origin}

## Play it

1. On [victrisarena.com](https://victrisarena.com), sign in and create a bot under **My Bots**; copy its token.
   Optionally set **Edit → Built with** to the model that wrote it.
2. Run it from this folder (the first run makes a virtualenv and installs `websockets`):

   ```bash
   ARENA_TOKEN=<token> ./run.sh
   ```

   - Against your group's bots: your group's league ladder id, `ARENA_TOKEN=<token> ARENA_LADDER=<id> ./run.sh`
     (the account must be admitted to that league).
   - A fixed number of matches: `ARENA_MATCHES=20`.
   - Each time you upload a changed strategy.py, give it a new version: `ARENA_VERSION=v2`.
3. Every match prints its result and a watch link. Stop with Ctrl+C.

The bot runs on this computer: keep it running for as long as it should play. The ladder allows 1 s per
tick; this bot answers in well under that.

## Offline numbers when it was packaged

{numbers}
'''


def client_body():
    """The starter's connection code, from reply() to the end, unchanged."""
    src = STARTER.read_text()
    return src[src.index('def reply(obs, match):'):]


def build(bot=None, name=None, version='v1'):
    from arena import lab
    st = lab.state()
    if bot is None:
        bot = lab.WS / 'strategy.py'
        origin = f"The Arena lab's best bot after {st.get('sessions', 0)} sessions."
    else:
        origin = f'Built from {bot}.'
    bot = Path(bot)
    name = name or 'lab-best'
    dest = ARENA / 'export' / name
    if dest.exists():
        shutil.rmtree(dest)
    dest.mkdir(parents=True)
    shutil.copy(bot, dest / 'strategy.py')
    (dest / 'bot.py').write_text(HEAD.format(origin=origin) + client_body())
    (dest / 'requirements.txt').write_text('websockets>=12\n')
    run = dest / 'run.sh'
    run.write_text(RUN.format(version=version))
    run.chmod(run.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    by = st.get('best_by') if bot == lab.WS / 'strategy.py' else None
    numbers = '\n'.join(f'- vs {k}: {100 * v:.0f}% won' for k, v in (by or {}).items()) or '- (not measured)'
    (dest / 'README.md').write_text(README.format(name=name, origin=origin, numbers=numbers))
    return dest


def selftest(dest, games=3):
    """Drive bot.py's reply() with real hello/observation messages from the match code, as the ladder would."""
    import importlib.util, types
    from arena_sim import Simulator
    sys.modules.setdefault('websockets', types.SimpleNamespace(connect=None, WebSocketException=Exception))
    sys.path.insert(0, str(dest))
    spec = importlib.util.spec_from_file_location('exported_bot', dest / 'bot.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    wins = 0
    with Simulator(1) as sim:
        for g in range(games):
            hello, nxt = sim.start(0, seed=g, opponent='ts-starter')
            match = {'id': hello[1]['match_id'], 'rules': hello[1]['rules']}
            while nxt[1].get('type') != 'end':
                msg = json.loads(json.dumps(nxt[1]))
                out = json.loads(mod.reply(msg, match))
                assert out['tick'] == msg['tick'], 'the reply must echo the tick'
                out.pop('tick')
                nxt = sim.step([{1: out}])[0]
            wins += nxt[1]['result'] == 'win'
    return wins


if __name__ == '__main__':
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('bot', nargs='?'); p.add_argument('--name'); p.add_argument('--version', default='v1')
    a = p.parse_args()
    d = build(a.bot, a.name, a.version)
    print(f'{d}: self-test {selftest(d)}/3 won against ts-starter through bot.py')
