"""Agentic coding bench: a model works on small repositories with its own tools (pi).

Each task (benches/agentic-v1/tasks.py, or another bench with --bench agentic-v2 or agentic-v3) is copied to a fresh workspace, pi runs once
with the task's instruction inside a macOS sandbox (writes only in the workspace,
no network except localhost), then hidden tests decide the grade.

    .venv/bin/python agent_bench.py run --name NAME --model ollama/qwen3.8:27b-mlx [--thinking off]
    .venv/bin/python agent_bench.py report --names A,B --out OUT

Kept per run, under results/BENCH-NAME/TASK/ (BENCH defaults to agentic-v1): pi.jsonl (every pi event),
pi.stderr, workspace/ (final files), changes.diff, visible.txt, hidden.txt and
result.json. Runs are resumable and take the lab's .bench.lock (one GPU job at a time).
"""
import argparse, urllib.request, concurrent.futures, difflib, re, fcntl, importlib.util, json, os, shutil, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BENCHES = Path(os.environ.get('BENCHES_DIR') or ROOT / 'benches')   # the bench folders (tasks, hidden tests, graders); BENCHES_DIR when they live in another checkout (2026-11-25)


def load_bench(name):
    """The task module of a bench folder (benches/<name>/tasks.py): agentic-v1 or the held-out agentic-v2 and agentic-v3."""
    spec = importlib.util.spec_from_file_location(f'{name.replace("-", "_")}_tasks', BENCHES / name / 'tasks.py')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


T = load_bench('agentic-v1') if (BENCHES / 'agentic-v1' / 'tasks.py').exists() else None  # replaced by --bench in main()

SANDBOX = BENCHES / 'agentic-v1' / 'sandbox.sb'
PI_HOME = ROOT / 'results' / 'agentic-pi-home'
TERSE = ("Be terse. Keep your reasoning short: decide and act with a tool call instead of deliberating. "
         "When you are done, reply in one or two lines.")
SUFFIX = ("\n\nWork only in the current folder; it is a fresh copy made for you. "
          "When you are finished, reply with a short summary of what you changed.")


def pi_home(ollama_url=''):
    """A private pi config for the bench, so the user's own pi settings and sessions are not used or changed.

    With ollama_url (an isolated Ollama, e.g. http://127.0.0.1:11500), a separate home whose ollama
    provider points there is used instead, so the default home keeps pointing at the system Ollama."""
    home = PI_HOME
    src = Path.home() / '.pi' / 'agent' / 'models.json'
    if ollama_url:
        home = PI_HOME.with_name(PI_HOME.name + '-' + ollama_url.rsplit(':', 1)[-1].strip('/'))
    home.mkdir(parents=True, exist_ok=True)
    if not (home / 'models.json').exists():
        shutil.copy(src, home / 'models.json')
    if ollama_url:
        cfg = json.loads((home / 'models.json').read_text())
        cfg['providers']['ollama']['baseUrl'] = ollama_url.rstrip('/') + '/v1'
        (home / 'models.json').write_text(json.dumps(cfg, indent=2))
    (home / 'settings.json').write_text(json.dumps({'quietStartup': 'header'}))
    sync_context_windows(home / 'models.json', ollama_url or 'http://127.0.0.1:11434')
    return home


GGUF_CTX = 32768   # the context Ollama gives GGUF (llama.cpp runner) models here; MLX/safetensors models get their full length


def sync_context_windows(models_json, ollama_url):
    """Give pi every installed Ollama model with the context Ollama really serves. Found 2026-11-24: GGUF models run
    at 32,768 tokens; past that Ollama silently drops the oldest messages, the task included (qwen3.8 27b mtp's
    template then failed with "no user query found"; huihui's listed 262,144, so pi never compacted). With the
    real window pi compacts the conversation itself before Ollama would cut it."""
    try:
        names = [l.split()[0] for l in subprocess.run(['ollama', 'list'], capture_output=True, text=True, timeout=20).stdout.splitlines()[1:] if l.strip()]
    except Exception:
        return
    cfg = json.loads(Path(models_json).read_text())
    models = cfg.setdefault('providers', {}).setdefault('ollama', {}).setdefault('models', [])
    by_id = {m['id']: m for m in models}
    changed = False
    for name in names:
        try:
            req = urllib.request.Request(ollama_url.rstrip('/') + '/api/show', data=json.dumps({'model': name}).encode())
            info = json.load(urllib.request.urlopen(req, timeout=20))
        except Exception:
            continue
        trained = next((v for k, v in info.get('model_info', {}).items() if k.endswith('context_length')), None)
        set_ctx = re.search(r'num_ctx\s+(\d+)', info.get('parameters') or '')   # the model's own setting wins (2026-11-24: 64k on the GGUF 27Bs)
        ctx = int(set_ctx.group(1)) if set_ctx else GGUF_CTX if info.get('details', {}).get('format') == 'gguf' else trained
        if not ctx:
            continue
        m = by_id.get(name)
        if m is None:
            m = dict(id=name)   # only the window: pi ran these as custom ids before, keep their other defaults
            models.append(m)
            changed = True
        if m.get('contextWindow') != ctx:
            m['contextWindow'] = ctx
            changed = True
    if changed:
        Path(models_json).write_text(json.dumps(cfg, indent=2))


def summarize(events_path):
    """Count steps, tool calls, tokens and model time from pi's JSON events."""
    s = dict(assistant_messages=0, tool_calls=0, output_tokens=0, input_tokens=0, cache_read_tokens=0,
             model_ms=0, tools=[], stop_reasons=[], final_text='', errors=[])
    for line in Path(events_path).read_text(errors='replace').splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        t = e.get('type')
        if t == 'tool_execution_start':
            s['tool_calls'] += 1
            s['tools'].append(e.get('toolName') or e.get('name') or (e.get('tool') or {}).get('name') or '?')
        elif t == 'message_end' and (e.get('message') or {}).get('role') == 'assistant':
            m = e['message']
            s['assistant_messages'] += 1
            u = m.get('usage') or {}
            s['output_tokens'] += u.get('output') or 0
            s['input_tokens'] += u.get('input') or 0
            s['cache_read_tokens'] += u.get('cacheRead') or 0
            s['model_ms'] += m.get('durationMs') or 0
            s['stop_reasons'].append(m.get('stopReason'))
            if m.get('errorMessage'):
                s['errors'].append(m['errorMessage'][:300])
            text = ''.join(c.get('text', '') for c in m.get('content') or [] if isinstance(c, dict) and c.get('type') == 'text')
            if text.strip():
                s['final_text'] = text.strip()[:2000]
    return s


def diff(task, workspace):
    out = []
    names = set(task['files']) | {str(p.relative_to(workspace)) for p in Path(workspace).rglob('*')
                                  if p.is_file() and '__pycache__' not in p.parts}
    for rel in sorted(names):
        before = task['files'].get(rel, '').splitlines(keepends=True)
        p = Path(workspace, rel)
        after = p.read_text(errors='replace').splitlines(keepends=True) if p.exists() else []
        out += difflib.unified_diff(before, after, 'a/' + rel, 'b/' + rel)
    return ''.join(out)


def run_sandbox(home, model, key):
    """The task's sandbox profile with network narrowed to the model's own server (2026-11-25: DeepSeek's agentic-v5 rerun,
    allowed every localhost port, asked the local Ollama to recall an exercise's tests; a local model could as well have
    called the paid DeepSeek proxy). The port comes from the provider's baseUrl in the pi home's models.json."""
    text = Path(getattr(T, 'SANDBOX', SANDBOX)).read_text()
    try:
        prov = json.loads((home / 'models.json').read_text())['providers'][model.split('/', 1)[0]]
        port = int(prov['baseUrl'].split('://', 1)[1].split('/', 1)[0].rsplit(':', 1)[1])
    except (OSError, KeyError, ValueError, IndexError) as e:
        sys.exit(f'cannot find the port of {model} in {home / "models.json"} ({e}): not running with every localhost port open')
    assert text.count('(remote ip "localhost:*")') == 1, 'sandbox has no single localhost rule to narrow'
    text = text.replace('(remote ip "localhost:*")', f'(remote ip "localhost:{port}")')
    # 2026-11-25: /tmp is shared with every other run and with this Mac's other tools (DeepSeek's react rerun found and read
    # an earlier run's /tmp/react.py; Claude sessions keep their scratch there). Each run gets its own temp folder (TMPDIR)
    # and the rest of /tmp and /var/folders is closed, for reading and writing.
    tmp = run_tmp(key)
    text += f'''
(deny file-read* file-write* (subpath "/private/tmp") (subpath "/private/var/folders"))
(allow file-read-metadata (literal "/private/tmp") (literal "/tmp") (literal "/private/var/folders"))
(allow file-read* file-write* (subpath "{tmp}"))
(allow file-read* file-write* (subpath "{os.path.realpath(subprocess.run(['getconf', 'DARWIN_USER_CACHE_DIR'], capture_output=True, text=True).stdout.strip()).rstrip('/')}"))
'''   # node will not start without the per-user cache folder
    out = home / f'sandbox-{key}.sb'
    out.write_text(text)
    return out


def run_tmp(key):
    d = Path('/private/tmp/agent-bench-tmp') / key
    d.mkdir(parents=True, exist_ok=True)
    return d


def attempt(args, task, name, tdir, home, exts, extra_env=None, model=None, start_from=None, instruction=None):
    """One pi run of one task in tdir/workspace; returns its result record."""
    key = f'{args.name}-{name}-{time.time_ns()}'  # log names stay apart when attempts run at once
    model = model or args.model
    ws = tdir / 'workspace'
    if start_from:  # carry on from an earlier run's files
        tdir.mkdir(parents=True)
        shutil.copytree(start_from, ws)
    else:
        ws.mkdir(parents=True)
        T.write_files(task, ws)
    cmd = ['/usr/bin/sandbox-exec', '-f', str(run_sandbox(home, model, key)), '-D', f'WORKSPACE={ws}', '-D', f'SESSIONS={home}',
           '-D', f'HOME={Path.home()}', '-D', f'REPO={ROOT}', 'pi', '-p', '--model', model, '--thinking', args.thinking, '--offline',
           '--no-session', '--no-approve', '--no-context-files', '--no-extensions', '--no-skills',
           '--no-prompt-templates', *[x for e in exts for x in ('-e', e)], *(['--append-system-prompt', '\n\n'.join(x for x in (TERSE if args.terse else None, getattr(args, 'harness_prompt', None)) if x)] if args.terse or getattr(args, 'harness_prompt', None) else []), '--mode', 'json', (instruction or task['instruction']) + SUFFIX]
    env = dict(os.environ, PI_OFFLINE='1', PI_CODING_AGENT_DIR=str(home), TMPDIR=str(run_tmp(key)) + '/', **(extra_env or {}))
    if exts:  # the extensions' own logs of what they did, kept with the run (written where the sandbox allows)
        env['PI_GUARD_LOG'] = str(home / f'guard-{key}.jsonl')
        env['PI_CODING_LOG'] = str(home / f'boost-{key}.jsonl')
    # 2026-11-23: under memory pressure (swap nearly full) pi's node sometimes died at start-up with only a native
    # stack trace, and the task scored as a 0-tool failure. Such a start-up crash is retried (up to 4 starts).
    for start in range(4):
        t0 = time.time()
        with open(tdir / 'pi.jsonl', 'w') as so, open(tdir / 'pi.stderr', 'w') as se:
            try:
                p = subprocess.run(cmd, cwd=ws, env=env, stdin=subprocess.DEVNULL, stdout=so, stderr=se, timeout=task.get('timeout') or args.timeout)   # a task may set its own (arena-v2's rounds)
                exit_code, timed_out = p.returncode, False
            except subprocess.TimeoutExpired:
                exit_code, timed_out = None, True
        crashed = (exit_code not in (0, None) and time.time() - t0 < 10 and (tdir / 'pi.jsonl').stat().st_size == 0
                   and 'Native stack trace' in (tdir / 'pi.stderr').read_text(errors='replace'))
        if not crashed:
            break
        print(f'  pi crashed at start-up (start {start + 1}); retrying in 30 s', flush=True)
        time.sleep(30)
    err = (tdir / 'pi.stderr').read_text(errors='replace')
    # 2026-11-24: DeepSeek's account ran out of credit mid-run (HTTP 402 at the first request) and 25 tasks scored 0 with no tool
    # call. A provider refusal on the first request (bad key, no credit, budget cap) is a harness error too.
    first_err = re.search(r'"errorMessage":"(40[12]|429)\b', (tdir / 'pi.jsonl').read_text(errors='replace')[:200000])
    if first_err and '"tool_execution_start"' not in (tdir / 'pi.jsonl').read_text(errors='replace'):
        sys.exit(f'the model provider refused the request (HTTP {first_err.group(1)}: no credit, bad key or budget cap); stopping the run')
    if exit_code not in (0, None) and (tdir / 'pi.jsonl').stat().st_size == 0 and re.search(r'Failed to load extension|Unknown model|No API key|sandbox-exec: ', err):
        # a harness error, not the model's: stop the run rather than score it (and leave no result.json, so a rerun redoes it)
        sys.exit(f'pi could not start ({err.strip().splitlines()[0][:300]}); stopping the run')
    wall = time.time() - t0
    vis_ok, vis_out = T.run_visible(ws)
    passed, total, hid_out = T.run_hidden(task, ws)
    (tdir / 'visible.txt').write_text(vis_out)
    (tdir / 'hidden.txt').write_text(hid_out)
    (tdir / 'changes.diff').write_text(diff(task, ws))
    stats = summarize(tdir / 'pi.jsonl')
    guard_log = home / f'guard-{key}.jsonl'
    if guard_log.exists():
        guard_log.replace(tdir / 'guard.jsonl')
        events = [json.loads(l) for l in (tdir / 'guard.jsonl').read_text().splitlines() if l.strip()]
        stats['guard'] = {k: sum(e['event'] == k for e in events) for k in ('adjusted', 'repaired', 'nudged', 'stall', 'out-of-nudges')}
    boost_log = home / f'boost-{key}.jsonl'
    if boost_log.exists():
        boost_log.replace(tdir / 'boost.jsonl')
        events = [json.loads(l) for l in (tdir / 'boost.jsonl').read_text().splitlines() if l.strip()]
        stats['boost'] = {k: sum(e['event'] == k for e in events) for k in sorted({e['event'] for e in events})}
    tests_changed = any(rel.startswith('test_') and Path(ws, rel).read_text() != text
                        for rel, text in task['files'].items() if Path(ws, rel).exists())
    rec = dict(task=name, area=task['area'], model=model, thinking=args.thinking, exit=exit_code,
               timed_out=timed_out, wall_s=round(wall, 1), visible_pass=vis_ok, hidden_passed=passed,
               hidden_total=total, solved=bool(total) and passed == total, original_tests_changed=tests_changed, **stats)
    return rec


def discussion(task, other, ok):
    """Round-2 request for --pair: the task again, plus the colleague's changes and visible test result."""
    d = (other / 'changes.diff').read_text(errors='replace')
    v = (other / 'visible.txt').read_text(errors='replace')
    return (task['instruction'] + "\n\nYou already worked on this task: the files are as you left them. A colleague worked on the same "
            "task at the same time, separately. Their changes:\n```diff\n" + (d[:6000] or '(no changes)') + "\n```\n"
            "Their run of the existing tests: " + ('passed' if ok else 'FAILED') + "\n```\n" + v[-1500:] + "\n```\n"
            "Compare the two solutions: where do they differ, and which handles the request and its edge cases better? "
            "Then improve your own files: keep what is right, take what is better from your colleague, and fix anything "
            "either of you missed. Run the tests before you finish.")


def by_generated_tests(args, task, tries, suites=2):
    """--pick tests: the model writes extra tests from the request and the starting code (no reference
    solution, no hidden tests), and the try passing the most of them is kept; ties keep the earlier try.
    Only tries that pass their visible tests compete. pick_bench.py measured this on saved tries."""
    import pick_bench as PB
    PB.OLLAMA = args.ollama_url or PB.OLLAMA  # the server already holding the model, never a second copy
    model = (args.model or '').removeprefix('ollama/')
    score = {id(r): 0 for r, _ in tries}
    for k in range(suites):
        fname = f'test_gen_extra{k}.py'
        try:
            text = PB.chat(model, PB.GEN_PROMPT.format(instruction=task['instruction'], files=PB.start_files(task), fname=fname),
                           PB.think_for(model, 'medium'), seed=k)
        except Exception as e:
            print(f'  pick: test writing failed: {e}', flush=True)
            continue
        m = re.findall(r'```(?:python)?\n(.*?)```', text, re.S)
        code = max(m, key=len) if m else text
        for r, ws in tries:
            if r['visible_pass']:
                score[id(r)] += len(PB.run_suite(ws, code, fname).get('passed', []))
    ok = [(r, ws) for r, ws in tries if r['visible_pass']]
    best = max(ok, key=lambda t: score[id(t[0])])[0]  # max keeps the first of equals
    print('  pick by generated tests: ' + ', '.join(f"try{i} {score[id(r)]}" for i, (r, _) in enumerate(tries, 1) if r['visible_pass']), flush=True)
    return best, 'generated tests ' + '/'.join(str(score[id(r)]) for r, _ in ok)


def several(args, task, name, tdir, home, exts):
    """--attempts N: N runs at once, keep the first whose visible tests pass (hidden tests are never used to pick).
    --escalate EFFORT: when no kept run passes its visible tests, run once more at that gpt-oss reasoning effort.
    --escalate-model MODEL: the same, handing that one more run to another model.
    --pair: after the 2 runs, each reads the other's changes and test result and revises its own, at once.
    Time is the wall time of the whole thing; tool calls and tokens are summed over every run."""
    tdir.mkdir(parents=True)
    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(args.attempts) as ex:
        recs = list(ex.map(lambda k: attempt(args, task, name, tdir / f'try{k}', home, exts), range(1, args.attempts + 1)))
    pick = next((r for r in recs if r['visible_pass']), recs[0])
    picked_by = 'first visible pass'
    if args.pick == 'tests' and sum(r['visible_pass'] for r in recs) > 1:
        pick, picked_by = by_generated_tests(args, task, [(r, tdir / f'try{k}' / 'workspace') for k, r in enumerate(recs, 1)])
    discussed = False
    if args.pair:
        discussed = True
        with concurrent.futures.ThreadPoolExecutor(2) as ex:
            second = list(ex.map(lambda k: attempt(args, task, name, tdir / f'discuss{k}', home, exts,
                                                   start_from=tdir / f'try{k}' / 'workspace',
                                                   instruction=discussion(task, tdir / f'try{3 - k}', recs[2 - k]['visible_pass'])), (1, 2)))
        recs += second
        pick = next((r for r in second if r['visible_pass']), pick if pick['visible_pass'] else second[0])
    escalated = False
    if (args.escalate or args.escalate_model) and not pick['visible_pass']:
        r = attempt(args, task, name, tdir / 'escalated', home, exts,
                    {'PI_GUARD_GPT_OSS_EFFORT': args.escalate} if args.escalate else None, args.escalate_model or None)
        recs.append(r)
        escalated = True
        if r['visible_pass']:
            pick = r
    rec = dict(pick, wall_s=round(time.time() - t0, 1), tool_calls=sum(r['tool_calls'] for r in recs),
               output_tokens=sum(r['output_tokens'] for r in recs), picked=next(i for i, r in enumerate(recs) if r is pick) + 1, picked_by=picked_by, escalated=escalated, discussed=discussed,
               runs=[dict(model=r['model'], solved=r['solved'], visible_pass=r['visible_pass'], wall_s=r['wall_s']) for r in recs])
    return rec


def run(args):
    out_dir = ROOT / 'results' / f'{args.bench}-{args.name}'
    out_dir.mkdir(parents=True, exist_ok=True)
    lock = (ROOT / '.bench.lock').open('w')
    if not args.no_lock and not args.model.startswith('deepseek/'):  # --no-lock: plumbing tests; deepseek/ is hosted, no GPU
        print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
        fcntl.flock(lock, fcntl.LOCK_EX)
    home = pi_home(args.ollama_url)
    sys.path.insert(0, str(ROOT / 'benches'))
    import local_engines
    local_engines.add_providers(home / 'models.json')  # mlxlm/, mtplx/, ds4/... next to ollama/
    # 2026-11-25: pi matches an unknown model id to a similar one without a word (deepseek/flash ran as flash-off when the
    # config lacked it), so check the id is in the bench's pi config exactly
    prov, _, mid = args.model.partition('/')
    known = [m['id'] for m in json.loads((home / 'models.json').read_text()).get('providers', {}).get(prov, {}).get('models', [])]
    if known and mid not in known:
        msg = f'{args.model} is not in {home}/models.json ({prov}: {", ".join(known[:12])}): pi would run a similar model instead'
        if prov == 'deepseek':
            sys.exit(msg)
        print('WARNING: ' + msg, flush=True)
    harness = None
    if args.harness == 'agentbox':   # 2026-11-25: run the model exactly as agentbox would (its extensions and prompt lines)
        cmd = ['agentbox', 'harness', '--model', args.model, '--json'] + (['--thinking', args.thinking] if args.thinking != 'off' else [])
        harness = json.loads(subprocess.run(cmd, capture_output=True, text=True, check=True).stdout)
        args.extension = ','.join([e['path'] for e in harness['extensions']] + [e for e in args.extension.split(',') if e])
        args.harness_prompt = '\n\n'.join(pr['text'] for pr in harness['prompts']) or None
    meta = out_dir / 'meta.json'
    if not meta.exists():
        meta.write_text(json.dumps({
            'started': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'bench': args.bench, 'model': args.model, 'escalate_model': args.escalate_model or None, 'thinking': args.thinking,
            'pi_version': subprocess.run(['pi', '--version'], capture_output=True, text=True).stdout.strip(),
            'ollama_version': subprocess.run(['ollama', '--version'], capture_output=True, text=True).stdout.strip(),
            'ollama_url': args.ollama_url or 'http://127.0.0.1:11434',
            'terse': TERSE if args.terse else None, 'harness': harness,
            'timeout_s': args.timeout, 'extension': args.extension or None, 'sandbox': Path(getattr(T, 'SANDBOX', SANDBOX)).read_text(), 'suffix': SUFFIX}, indent=1))
    exts = []
    copied = set()
    for e in [e for e in args.extension.split(',') if e]:
        # copied into the run's pi home: the sandboxes deny reads in most of the repo (2026-11-24: arena-v3's pi could not load
        # benches/pi-ext/command-timeout and every round ran with 0 tools)
        src = Path(e).expanduser().resolve()
        # 2026-11-25: an extension inside an npm package (agentbox's pi-core parts) imports its dependencies from the
        # package's node_modules, so copy the whole package and keep the extension's place in it
        pkg = next((p for p in src.parents if (p / 'package.json').exists() and (p / 'node_modules').is_dir()), None)
        if pkg:
            dst = home / 'extensions-copy' / pkg.name
            if pkg not in copied:   # once per run, fresh: several extensions can come from the same package
                shutil.rmtree(dst, ignore_errors=True)
                shutil.copytree(pkg, dst, symlinks=True, ignore=shutil.ignore_patterns('.git', 'test', '__pycache__'))
                copied.add(pkg)
            exts.append(str(dst / src.relative_to(pkg)))
        else:
            dst = home / 'extensions-copy' / src.parent.name
            shutil.copytree(src.parent, dst, dirs_exist_ok=True)
            exts.append(str(dst / src.name))
    names = [n for n in T.TASKS if not args.only or n in args.only.split(',')]
    if args.rerun:  # measure again: earlier task runs move to attempts/rerun-<time>/ (kept, never deleted)
        dest = out_dir / 'attempts' / f'rerun-{time.strftime("%Y%m%dT%H%M%S")}'
        for name in names:
            if (out_dir / name).exists():
                dest.mkdir(parents=True, exist_ok=True)
                (out_dir / name).replace(dest / name)
    for i, name in enumerate(names, 1):
        task = T.TASKS[name]
        tdir = out_dir / name
        if (tdir / 'result.json').exists():
            print(f'[{i}/{len(names)}] {name}: done before, skipped', flush=True)
            continue
        if args.model.startswith('deepseek/'):   # hosted DeepSeek: only off-peak (the task's limit + 10 min for fights and grading)
            import deepseek_proxy
            deepseek_proxy.wait_offpeak((task.get('timeout') or args.timeout) + 600, log=lambda m: print(m, flush=True))
        if tdir.exists():  # an interrupted attempt: keep it under attempts/
            (out_dir / 'attempts').mkdir(exist_ok=True)
            tdir.replace(out_dir / 'attempts' / f'{name}-{time.time_ns()}')
        if args.pair:
            args.attempts = 2
        if args.attempts > 1 or args.escalate or args.escalate_model or args.pair:
            rec = several(args, task, name, tdir, home, exts)
        else:
            rec = attempt(args, task, name, tdir, home, exts)
        passed, total, stats, timed_out = rec['hidden_passed'], rec['hidden_total'], rec, rec['timed_out']
        vis_ok = rec['visible_pass']
        (tdir / 'result.json').write_text(json.dumps(rec, indent=1))
        print(f"[{i}/{len(names)}] {name}: solved={rec['solved']} hidden={passed}/{total} visible={vis_ok} "
              f"wall={rec['wall_s']}s tools={stats['tool_calls']} out_tokens={stats['output_tokens']} "
              f"timeout={timed_out}", flush=True)


def report(args):
    rows, lines = [], []
    names = args.names.split(',')
    per = {}
    for n in names:
        d = ROOT / 'results' / f'{args.bench}-{n}'
        meta = json.loads((d / 'meta.json').read_text())
        rs = [json.loads(p.read_text()) for p in sorted(d.glob('*/result.json'))]
        per[n] = {r['task']: r for r in rs}
        solved = sum(r['solved'] for r in rs)
        tests = sum(r['hidden_passed'] for r in rs), sum(r['hidden_total'] or 0 for r in rs)
        wall = sum(r['wall_s'] for r in rs)
        rows.append(dict(name=n, model=meta['model'], thinking=meta['thinking'], tasks=len(rs), solved=solved,
                         hidden_tests=tests, total_wall_s=wall, tool_calls=sum(r['tool_calls'] for r in rs),
                         output_tokens=sum(r['output_tokens'] for r in rs), timeouts=sum(r['timed_out'] for r in rs),
                         tests_changed=sum(r['original_tests_changed'] for r in rs)))
    lines.append('| Run | Model | Thinking | Solved | Hidden tests | Total time | Tool calls | Output tokens | Timeouts | Changed given tests |')
    lines.append('|---|---|---|---:|---:|---:|---:|---:|---:|---:|')
    for r in rows:
        lines.append(f"| {r['name']} | {r['model']} | {r['thinking']} | {r['solved']}/{r['tasks']} | {r['hidden_tests'][0]}/{r['hidden_tests'][1]} | "
                     f"{r['total_wall_s'] / 60:.1f} min | {r['tool_calls']} | {r['output_tokens']} | {r['timeouts']} | {r['tests_changed']} |")
    lines += ['', '| Task | ' + ' | '.join(names) + ' |', '|---|' + '---|' * len(names)]
    for task in T.TASKS:
        cells = []
        for n in names:
            r = per[n].get(task)
            cells.append('—' if not r else f"{'✔' if r['solved'] else '✘'} {r['hidden_passed']}/{r['hidden_total']} · {r['wall_s']:.0f}s · {r['tool_calls']} tools")
        lines.append(f'| {task} | ' + ' | '.join(cells) + ' |')
    text = '\n'.join(lines) + '\n'
    out = ROOT / 'results' / f'compare-{args.out}'
    out.mkdir(parents=True, exist_ok=True)
    stem = 'agentic' if args.bench == 'agentic-v1' else args.bench
    (out / f'{stem}.md').write_text(text)
    (out / f'{stem}.json').write_text(json.dumps(rows, indent=1))
    print(text)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest='cmd', required=True)
    r = sub.add_parser('run')
    r.add_argument('--name', required=True)
    r.add_argument('--model', required=True, help='pi model, e.g. ollama/qwen3.8:27b-mlx')
    r.add_argument('--thinking', default='off')
    r.add_argument('--only', default='')
    r.add_argument('--timeout', type=int, default=1200)
    r.add_argument('--no-lock', action='store_true', help='do not take .bench.lock: only when the caller already holds it '
                   '(parallel_requests_bench.py agents) or for plumbing tests with a stand-in pi')
    r.add_argument('--terse', action='store_true', help="append a terse instruction to pi's system prompt (short reasoning, act, one- or two-line reply)")
    r.add_argument('--ollama-url', default='', help='point pi at another Ollama, e.g. an isolated one on http://127.0.0.1:11500')
    r.add_argument('--rerun', action='store_true', help='measure again: earlier task runs move to attempts/rerun-<time>/')
    r.add_argument('--attempts', type=int, default=1, help='runs per task at once; keep the first that passes its visible tests')
    r.add_argument('--pick', choices=['first', 'tests'], default='first', help='with --attempts: tests = keep the try passing the most model-written tests')
    r.add_argument('--escalate', default='', help='gpt-oss effort (e.g. high) for one more run when visible tests still fail')
    r.add_argument('--escalate-model', default='', help='another pi model (e.g. ollama/qwen3.8:27b-mlx) for one more run when visible tests still fail')
    r.add_argument('--pair', action='store_true', help='two runs at once, then each sees the other\'s changes and test result and revises its own (2 rounds)')
    r.add_argument('--harness', choices=['bare', 'agentbox'], default='bare', help='bare: pi as it comes; agentbox: with agentbox\'s extensions and prompt lines for the model (`agentbox harness`)')
    r.add_argument('--extension', default='', help='pi extensions to load for the run, comma-separated, e.g. ~/Code/pi-core/local-model-guard/index.ts')
    p = sub.add_parser('report')
    p.add_argument('--names', required=True)
    p.add_argument('--out', required=True)
    for sp in (r, p):
        sp.add_argument('--bench', default='agentic-v1', help='task folder under benches/: agentic-v1, or agentic-v2 or agentic-v3 (held out)')
    a = ap.parse_args()
    global T
    T = load_bench(a.bench)
    {'run': run, 'report': report}[a.cmd](a)


if __name__ == '__main__':
    main()
