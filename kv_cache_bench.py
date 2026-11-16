#!/usr/bin/env python3
"""Can a smaller attention cache fit more on this 32 GB Mac without hurting answers?

Ollama keeps each conversation's attention cache (KV cache) next to the model weights. Its
size grows with the context length; OLLAMA_KV_CACHE_TYPE=q8_0 or q4_0 stores it at 8 or 4
bits instead of 16 (needs OLLAMA_FLASH_ATTENTION=1). This measures, per model and setting:

  memory   Ollama's reported size of the loaded model + cache at the given context
  speed    prompt reading and answer writing (tokens/s) on a ~12k-token prompt
  recall   three facts hidden early, mid-way and late in a ~24k-token text, asked back
           (a 4-bit cache is where long-context recall would suffer first)

    .venv/bin/python kv_cache_bench.py single                # each model alone, every setting
    .venv/bin/python kv_cache_bench.py pair                  # two models loaded together
    .venv/bin/python kv_cache_bench.py report

Runs on an isolated Ollama (port 11501) with the same model store, under the lab's
.bench.lock; the system Ollama is not reconfigured. Results: results/kv-cache/*.json.
"""
import argparse, fcntl, json, os, random, sys, threading, time
from pathlib import Path
from parallel_requests_bench import IsolatedOllama, api, swap_mib, available_mib

ROOT = Path(__file__).resolve().parent
OUT = ROOT / 'results' / 'kv-cache'
SETTINGS = {  # name -> extra Ollama environment
    'f16': {'OLLAMA_FLASH_ATTENTION': '0'},
    'f16-flash': {'OLLAMA_FLASH_ATTENTION': '1'},
    'q8_0': {'OLLAMA_FLASH_ATTENTION': '1', 'OLLAMA_KV_CACHE_TYPE': 'q8_0'},
    'q4_0': {'OLLAMA_FLASH_ATTENTION': '1', 'OLLAMA_KV_CACHE_TYPE': 'q4_0'},
}
MODELS = ['gpt-oss:20b', 'qwen3.8:27b-mlx', 'gemma4:26b-mlx']
PAIRS = [('gpt-oss:20b', 'gemma4:e4b-mlx'), ('qwen3.8:27b-mlx', 'gemma4:e4b-mlx')]
NEEDLES = [('the vault code', 'DELTA-4471'), ('the name of the ship', 'Marigold Seven'), ('the color of the third door', 'ochre')]


def filler(words, seed):
    rng = random.Random(seed)
    vocab = ('report team river morning budget window garden signal market paper station winter coffee '
             'engine harbor letter summer forest number bridge planet music doctor island camera').split()
    out = []
    while len(out) < words:
        out += [rng.choice(vocab) for _ in range(rng.randint(8, 16))] + ['.']
    return ' '.join(out[:words])


def recall_prompt():
    parts, total = [], 18000  # ~24k tokens
    for k, (what, value) in enumerate(NEEDLES):
        parts.append(filler(total // 4, k))
        parts.append(f'Remember this: {what} is {value}.')
    parts.append(filler(total // 4, 9))
    q = 'Answer in three short lines: what is the vault code, what is the name of the ship, and what is the color of the third door?'
    return '\n'.join(parts) + '\n\n' + q


def gen(url, model, prompt, ctx, n=200, think=None):
    body = dict(model=model, prompt=prompt, stream=False, keep_alive='10m',
                options=dict(num_ctx=ctx, num_predict=n, temperature=0))
    if think is not None:
        body['think'] = think
    t = time.time()
    r = api(url, 'generate', body, timeout=1800)
    pe, pd = r.get('prompt_eval_count', 0), r.get('prompt_eval_duration', 0) or 1
    ec, ed = r.get('eval_count', 0), r.get('eval_duration', 0) or 1
    return dict(text=r.get('response', ''), prompt_tokens=pe, prompt_tps=round(pe / pd * 1e9, 1),
                gen_tokens=ec, gen_tps=round(ec / ed * 1e9, 1), wall=round(time.time() - t, 1))


def think_off(model):
    return 'low' if model.startswith('gpt-oss') else False


def loaded(url):
    return {m['name']: round(m.get('size', 0) / 2 ** 30, 2) for m in api(url, 'ps').get('models', [])}


def norm(text):
    """Compare answers without formatting: bold marks and the look-alike hyphens models like to use."""
    for ch in '\u2010\u2011\u2012\u2013\u2014\u2212':
        text = text.replace(ch, '-')
    return text.replace('*', '').lower()


def recall_of(text):
    return f"{sum(v.lower() in norm(text) for _, v in NEEDLES)}/{len(NEEDLES)}"


def measure(url, model, ctx):
    speed = gen(url, model, filler(9000, 1) + '\n\nSummarize the text above in one sentence.', ctx, 200, think_off(model))
    mem = loaded(url).get(model)
    rec = gen(url, model, recall_prompt(), ctx, 300, think_off(model))
    return dict(model=model, ctx=ctx, size_gib=mem, prompt_tps=speed['prompt_tps'], gen_tps=speed['gen_tps'],
                prompt_tokens=speed['prompt_tokens'], recall=recall_of(rec['text']), recall_text=rec['text'][:400],
                recall_prompt_tokens=rec['prompt_tokens'], recall_prompt_tps=rec['prompt_tps'])


def run_single(args):
    rows = []
    for setting in args.settings:
        with IsolatedOllama(11501, 1, OUT, context_length=args.ctx, extra_env=SETTINGS[setting]) as srv:
            for model in args.models:
                swap0 = swap_mib()
                try:
                    r = measure(srv.url, model, args.ctx)
                except Exception as e:
                    r = dict(model=model, ctx=args.ctx, error=str(e)[:300])
                r.update(setting=setting, swap_grew_mib=round((swap_mib() or 0) - (swap0 or 0)), available_gib=round(available_mib() / 1024, 1))
                rows.append(r)
                print(json.dumps(r, ensure_ascii=False)[:400], flush=True)
                srv.unload(model)
                time.sleep(3)
                (OUT / f'single-{args.ctx}.json').write_text(json.dumps(rows, indent=1, ensure_ascii=False))


def run_pair(args):
    rows = []
    for setting in args.settings:
        env = dict(SETTINGS[setting], OLLAMA_MAX_LOADED_MODELS='2')
        with IsolatedOllama(11501, 1, OUT, context_length=args.ctx, extra_env=env) as srv:
            for a, b in PAIRS:
                swap0 = swap_mib()
                r = dict(setting=setting, ctx=args.ctx, models=[a, b])
                try:
                    gen(srv.url, a, 'Say hi.', args.ctx, 5, think_off(a))
                    gen(srv.url, b, 'Say hi.', args.ctx, 5, think_off(b))
                    r['loaded'] = loaded(srv.url)
                    r['both_loaded'] = len(r['loaded']) == 2
                    res = {}
                    def go(m):
                        res[m] = gen(srv.url, m, filler(3000, 2) + '\n\nSummarize in one sentence.', args.ctx, 150, think_off(m))
                    ts = [threading.Thread(target=go, args=(m,)) for m in (a, b)]
                    [t.start() for t in ts]; [t.join() for t in ts]
                    r['gen_tps_together'] = {m: res[m]['gen_tps'] for m in res}
                    r['prompt_tps_together'] = {m: res[m]['prompt_tps'] for m in res}
                except Exception as e:
                    r['error'] = str(e)[:300]
                r['swap_grew_mib'] = round((swap_mib() or 0) - (swap0 or 0))
                r['available_gib'] = round(available_mib() / 1024, 1)
                rows.append(r)
                print(json.dumps(r)[:400], flush=True)
                srv.unload(a); srv.unload(b)
                time.sleep(5)
                (OUT / f'pair-{args.ctx}.json').write_text(json.dumps(rows, indent=1))


def report(_):
    for f in sorted(OUT.glob('*.json')):
        print('==', f.name)
        for r in json.loads(f.read_text()):
            if r.get('recall_text') is not None:  # graded again: older runs missed look-alike hyphens
                r['recall'] = recall_of(r['recall_text'])
            if 'models' in r:
                print(f"  {r['setting']:9} {' + '.join(r['models']):34} both={r.get('both_loaded')} {r.get('loaded')} "
                      f"gen={r.get('gen_tps_together')} swap+{r['swap_grew_mib']}MiB {r.get('error', '')}")
            else:
                print(f"  {r['setting']:9} {r['model']:18} {r.get('size_gib')} GiB  read {r.get('prompt_tps')} t/s  "
                      f"write {r.get('gen_tps')} t/s  recall {r.get('recall')}  swap+{r['swap_grew_mib']}MiB {r.get('error', '')}")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('cmd', choices=['single', 'pair', 'report'])
    p.add_argument('--ctx', type=int, default=32768)
    p.add_argument('--settings', nargs='+', default=list(SETTINGS))
    p.add_argument('--models', nargs='+', default=MODELS)
    a = p.parse_args()
    OUT.mkdir(parents=True, exist_ok=True)
    if a.cmd == 'report':
        return report(a)
    lock = (ROOT / '.bench.lock').open('w')
    print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)
    for m in set(a.models) | {x for pair in PAIRS for x in pair}:  # nothing doubled in the system Ollama
        try:
            api('http://127.0.0.1:11434', 'generate', dict(model=m, keep_alive=0), timeout=60)
        except Exception:
            pass
    {'single': run_single, 'pair': run_pair}[a.cmd](a)


if __name__ == '__main__':
    main()
