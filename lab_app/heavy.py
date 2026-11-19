"""The big model downloads (100+ GB each) for the Downloads screen: a queue you choose and order,
how many download at once, progress, speed and time left.

lab_app/heavy_runner.py does the downloading (`hf download`, which resumes partial files), started
by the Start button and stopped by Pause; the partial files stay. The queue and settings are in
models/heavy-queue.json, finished models in models/heavy-done.json. Nothing here starts a download
by itself.
"""
import json, os, signal, subprocess, threading, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / 'models'
SIZES = MODELS / 'heavy-sizes.json'   # expected bytes per model, read once from Hugging Face
STATE = Path(os.environ.get('HEAVY_STATE_DIR') or MODELS)   # a test points this elsewhere
QUEUE = STATE / 'heavy-queue.json'    # {"queue": [ids in order], "parallel": n}
DONE = STATE / 'heavy-done.json'      # id -> when it finished
FAILED = STATE / 'heavy-failed.json'  # id -> why it was given up
RUNNER_PID = STATE / 'heavy-runner.pid'
RUNNER = Path(__file__).resolve().parent / 'heavy_runner.py'
PY = ROOT / '.venv' / 'bin' / 'python'

HEAVY = [
    dict(id='qwen-flash-next-mlx', name='Qwen3.8-Flash-Next 4-bit (MLX)', engine='SlotStream',
         repo='carloslfu/Qwen3.8-Flash-Next-MLX-4bit', revision='e9d552f83de4665d243d5c9cf73201a1ca6c16d7', files=None,
         dir=MODELS / 'qwen3.8-flash-next-mlx-4bit', out=ROOT / 'results' / 'download-qwen-flash-next.out', done='QWEN_FLASH_NEXT_DONE',
         why='125B mixture of experts that streams from the SSD; SlotStream estimates 7-11 tokens/s here.'),
    dict(id='glm53-flash-ds4', name='GLM-5.3-Flash Q2 (ds4)', engine='ds4',
         repo='antirez/glm-5.3-flash-gguf', revision='main', files=['GLM-5.3-Flash-Q2.gguf'],
         dir=MODELS / 'ds4', out=ROOT / 'results' / 'download-ds4.out', done='GLM53F_DS4_DONE',
         why='Smarter than Qwen 3.8 27B (Artificial Analysis 42 vs 34); runs on Metal with SSD streaming.'),
    dict(id='qwen-flash-next-ds4', name='Qwen3.8-Flash-Next Q2 (ds4)', engine='ds4',
         repo='antirez/qwen3.8-flash-next-gguf', revision='main', files=['Qwen3.8-Flash-Next-Q2.gguf'],
         dir=MODELS / 'ds4', out=ROOT / 'results' / 'download-ds4.out', done='QWEN38_DS4_DONE',
         why='The same model in ds4\'s format: 8.4 tokens/s measured on a 32 GB M1 Max.'),
    dict(id='deepseek-v41-flash-ds4', name='DeepSeek V4.1 Flash Q2 (ds4)', engine='ds4',
         repo='antirez/deepseek-v4.1-flash-gguf', revision='main', files=['DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix.gguf'],
         dir=MODELS / 'ds4', out=None, done=None,
         why='552B (about 16B used per token), rated above full GLM-5.3 (AA 45.1 vs 44.8) while reading a third to half the bytes per token; '
             'estimated 1.5-2.5 tokens/s here vs 0.43 for GLM-5.3. ds4 sizes it for 128 GB Macs: check it runs in 32 GB before relying on it.'),
]

BY_ID = {m['id']: m for m in HEAVY}
_lock = threading.Lock()


def read_json(path, default=None):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {} if default is None else default


def write_json(path, data):
    tmp = path.with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def read_queue():
    q = read_json(QUEUE, {'queue': [], 'parallel': 1})
    q['queue'] = [i for i in q.get('queue', []) if i in BY_ID]
    q['parallel'] = max(1, min(len(HEAVY), int(q.get('parallel') or 1)))
    return q


def mark(path, i, value):
    with _lock:
        d = read_json(path)
        d[i] = value
        write_json(path, d)


def drop_from_queue(i):
    with _lock:
        q = read_queue()
        q['queue'] = [x for x in q['queue'] if x != i]
        write_json(QUEUE, q)


def log_of(i):
    return (STATE if os.environ.get('HEAVY_STATE_DIR') else ROOT / 'results') / f'download-{i}.out'


def runner_pid():
    try:
        pid = int(RUNNER_PID.read_text())
        os.kill(pid, 0)
        return pid
    except (OSError, ValueError):
        return None


def _legacy_done(m):
    """Finished by the earlier downloads.sh chain (its done markers)."""
    out = {'qwen-flash-next-mlx': ('download-qwen-flash-next.out', 'QWEN_FLASH_NEXT_DONE'),
           'glm53-flash-ds4': ('download-ds4.out', 'GLM53F_DS4_DONE'), 'qwen-flash-next-ds4': ('download-ds4.out', 'QWEN38_DS4_DONE')}.get(m['id'])
    f = ROOT / 'results' / out[0] if out else None
    return bool(f and f.exists() and out[1] in f.read_text(errors='ignore'))
_samples = {}   # id -> [(time, bytes)], for the speed over the last minute


def _expected():
    try:
        sizes = json.loads(SIZES.read_text())
    except (OSError, ValueError):
        sizes = {}
    changed = False
    for m in HEAVY:
        if m['id'] in sizes:
            continue
        try:
            url = f"https://huggingface.co/api/models/{m['repo']}/tree/{m['revision']}?recursive=true"
            tree = json.load(urllib.request.urlopen(url, timeout=10))
            mine = [x for x in tree if x.get('type') == 'file' and (m['files'] is None or x['path'] in m['files'])]
            sizes[m['id']] = sum(x.get('size', 0) for x in mine)
            sizes[m['id'] + ':oids'] = [x['lfs']['oid'] for x in mine if x.get('lfs')]
            changed = True
        except Exception:
            pass
    if changed:
        SIZES.write_text(json.dumps(sizes, indent=1))
    return sizes


def _bytes(m, oids):
    """Bytes on disk for this model: its finished files plus the partial ones being written. hf names a
    partial file after the file's SHA-256 (<key>.<sha256>.<n>.incomplete), which tells whose it is."""
    d, total = m['dir'], 0
    if not d.exists():
        return 0
    if m['files'] is None:
        total += sum(p.stat().st_size for p in d.rglob('*') if p.is_file() and '.cache' not in p.parts)
    else:
        total += sum((d / f).stat().st_size for f in m['files'] if (d / f).exists())
    part_dir = d / '.cache' / 'huggingface' / 'download'
    if part_dir.exists():
        for q in part_dir.rglob('*.incomplete'):
            if m['files'] is None or any(o in q.name for o in oids):
                total += q.stat().st_size
    return total


import re
HF_RE = re.compile(r'(?:^|/|\s)\.?hf(?:-wrapped)? download\b')   # nix runs hf as .hf-wrapped


def _procs():
    out = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True).stdout
    rows = [l.strip().split(None, 1) for l in out.splitlines() if l.strip()]
    return [(int(r[0]), r[1]) for r in rows if len(r) == 2 and ('downloads.sh' in r[1] or HF_RE.search(r[1]))]


def status():
    sizes, procs, now = _expected(), _procs(), time.time()
    q, done, failed, pid = read_queue(), read_json(DONE), read_json(FAILED), runner_pid()
    items, total_speed = [], 0.0
    for m in HEAVY:
        got = _bytes(m, sizes.get(m['id'] + ':oids', []))
        want = sizes.get(m['id'])
        active = any(m['repo'] in c and HF_RE.search(c) and (m['files'] is None or any(f in c for f in m['files'])) for _, c in procs)
        is_done = m['id'] in done or _legacy_done(m)
        with _lock:
            smp = [x for x in _samples.get(m['id'], []) if now - x[0] <= 90] + [(now, got)]
            _samples[m['id']] = smp
        speed = (smp[-1][1] - smp[0][1]) / (smp[-1][0] - smp[0][0]) if active and smp[-1][0] - smp[0][0] >= 5 else None
        total_speed += speed or 0
        pos = q['queue'].index(m['id']) + 1 if m['id'] in q['queue'] else None
        state = ('done' if is_done else 'failed' if m['id'] in failed else 'downloading' if active
                 else ('queued' if pid else 'queued, paused') if pos else 'paused' if got else 'not queued')
        items.append(dict(id=m['id'], name=m['name'], engine=m['engine'], why=m['why'], repo=m['repo'],
                          files=m['files'], dir=str(m['dir'].relative_to(ROOT)), bytes=got, expected=want, state=state,
                          position=pos, failed=failed.get(m['id']), speed=speed,
                          eta_s=(want - got) / speed if speed and want and want > got else None))
    items.sort(key=lambda x: (x['position'] is None, x['position'] or 0))
    left = sum((i['expected'] or 0) - i['bytes'] for i in items if i['position'] and i['state'] != 'done')
    st = os.statvfs(MODELS)
    return dict(items=items, running=bool(pid), parallel=q['parallel'], queue=q['queue'], speed=total_speed or None,
                left_bytes=left, eta_s=left / total_speed if total_speed else None,
                free_bytes=st.f_bavail * st.f_frsize, checked=now)


def set_queue(ids, parallel=None):
    """The queue, in order (ids not yet done); and how many download at once."""
    if not isinstance(ids, list) or not all(isinstance(i, str) and i in BY_ID for i in ids):
        raise ValueError('ids: a list of model ids from the Downloads screen')
    with _lock:
        q = read_queue()
        q['queue'] = list(dict.fromkeys(ids))
        if parallel is not None:
            q['parallel'] = max(1, min(len(HEAVY), int(parallel)))
        write_json(QUEUE, q)
    failed = read_json(FAILED)
    if any(i in failed for i in ids):  # queueing a failed model again means: try again
        write_json(FAILED, {k: v for k, v in failed.items() if k not in ids})
    return read_queue()


def start():
    if runner_pid():
        return dict(ok=True, note='already running')
    if not read_queue()['queue']:
        raise ValueError('the queue is empty: add a model first')
    for pid, c in _procs():   # the earlier downloads.sh chain, if it still runs
        if 'downloads.sh' in c:
            os.kill(pid, signal.SIGTERM)
    subprocess.Popen([str(PY), str(RUNNER)], cwd=ROOT, start_new_session=True,
                     stdout=open(ROOT / 'results' / 'download-runner.out', 'a'), stderr=subprocess.STDOUT)
    return dict(ok=True, note='started')


def pause():
    stopped = []
    pid = runner_pid()
    if pid:
        os.kill(pid, signal.SIGTERM)   # it stops its own hf downloads
        stopped.append(pid)
    for p, c in _procs():
        try:
            os.kill(p, signal.SIGTERM)
            stopped.append(p)
        except ProcessLookupError:
            pass
    for _ in range(20):   # a download that ignores the stop for 10 s is killed
        if not _procs():
            break
        time.sleep(0.5)
    for p, _ in _procs():
        try:
            os.kill(p, signal.SIGKILL)
        except ProcessLookupError:
            pass
    left = _procs()
    if left:
        raise ValueError('could not stop: ' + ', '.join(str(p) for p, _ in left))
    return dict(ok=True, stopped=stopped)
