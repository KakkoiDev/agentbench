"""Can this model handle several requests at once? A smoke test, not a throughput bench.

    python3 scripts/concurrency_smoke.py MODEL      (an Ollama tag, or ENGINE/ID with its server running)

One short request alone, then 4 short requests sent at the same moment. Records whether all 4
answered, how long the 4 took next to one alone, and a verdict:
  "parallel"  all answered and the 4 took under 2x the time of one (the server works on them together),
  "queued"    all answered but one after another (about 3x or more),
  "partly"    in between,
  "failed"    any request errored or timed out.
Writes results/concurrency/concurrency-DATE-MODEL.json.
"""
import concurrent.futures as cf, json, sys, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'benches'))
import local_engines  # noqa: E402

ASKS = ['Name three fruits that are red. One line.', 'What is 12 times 7? Answer with the number.',
        'Translate "good morning" into French. One line.', 'Give one tip for sleeping better. One sentence.']


def request(model, ask):
    eng = model.split('/', 1)[0] if '/' in model and model.split('/', 1)[0] in local_engines.ENGINES else None
    if eng:
        port, key = local_engines.ENGINES[eng]['port'], local_engines.api_key(eng)
        body = {'model': model.split('/', 1)[1], 'max_tokens': 400, 'temperature': 0, 'messages': [{'role': 'user', 'content': ask}]}
        req = urllib.request.Request(f'http://127.0.0.1:{port}/v1/chat/completions', json.dumps(body).encode(),
                                     {'Content-Type': 'application/json', 'Authorization': 'Bearer ' + key})
    else:
        body = {'model': model, 'stream': False, 'think': False, 'keep_alive': '10m', 'options': {'num_predict': 400, 'temperature': 0},
                'messages': [{'role': 'user', 'content': ask}]}
        if model.startswith('gpt-oss'):
            body['think'] = 'low'
        req = urllib.request.Request('http://127.0.0.1:11434/api/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'})
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(req, timeout=600))
        text = (r['choices'][0]['message'].get('content') if eng else r['message'].get('content')) or ''
        return dict(ok=bool(text.strip()), seconds=round(time.time() - t, 2), chars=len(text))
    except Exception as e:
        return dict(ok=False, seconds=round(time.time() - t, 2), error=str(e)[:200])


def main():
    model = sys.argv[1]
    request(model, 'hi')                          # load it
    one = request(model, ASKS[1])
    t = time.time()
    with cf.ThreadPoolExecutor(4) as ex:
        four = list(ex.map(lambda a: request(model, a), ASKS))
    wall = round(time.time() - t, 2)
    ratio = round(wall / one['seconds'], 2) if one.get('seconds') else None
    verdict = ('failed' if not one['ok'] or not all(x['ok'] for x in four) else
               'parallel' if ratio is not None and ratio < 2 else 'queued' if ratio is not None and ratio >= 3 else 'partly')
    row = dict(model=model, case='concurrency-4', one=one, four=four, wall=wall, ratio=ratio, verdict=verdict, when=time.strftime('%F %T'))
    print(json.dumps(row), flush=True)
    out = ROOT / 'results' / 'concurrency'
    out.mkdir(parents=True, exist_ok=True)
    (out / ('concurrency-%s-%s.json' % (time.strftime('%Y%m%d-%H%M'), model.replace('/', '-').replace(':', '-')))).write_text(json.dumps(row, indent=1))
    return 0 if verdict != 'failed' else 1


if __name__ == '__main__':
    sys.exit(main())
