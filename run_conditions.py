"""How every agent bench run was run (asked for on 2026-11-25: tag runs with how they were run, so results made
under different conditions are never compared as if they were the same).

The conditions of a run:
- mode:          "timed" (the old rule: short time limits, a model's speed shaped its score) or "best" (from
                 2026-11-25: each model's best possible result: time limits only as safety caps of hours, thinking at the
                 highest level it supports, agentbox's harness, instructions that encourage testing)
- time_limit_s:  the per-task limit, and how many tasks hit it (a run where many did measured speed, not ceiling)
- thinking:      what was asked (pi's --thinking) AND what the model actually did, measured from its transcripts:
                 2026-11-25 showed pi's "off" did not stop local models from thinking (Ollama's default thinks), so the
                 asked value alone is wrong for every local run before that day
- harness:       bare pi or agentbox (version and parts)
- context:       the context window pi was told the model has

`python run_conditions.py` writes results/run-conditions.json for every run (new runs record theirs in meta.json under
"conditions"; older ones get them filled in from their meta.json and transcripts). Reports read it from there.
"""
import json, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RES = ROOT / 'results'
OUT = RES / 'run-conditions.json'
BEST_FROM = '2026-11-25T08'   # runs started after this with conditions.mode == "best" in meta.json


def thinking_used(pi_jsonl):
    """(assistant turns, turns with thinking, thinking characters) from a pi --mode json transcript."""
    turns = think = chars = 0
    try:
        lines = Path(pi_jsonl).read_text(errors='replace').splitlines()
    except OSError:
        return 0, 0, 0
    for l in lines:
        if '"message_end"' not in l:
            continue
        try:
            j = json.loads(l)
        except ValueError:
            continue
        m = j.get('message') or {}
        if m.get('role') != 'assistant':
            continue
        turns += 1
        t = sum(len(c.get('thinking') or '') for c in m.get('content') or [] if c.get('type') == 'thinking')
        think += t > 0
        chars += t
    return turns, think, chars


def conditions(run_dir):
    meta = json.loads((run_dir / 'meta.json').read_text())
    tasks = [p.parent for p in run_dir.glob('*/result.json')]
    if not tasks:
        return None
    results = [json.loads((t / 'result.json').read_text()) for t in tasks]
    used = [thinking_used(t / 'pi.jsonl') for t in tasks]
    turns, think, chars = (sum(u[i] for u in used) for i in range(3))
    c = dict(meta.get('conditions') or {})
    limits = sorted({r.get('timeout_s') or meta.get('timeout_s') for r in results if (r.get('timeout_s') or meta.get('timeout_s'))})
    c.setdefault('mode', 'timed')
    c.setdefault('time_limit_s', limits[-1] if limits else meta.get('timeout_s'))
    c['tasks'] = len(results)
    c['hit_time_limit'] = sum(bool(r.get('timed_out')) for r in results)
    h = meta.get('harness')
    c.setdefault('harness', f"agentbox {h.get('version')} ({', '.join(e.get('part', '?') for e in h.get('extensions', []))})" if h
                 else ('bare pi + ' + Path(meta['extension']).parent.name if meta.get('extension') else 'bare pi'))
    c['thinking_asked'] = c.get('thinking_asked') or meta.get('thinking')
    c['thinking_used'] = dict(turns=turns, turns_with_thinking=think, chars=chars,
                              summary='none' if not think else f"{round(100 * think / max(1, turns))}% of turns, {chars // max(1, turns)} chars/turn")
    c['model'] = meta.get('model')
    c['bench'] = meta.get('bench')
    c['started'] = meta.get('started')
    return c


def main():
    out = {}
    for meta in sorted(RES.glob('*/meta.json')):
        d = meta.parent
        if d.name.startswith('_'):
            continue
        try:
            c = conditions(d)
        except (ValueError, KeyError, OSError) as e:
            print(f'skip {d.name}: {e}', file=sys.stderr)
            continue
        if c:
            out[d.name] = c
    OUT.write_text(json.dumps(out, indent=1))
    modes = {}
    for c in out.values():
        modes[c['mode']] = modes.get(c['mode'], 0) + 1
    print(f'{len(out)} runs tagged in {OUT.relative_to(ROOT)}: {modes}')


if __name__ == '__main__':
    main()
