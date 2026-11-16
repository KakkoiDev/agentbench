"""Let a shell queue of bench runs show in the lab app's "Up next".

The lab's own queue only runs whitelisted jobs. A script that runs its own sequence (with
custom extensions, flags...) publishes its steps here, and the lab lists the steps not yet
done as read-only queue items, in order, with an estimate:

    python script_queue.py plan queue-20261116 "Bench queue" "Coding v2 qwen terse|3600" "Vision qwen|1200" ...
    python script_queue.py run queue-20261116 "Coding v2 qwen terse" -- .venv/bin/python glm_bench.py run ...

`plan` records the calling script's PID: once that script is gone, the lab hides its steps.
`run` marks the step running, runs the command, then marks it done or failed (its exit code
is passed through). State: results/lab-app/script-queues/<name>.json.
"""
import json, os, subprocess, sys, time
from pathlib import Path

DIR = Path(__file__).resolve().parent / 'results' / 'lab-app' / 'script-queues'


def _path(name):
    if not name.replace('-', '').replace('_', '').isalnum():
        sys.exit('queue name: letters, digits, - and _ only')
    return DIR / f'{name}.json'


def _write(name, data):
    DIR.mkdir(parents=True, exist_ok=True)
    tmp = _path(name).with_suffix('.tmp')
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(_path(name))


def _update(name, title, **fields):
    data = json.loads(_path(name).read_text())
    for s in data['steps']:
        if s['title'] == title:
            s.update(fields)
            break
    else:
        data['steps'].append(dict(title=title, est_s=None, **fields))
    _write(name, data)


def load_all():
    """Steps of every live script queue, for the lab: [{queue, title, est_s, status, pid}]."""
    out = []
    for f in sorted(DIR.glob('*.json')) if DIR.exists() else []:
        try:
            data = json.loads(f.read_text())
            os.kill(int(data['pid']), 0)  # the script still runs
        except (OSError, ValueError, KeyError):
            continue
        for s in data['steps']:
            out.append(dict(queue=data.get('title') or f.stem, name=f.stem, pid=data['pid'], **s))
    return out


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    cmd, name = sys.argv[1], sys.argv[2]
    if cmd == 'plan':
        title, steps = sys.argv[3], []
        for s in sys.argv[4:]:
            t, _, est = s.partition('|')
            steps.append(dict(title=t, est_s=int(est) if est.isdigit() else None, status='queued'))
        _write(name, dict(title=title, pid=os.getppid(), planned=time.time(), steps=steps))
    elif cmd == 'run':
        title, rest = sys.argv[3], sys.argv[4:]
        if rest[:1] == ['--']:
            rest = rest[1:]
        _update(name, title, status='running', started=time.time())
        rc = subprocess.call(rest)
        _update(name, title, status='done' if rc == 0 else 'failed', ended=time.time(), exit=rc)
        sys.exit(rc)
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
