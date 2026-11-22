"""./replays: study ladder games (victrisarena.com) that the lab imported. Read-only.

    ./replays scout [BOT]       the scouting report: every imported bot's habits, strongest first
    ./replays list [--bot NAME] imported matches (id, map, the two bots and their ratings, winner)
    ./replays show MATCH [--fighter NAME] [--from T] [--to T]
                                a match tick by tick from one fighter's view: positions, HP, its turn, hits
    ./replays mine [--vs OPP] [--games N]
                                strategy.py measured the same way as the scouting report, to compare
"""
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, sys.argv.pop(1))   # the repository root, passed by ./replays
from arena import replays  # noqa: E402

args = sys.argv[1:]
if not args or args[0] in ('-h', '--help') or args[0] not in ('scout', 'list', 'show', 'mine'):
    print(__doc__)
    sys.exit(0 if not args or args[0] in ('-h', '--help') else 2)
if args[0] == 'mine':
    args = ['mine', str(HERE / 'strategy.py')] + args[1:]
sys.argv = ['replays'] + args
replays.main()
