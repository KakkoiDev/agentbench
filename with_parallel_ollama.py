"""Run bench commands against an isolated Ollama that answers several requests at once.

The system Ollama (127.0.0.1:11434) runs with OLLAMA_NUM_PARALLEL=1, so `agent_bench.py --attempts 2`
would queue its two runs there. This takes the lab's .bench.lock, unloads the given model from the
system Ollama (so two copies never sit in memory), starts `ollama serve` on its own port with
OLLAMA_NUM_PARALLEL and the same model store, runs each command with {url} replaced by the server's
address (commands must pass --no-lock, since this already holds the lock), then stops the server.

    python with_parallel_ollama.py --model gpt-oss:20b --parallel 2 -- \
        "python agent_bench.py run --no-lock --ollama-url {url} --attempts 2 ..."
"""
import argparse, fcntl, shlex, subprocess, sys
from pathlib import Path

from parallel_requests_bench import IsolatedOllama, api

ROOT = Path(__file__).resolve().parent


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--model', required=True, help='Ollama model to unload from the system server first, e.g. gpt-oss:20b')
    ap.add_argument('--parallel', type=int, default=2)
    ap.add_argument('--port', type=int, default=11500)
    ap.add_argument('--context', type=int, default=32768, help='OLLAMA_CONTEXT_LENGTH, same as the system service')
    ap.add_argument('commands', nargs='+')
    a = ap.parse_args()
    lock = (ROOT / '.bench.lock').open('w')
    print('Waiting for .bench.lock (one GPU job at a time)…', flush=True)
    fcntl.flock(lock, fcntl.LOCK_EX)
    try:
        api('http://127.0.0.1:11434', 'generate', dict(model=a.model, keep_alive=0), timeout=60)
    except Exception as e:
        print(f'note: could not unload {a.model} from the system Ollama: {e}', flush=True)
    out = ROOT / 'results'
    status = 0
    with IsolatedOllama(a.port, a.parallel, out, context_length=a.context) as srv:
        print(f'Isolated Ollama {srv.version} on {srv.url}, OLLAMA_NUM_PARALLEL={a.parallel}', flush=True)
        for c in a.commands:
            r = subprocess.run(shlex.split(c.replace('{url}', srv.url)), cwd=ROOT)
            status = status or r.returncode
        srv.unload(a.model)
    sys.exit(status)


if __name__ == '__main__':
    main()
