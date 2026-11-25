"""Image-recognition bench: generated images with exact answers (benches/vision-v1, held-out benches/vision-v2).

    .venv/bin/python vision_bench.py run --bench vision-v1 --model qwen3.8:27b-mlx --think off
    .venv/bin/python vision_bench.py report --out NAME --runs vision-v1-qwen3-8-27b-mlx-off,...
    .venv/bin/python vision_bench.py coload --model gemma4:e4b-mlx --with gpt-oss:20b   # do both fit at once?

Each task is one /api/chat request with the image, temperature 0. --think off sends think=false (answer limit 512
tokens); --think on sends think=true (limit 4096). Results: results/<bench>-<model>-<think>/results.json with the
answer, grade, tokens, time and the Ollama-reported model size. Takes the lab's .bench.lock.
"""
import argparse, base64, fcntl, json, os, re, subprocess, time, unicodedata, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BENCHES = Path(os.environ.get('BENCHES_DIR') or ROOT / 'benches')   # the bench folders (tasks, hidden tests, graders); BENCHES_DIR when they live in another checkout (2026-11-25)
URL = 'http://127.0.0.1:11434'
GROUPS = {'ocr': 'OCR', 'ocr-receipt': 'OCR', 'ocr-japanese': 'OCR', 'ocr-french': 'OCR', 'handwriting': 'OCR',
          'chart': 'charts', 'count': 'counting', 'position': 'layout', 'ui': 'UI', 'table': 'tables', 'multi-step': 'multi-step'}
ORDER = ['OCR', 'charts', 'counting', 'layout', 'UI', 'tables', 'multi-step']


def api(path, body=None, timeout=600):
    req = urllib.request.Request(URL + '/api/' + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={'Content-Type': 'application/json'})
    return json.load(urllib.request.urlopen(req, timeout=timeout))


def norm(text):
    t = unicodedata.normalize('NFKC', str(text)).strip().lower()
    t = re.sub(r'<think>.*?</think>', '', t, flags=re.S)
    t = t.strip().strip('`"\'“”「」*').strip()
    t = re.sub(r'\s+', ' ', t).strip(' .。!！:')
    return t


def grade(task, answer):
    a = norm(answer)
    if task['score'] == 'number':
        nums = re.findall(r'-?\d[\d,]*(?:\.\d+)?', a)
        if not nums:
            return False
        try:
            v = float(nums[-1].replace(',', ''))
        except ValueError:
            return False
        return abs(v - float(task['answer'])) <= (task.get('tol') or 0) + 1e-9
    want = norm(task['answer'])
    if task['score'] == 'text':
        squash = lambda s: re.sub(r'[\s　]', '', s)
        return a == want or squash(a) == squash(want) or a.splitlines()[0:1] == [want] if a else False
    return a == want or (len(a.split()) <= 3 and re.search(rf'\b{re.escape(want)}\b', a) is not None)


def available_mib():
    text = subprocess.check_output(['vm_stat'], text=True)
    page = int(re.search(r'page size of (\d+)', text)[1])
    return sum(int(re.search(rf'{k}:\s+(\d+)', text)[1]) for k in ('Pages free', 'Pages inactive', 'Pages speculative')) * page / 2**20


def run(args):
    bench = BENCHES / args.bench
    tasks = json.loads((bench / 'tasks.json').read_text())['tasks']
    slug = args.model.replace(':', '-').replace('.', '-').replace('/', '-')
    out = ROOT / 'results' / f'{args.bench}-{slug}-think-{args.think}'
    out.mkdir(parents=True, exist_ok=True)
    remote = args.model.startswith('deepseek/')   # DeepSeek's hosted model through benches/deepseek_proxy.py (OpenAI format)
    caps = ['vision', 'thinking'] if remote else api('show', {'model': args.model}).get('capabilities', [])
    if 'vision' not in caps:
        raise SystemExit(f'{args.model} does not accept images (capabilities {caps})')
    if args.think == 'on' and 'thinking' not in caps:
        raise SystemExit(f'{args.model} has no thinking mode')
    rows, avail = [], []
    for i, t in enumerate(tasks, 1):
        img = base64.b64encode((bench / t['image']).read_bytes()).decode()
        body = dict(model=args.model, stream=False, keep_alive='5m',
                    messages=[dict(role='user', content=t['question'], images=[img])],
                    options=dict(temperature=0, num_predict=4096 if args.think == 'on' else 512, num_ctx=8192))
        if 'thinking' in caps:
            body['think'] = args.think == 'on'
        t0 = time.time()
        try:
            if remote:
                ob = dict(model=args.model.split('/', 1)[1], messages=[dict(role='user', content=[
                    dict(type='image_url', image_url=dict(url='data:image/' + Path(t['image']).suffix.lstrip('.').replace('jpg', 'jpeg') + ';base64,' + img)),
                    dict(type='text', text=t['question'])])])
                o = json.load(urllib.request.urlopen(urllib.request.Request('http://127.0.0.1:8096/v1/chat/completions', json.dumps(ob).encode(),
                                                                           {'Content-Type': 'application/json'}), timeout=args.timeout))
                m = o['choices'][0]['message']
                r = dict(message=dict(content=m.get('content') or '', thinking=m.get('reasoning_content') or ''),
                         eval_count=o['usage'].get('completion_tokens'), prompt_eval_count=o['usage'].get('prompt_tokens'),
                         done_reason=o['choices'][0].get('finish_reason'))
            else:
                r = api('chat', body, timeout=args.timeout)
            answer, err = r.get('message', {}).get('content', ''), None
        except Exception as e:  # keep going; a failed request is a wrong answer
            r, answer, err = {}, '', str(e)[:300]
        wall = time.time() - t0
        avail.append(available_mib())
        ok = grade(t, answer)
        rows.append(dict(id=t['id'], category=t['category'], group=GROUPS[t['category']], expected=t['answer'], answer=answer[:500],
                         correct=ok, error=err, wall_s=round(wall, 2), eval_tokens=r.get('eval_count'), prompt_tokens=r.get('prompt_eval_count'),
                         thinking_chars=len(r.get('message', {}).get('thinking') or ''), done_reason=r.get('done_reason')))
        print(f"[{i}/{len(tasks)}] {t['id']}: {'ok ' if ok else 'BAD'} {wall:.1f}s {r.get('eval_count')} tok  {answer[:60]!r}", flush=True)
    ps = [] if remote else [m for m in api('ps').get('models', []) if m['name'] == args.model]
    rec = dict(bench=args.bench, model=args.model, think=args.think, capabilities=caps, finished=time.strftime('%Y-%m-%dT%H:%M:%S%z'),
               ollama_size_mib=round(ps[0]['size'] / 2**20) if ps else None, min_available_mib=round(min(avail, default=0)),
               system_num_parallel_note='system Ollama restarted with OLLAMA_NUM_PARALLEL=4 on 2026-11-16 before these runs', rows=rows)
    (out / 'results.json').write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    print(f"{args.model} think={args.think}: {sum(x['correct'] for x in rows)}/{len(rows)}", flush=True)
    if args.unload:
        try:
            api('generate', dict(model=args.model, keep_alive=0), timeout=60)
        except Exception:
            pass


def report(args):
    lines = []
    for bench in ('vision-v1', 'vision-v2'):
        recs = []
        for name in args.runs.split(','):
            p = ROOT / 'results' / f'{bench}-{name}' / 'results.json'
            if p.exists():
                recs.append(json.loads(p.read_text()))
        if not recs:
            continue
        lines += [f'## {bench}', '', '| Model | Think | Total | ' + ' | '.join(ORDER) + ' | s / image | out tokens / image | Ollama size |',
                  '|---|---|---:|' + '---:|' * len(ORDER) + '---:|---:|---:|']
        for r in recs:
            rows = r['rows']
            cell = []
            for g in ORDER:
                gs = [x for x in rows if x['group'] == g]
                cell.append(f"{sum(x['correct'] for x in gs)}/{len(gs)}" if gs else '—')
            n = len(rows)
            tok = [x['eval_tokens'] or 0 for x in rows]
            lines.append(f"| {r['model']} | {r['think']} | **{sum(x['correct'] for x in rows)}/{n}** | " + ' | '.join(cell) +
                         f" | {sum(x['wall_s'] for x in rows) / n:.1f} | {sum(tok) / n:.0f} | {(r['ollama_size_mib'] or 0) / 1024:.1f} GiB |")
        lines.append('')
    text = '\n'.join(lines)
    out = ROOT / 'results' / f'compare-{args.out}'
    out.mkdir(parents=True, exist_ok=True)
    (out / 'vision.md').write_text(text)
    print(text)


def coload(args):
    """Load `with` (e.g. gpt-oss:20b) and then `model` on the system Ollama; report whether both stay loaded and the memory left."""
    before = available_mib()
    for m in (args.with_, args.model):
        api('generate', dict(model=m, prompt='hi', keep_alive='5m', options=dict(num_predict=1)), timeout=600)
    ps = api('ps').get('models', [])
    rec = dict(models=[(m['name'], round(m['size'] / 2**20)) for m in ps], available_before_mib=round(before),
               available_after_mib=round(available_mib()), both_loaded={args.with_, args.model} <= {m['name'] for m in ps})
    print(json.dumps(rec), flush=True)
    out = ROOT / 'results' / 'vision-coload.jsonl'
    with out.open('a') as f:
        f.write(json.dumps(dict(rec, model=args.model, with_=args.with_, at=time.strftime('%Y-%m-%dT%H:%M:%S%z'))) + '\n')
    for m in (args.model, args.with_):
        try:
            api('generate', dict(model=m, keep_alive=0), timeout=60)
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--bench', default='vision-v1')
    r.add_argument('--model', required=True)
    r.add_argument('--think', choices=['off', 'on'], default='off')
    r.add_argument('--timeout', type=int, default=600)
    r.add_argument('--unload', action='store_true')
    p = sub.add_parser('report')
    p.add_argument('--runs', required=True, help='comma-separated <model-slug>-think-<off|on>')
    p.add_argument('--out', required=True)
    c = sub.add_parser('coload')
    c.add_argument('--model', required=True)
    c.add_argument('--with', dest='with_', default='gpt-oss:20b')
    a = ap.parse_args()
    if a.cmd == 'report':
        return report(a)
    with (ROOT / '.bench.lock').open('a') as lock:
        if not a.model.startswith('deepseek/'):   # hosted: no GPU
            print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
            fcntl.flock(lock, fcntl.LOCK_EX)
        {'run': run, 'coload': coload}[a.cmd](a)


if __name__ == '__main__':
    main()
