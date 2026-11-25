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
from concurrent.futures import ThreadPoolExecutor
import argparse, fcntl, json, os, re, shutil, subprocess, sys, tempfile, time, urllib.request, wave
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BENCHES = Path(os.environ.get('BENCHES_DIR') or ROOT / 'benches')   # the bench folders (tasks, hidden tests, graders); BENCHES_DIR when they live in another checkout (2026-11-25)
sys.path.insert(0, str(BENCHES / 'dropbox'))
sys.path.insert(0, str(ROOT / 'benches'))
import cases  # noqa: E402
import local_engines  # noqa: E402

OLLAMA = 'http://127.0.0.1:11434'
DROPBOX = Path.home() / '.local' / 'share' / 'dropbox'
VOICE_PY = DROPBOX / 'voice-venv' / 'bin' / 'python'


def slug(s):
    return re.sub(r'[^a-z0-9]+', '-', s.lower()).strip('-')


def out_dir(job, model):
    # ollama/<tag> -> <tag>; a model on another engine keeps its engine (mtplx/bonsai-2-27b -> mtplx-bonsai-2-27b), so the same
    # model on two engines never shares a folder (2026-11-22: MTPLX's Bonsai runs had overwritten Ollama's)
    name = model.rsplit("/", 1)[-1] if job == "captions" else model.split("/", 1)[-1] if model.startswith("ollama/") or "/" not in model else model
    d = ROOT / 'results' / f'dropbox-{job}-{slug(name)}'
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


LANG_NAMES = {'en': 'English', 'ja': 'Japanese', 'fr': 'French'}


def ollama_hear(model, path, lang='', think=False):
    """A model that hears (gemma4 e4b) transcribes a WAV through Ollama: {text, language} like voice.py."""
    import base64
    hint = f' The speaker speaks {LANG_NAMES[lang]}.' if lang else ''
    body = dict(model=model.removeprefix('ollama/'), stream=False, think=think, options=dict(temperature=0),
                messages=[dict(role='user', images=[base64.b64encode(Path(path).read_bytes()).decode()],
                               content='Transcribe this audio exactly, in the language spoken.' + hint +
                               ' Answer only with JSON: {"language": "<ISO 639-1 code>", "text": "<the words>"}')])
    try:
        req = urllib.request.Request('http://127.0.0.1:11434/api/chat', json.dumps(body).encode(), {'Content-Type': 'application/json'})
        content = json.load(urllib.request.urlopen(req, timeout=300))['message']['content']
        m = re.search(r'\{.*\}', content, re.S)
        out = json.loads(m.group(0)) if m else {'text': content.strip()}
        return {'text': str(out.get('text', '')).strip(), 'language': str(out.get('language', '') or '').lower()[:2] or None}
    except Exception as e:
        return {'error': str(e)[:300]}


def run_captions(args):
    think = {'off': False, 'on': True}.get(args.think, args.think)
    d = out_dir('captions', (args.ollama + ('' if args.think == 'off' else '-think-' + args.think)) if args.ollama else args.whisper)
    items = make_audio(ROOT / 'results' / 'dropbox-captions-audio')
    proc = None
    if args.ollama:
        ollama_hear(args.ollama, items[0]['path'], think=think)  # warm-up: loads the model; not timed
    else:
        env = dict(os.environ, DROPBOX_VOICE_MODEL=args.whisper)
        proc = subprocess.Popen([str(VOICE_PY), '-I', str(DROPBOX / 'voice.py')], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                text=True, env=env, bufsize=1)
        # Warm-up: the first request loads the model; not timed.
        proc.stdin.write(items[0]['path'] + '\t\t\n'); proc.stdout.readline()
    rows = []
    for it in items:
        for mode in ('detect', 'pinned'):
            prior, pin = (it['lang'], 'pin') if mode == 'pinned' else ('', '')
            t = time.time()
            if proc:
                proc.stdin.write(f'{it["path"]}\t{prior}\t{pin}\n')
                out = json.loads(proc.stdout.readline() or '{}')
            else:
                out = ollama_hear(args.ollama, it['path'], prior, think=think)
            sec = time.time() - t
            with wave.open(it['path']) as w:
                dur = w.getnframes() / w.getframerate()
            text = out.get('text', '')
            rows.append(dict(**{k: it[k] for k in ('i', 'lang', 'voice', 'cond', 'ref')}, mode=mode, text=text,
                             detected=out.get('language'), error=out.get('error', ''),
                             er=round(error_rate(text, it['ref'], it['lang']), 3), rtf=round(sec / dur, 3)))
            print(f'{it["lang"]} {it["cond"]:5} {mode:6} err {rows[-1]["er"]:.2f} lang {out.get("language")}  {text[:60]}', flush=True)
    if proc:
        proc.stdin.close(); proc.wait(timeout=60)

    def avg(sel, key):
        v = [r[key] for r in rows if sel(r)]
        return round(sum(v) / len(v), 3) if v else None
    summary = dict(job='captions', model=args.ollama or args.whisper, think=args.think if args.ollama else None,
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

def load_mini(state, mini_py=None):
    """DropBox's mini.py with a private state folder (its own pi sessions), never DropBox's."""
    env = dict(DROPBOX_MINI_URL='http://127.0.0.1:9', DROPBOX_MINI_TOKEN='bench', DROPBOX_MINI_STATE=str(state),
               DROPBOX_MINI_WORKSPACE=str(state / 'root' / 'files'), DROPBOX_MINI_ROOT=str(state / 'root'),  # a stand-in DropBox folder: mini's sandbox shields ROOT, so it must not contain STATE
               DROPBOX_MINI_PROFILE=str(Path(mini_py).parent / 'mini-sandbox.sb' if mini_py else DROPBOX / 'mini' / 'mini-sandbox.sb'),
               DROPBOX_MINI_SKILLS=str(DROPBOX / 'mini' / 'skills'), DROPBOX_MINI_TIMEOUT='600')
    os.environ.update(env)
    import importlib.util
    spec = importlib.util.spec_from_file_location('dropbox_mini', mini_py or DROPBOX / 'mini' / 'mini.py')
    mini = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mini)
    return mini


def plain(text):
    """Reply text without typographic look-alikes, so a check never fails on how a model types a hyphen or quote."""
    for a, b in (('\u2010', '-'), ('\u2011', '-'), ('\u2012', '-'), ('\u2013', '-'), ('\u2014', '-'), ('\u2212', '-'),
                 ('\u2018', "'"), ('\u2019', "'"), ('\u00a0', ' '), ('\u202f', ' '), ('\u2009', ' ')):
        text = text.replace(a, b)
    return text


def ask(mini, job):
    """mini's answer as DropBox would deliver it: mini.answer (pi turn, checks, and the fallback answer when pi
    fails) where the harness has it, else run_pi alone (harness v3.1 and older)."""
    return (mini.answer if hasattr(mini, 'answer') else mini.run_pi)(job)


def keep_case(d, case_id, mini, job, folder):
    """Save what a case left behind, so it can be graded again and its failures read: the files and pi's transcript."""
    out = d / 'cases' / case_id
    shutil.rmtree(out, ignore_errors=True)
    out.mkdir(parents=True)
    if folder.is_dir():
        shutil.copytree(folder, out / 'files', symlinks=True)
    for f in sorted((mini.PI_HOME / 'sessions').glob(f'*_{mini.session_id(job)}.jsonl')):
        shutil.copy(f, out / 'session.jsonl')


def grade(check, reply, folder, silent_token):
    reply = plain(reply)
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
    d = out_dir('mini' + (f'-{args.tag}' if args.tag else ''), args.model)
    state = Path(tempfile.mkdtemp(prefix='mini-bench-')).resolve()  # the sandbox checks real paths (/var is /private/var)
    mini = load_mini(state, args.mini_py)
    mini.CURRENT['tools'] = args.tools_model or ''  # mini's own router picks the model per request
    mini.prepare_home()
    local_engines.add_providers(mini.PI_HOME / 'models.json')  # mlxlm/, mtplx/, ds4/... next to ollama/
    CASES = cases.MINI_LANG if args.cases == 'lang' else cases.MINI

    def one(c):
        folder = state / 'root' / 'files' / c['id'] if c.get('workspace') else state / 'scratch' / c['id']
        folder.mkdir(parents=True, exist_ok=True)
        job = dict(title='Launch team', room='bench' + slug(c['id']).replace('-', ''), author=c['author'], text=c['text'],
                   ambient=c.get('ambient', False), history=[dict(author=a, text=t) for a, t in c['history']],
                   _model=args.model)
        if c.get('workspace'):
            job['workspace'] = str(folder)
        t = time.time()
        try:
            reply, err = ask(mini, job), ''
        except Exception as e:
            reply, err = '', str(e)[:300]
        keep_case(d, c['id'], mini, job, folder)
        ok, why = grade(c['check'], reply, folder, mini.SILENT)
        if err:
            ok, why = False, ['error: ' + err]
        row = dict(id=c['id'], model=job['_model'], ok=ok, why=why, reply=reply[:2000], seconds=round(time.time() - t, 1))
        print(f'{c["id"]:18} {"PASS" if ok else "FAIL"} {row["seconds"]:6.1f}s  {"; ".join(why)[:80]}  | {reply[:80]!r}', flush=True)
        return row

    t0 = time.time()
    with ThreadPoolExecutor(args.concurrency) as pool:  # N people asking at once, as in a family chat
        rows = list(pool.map(one, CASES))
    summary = dict(job='mini', model=args.model, passed=sum(r['ok'] for r in rows), total=len(rows),
                   silence_right=sum(1 for r, c in zip(rows, CASES) if c.get('ambient') and r['ok']),
                   ambient_total=sum(1 for c in CASES if c.get('ambient')),
                   seconds_per_request=round(sum(r['seconds'] for r in rows) / len(rows), 1),
                   slowest_request=max(r['seconds'] for r in rows), concurrency=args.concurrency, tag=args.tag, tools_model=args.tools_model,
                   wall_seconds=round(time.time() - t0, 1))
    (d / 'results.json').write_text(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False, indent=1))
    shutil.rmtree(state, ignore_errors=True)
    print(json.dumps(summary), flush=True)


def run_family(args):
    """Multi-step jobs in a made-up family folder (benches/dropbox/family.py), each from a fresh copy."""
    import family
    hard = args.suite in ('hard', 'expert', 'master', 'grandmaster')
    if args.suite == 'hard':
        import family_hard as family  # graded in steps (points), see benches/dropbox/family_hard.py
    elif args.suite == 'expert':
        import family_expert as family  # harder still, graded in steps, see benches/dropbox/family_expert.py
    elif args.suite == 'master':
        import family_master as family  # harder than expert (2026-11-24, DeepSeek's level), see benches/dropbox/family_master.py
    elif args.suite == 'grandmaster':
        import family_grandmaster as family  # harder than master (2026-11-25), see benches/dropbox/family_grandmaster.py
    job_name = {'hard': 'family-hard', 'expert': 'family-expert', 'master': 'family-master', 'grandmaster': 'family-grandmaster'}.get(args.suite, 'family')
    d = out_dir(job_name + (f'-{args.tag}' if args.tag else ''), args.model)
    state = Path(tempfile.mkdtemp(prefix='family-bench-')).resolve()
    mini = load_mini(state, args.mini_py)
    mini.prepare_home()
    local_engines.add_providers(mini.PI_HOME / 'models.json')  # mlxlm/, mtplx/, ds4/... next to ollama/
    mini.CURRENT['tools'] = args.tools_model or ''
    tasks = [t for t in family.TASKS if not args.only or t['id'] in args.only.split(',')]

    def one(task):
        folder = state / 'root' / 'files' / task['id']
        family.write_seed(folder)
        job = dict(title='Family', room='family' + slug(task['id']).replace('-', ''), author='Aiko', text=task['text'],
                   ambient=False, history=[], _model=args.model, workspace=str(folder))
        t = time.time()
        try:
            reply, err = ask(mini, job), ''
        except Exception as e:
            reply, err = '', str(e)[:300]
        keep_case(d, task['id'], mini, job, folder)
        reply = plain(reply)
        steps = None
        if hard:
            steps = family.score(task, folder, reply)
            if err:  # what it did before the error still counts, so a timeout keeps its finished steps
                steps = steps if any(ok for _, ok in steps) else [(st, False) for st, _ in steps]
            why = (['error: ' + err] if err else []) + [st for st, ok in steps if not ok]
        else:
            why = ['error: ' + err] if err else task['check'](folder, reply)
            changed = family.untouched(folder, task)
            if changed:
                why.append('changed files it should not: ' + ', '.join(changed[:5]))
        row = dict(id=task['id'], lang=task['lang'], model=job['_model'], ok=not why, why=why, reply=reply[:2000],
                   seconds=round(time.time() - t, 1), files=sorted(str(p.relative_to(folder)) for p in folder.rglob('*') if p.is_file()))
        if steps is not None:
            row |= dict(steps=[[st, ok] for st, ok in steps], points=sum(ok for _, ok in steps), out_of=len(steps))
        print(f"{task['id']:18} {'PASS' if row['ok'] else 'FAIL'} {row['seconds']:6.1f}s {row['model'].split('/')[-1]:16} "
              f"{'; '.join(why)[:90]}  | {reply[:70]!r}", flush=True)
        return row

    t0 = time.time()
    with ThreadPoolExecutor(args.concurrency) as pool:
        rows = list(pool.map(one, tasks))
    summary = dict(job=job_name, model=args.model, tools_model=args.tools_model, tag=args.tag,
                   passed=sum(r['points'] for r in rows) if hard else sum(r['ok'] for r in rows),
                   total=sum(r['out_of'] for r in rows) if hard else len(rows), jobs_complete=sum(r['ok'] for r in rows), seconds_per_job=round(sum(r['seconds'] for r in rows) / len(rows), 1),
                   slowest_job=max(r['seconds'] for r in rows), concurrency=args.concurrency, wall_seconds=round(time.time() - t0, 1))
    (d / 'results.json').write_text(json.dumps(dict(summary=summary, rows=rows), ensure_ascii=False, indent=1))
    shutil.rmtree(state, ignore_errors=True)
    print(json.dumps(summary), flush=True)


def regrade(args):
    """Grade saved runs again with the current checks. The first time, the file as run is kept as
    results-as-run.json. A case is graded again from its saved files when the run kept them
    (cases/ID/files), else from its saved reply when the check reads only the reply; otherwise its
    recorded result stands. Errors (timeouts, crashes) always stand."""
    import family
    mini_by = {c['id']: c for c in cases.MINI + cases.MINI_LANG}
    fam_by = {t['id']: t for t in family.TASKS}
    for f in sorted((ROOT / 'results').glob('dropbox-*/results.json')):
        data = json.loads(f.read_text())
        s = data.get('summary') or {}
        if s.get('job') in ('family-hard', 'family-expert', 'family-master', 'family-grandmaster'):
            import family_expert, family_grandmaster, family_hard, family_master
            suite = {'family-hard': family_hard, 'family-expert': family_expert, 'family-master': family_master,
                     'family-grandmaster': family_grandmaster}[s['job']]
            hb = {t['id']: t for t in suite.TASKS}
            for r in data['rows']:
                saved = f.parent / 'cases' / r['id'] / 'files'
                if saved.is_dir() and r['id'] in hb and not any(str(w).startswith('error') for w in r.get('why') or []):
                    steps = suite.score(hb[r['id']], saved, plain(r.get('reply') or ''))
                    r |= dict(steps=[[a, b] for a, b in steps], points=sum(b for _, b in steps), out_of=len(steps),
                              ok=all(b for _, b in steps), why=[a for a, b in steps if not b])
            data['summary'] |= dict(passed=sum(r['points'] for r in data['rows']), total=sum(r['out_of'] for r in data['rows']),
                                    jobs_complete=sum(r['ok'] for r in data['rows']), regraded=time.strftime('%Y-%m-%d'))
            f.write_text(json.dumps(data, ensure_ascii=False, indent=1))
            continue
        if s.get('job') not in ('mini', 'family'):
            continue
        kept = f.parent / 'results-as-run.json'
        if not kept.exists():
            kept.write_text(f.read_text())
        orig = json.loads(kept.read_text())
        changed = []
        for r, o in zip(data['rows'], orig['rows']):
            why0 = o.get('why') or []
            if any(str(w).startswith('error') for w in why0):
                continue
            saved = f.parent / 'cases' / r['id'] / 'files'
            if s['job'] == 'mini':
                c = mini_by.get(r['id'])
                if not c:
                    continue
                check = c['check'] if saved.is_dir() or not c['check'].get('file') else dict(c['check'], file=None)
                ok, why = grade(check, o.get('reply') or '', saved, '[silent]')
                if not saved.is_dir() and c['check'].get('file'):
                    file_why = [w for w in why0 if w.startswith('no file') or ' lacks /' in w]
                    why, ok = why + file_why, ok and not file_why
            else:
                t = fam_by.get(r['id'])
                if not t:
                    continue
                if saved.is_dir():
                    why = t['check'](saved, plain(o.get('reply') or ''))
                    why += [f'changed files it should not: {", ".join(x[:5])}' for x in [family.untouched(saved, t)] if x]
                elif t.get('reply_only'):
                    why = t['check'](None, plain(o.get('reply') or '')) + [w for w in why0 if w.startswith('changed files')]
                else:
                    continue
                ok = not why
            if ok != (o.get('ok') in (True, 'True')):
                changed.append(f"{r['id']}: {'FAIL->PASS' if ok else 'PASS->FAIL'}")
            r['ok'], r['why'] = ok, why
            r['regraded'] = True
        data['summary']['passed'] = sum(1 for r in data['rows'] if r['ok'] in (True, 'True'))
        if s['job'] == 'mini':
            data['summary']['silence_right'] = sum(1 for r in data['rows'] if mini_by.get(r['id'], {}).get('ambient') and r['ok'] in (True, 'True'))
        data['summary']['regraded'] = time.strftime('%Y-%m-%d')
        f.write_text(json.dumps(data, ensure_ascii=False, indent=1))
        if changed:
            print(f'{f.parent.name}: {orig["summary"]["passed"]} -> {data["summary"]["passed"]}  ({"; ".join(changed)})')


def report(_args):
    for f in sorted((ROOT / 'results').glob('dropbox-*/results.json')):
        s = json.loads(f.read_text())['summary']
        if s['job'] == 'translate':
            print(f'translate {s["model"]:28} think {s["think"]:6} chrF {s["chrf"]:5.1f}  {s["seconds_per_message"]:5.2f}s/msg  {s["by_direction"]}')
        elif s['job'] == 'captions':
            print(f'captions  {s["model"]:40} error {s["error_rate_all"]:.3f}  lang ok {s["language_detected_right"]:.0%}  {s["compute_per_audio_second"]:.2f}s per audio s')
        else:  # graded again from the saved replies (checks improve); a missing or wrong file stays as recorded
            rows = json.loads(f.read_text())['rows']
            by = {c['id']: c for c in cases.MINI + cases.MINI_LANG}
            ok = []
            for r in rows:
                file_why = [w for w in r['why'] if w.startswith('no file') or ' lacks /' in w or w.startswith('error')]
                good, _ = grade(dict(by[r['id']]['check'], file=None), r['reply'], Path('/nonexistent'), '[silent]')
                ok.append(good and not file_why)
            silence = sum(1 for o, r in zip(ok, rows) if by[r['id']].get('ambient') and o)
            print(f'{"mini " + s.get("tag", ""):9} {s["model"]:28} {sum(ok)}/{len(rows)}  silence {silence}/{s["ambient_total"]}  {s["seconds_per_request"]}s/request'
                  + (f'  ×{s["concurrency"]} at once: slowest {s["slowest_request"]}s, all {s["wall_seconds"]}s' if s.get('concurrency', 1) > 1 else '')
                  + f'  (first grading {s["passed"]}/{s["total"]})')


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest='cmd', required=True)
    t = sub.add_parser('translate'); t.add_argument('--model', required=True); t.add_argument('--think', default='off')
    c = sub.add_parser('captions'); c.add_argument('--whisper', default='mlx-community/whisper-large-v3-turbo')
    c.add_argument('--think', default='off', help='with --ollama: off, on, or low/medium/high'); c.add_argument('--ollama', help='instead of Whisper, a model that hears, through Ollama (e.g. ollama/gemma4:e4b-mlx)')
    m = sub.add_parser('mini'); m.add_argument('--model', default='ollama/qwen3.8:27b-mlx')
    m.add_argument('--mini-py', help="a mini.py to test instead of DropBox's installed one")
    m.add_argument('--tools-model', help="mini's router sends requests that need tools (files, web) to this model; the rest use --model")
    m.add_argument('--cases', choices=['en', 'lang'], default='en', help='lang: the Japanese and French requests')
    m.add_argument('--concurrency', type=int, default=1, help='requests in flight at once')
    m.add_argument('--tag', default='', help='results go to mini-TAG instead of mini')
    f = sub.add_parser('family', help='multi-step jobs in a made-up family folder')
    f.add_argument('--model', default='ollama/gemma4:e4b-mlx')
    f.add_argument('--tools-model')
    f.add_argument('--mini-py')
    f.add_argument('--only', help='comma-separated job ids')
    f.add_argument('--concurrency', type=int, default=1)
    f.add_argument('--tag', default='')
    f.add_argument('--suite', choices=['standard', 'hard', 'expert', 'master', 'grandmaster'], default='standard',
                   help='hard: benches/dropbox/family_hard.py, expert: benches/dropbox/family_expert.py, both graded in points')
    sub.add_parser('report')
    sub.add_parser('regrade', help='grade the saved runs again with the current checks')
    for sp in (t, c, m, f):
        sp.add_argument('--no-lock', action='store_true')
    args = p.parse_args()
    if args.cmd == 'report':
        return report(args)
    if args.cmd == 'regrade':
        return regrade(args)
    # a hosted model (deepseek/, benches/deepseek_proxy.py) uses no GPU: it runs beside the local queue, so no lock and no unloading
    remote = (getattr(args, 'model', '') or '').startswith('deepseek/')
    lock = None if args.no_lock or remote else take_lock()
    if not remote:
        unload_all()
    try:
        {'translate': run_translate, 'captions': run_captions, 'mini': run_mini, 'family': run_family}[args.cmd](args)
    finally:
        if not remote:
            unload_all()
        if lock:
            lock.close()


if __name__ == '__main__':
    main()
