"""Model comparison, coding-task and agentic-task data for the lab app (read-only).

Everything comes from the files the bench tools write:
- results/glm-bench-NAME/ (glm_bench.py): meta.json names the Ollama model and the prompt
  file, one ID__STYLE__CONFIG.json per answer.
- results/agentic-v1-NAME/ (agent_bench.py): meta.json, and per task result.json, pi.jsonl,
  changes.diff, hidden.txt, visible.txt and guard.jsonl.
Folders are matched to models through meta.json, never through their names.
"""
import json, re, statistics, subprocess, sys, tempfile, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RESULTS = ROOT / 'results'
TASKS_DIR = ROOT / 'benches' / 'agentic-v1'   # the task definitions ship with the lab code
QUALITY_PROMPTS = 'glm-prompts.json'
CODING_PROMPTS = 'benches/coding-v1.json'
FAMILIES = [  # (family id, title, prompt file, kinds in that file, style used for the comparison)
    ('coding', 'Coding', CODING_PROMPTS, None, 'normal'),
    ('agentic', 'Agentic coding', None, None, None),
    ('fact', 'Facts', QUALITY_PROMPTS, {'fact'}, 'terse'),
    ('math', 'Maths', QUALITY_PROMPTS, {'math'}, 'terse'),
    ('logic', 'Logic', QUALITY_PROMPTS, {'logic'}, 'terse'),
    ('code', 'Small code', QUALITY_PROMPTS, {'code'}, 'terse'),
    ('explain', 'Explain', QUALITY_PROMPTS, {'explain'}, 'terse'),
]
# The two answer settings the comparison shows, per bench kind.
OFF = {'exact'}
OWN = {'native', 'native-long'}


def _json(path, default=None):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def bench_dirs():
    """[(folder, meta)] for every glm-bench folder that ran on Ollama."""
    out = []
    for d in sorted(RESULTS.glob('glm-bench-*')):
        meta = _json(d / 'meta.json', {})
        if meta.get('ollama_model'):
            out.append((d, meta))
    return out


def agentic_dirs():
    out = []
    for d in sorted(RESULTS.glob('agentic-v1-*')):
        meta = _json(d / 'meta.json', {})
        if meta.get('model'):
            meta['ollama_model'] = meta['model'].split('/', 1)[-1]
            out.append((d, meta))
    return out


def models():
    """Ollama models that have any comparable result, with what each has."""
    seen = {}
    for d, meta in bench_dirs():
        kind = 'coding' if meta.get('prompts') == CODING_PROMPTS else 'quality'
        seen.setdefault(meta['ollama_model'], set()).add(kind)
    for d, meta in agentic_dirs():
        seen.setdefault(meta['ollama_model'], set()).add('agentic')
    return [dict(model=m, has=sorted(k)) for m, k in sorted(seen.items())]


def _median(xs):
    xs = [x for x in xs if isinstance(x, (int, float))]
    return round(statistics.median(xs), 2) if xs else None


def _cell_from_answers(recs):
    graded = [r for r in recs if r.get('correct') is not None]
    return dict(solved=sum(1 for r in graded if r['correct']), total=len(graded),
                answer_s=_median(r.get('answer_s') for r in recs),
                tok_s=_median(r.get('decode_tok_s') for r in recs),
                cut_off=sum(1 for r in recs if r.get('truncated')), runs=1) if graded else None


def _answers(model, prompts, kinds, style, configs):
    """The newest folder's answers for this model, prompt file and setting group."""
    found = []
    for d, meta in bench_dirs():
        if meta['ollama_model'] != model or (meta.get('prompts') or QUALITY_PROMPTS) != prompts:
            continue
        recs = []
        for f in d.glob(f'*__{style}__*.json'):
            r = _json(f, {})
            if r.get('config') in configs and r.get('answer_s') is not None and (kinds is None or r.get('kind') in kinds):
                recs.append(r)
        if recs:
            found.append((meta.get('started', ''), d.name.removeprefix('glm-bench-'), recs))
    if not found:
        return None, None
    found.sort()
    return found[-1][1], found[-1][2]


def agentic_runs(model):
    """Every agentic run for a model: name, thinking, guard, solved, tests, time per repo."""
    runs = []
    for d, meta in agentic_dirs():
        if meta['ollama_model'] != model:
            continue
        rs = [_json(p, {}) for p in sorted(d.glob('*/result.json'))]
        if not rs:
            continue
        runs.append(dict(name=d.name.removeprefix('agentic-v1-'), thinking=meta.get('thinking'), guard=bool(meta.get('extension')),
                         started=meta.get('started'), solved=sum(1 for r in rs if r.get('solved')), total=len(rs),
                         tests_passed=sum(r.get('hidden_passed') or 0 for r in rs),
                         tests_total=sum(r.get('hidden_total') or 0 for r in rs),
                         per_repo_s=round(sum(r.get('wall_s') or 0 for r in rs) / len(rs), 1),
                         timeouts=sum(1 for r in rs if r.get('timed_out'))))
    return sorted(runs, key=lambda r: r['started'] or '')


def _agentic_cell(runs):
    """Plain runs (no extension) only; several runs show the mean and each run."""
    if not runs:
        return None
    last = runs[-1]
    return dict(solved=last['solved'], total=last['total'], per_repo_s=last['per_repo_s'], runs=len(runs),
                each=[r['solved'] for r in runs], timeouts=sum(r['timeouts'] for r in runs), names=[r['name'] for r in runs])


def compare(names):
    """Scorecard rows for the given models: one row per family, thinking off and own thinking."""
    names = [n for n in names if n][:4]
    rows = []
    for fid, title, prompts, kinds, style in FAMILIES:
        row = dict(family=fid, title=title, style=style, cells={})
        for m in names:
            if fid == 'agentic':
                runs = agentic_runs(m)
                plain = [r for r in runs if not r['guard']]
                row['cells'][m] = dict(off=_agentic_cell([r for r in plain if r['thinking'] == 'off']),
                                       own=_agentic_cell([r for r in plain if r['thinking'] != 'off']),
                                       guard=_agentic_cell([r for r in runs if r['guard']]))
            else:
                cell = {}
                for key, configs in (('off', OFF), ('own', OWN)):
                    bench, recs = _answers(m, prompts, kinds, style, configs)
                    c = _cell_from_answers(recs) if recs else None
                    if c:
                        c['bench'] = bench
                    cell[key] = c
                row['cells'][m] = cell
        if any(c['off'] or c['own'] for c in row['cells'].values()):
            rows.append(row)
    return dict(models=names, available=models(), rows=rows)


# ---------- coding answers: why one failed ----------

_reason_cache = {}


def _first_code_block(text):
    m = re.search(r'```(?:python|py)?\s*\n(.*?)(?:```|$)', text or '', re.S)
    return m[1] if m else (text or '')


def failure_reason(item, rec):
    """('passed'|'failed-test'|'too-slow'|'cut-off'|'no-code'|'crashed', detail lines). Runs the code like the grader."""
    key = (item['id'], rec.get('text'))
    if key in _reason_cache:
        return _reason_cache[key]
    text = rec.get('text') or ''
    code = _first_code_block(text)
    if rec.get('truncated') and not re.search(r'^\s*(def|class)\s', code, re.M):
        out = ('cut-off', [f"The answer used all {rec.get('output_tokens')} tokens before any code was written."])
    elif not re.search(r'^\s*(def|class|import|from)\s', code, re.M):
        out = ('no-code', ['No Python function or class was found in the answer.'])
    else:
        with tempfile.TemporaryDirectory() as d:
            src = Path(d, 'answer.py')
            src.write_text(code + '\n\n' + item['tests'] + '\n')
            try:
                p = subprocess.run([sys.executable, '-I', str(src)], cwd=d, capture_output=True, text=True, timeout=15)
                lines = p.stderr.strip().splitlines()
                if p.returncode == 0:
                    out = ('passed', [])
                elif lines and lines[-1].startswith('AssertionError'):
                    at = [l.strip() for l in lines if l.strip().startswith(('assert ', 'File "'))]
                    code_line = next((l.strip() for l in reversed(lines) if l.strip().startswith('assert')), '')
                    out = ('failed-test', [code_line or 'an assertion failed', lines[-1]] if lines[-1] != 'AssertionError' else [code_line or 'an assertion failed'])
                    if 'too slow' in lines[-1] or 'time.time()' in code_line:
                        out = ('too-slow', [code_line, lines[-1]])
                else:
                    out = ('cut-off' if rec.get('truncated') else 'crashed', ([lines[-1]] + lines[-3:-1]) if lines else [f'exit code {p.returncode}'])
            except subprocess.TimeoutExpired:
                out = ('too-slow', ['The code with its tests ran longer than 15 seconds.'])
    _reason_cache[key] = out
    return out


def coding_task(bench, task_id):
    """One coding task in one bench folder: the prompt, tests and each setting's answer with its reason."""
    d = RESULTS / f'glm-bench-{bench}'
    if not re.fullmatch(r'[A-Za-z0-9._-]+', bench or '') or not d.is_dir():
        raise KeyError('no such bench')
    meta = _json(d / 'meta.json', {})
    spec = _json(ROOT / (meta.get('prompts') or QUALITY_PROMPTS), {})
    items = [p for p in spec.get('prompts', []) if 'tests' in p]
    ids = [p['id'] for p in items]
    item = next((p for p in items if p['id'] == task_id), None)
    if not item:
        raise KeyError('no such task')
    answers = []
    for f in sorted(d.glob(f'{task_id}__*.json')):
        r = _json(f, {})
        reason, detail = failure_reason(item, r) if r.get('correct') is False else ('passed', [])
        answers.append(dict(style=r.get('style'), config=r.get('config'), correct=r.get('correct'), reason=reason, detail=detail,
                            answer_s=r.get('answer_s'), output_tokens=r.get('output_tokens'), thinking_chars=r.get('thinking_chars'),
                            tok_s=r.get('decode_tok_s'), truncated=r.get('truncated'), code=_first_code_block(r.get('text') or '')[:20000],
                            regrade_note=r.get('regrade_note')))
    i = ids.index(task_id)
    return dict(bench=bench, model=meta.get('ollama_model'), id=task_id, area=item.get('area'), prompt=item['prompt'],
                tests=item['tests'], answers=answers, index=i + 1, count=len(ids),
                prev=ids[i - 1] if i else None, next=ids[i + 1] if i + 1 < len(ids) else None)


def coding_failures(bench):
    """Every coding answer in a bench with its grade and failure reason, for the Results table."""
    d = RESULTS / f'glm-bench-{bench}'
    if not re.fullmatch(r'[A-Za-z0-9._-]+', bench or '') or not d.is_dir():
        raise KeyError('no such bench')
    meta = _json(d / 'meta.json', {})
    spec = {p['id']: p for p in _json(ROOT / (meta.get('prompts') or QUALITY_PROMPTS), {}).get('prompts', []) if 'tests' in p}
    out = []
    for f in sorted(d.glob('*__*.json')):
        r = _json(f, {})
        if r.get('id') in spec and r.get('correct') is False:
            reason, detail = failure_reason(spec[r['id']], r)
            out.append(dict(id=r['id'], config=r.get('config'), style=r.get('style'), reason=reason, detail=detail[:1]))
    return dict(bench=bench, model=meta.get('ollama_model'), failures=out)


# ---------- agentic runs ----------

def _task_defs():
    sys.path.insert(0, str(TASKS_DIR))
    import tasks
    return tasks.TASKS


def agentic(name):
    d = RESULTS / f'agentic-v1-{name}'
    if not re.fullmatch(r'[A-Za-z0-9._-]+', name or '') or not d.is_dir():
        raise KeyError('no such run')
    meta = _json(d / 'meta.json', {})
    defs = _task_defs()
    tasks = []
    for t in defs:
        r = _json(d / t / 'result.json')
        tasks.append(dict(task=t, area=defs[t]['area'], **({k: r.get(k) for k in ('solved', 'hidden_passed', 'hidden_total', 'wall_s', 'tool_calls', 'timed_out', 'guard')} if r else {'missing': True})))
    return dict(name=name, model=meta.get('model'), thinking=meta.get('thinking'), guard=bool(meta.get('extension')),
                started=meta.get('started'), tasks=tasks)


def _steps(path, limit=200):
    """Tool calls in order, with a short description and the time since the run started."""
    steps, start = [], None
    for line in Path(path).read_text(errors='replace').splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        ts = (e.get('message') or {}).get('timestamp')
        if start is None and isinstance(ts, (int, float)):
            start = ts
        if e.get('type') == 'message_end' and (e.get('message') or {}).get('role') == 'assistant':
            m = e['message']
            for c in m.get('content') or []:
                if isinstance(c, dict) and c.get('type') == 'toolCall':
                    a = c.get('arguments') or {}
                    what = a.get('command') or a.get('path') or a.get('file_path') or json.dumps(a)[:120]
                    steps.append(dict(tool=c.get('name'), what=str(what)[:200], repaired=str(c.get('id', '')).startswith('guard_'),
                                      t=round((m['timestamp'] - start) / 1000) if isinstance(m.get('timestamp'), (int, float)) and start else None))
        if e.get('type') == 'tool_execution_end' and steps:
            res = e.get('result')
            sc = res.get('structuredContent') if isinstance(res, dict) else None
            if isinstance(sc, dict) and sc.get('exit_code') not in (None, 0):
                steps[-1]['failed'] = True
    return steps[:limit], len(steps)


def agentic_task(name, task):
    d = RESULTS / f'agentic-v1-{name}' / task
    defs = _task_defs()
    if not re.fullmatch(r'[A-Za-z0-9._-]+', name or '') or task not in defs or not (d / 'result.json').exists():
        raise KeyError('no such task run')
    r = _json(d / 'result.json', {})
    steps, n = _steps(d / 'pi.jsonl') if (d / 'pi.jsonl').exists() else ([], 0)
    read = lambda f, cap=60000: (d / f).read_text(errors='replace')[:cap] if (d / f).exists() else ''
    hidden = read('hidden.txt')
    tests = [dict(name=m[1], ok=m[2] == 'ok') for m in re.finditer(r'^(test_\w+) \([^)]*\) \.\.\. (ok|FAIL|ERROR)', hidden, re.M)]
    guard = [json.loads(l) for l in read('guard.jsonl').splitlines() if l.strip()] if (d / 'guard.jsonl').exists() else []
    order = list(defs)
    i = order.index(task)
    return dict(name=name, task=task, area=defs[task]['area'], instruction=defs[task]['instruction'], result=r,
                steps=steps, step_count=n, diff=read('changes.diff'), hidden=hidden, hidden_tests=tests, visible=read('visible.txt', 20000),
                guard=[g for g in guard if g.get('event') != 'adjusted'], adjusted=sum(1 for g in guard if g.get('event') == 'adjusted'),
                index=i + 1, count=len(order), prev=order[i - 1] if i else None, next=order[i + 1] if i + 1 < len(order) else None)


def agentic_list():
    """Every agentic run, newest first, with its score."""
    out = []
    for d, meta in agentic_dirs():
        rs = [_json(p, {}) for p in sorted(d.glob('*/result.json'))]
        out.append(dict(name=d.name.removeprefix('agentic-v1-'), model=meta['ollama_model'], thinking=meta.get('thinking'),
                        guard=bool(meta.get('extension')), started=meta.get('started'), tasks=len(rs),
                        solved=sum(1 for r in rs if r.get('solved')), tests_passed=sum(r.get('hidden_passed') or 0 for r in rs),
                        tests_total=sum(r.get('hidden_total') or 0 for r in rs), wall_s=round(sum(r.get('wall_s') or 0 for r in rs), 1),
                        timeouts=sum(1 for r in rs if r.get('timed_out'))))
    return dict(runs=sorted(out, key=lambda r: r['started'] or '', reverse=True))
