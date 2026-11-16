#!/usr/bin/env python3
"""Bench DropBox's real jobs on this Mac, to pick its models (cases in benches/dropbox/cases.py).

    .venv/bin/python dropbox_bench.py translate --model gemma4:e4b-mlx
    .venv/bin/python dropbox_bench.py captions  --whisper mlx-community/whisper-large-v3-turbo
    .venv/bin/python dropbox_bench.py mini      --model ollama/qwen3.8:27b-mlx
    .venv/bin/python dropbox_bench.py report

translate: every sentence in all 6 directions between English, Japanese and French, through
  Ollama with a plain "translate this chat message" prompt. Scored with chrF (character n-gram
  F-score, works for Japanese without word splitting) against the human-written versions.
captions: the sentences spoken by two macOS voices per language, clean and with background
  noise, through DropBox's own voice.py (the server-side Whisper the call captions use, with its
  language detection). Scored by word error rate (character error rate for Japanese),
  language detection and seconds of compute per second of audio.
mini: DropBox's house agent itself (its mini.py, rules, sandbox and skills, with a private
  state folder), one pi run per request. Checked for staying silent when it should, facts
  that must be in the answer, and a file it must write.

Results: results/dropbox-<job>-<model>/results.json. Takes the lab's .bench.lock.
"""
import argparse, fcntl, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT / 'benches' / 'dropbox'))
import cases  # noqa: E402

OLLAMA = 'http://127.0.0.1:11434'
DROPBOX = Path.home() / '.local' / 'share' / 'dropbox'
VOICE_PY = DROPBOX / 'voice-venv' / 'bin' / 'python'


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def out_dir(job, model):
    d = ROOT / 'results' / f'dropbox-{job}-{slug(model.split("/", 1)[-1] if job != "captions" else model.rsplit("/", 1)[-1])}'
    d.mkdir(parents=True, exist_ok=True)
    return d


def take_lock():
    lock = (ROOT / '.bench.lock').open('w')
    print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)
    return lock


def unload_all():
    """Free the GPU between jobs: every model Ollama holds."""
    try:
        for m in json.load(urllib.request.urlopen(OLLAMA + '/api/ps', timeout=10)).get('models', []):
            body = json.dumps({'model': m['name'], 'keep_alive': 0}).encode()
            urllib.request.urlopen(urllib.request.Request(OLLAMA + '/api/generate', body, {'Content-Type': 'application/json'}), timeout=60).read()
    except Exception as e:
        print('unload:', e, flush=True)


# --- scoring -------------------------------------------------------------------------------

def chrf(hyp, ref, n=6, beta=2.0):
    """chrF (Popović 2015): character n-gram precision/recall, n = 1..6, spaces removed."""
    hyp, ref = re.sub(r'\s+', '', hyp), re.sub(r'\s+', '', ref)
    if not hyp or not ref:
        return 0.0
    precs, recs = [], []
    for k in range(1, n + 1):
        h = [hyp[i:i + k] for i in range(len(hyp) - k + 1)]
        r = [ref[i:i + k] for i in range(len(ref) - k + 1)]
        if not h or not r:
            continue
        from collections import Counter
        hc, rc = Counter(h), Counter(r)
        match = sum((hc & rc).values())
        precs.append(match / len(h))
        recs.append(match / len(r))
    if not precs:
        return 0.0
    p, r = sum(precs) / len(precs), sum(recs) / len(recs)
    return 0.0 if p + r == 0 else 100 * (1 + beta ** 2) * p * r / (beta ** 2 * p + r)


def normalize(text, lang):
    text = text.lower()
    text = re.sub(r"[^\w\s']" if lang != 'ja' else r'[^\w]', ' ', text)
    text = re.sub(r'[、。！？・「」]', ' ', text)
    return text.split() if lang != 'ja' else list(re.sub(r'\s+', '', text))


def error_rate(hyp, ref, lang):
    """Word error rate (character error rate for Japanese): edit distance / reference length."""
    h, r = normalize(hyp, lang), normalize(ref, lang)
    if not r:
        return 0.0
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[-1] / len(r)


# --- translate -----------------------------------------------------------------------------

def chat(model, messages, think):
    body = {'model': model, 'messages': messages, 'stream': False, 'options': {'temperature': 0}, 'keep_alive': '10m'}
    if think is not None:
        body['think'] = think
    req = urllib.request.Request(OLLAMA + '/api/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'})
    with urllib.request.urlopen(req, timeout=600) as r:
        return json.load(r)


def run_translate(args):
    think = {'off': False, 'on': True}.get(args.think, args.think)  # gpt-oss takes low/medium/high
    d = out_dir('translate', args.model + ('' if args.think == 'off' else '-think-' + args.think))
    rows = []
    for i, s in enumerate(cases.SENTENCES):
        for src in ('en', 'ja', 'fr'):
            for tgt in ('en', 'ja', 'fr'):
                if src == tgt:
                    continue
                prompt = (f'Translate this chat message from {cases.LANG_NAMES[src]} into {cases.LANG_NAMES[tgt]}. '
                          f'Keep the tone. Output only the translation, nothing else.\n\n{s[src]}')
                t = time.time()
                try:
                    res = chat(args.model, [{'role': 'user', 'content': prompt}], think)
                    hyp = (res.get('message') or {}).get('content', '').strip().strip('"「」').strip()
                    err = ''
                except Exception as e:
                    hyp, err = '', str(e)[:200]
                rows.append(dict(i=i, src=src, tgt=tgt, hyp=hyp, ref=s[tgt], chrf=round(chrf(hyp, s[tgt]), 1),
                                 seconds=round(time.time() - t, 2), error=err))
                print(f'[{i}] {src}->{tgt} chrF {rows[-1]["chrf"]:5.1f}  {hyp[:70]}', flush=True)
    by = {}
    for r in rows:
        by.setdefault(f'{r["src"]}->{r["tgt"]}', []).append(r['chrf'])
    summary = dict(job='translate', model=args.model, think=args.think, chrf=round(sum(r['chrf'] for r in rows) / len(rows), 1),
                   by_direction={k: round(sum(v) / len(v), 1) for k, v in by.items()},
                   seconds_per_message=round(sum(r['seconds'] for r in rows) / len(rows), 2),
                   errors=sum(1 for r in rows if r['error']))
    (d / 'results.json').write_text(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


# --- captions ------------------------------------------------------------------------------

def make_audio(folder):
    """Each sentence by each voice, 16 kHz mono WAV, clean and with noise at about 10 dB SNR."""
    import numpy as np
    items = []
    folder.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(7)
    for i, s in enumerate(cases.SENTENCES):
        for lang, voices in cases.VOICES.items():
            for v, voice in enumerate(voices):
                clean = folder / f'{i:02d}-{lang}-{v}.wav'
                if not clean.exists():
                    subprocess.run(['say', '-v', voice, '-o', str(clean), '--file-format=WAVE', '--data-format=LEI16@16000', s[lang]], check=True)
                noisy = folder / f'{i:02d}-{lang}-{v}-noise.wav'
                if not noisy.exists():
                    with wave.open(str(clean)) as w:
                        a = np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32)
                    # Pad with half a second of silence each side, like a real utterance cut.
                    a = np.concatenate([np.zeros(8000, np.float32), a, np.zeros(8000, np.float32)])
                    p = float(np.mean(a[a != 0] ** 2)) if np.any(a) else 1.0
                    noise = rng.normal(0, (p / 10) ** 0.5, len(a)).astype(np.float32)
                    b = np.clip(a + noise, -32768, 32767).astype(np.int16)
                    with wave.open(str(noisy), 'wb') as w:
                        w.setnchannels(1); w.setsampwidth(2); w.setframerate(16000); w.writeframes(b.tobytes())
                for path, cond in ((clean, 'clean'), (noisy, 'noise')):
                    items.append(dict(i=i, lang=lang, voice=voice, cond=cond, path=str(path), ref=s[lang]))
    return items


def run_captions(args):
    d = out_dir('captions', args.whisper)
    items = make_audio(ROOT / 'results' / 'dropbox-captions-audio')
    env = dict(os.environ, DROPBOX_VOICE_MODEL=args.whisper)
    proc = subprocess.Popen([str(VOICE_PY), '-I', str(DROPBOX / 'voice.py')], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            text=True, env=env, bufsize=1)
    rows = []
    # Warm-up: the first request loads the model; not timed.
    proc.stdin.write(items[0]['path'] + '\t\t\n'); proc.stdout.readline()
    for it in items:
        for mode in ('detect', 'pinned'):
            prior, pin = (it['lang'], 'pin') if mode == 'pinned' else ('', '')
            t = time.time()
            proc.stdin.write(f'{it["path"]}\t{prior}\t{pin}\n')
            out = json.loads(proc.stdout.readline() or '{}')
            sec = time.time() - t
            with wave.open(it['path']) as w:
                dur = w.getnframes() / w.getframerate()
            text = out.get('text', '')
            rows.append(dict(**{k: it[k] for k in ('i', 'lang', 'voice', 'cond', 'ref')}, mode=mode, text=text,
                             detected=out.get('language'), error=out.get('error', ''),
                             er=round(error_rate(text, it['ref'], it['lang']), 3), rtf=round(sec / dur, 3)))
            print(f'{it["lang"]} {it["cond"]:5} {mode:6} err {rows[-1]["er"]:.2f} lang {out.get("language")}  {text[:60]}', flush=True)
    proc.stdin.close(); proc.wait(timeout=60)

    def avg(sel, key):
        v = [r[key] for r in rows if sel(r)]
        return round(sum(v) / len(v), 3) if v else None
    summary = dict(job='captions', model=args.whisper,
                   error_rate={f'{l}-{c}-{m}': avg(lambda r: r['lang'] == l and r['cond'] == c and r['mode'] == m, 'er')
                               for l in ('en', 'ja', 'fr') for c in ('clean', 'noise') for m in ('detect', 'pinned')},
                   error_rate_all=avg(lambda r: True, 'er'),
                   language_detected_right=round(sum(1 for r in rows if r['mode'] == 'detect' and r['detected'] == r['lang'])
                                                 / sum(1 for r in rows if r['mode'] == 'detect'), 3),
                   compute_per_audio_second=avg(lambda r: True, 'rtf'),
                   errors=sum(1 for r in rows if r['error']))
    (d / 'results.json').write_text(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False, indent=1))
    print(json.dumps(summary, ensure_ascii=False), flush=True)


# --- mini ----------------------------------------------------------------------------------

def load_mini(state):
    """DropBox's mini.py with a private state folder (its own pi sessions), never DropBox's."""
    env = dict(DROPBOX_MINI_URL='http://127.0.0.1:9', DROPBOX_MINI_TOKEN='bench', DROPBOX_MINI_STATE=str(state),
               DROPBOX_MINI_WORKSPACE=str(state / 'root' / 'files'), DROPBOX_MINI_ROOT=str(state / 'root'),  # a stand-in DropBox folder: mini's sandbox shields ROOT, so it must not contain STATE
               DROPBOX_MINI_PROFILE=str(DROPBOX / 'mini' / 'mini-sandbox.sb'),
               DROPBOX_MINI_SKILLS=str(DROPBOX / 'mini' / 'skills'), DROPBOX_MINI_TIMEOUT='600')
    os.environ.update(env)
    import importlib.util
    spec = importlib.util.spec_from_file_location('dropbox_mini', DROPBOX / 'mini' / 'mini.py')
    mini = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mini)
    return mini


def grade(check, reply, folder, silent_token):
    silent = reply.strip().strip('`').strip() == silent_token or reply.strip().endswith(silent_token)
    why = []
    if check.get('silent') is True and not silent:
        why.append('answered but should stay silent')
    if check.get('silent') is False and silent:
        why.append('stayed silent but should answer')
    if not silent:
        for rx in check.get('has', []):
            if not re.search(rx, reply, re.I):
                why.append(f'missing /{rx}/')
        for rx in check.get('lacks', []):
            if re.search(rx, reply, re.I):
                why.append(f'should not contain /{rx}/')
    if check.get('file'):
        name, rx = check['file']
        f = folder / name
        if not f.exists():
            why.append(f'no file {name}')
        elif not re.search(rx, f.read_text(errors='replace'), re.I):
            why.append(f'{name} lacks /{rx}/')
    return not why, why


def run_mini(args):
    d = out_dir('mini', args.model)
    state = Path(tempfile.mkdtemp(prefix='mini-bench-')).resolve()  # the sandbox checks real paths (/var is /private/var)
    mini = load_mini(state)
    mini.prepare_home()
    rows = []
    for c in cases.MINI:
        folder = state / 'root' / 'files' / c['id'] if c.get('workspace') else state / 'scratch' / c['id']
        folder.mkdir(parents=True, exist_ok=True)
        job = dict(title='Launch team', room='bench' + slug(c['id']).replace('-', ''), author=c['author'], text=c['text'],
                   ambient=c.get('ambient', False), history=[dict(author=a, text=t) for a, t in c['history']],
                   _model=args.model)
        if c.get('workspace'):
            job['workspace'] = str(folder)
        t = time.time()
        try:
            reply, err = mini.run_pi(job), ''
        except Exception as e:
            reply, err = '', str(e)[:300]
        ok, why = grade(c['check'], reply, folder, mini.SILENT)
        if err:
            ok, why = False, ['error: ' + err]
        rows.append(dict(id=c['id'], ok=ok, why=why, reply=reply[:2000], seconds=round(time.time() - t, 1)))
        print(f'{c["id"]:18} {"PASS" if ok else "FAIL"} {rows[-1]["seconds"]:6.1f}s  {"; ".join(why)[:80]}  | {reply[:80]!r}', flush=True)
    summary = dict(job='mini', model=args.model, passed=sum(r['ok'] for r in rows), total=len(rows),
                   silence_right=sum(1 for r, c in zip(rows, cases.MINI) if c.get('ambient') and r['ok']),
                   ambient_total=sum(1 for c in cases.MINI if c.get('ambient')),
                   seconds_per_request=round(sum(r['seconds'] for r in rows) / len(rows), 1))
    (d / 'results.json').write_text(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False, indent=1))
    shutil.rmtree(state, ignore_errors=True)
    print(json.dumps(summary), flush=True)


def report(_args):
    for f in sorted((ROOT / 'results').glob('dropbox-*/results.json')):
        s = json.loads(f.read_text())['summary']
        if s['job'] == 'translate':
            print(f'translate {s["model"]:28} think {s["think"]:6} chrF {s["chrf"]:5.1f}  {s["seconds_per_message"]:5.2f}s/msg  {s["by_direction"]}')
        elif s['job'] == 'captions':
            print(f'captions  {s["model"]:40} error {s["error_rate_all"]:.3f}  lang ok {s["language_detected_right"]:.0%}  {s["compute_per_audio_second"]:.2f}s per audio s')
        else:
            print(f'mini      {s["model"]:28} {s["passed"]}/{s["total"]}  silence {s["silence_right"]}/{s["ambient_total"]}  {s["seconds_per_request"]}s/request')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('translate'); t.add_argument('--model', required=True); t.add_argument('--think', default='off')
    c = sub.add_parser('captions'); c.add_argument('--whisper', default='mlx-community/whisper-large-v3-turbo')
    m = sub.add_parser('mini'); m.add_argument('--model', default='ollama/qwen3.8:27b-mlx')
    sub.add_parser('report')
    for sp in (t, c, m):
        sp.add_argument('--no-lock', action='store_true')
    args = p.parse_args()
    if args.cmd == 'report':
        return report(args)
    lock = None if args.no_lock else take_lock()
    unload_all()
    try:
        {'translate': run_translate, 'captions': run_captions, 'mini': run_mini}[args.cmd](args)
    finally:
        unload_all()
        if lock:
            lock.close()


if __name__ == '__main__':
    main()
