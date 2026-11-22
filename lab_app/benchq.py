"""The bench queue for the lab app's Benches screen: results/resume_queue.py runs every bench for the
Ollama models, model by model, smallest first, and writes results/bench-progress.json. This reads it
and the running job's log for live progress, and starts or stops the queue.
"""
import json, os, re, signal, subprocess, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROGRESS = ROOT / 'results' / 'bench-progress.json'
QUEUE = ROOT / 'results' / 'resume_queue.py'
PY = ROOT / '.venv' / 'bin' / 'python'
OUT = ROOT / 'results' / 'bench-queue.out'

# items each kind of job works through, and how its log shows one finished
TOTAL = {'family': 12, 'hard': 10, 'expert': 10, 'mini-en': 14, 'mini-lang': 8, 'coding': 5, 'speed': 10, 'concurrency': 1}
CASE = re.compile(r'^\S+\s+(PASS|FAIL)\s', re.M)
TASK = re.compile(r'^\[(\d+)/(\d+)\]', re.M)


def alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except (OSError, TypeError):
        return False


def live(job):
    """Done/total for the running job, from its log."""
    try:
        text = (ROOT / job['log']).read_text(errors='ignore')
    except (OSError, KeyError):
        return None
    kind, total = job.get('kind'), TOTAL.get(job.get('kind'))
    if kind == 'coding':
        m = TASK.findall(text)
        return dict(done=int(m[-1][0]), total=int(m[-1][1])) if m else dict(done=0, total=5)
    if kind == 'concurrency':
        return dict(done=sum(1 for l in text.splitlines() if '"verdict"' in l), total=1)
    if kind == 'speed':
        return dict(done=sum(1 for l in text.splitlines() if l.startswith('{') and '"case"' in l), total=total)
    if job['desc'].startswith('Smoke') and kind == 'family':
        total = 3
    return dict(done=len(CASE.findall(text)), total=total, last=(text.strip().splitlines() or [''])[-1][:160])


def status():
    try:
        d = json.loads(PROGRESS.read_text())
    except (OSError, ValueError):
        return dict(running=False, jobs=[], note='The bench queue has not run yet.')
    d['running'] = alive(d.get('pid'))
    now = time.time()
    # typical minutes per kind of job, from the ones finished in this queue
    took = {}
    for j in d['jobs']:
        if j.get('state') in ('done', 'failed') and j.get('started') and j.get('ended'):
            took.setdefault(j['kind'], []).append(j['ended'] - j['started'])
    typical = {k: sorted(v)[len(v) // 2] for k, v in took.items()}
    left_s = 0.0
    for j in d['jobs']:
        if j.get('state') == 'running':
            if not d['running']:
                j['state'] = 'stopped'
            else:
                j['elapsed_s'] = now - j['started']
                j['progress'] = live(j)
        if j.get('state') in ('pending', 'running'):
            t = typical.get(j['kind'])
            if t:
                left_s += max(0.0, t - j.get('elapsed_s', 0))
    d['left_s'] = left_s if typical else None
    d['counts'] = {s: sum(1 for j in d['jobs'] if j.get('state', '').startswith(s)) for s in ('done', 'running', 'pending', 'failed', 'stopped')}
    return d


def start():
    st = status()
    if st.get('running'):
        return dict(ok=True, note='already running')
    if subprocess.run(['pgrep', '-f', 'dropbox_bench|agent_bench|lab.py speed'], capture_output=True).returncode == 0:
        raise ValueError('another bench is running; wait for it to finish')
    subprocess.Popen([str(PY), str(QUEUE)], cwd=ROOT, start_new_session=True,
                     stdout=open(OUT, 'a'), stderr=subprocess.STDOUT)
    return dict(ok=True, note='started')


def stop():
    """Stop the queue and the bench it is running (its whole process group); mini is resumed."""
    st = status()
    pid = st.get('pid')
    if not st.get('running'):
        return dict(ok=True, note='not running')
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except ProcessLookupError:
        pass
    for _ in range(20):
        if not alive(pid):
            break
        time.sleep(0.5)
    # mini is resumed by the queue's own cleanup; if it was killed hard, resume it here
    for p in subprocess.run(['pgrep', '-f', 'share/dropbox/mini/mini.py'], capture_output=True, text=True).stdout.split():
        for q in [p] + subprocess.run(['pgrep', '-P', p], capture_output=True, text=True).stdout.split():
            try:
                os.kill(int(q), signal.SIGCONT)
            except ProcessLookupError:
                pass
    return dict(ok=True, note='stopped')
