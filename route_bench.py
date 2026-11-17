#!/usr/bin/env python3
"""How well does mini's router send each request to the right model? Uses mini.py's own routing code.

    .venv/bin/python route_bench.py --mini-py ~/Documents/ChatGPT/DropBox/dropbox/mini.py --model ollama/gemma4:e4b-mlx

Scores three ways on benches/dropbox/routing.py: rules only (undecided -> chat), the model's one-word
answer for every request, and rules then the model (what mini does). Results: results/route-bench/.
"""
import argparse, json, sys, tempfile, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'benches' / 'dropbox'))
from routing import ROUTING  # noqa: E402
import dropbox_bench as D  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--mini-py')
    ap.add_argument('--model', nargs='+', default=['ollama/gemma4:e4b-mlx'])
    a = ap.parse_args()
    mini = D.load_mini(Path(tempfile.mkdtemp(prefix='route-bench-')).resolve(), a.mini_py)
    out = ROOT / 'results' / 'route-bench'
    out.mkdir(parents=True, exist_ok=True)
    for model in a.model:
        rows = []
        for c in ROUTING:
            rule, why = mini.route_by_rule(c['text'], '', c['ambient'])
            t = time.time()
            try:
                clf, answer = mini.route_by_model(c['text'], model)
            except Exception as e:
                clf, answer = 'chat', f'error {e}'[:60]
            ms = round((time.time() - t) * 1000)
            rows.append(dict(c, rule=rule, why=why, clf=clf, answer=answer, ms=ms, hybrid=rule or clf))
            mark = lambda x: '✓' if x == c['want'] else '✗'
            print(f"{c['id']:18} want={c['want']:5} rule={str(rule):5}{mark(rule or 'chat')} model={clf:5}{mark(clf)} "
                  f"both={rule or clf:5}{mark(rule or clf)} {ms:5}ms  {c['text'][:50]}", flush=True)
        def score(key, subset):
            rs = [r for r in rows if subset(r)]
            return f"{sum((r[key] or 'chat') == r['want'] for r in rs)}/{len(rs)}"
        summary = {}
        for name, sub in [('all', lambda r: True), ('mini cases', lambda r: r['set'] == 'mini'), ('hard cases', lambda r: r['set'] == 'hard'),
                          ('needs tools', lambda r: r['want'] == 'tools'), ('chat', lambda r: r['want'] == 'chat')]:
            summary[name] = dict(rules=score('rule', sub), model=score('clf', sub), both=score('hybrid', sub))
        ms = sorted(r['ms'] for r in rows)
        summary['model_ms_median'], summary['model_ms_max'] = ms[len(ms) // 2], ms[-1]
        print(json.dumps(dict(model=model, **summary), ensure_ascii=False), flush=True)
        (out / f"{model.replace('/', '-').replace(':', '-')}.json").write_text(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
