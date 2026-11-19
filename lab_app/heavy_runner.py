#!/usr/bin/env python3
"""Downloads the big models queued on the lab app's Downloads screen (models/heavy-queue.json).

Runs up to `parallel` `hf download`s at once, in queue order; a finished model leaves the queue and
is marked done in models/heavy-done.json. A failed download is retried three times (a minute
apart), then marked failed and skipped. `hf download` resumes partial files, so stopping this
runner (SIGTERM, the Pause button) and starting it again loses nothing. Exits when the queue is
empty. Started only by the Start button (lab_app/heavy.py start()).
"""
import json, os, signal, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import heavy  # noqa: E402

children = {}   # id -> Popen
stopping = False


def stop(*_):
    global stopping
    stopping = True
    for p in children.values():
        try:
            p.terminate()
        except ProcessLookupError:
            pass


def main():
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    heavy.RUNNER_PID.write_text(str(os.getpid()))
    tries, wait_until = {}, {}
    try:
        while not stopping:
            for i, p in list(children.items()):
                rc = p.poll()
                if rc is None:
                    continue
                del children[i]
                if rc == 0:
                    heavy.mark(heavy.DONE, i, time.strftime('%F %T'))
                    heavy.drop_from_queue(i)
                elif not stopping:
                    tries[i] = tries.get(i, 0) + 1
                    if tries[i] >= 3:
                        heavy.mark(heavy.FAILED, i, f'hf download exited {rc} three times; see {heavy.log_of(i).name}')
                    else:
                        wait_until[i] = time.time() + 60
            q = heavy.read_queue()   # read after the exits above, so a model just given up on is not started again
            done, failed = heavy.read_json(heavy.DONE), heavy.read_json(heavy.FAILED)
            todo = [i for i in q['queue'] if i in heavy.BY_ID and i not in done and i not in failed]
            if not todo and not children:
                break
            for i in todo:
                if len(children) >= max(1, int(q.get('parallel') or 1)):
                    break
                if i in children or wait_until.get(i, 0) > time.time():
                    continue
                m = heavy.BY_ID[i]
                cmd = ['hf', 'download', m['repo'], '--revision', m['revision'], '--local-dir', str(m['dir'])] + (m['files'] or [])
                log = open(heavy.log_of(i), 'a')
                log.write(f"\n{time.strftime('%F %T')} start: {' '.join(cmd)}\n")
                log.flush()
                children[i] = subprocess.Popen(cmd, cwd=heavy.ROOT, stdout=log, stderr=subprocess.STDOUT)
            time.sleep(3)
    finally:
        stop()
        for p in children.values():
            try:
                p.wait(timeout=10)
            except subprocess.TimeoutExpired:
                p.kill()
        try:
            heavy.RUNNER_PID.unlink()
        except FileNotFoundError:
            pass


if __name__ == '__main__':
    main()
