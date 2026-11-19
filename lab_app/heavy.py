"""The big model downloads (100+ GB each) for the Downloads screen: progress, speed, time left, start
and pause.

The downloads themselves are results/downloads.sh: one model after another (the connection is the
limit), each with `hf download`, which resumes partial files when started again. Pausing stops
that script and its `hf download`; the partial files stay. Nothing here starts a download by
itself: only the Start button does.
"""
import json, os, signal, subprocess, threading, time, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MODELS = ROOT / 'models'
SCRIPT = ROOT / 'results' / 'downloads.sh'
SIZES = MODELS / 'heavy-sizes.json'   # expected bytes per model, read once from Hugging Face

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
]

_lock = threading.Lock()
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


def _procs():
    out = subprocess.run(['ps', '-axo', 'pid=,command='], capture_output=True, text=True).stdout
    rows = [l.strip().split(None, 1) for l in out.splitlines() if l.strip()]
    return [(int(r[0]), r[1]) for r in rows if len(r) == 2 and ('downloads.sh' in r[1] or 'hf download' in r[1])]


def status():
    sizes, procs, now = _expected(), _procs(), time.time()
    script_running = any('downloads.sh' in c for _, c in procs)
    items, total_speed = [], 0.0
    for m in HEAVY:
        got = _bytes(m, sizes.get(m['id'] + ':oids', []))
        want = sizes.get(m['id'])
        log = m['out'].read_text(errors='ignore') if m['out'].exists() else ''
        done = m['done'] in log or (want and got >= want and not any(m['repo'] in c for _, c in procs))
        active = any(m['repo'] in c for _, c in procs)
        with _lock:
            s = [x for x in _samples.get(m['id'], []) if now - x[0] <= 90] + [(now, got)]
            _samples[m['id']] = s
        speed = (s[-1][1] - s[0][1]) / (s[-1][0] - s[0][0]) if active and len(s) > 1 and s[-1][0] - s[0][0] >= 5 else None
        total_speed += speed or 0
        state = 'done' if done else 'downloading' if active else 'waiting' if script_running else 'paused' if got else 'not started'
        items.append(dict(id=m['id'], name=m['name'], engine=m['engine'], why=m['why'], repo=m['repo'],
                          files=m['files'], dir=str(m['dir'].relative_to(ROOT)), bytes=got, expected=want, state=state,
                          speed=speed, eta_s=(want - got) / speed if speed and want and want > got else None))
    left = sum((i['expected'] or 0) - i['bytes'] for i in items if i['state'] != 'done')
    st = os.statvfs(MODELS)
    return dict(items=items, running=script_running, speed=total_speed or None,
                left_bytes=left, eta_s=left / total_speed if total_speed else None,
                free_bytes=st.f_bavail * st.f_frsize, checked=now)


def start():
    if any('downloads.sh' in c for _, c in _procs()):
        return dict(ok=True, note='already running')
    subprocess.Popen(['sh', str(SCRIPT)], cwd=ROOT, start_new_session=True,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return dict(ok=True, note='started')


def pause():
    stopped = []
    for pid, c in _procs():
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except ProcessLookupError:
            pass
    return dict(ok=True, stopped=stopped)
