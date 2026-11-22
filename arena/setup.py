"""Rebuild the Victris-derived files the Arena needs from Victris's own download (engines/victris-arena, not in git).

Victris Arena's docs, skills and starter code carry no license, so the repository keeps only our own code; run
this after cloning (and after updating engines/victris-arena):

    .venv/bin/python -m arena.setup

It writes arena/harness/docs (the rules and guides), arena/harness/skills (Victris's three skills; our own
arena-search skill is in git), arena/template/strategy.py (the Python starter's strategy, its constants in a
TUNE block for ./tune) and arena/champions/000-py-starter (the starter, the league's first bot).
"""
import json, re, shutil
from pathlib import Path

ARENA = Path(__file__).resolve().parent
VICTRIS = ARENA.parent / 'engines' / 'victris-arena'
DOCS = ['bot-guide.md', 'frame-data.md', 'observation.json', 'ranged-attacks.md', 'rules.json', 'rules.md', 'simulator.md', 'spec.md']
SKILLS = ['arena-combat', 'arena-match-review', 'arena-simulator']

HEADER = '''"""Your Victris Arena bot: decide(obs, match) is called every tick and returns this tick's turn.

Started from Victris Arena's Python starter bot (https://victrisarena.com/download/bots), with only its
strategy kept (the connection code is not needed offline). `obs` is the observation the ladder sends
(docs/observation.json), `match` a dict kept for the whole match: {"id", "rules"} plus anything you store.
Return {} to idle, or {"move": "N"|"S"|"E"|"W"|..., "attack": {...}} as in docs/bot-guide.md.
Keep each call fast: the ladder allows 1 second per tick, the bench budget is 200 ms.
"""

'''
TUNE_RANGES = "TUNE_RANGES = {'FIGHTING_RANGE': (1, 8), 'PATROL_TICKS': (5, 60), 'PATROL_RADIUS': (0, 6)}\n"


def template():
    src = (VICTRIS / 'bots' / 'py-starter' / 'bot.py').read_text()
    body = src[src.index('# How close the bot likes to fight'):src.index('def reply(obs, match):')].rstrip() + '\n'
    consts, rest = body.split('\n\n\ndef ', 1)
    tuned = ('# TUNE-BEGIN: ./tune searches these values (each within its TUNE_RANGES range) and writes back the best.\n'
             + consts.strip() + '\n' + TUNE_RANGES + '# TUNE-END\n')
    return HEADER + tuned + '\n\ndef ' + rest


def main():
    h = ARENA / 'harness'
    (h / 'docs').mkdir(parents=True, exist_ok=True)
    for d in DOCS:
        shutil.copy(VICTRIS / 'docs' / d, h / 'docs' / d)
    for s in SKILLS:
        src = VICTRIS / 'skills' / s / s
        shutil.copytree(src, h / 'skills' / s, dirs_exist_ok=True)
    (ARENA / 'template').mkdir(exist_ok=True)
    (ARENA / 'template' / 'strategy.py').write_text(template())
    c = ARENA / 'champions' / '000-py-starter'
    if not (c / 'strategy.py').exists():
        c.mkdir(parents=True, exist_ok=True)
        # the league's first bot is the starter without the TUNE block, as it was entered
        (c / 'strategy.py').write_text(re.sub(r'# TUNE-BEGIN.*?\n|TUNE_RANGES = .*\n|# TUNE-END\n', '', template()))
        (c / 'meta.json').write_text(json.dumps(dict(label='py-starter', origin="Victris Arena's Python starter bot, strategy only", crowned='baseline'), indent=1))
    print('arena: Victris docs, skills, template and first champion rebuilt from', VICTRIS)


if __name__ == '__main__':
    main()
