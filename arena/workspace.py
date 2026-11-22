"""Build an Arena workspace: the folder a model works in to build its bot.

    strategy.py   the bot to improve (the starter, the reigning champion, or the previous round's)
    AGENT.md      the goal and the work loop          NOTES.md   what earlier rounds and runs learned
    docs/ skills/ Victris's rules, bot guide and skills
    ./eval        plays strategy.py against the pool (Victris's bots + our champions) and explains the losses
    ./tune        searches the values of strategy.py's tunable constants for more wins
    ./replays     ladder games from victrisarena.com: scouting reports, any match tick by tick, our bot compared
"""
import shutil, stat, sys
from pathlib import Path

ARENA = Path(__file__).resolve().parent
REPO = ARENA.parent
HARNESS = ARENA / 'harness'
PY = REPO / '.venv' / 'bin' / 'python'

TOOLS = {'eval': 'Play strategy.py against the opponent pool: ./eval [--games N] [--vs NAME]',
         'tune': "Tune strategy.py's constants against the pool: ./tune [--minutes M] [--vs NAME]",
         'replays': 'Study imported ladder games: ./replays scout | list | show MATCH | mine'}
SCRIPT = '''#!/bin/sh
# {doc}
exec "{py}" "$(dirname "$0")/{name}_tool.py" "{repo}" "$@"
'''


def build(dest, strategy=None, notes=None, keep=()):
    """Fill `dest` with a fresh workspace. strategy/notes: files to start from (default: the starter, the
    template notes). keep: extra files or folders (paths) copied in as they are, e.g. tools a model wrote."""
    dest = Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    for name in ['AGENT.md'] + [f'{t}_tool.py' for t in TOOLS]:
        shutil.copy(HARNESS / name, dest / name)
    for d in ('docs', 'skills'):
        shutil.copytree(HARNESS / d, dest / d, dirs_exist_ok=True)
    shutil.copy(strategy or ARENA / 'template' / 'strategy.py', dest / 'strategy.py')
    shutil.copy(notes or HARNESS / 'NOTES.md', dest / 'NOTES.md')
    for k in keep:
        k = Path(k)
        if k.is_dir():
            shutil.copytree(k, dest / k.name, dirs_exist_ok=True, ignore=shutil.ignore_patterns('__pycache__'))
        elif k.exists():
            shutil.copy(k, dest / k.name)
    for name, doc in TOOLS.items():
        ev = dest / name
        ev.write_text(SCRIPT.format(doc=doc, py=PY, repo=REPO, name=name))
        ev.chmod(ev.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return dest


if __name__ == '__main__':
    print(build(sys.argv[1]))
