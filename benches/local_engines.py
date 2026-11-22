"""Local model servers other than Ollama (mlx-lm, MTPLX, oMLX, SlotStream, ds4), for the benches.

Each serves an OpenAI-compatible API on 127.0.0.1. `add_providers(models_json)` adds one pi provider
per engine to a bench's private pi config, so a bench can run `--model mlxlm/<id>` like
`--model ollama/<id>`. `serve(model)` starts the right server for one of MODELS, waits until it
answers, and returns a function that stops it; the bench queue (results/resume_queue.py) wraps a
model's benches in it, so only one big model is in memory at a time.
"""
import json, os, signal, subprocess, time, urllib.error, urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOGS = ROOT / 'results' / 'engine-logs'

E = ROOT / 'engines'
OFFLINE = {'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1'}

ENGINES = {   # engine -> port, the command that serves one model, and its environment and working folder
    # mlx-lm resolves a request's "model" as a path or a Hub repo: run it from models/mlx and name the model by its folder
    'mlxlm': dict(port=8091, cwd=ROOT / 'models/mlx', cmd=lambda m: [str(ROOT / '.venv/bin/python'), '-m', 'mlx_lm', 'server', '--model',
                                                                     Path(m['path']).name, '--host', '127.0.0.1', '--port', '8091'], env=OFFLINE),
    # MTPLX (engines/mtplx, reviewed 2026-11-20): logs off, session cache and HOME kept in engines/mtplx/state, API key even on loopback;
    # never `mtplx max`, `--fan-mode max` or `start pi`. Issue #691 strips trailing newlines from tool-call arguments (affects coding).
    'mtplx': dict(port=8092, key_file=E / 'mtplx/state/api-key',
                  cmd=lambda m: [str(E / 'mtplx/.venv/bin/mtplx'), 'serve', '--model', m['path'], '--host', '127.0.0.1', '--port', '8092',
                                 '--api-key-file', str(E / 'mtplx/state/api-key'), '--model-id', m['id'], '--depth', str(m.get('depth', 3)),
                                 '--ssd-session-cache-dir', str(E / 'mtplx/state/session-bank'), '--ssd-session-cache-max-size', '20GB',
                                 '--fan-mode', 'default'] + (['--memory-limit', m['memory']] if m.get('memory') else []),
                  env=dict(OFFLINE, HOME=str(E / 'mtplx/state'), MTPLX_REQUEST_LOG_JSONL='off', MTPLX_FLIGHT_RECORDER='off',
                           MTPLX_SSD_SESSION_CACHE_DIR=str(E / 'mtplx/state/session-bank'))),
    # oMLX 0.8.0 (engines/omlx, release wheel, reviewed): headless, base path in engines/omlx/state, run from there (not the source
    # tree, which would shadow the installed package); distributed inference stays off; never its admin-UI benchmark (uploads results).
    'omlx': dict(port=8093, cwd=E / 'omlx/state',
                 cmd=lambda m: [str(E / 'omlx/.venv/bin/omlx'), 'serve', '--headless', '--model-dir', str(ROOT / 'models/omlx'),
                                '--base-path', str(E / 'omlx/state'), '--host', '127.0.0.1', '--port', '8093', '--no-hf-cache',
                                '--max-concurrent-requests', '2', '--memory-guard', 'balanced'],
                 env=OFFLINE),
    # SlotStream 0.2.30 (engines/slotstream-release, checksum and attestation verified): binds 127.0.0.1 only; --model means no
    # download and no hash check; writes logs under ~/.slotstream. Never install.sh or `launch pi`.
    'slotstream': dict(port=8094, cmd=lambda m: [str(E / 'slotstream-release/bin/slotstream'), 'serve', '--model', m['path'],
                                                 '--port', '8094']),   # auto plan: --memory-gb 26 hit insufficient_memory
    # ds4 (engines/ds4, built from source, reviewed): SSD streaming for models far larger than RAM; binds 127.0.0.1.
    'ds4': dict(port=8095, cwd=E / 'ds4', cmd=lambda m: [str(E / 'ds4/ds4-server'), '-m', m['path'], '--ssd-streaming', '--ctx', str(m.get('ctx', 16384)),
                                          '--ssd-streaming-cache-experts', m.get('cache', '14GB'), '--host', '127.0.0.1', '--port', '8095'],
                # GLM's default memory reserve is a flat 32 GiB (sized for 128 GB+ hosts), so on 32 GB its guard budget is 0:
                # reserve 8 GiB for macOS and the benches instead (GLM-5.3-Flash with SSD streaming needs about 5.3 GiB)
                env={'DS4_GLM_MEMORY_GUARD_RESERVE_GB': '8'}),
}

M = ROOT / 'models'
# model id (as benches name it: <engine>/<id>) -> where its files are, whether it reasons, engine-specific settings
MODELS = {
    'mlxlm/nanbeige4.3-33b-4bit': dict(path=str(M / 'mlx/nanbeige4.3-33b-4bit'), reasoning=True),
    'mtplx/qwen3.8-27b-bare': dict(path=str(M / 'mtplx/qwen3.8-27b-bare-speed'), reasoning=True, depth=3),
    'mtplx/qwen3.8-27b-optimized': dict(path=str(M / 'mtplx/qwen3.8-27b-optimized-speed'), reasoning=True, depth=3, memory='23G'),
    'mtplx/bonsai-2-27b': dict(path=str(M / 'mtplx/bonsai-2-27b-optimized-speed'), reasoning=True, depth=1),
    'omlx/qwen3.8-27b-oq3h-mtp': dict(path=str(M / 'omlx/qwen3.8-27b-oq3h-mtp'), reasoning=True),
    'slotstream/qwen3.8-flash-next:4bit': dict(path=str(M / 'qwen3.8-flash-next-mlx-4bit'), reasoning=True),
    'ds4/glm-5.3-flash-q2': dict(path=str(M / 'ds4/GLM-5.3-Flash-Q2.gguf'), reasoning=True),
    'ds4/qwen3.8-flash-next-q2': dict(path=str(M / 'ds4/Qwen3.8-Flash-Next-Q2.gguf'), reasoning=True),
    'ds4/deepseek-v4.1-flash-q2': dict(path=str(M / 'ds4/DeepSeek-V4.1-Flash-1017-IQ2_XXS-Q2_K-imatrix.gguf'), reasoning=True, ctx=8192, cache='300'),   # at ctx 16384 / 12GB it needed 25.17 GiB vs a 24.96 GiB budget
}
for _k, _m in MODELS.items():
    _m['id'] = _k.split('/', 1)[1]


def api_key(engine):
    f = ENGINES[engine].get('key_file')
    if not f:
        return 'local'
    f = Path(f)
    if not f.exists():
        import secrets
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(secrets.token_urlsafe(24))
        f.chmod(0o600)
    return f.read_text().strip()


def add_providers(models_json):
    """Add a pi provider for each local engine to this pi config (a bench's private copy)."""
    p = Path(models_json)
    cfg = json.loads(p.read_text()) if p.exists() else {'providers': {}}
    for engine, e in ENGINES.items():
        ids = [k.split('/', 1)[1] for k in MODELS if k.startswith(engine + '/')]
        if not ids:
            continue
        cfg['providers'][engine] = {'api': 'openai-completions', 'apiKey': api_key(engine), 'baseUrl': f"http://127.0.0.1:{e['port']}/v1",
                                    'compat': {'supportsStore': False, 'supportsStrictMode': False},
                                    'models': [{'id': i, 'reasoning': MODELS[f'{engine}/{i}'].get('reasoning', False)} for i in ids]}
    p.write_text(json.dumps(cfg, indent=2))


def answering(port, engine=None):
    headers = {'Authorization': 'Bearer ' + api_key(engine)} if engine else {}
    try:
        urllib.request.urlopen(urllib.request.Request(f'http://127.0.0.1:{port}/v1/models', headers=headers), timeout=3).read()
        return True
    except urllib.error.HTTPError:
        return True   # it answers, even if this path needs something else
    except Exception:
        return False


def serve(model, timeout=900):
    """Start the server for `model` (e.g. 'mlxlm/nanbeige4.3-33b-4bit'); return a stop function."""
    engine = model.split('/', 1)[0]
    e, m = ENGINES[engine], MODELS[model]
    if answering(e['port'], engine):
        raise RuntimeError(f"port {e['port']} is already in use; stop that server first")
    LOGS.mkdir(parents=True, exist_ok=True)
    log = open(LOGS / f"{model.replace('/', '-')}.log", 'a')
    cwd = Path(e.get('cwd') or ROOT)
    cwd.mkdir(parents=True, exist_ok=True)
    if engine == 'mtplx':
        api_key(engine)   # the key file must exist before the server reads it
    if engine == 'omlx':   # MTP on for its model (oMLX turns it off by default)
        (cwd / 'model_settings.json').write_text(json.dumps({'version': 2, 'models': {m['id']: {
            'mtp_enabled': True, 'max_context_window': 65536, 'is_pinned': True, 'is_default': True}}}))
    proc = subprocess.Popen(e['cmd'](m), cwd=cwd, env=dict(os.environ, **e.get('env', {})),
                            stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

    def stop():
        try:
            os.killpg(proc.pid, signal.SIGTERM)
            proc.wait(timeout=30)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            try:
                os.killpg(proc.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass

    t0 = time.time()
    while not answering(e['port'], engine):
        if proc.poll() is not None:
            raise RuntimeError(f'{model}: server exited with {proc.returncode}; see {log.name}')
        if time.time() - t0 > timeout:
            stop()
            raise RuntimeError(f'{model}: server did not answer within {timeout}s')
        time.sleep(3)
    return stop
