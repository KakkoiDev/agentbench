"""The flawed bots of arena-analysis-v1: Victris's starter with one behaviour broken, by text substitution.

Each flaw: (the substitution on arena/template/strategy.py, what the grader accepts as naming it).
"""
import re
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parents[2] / 'arena' / 'template' / 'strategy.py'

FLAWS = {
    'no-dodge': dict(
        sub=[('    if hit_in(obs, me["pos"], 0, 2):\n        react = dodge(obs)', '    if False:\n        react = dodge(obs)')],
        accept=r"dodg|evad|avoid\w*|sidestep|step (out|away|aside)|get(s|ting)? out of the way|stands? (still )?(in|on|into)\b.{0,40}(danger|projectile|shot|incoming|fire|attack)|walks? into|ignor\w* .{0,30}(incoming|projectile|danger|shot)|tak\w* (every|all|incoming) (hit|shot)|doesn.?t (move|react)",
        about='never dodges what is about to hit it'),
    'no-pickups': dict(
        sub=[('    if pickups and dist(me["pos"], pickups[0]["pos"])', '    if False and pickups and dist(me["pos"], pickups[0]["pos"])')],
        accept=r"pick.?ups?|pads?\b|power.?ups?|health (pack|pad|item)|buffs?|heal(ing)? (item|pad|pickup)",
        about='never takes pickups'),
    'frost-spam': dict(
        sub=[('    if still and enemy["dash_cooldown"] > 0 and distance <= frost_range and ready("frostburst") and not enemy["slow_ticks_left"]:',
              '    if distance <= frost_range and ready("frostburst"):')],
        accept=r"frost\w*.{0,60}(miss|wast|spam|too (often|early|much)|whenever|every time|as soon as|dodge|inaccura|low (hit|accuracy)|poor|bad timing|without|regardless|while .{0,20}dash)|(miss|wast|spam|dodg).{0,40}frost",
        about='casts frostburst whenever it can, so it is easily dodged'),
    'ranged-only': dict(
        sub=[('    if distance == 1 and ready("repulsor"):', '    if False:'),
             ('    if still and enemy["dash_cooldown"] > 0', '    if False and enemy["dash_cooldown"] > 0')],
        accept=r"\b(never|doesn.?t|does not|no|not|rarely|zero|without)\b.{0,40}\b(frost\w*|repulsor|other (weapons|abilities|attacks))\b|only (uses?|fires?|shoots?|relies on) .{0,20}(ranged|projectile|shots?|basic)|(frost\w*|repulsor)\W.{0,30}(unused|never|0\.0|0 )",
        about='only ever shoots, never uses frostburst or repulsor'),
}


def source(flaw):
    s = TEMPLATE.read_text()
    for old, new in FLAWS[flaw]['sub']:
        if old not in s:
            raise ValueError(f'{flaw}: substitution target not found: {old[:60]!r}')
        s = s.replace(old, new, 1)
    return s


def named(answer, flaw):
    """The FLAW line names this flaw and not three or more of the others (no listing every flaw)."""
    m = re.search(r'^\s*\**FLAW\**\s*:\s*(.+)$', answer, re.I | re.M)
    if not m:
        return False
    line = m.group(1)
    hit = bool(re.search(FLAWS[flaw]['accept'], line, re.I))
    others = sum(bool(re.search(f['accept'], line, re.I)) for k, f in FLAWS.items() if k != flaw)
    return hit and others < 3
