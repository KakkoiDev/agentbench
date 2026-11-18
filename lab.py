#!/usr/bin/env python3
"""The lab's two bench modes, for one Ollama model at a time.

    .venv/bin/python lab.py speed        MODEL [MODEL ...]
    .venv/bin/python lab.py intelligence MODEL [--repeats 2] [--harness PATH] [--all-coding] [--vision]
    .venv/bin/python lab.py both         MODEL ...

speed: a smoke test of how fast the model is on this Mac and which settings make it faster. One
  request with an assistant-sized system prompt, the same prompt again (prompt cache), a terse
  reply, thinking on, a 32k context instead of 8k (and the memory it then takes), and 1, 2 and 4
  requests at once. Minutes per model. Results: results/speed/speed-DATE.json.

intelligence: what the model can actually do, with the harness it would run in. Family jobs and
  the hard family jobs (each --repeats times), the assistant tests in English and in Japanese and
  French, and the hard coding suite (agentic-v4, plain pi). --all-coding adds coding suites v1-v3;
  --vision adds both image suites. Hours per model.

Then `python3 scripts/collect_local_ai.py` rebuilds the site: the Speed and Intelligence pages,
and each model's page. Every bench takes the lab's lock, so only one GPU job runs at a time.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PY = sys.executable
HARNESS = Path.home() / 'Documents/ChatGPT/DropBox/dropbox/mini-next.py'  # harness v3.2


def run(*cmd):
    print('+', ' '.join(map(str, cmd)), flush=True)
    return subprocess.run([str(c) for c in cmd], cwd=ROOT).returncode


def tag(model):
    return model.replace(':', '-').replace('.', '-')


def speed(models):
    for m in models:
        run('ollama', 'stop', m)
    return run(PY, 'scripts/speed_test.py', '--sweep', *models)


def intelligence(model, repeats, harness, all_coding, vision):
    t, h = tag(model), ['--mini-py', harness]
    for r in range(1, repeats + 1):
        run(PY, 'dropbox_bench.py', 'family', *h, '--model', f'ollama/{model}', '--tag', f'{t}-checks32-r{r}')
        run(PY, 'dropbox_bench.py', 'family', '--suite', 'hard', *h, '--model', f'ollama/{model}', '--tag', f'{t}-hard-checks32-r{r}')
    run(PY, 'dropbox_bench.py', 'mini', *h, '--model', f'ollama/{model}', '--cases', 'en', '--tag', 'checks32-r1')
    run(PY, 'dropbox_bench.py', 'mini', *h, '--model', f'ollama/{model}', '--cases', 'lang', '--tag', 'lang-checks32-r1')
    for suite in (['agentic-v1', 'agentic-v2', 'agentic-v3'] if all_coding else []) + ['agentic-v4']:
        run(PY, 'agent_bench.py', 'run', '--bench', suite, '--name', f'{t}-off-r1', '--model', f'ollama/{model}', '--thinking', 'off', '--timeout', '900')
    if vision:
        for suite in ('vision-v1', 'vision-v2'):
            run(PY, 'vision_bench.py', 'run', '--bench', suite, '--model', model, '--think', 'off')
    run('ollama', 'stop', model)


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument('mode', choices=['speed', 'intelligence', 'both'])
    p.add_argument('models', nargs='+')
    p.add_argument('--repeats', type=int, default=2)
    p.add_argument('--harness', default=str(HARNESS), help='the mini.py the assistant benches run (default: harness v3.2)')
    p.add_argument('--all-coding', action='store_true')
    p.add_argument('--vision', action='store_true')
    a = p.parse_args()
    if a.mode in ('speed', 'both'):
        speed(a.models)
    if a.mode in ('intelligence', 'both'):
        for m in a.models:
            intelligence(m, a.repeats, a.harness, a.all_coding, a.vision)


if __name__ == '__main__':
    main()
