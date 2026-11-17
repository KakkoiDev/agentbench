#!/usr/bin/env python3
"""Laya as mini's router, on the same 53 labelled requests as route_bench.py.

Runs in its own environment (Laya pulls torch):
    USE_TF=0 .laya-venv/bin/python laya_route_bench.py --backend mlx      # cavi-ai/laya-MLX-8bit, in ~/models/laya-mlx
    USE_TF=0 .laya-venv/bin/python laya_route_bench.py --backend mps      # PyTorch on the Apple GPU (also: cpu)
    USE_TF=0 .laya-venv/bin/python laya_route_bench.py --backend mps --checkpoint router   # English texts to the English checkpoint

Two ways of asking:
  simple   one choice question, chat or tools, described like mini's ROUTE_PROMPT
  fine     eight categories (chat, conversation, translate, media, files-read, files-write, web, code),
           mapped to chat or tools
Each is scored alone and after mini's rules (rules first, Laya for the rest), as mini would use it.
Results: results/route-bench/laya-*.json
"""
import argparse, importlib.util, json, os, sys, tempfile, time, types
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'benches' / 'dropbox'))
from routing import ROUTING  # noqa: E402

MINI_PY = Path.home() / 'Documents' / 'ChatGPT' / 'DropBox' / 'dropbox' / 'mini.py'

SIMPLE = {'route': {'type': 'choice', 'instructions': 'Who should handle this chat message, `message`?', 'criteria': {
    'chat': 'conversation, greetings, thanks, quick facts or math, translating or rewording text, summarizing or answering '
            'about the messages shown, advice: answerable from the conversation and general knowledge',
    'tools': "needs the person's files or folders (reading, saving, organizing), searching the web or anything current "
             '(news, prices, weather, opening hours), or running code'}}}
FINE = {'route': {'type': 'choice', 'instructions': 'What does this chat message, `message`, ask for?', 'criteria': {
    'chat': 'small talk, greetings, thanks, advice, ideas, explanations, general knowledge, quick math',
    'conversation': 'something said earlier in this chat: a date, a decision, a summary of the messages',
    'translate': 'translating, rewording or writing a short text (a message, a note) to send',
    'media': 'a photo, picture, screenshot or voice message that is attached',
    'files_read': "reading or finding the person's own documents, notes, photos or files",
    'files_write': "saving, creating, copying, renaming or organizing the person's files or lists",
    'web': 'anything current from the internet: news, prices, opening hours, weather, sports results',
    'code': 'running code or a script, computing over data'}}}
TOOLS_FINE = {'files_read', 'files_write', 'web', 'code'}


def load_rules():
    """mini.py's route_by_rule, without starting mini."""
    state = tempfile.mkdtemp(prefix='laya-route-')
    os.environ.update(DROPBOX_MINI_URL='http://127.0.0.1:9', DROPBOX_MINI_TOKEN='x', DROPBOX_MINI_STATE=state,
                      DROPBOX_MINI_WORKSPACE=state, DROPBOX_MINI_PROFILE='x')
    spec = importlib.util.spec_from_file_location('dropbox_mini', MINI_PY)
    mini = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mini)
    return mini.route_by_rule


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--backend', default='mlx', choices=['mlx', 'mps', 'cpu'])
    ap.add_argument('--checkpoint', default='multilingual', choices=['multilingual', 'router'])
    a = ap.parse_args()
    t = time.time()
    if a.backend == 'mlx':  # the MLX port: 8-bit multilingual checkpoint
        sys.path.insert(0, str(Path.home() / 'models' / 'laya-mlx'))
        import laya as mlx_laya  # noqa: the port's own package, read before use
        base = Path.home() / 'models' / 'laya-mlx'
        models = {'multilingual': mlx_laya.load(base / 'multilingual')}
        if a.checkpoint == 'router':  # Laya's own language check picks the checkpoint; no PyTorch model is loaded
            import importlib
            sys.path.pop(0)
            sys.modules.pop('laya', None)
            pick = importlib.import_module('laya').Router().route
            sys.path.insert(0, str(base))
            models['english'] = mlx_laya.load(base)
        else:
            pick = lambda state, q: types.SimpleNamespace(model='multilingual')
        def ask(state, q):
            model, tok = models[pick(state, q).model]
            return mlx_laya.predict(model, tok, state, q)
    else:
        import laya
        if a.checkpoint == 'router':
            agent = laya.Router()
            agent.preload(['english', 'multilingual'])
        else:
            agent = laya.load('convaiinnovations/laya-multilingual', device=a.backend)
        ask = agent.predict
    ask({'message': 'warm up'}, SIMPLE)
    print(f'loaded in {time.time() - t:.1f}s', flush=True)
    rule_of = load_rules()
    rows = []
    for c in ROUTING:
        text = c['text'].removeprefix('@mini ').strip()
        rule, _ = rule_of(c['text'], '', c['ambient'])
        row = dict(id=c['id'], set=c['set'], want=c['want'], text=text, rule=rule)
        for name, q in (('simple', SIMPLE), ('fine', FINE)):
            t = time.time()
            ans = ask({'message': text}, q)['answers']['route']
            ms = round((time.time() - t) * 1000)
            choice = ans['choice']
            kind = choice if name == 'simple' else ('tools' if choice in TOOLS_FINE else 'chat')
            row[name] = dict(choice=choice, kind=kind, ms=ms, p=round(max((ans.get('probabilities') or {}).values() or [0]), 2))
        rows.append(row)
        ok = lambda k: '✓' if k == c['want'] else '✗'
        print(f"{c['id']:18} want={c['want']:5} simple={row['simple']['kind']:5}{ok(row['simple']['kind'])} "
              f"fine={row['fine']['choice']:12}{ok(row['fine']['kind'])} rules+fine={(rule or row['fine']['kind']):5}{ok(rule or row['fine']['kind'])} "
              f"{row['fine']['ms']:4}ms  {text[:45]}", flush=True)

    def score(get, sub):
        rs = [r for r in rows if sub(r)]
        return f"{sum(get(r) == r['want'] for r in rs)}/{len(rs)}"
    ways = {'simple': lambda r: r['simple']['kind'], 'fine': lambda r: r['fine']['kind'],
            'rules+simple': lambda r: r['rule'] or r['simple']['kind'], 'rules+fine': lambda r: r['rule'] or r['fine']['kind']}
    subsets = {'all': lambda r: True, 'mini cases': lambda r: r['set'] == 'mini', 'hard cases': lambda r: r['set'] == 'hard',
               'needs tools': lambda r: r['want'] == 'tools', 'chat': lambda r: r['want'] == 'chat',
               'japanese': lambda r: any('぀' <= ch <= '鿿' for ch in r['text'])}
    summary = {s: {w: score(g, f) for w, g in ways.items()} for s, f in subsets.items()}
    ms = sorted(r['fine']['ms'] for r in rows)
    summary['ms_median'], summary['ms_max'] = ms[len(ms) // 2], ms[-1]
    print(json.dumps(dict(checkpoint=a.checkpoint, backend=a.backend, **summary), ensure_ascii=False), flush=True)
    out = ROOT / 'results' / 'route-bench'
    out.mkdir(parents=True, exist_ok=True)
    (out / f'laya-{a.checkpoint}-{a.backend}.json').write_text(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
