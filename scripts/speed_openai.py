"""Speed mode for models served by an OpenAI-compatible server (mlx-lm, MTPLX, oMLX, SlotStream, ds4).

    python3 scripts/speed_openai.py ENGINE/MODEL     (the server must be running; benches/local_engines.py)

The same requests as scripts/speed_test.py (an assistant-sized system prompt and one everyday
question; the same prompt again; a terse reply), streamed, so the time to the first token
(prompt reading) and the writing speed after it are measured from the outside, the same way for
every engine. Writes results/speed/speed-DATE.json rows like speed_test.py, with "engine".
"""
import json, sys, time, urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'benches'))
import local_engines  # noqa: E402
sys.path.insert(0, str(Path(__file__).resolve().parent))
from speed_test import SYSTEM, TERSE, ASK  # noqa: E402  (the same prompts)


KEY = 'local'


def call(port, model, system, ask, max_tokens=600):
    body = {'model': model, 'stream': True, 'max_tokens': max_tokens, 'temperature': 0,
            'stream_options': {'include_usage': True},
            'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': ask}]}
    req = urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', json.dumps(body).encode(),
                                 {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + KEY})
    t0, first, n, usage = time.time(), None, 0, {}
    with urllib.request.urlopen(req, timeout=1800) as r:
        for line in r:
            line = line.decode(errors='ignore').strip()
            if not line.startswith('data:') or line == 'data: [DONE]':
                continue
            ev = json.loads(line[5:])
            usage = ev.get('usage') or usage
            delta = (ev.get('choices') or [{}])[0].get('delta') or {}
            if delta.get('content') or delta.get('reasoning_content') or delta.get('reasoning'):
                n += 1
                first = first or time.time()
    end = time.time()
    out = usage.get('completion_tokens') or n
    return dict(wall=round(end - t0, 2), prompt_tokens=usage.get('prompt_tokens'), prompt_s=round((first or end) - t0, 2),
                out_tokens=out, out_s=round(end - (first or end), 2))


def main():
    model = sys.argv[1]
    engine, mid = model.split('/', 1)
    port = local_engines.ENGINES[engine]['port']
    global KEY
    KEY = local_engines.api_key(engine)
    rows = []
    call(port, mid, 'You are terse.', 'hi', 8)   # warm up
    for name, system, ask in [('first', SYSTEM + ' ', ASK), ('cached', SYSTEM + ' ', ASK + ' Thanks.'), ('terse', SYSTEM + ' ' + TERSE, ASK)]:
        r = dict(model=model, engine=engine, case=name, **call(port, mid, system, ask))
        rows.append(r)
        print(json.dumps(r), flush=True)
    out = Path(__file__).resolve().parent.parent / 'results' / 'speed'
    out.mkdir(parents=True, exist_ok=True)
    (out / ('speed-%s-%s.json' % (time.strftime('%Y%m%d-%H%M'), model.replace('/', '-')))).write_text(json.dumps(rows, indent=1))


if __name__ == '__main__':
    main()
