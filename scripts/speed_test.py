"""Usage: python3 scripts/speed_test.py [--sweep] MODEL [MODEL ...]  (writes results/speed/speed-DATE.json)

--sweep (the lab's speed mode, lab.py speed) also tries the settings that change speed: a 32k context
instead of 8k, thinking on, and 1, 2 and 4 requests at once for every model.

Where a butler reply's time goes, per model: prompt reading (prefill) vs writing (decode),
first vs repeated system prompt (Ollama's prompt cache), normal vs terse replies, and
gpt-oss with 1, 2 and 4 requests at once. 2026-11-18."""
# v2 2026-11-18: fixed 256-token replies for the parallel test (1, 2, 4 at once), HTTP error bodies shown
import json, sys, time, urllib.request, urllib.error, concurrent.futures as cf
URL = "http://127.0.0.1:11434/api/chat"
# about the size of mini's system prompt plus pi's tool list (~3k tokens)
SYSTEM = ("You are mini, the local assistant inside DropBox, a private team chat on this Mac. " * 3 + "\n") + \
    "\n".join(f"- Rule {i}: search and read the person's files before answering; never guess; say where you saved things; "
              "do the work yourself with your tools; reply in the language of the message." for i in range(110))
TERSE = "\nAnswer in at most two short sentences."
ASK = "What's a good way to keep a weekly grocery list with my partner? Give me a practical suggestion."

LONG = "Write a detailed weekly meal plan for a family of four, day by day, with a shopping list."

def call(model, system, ask=ASK, think=False, num_predict=None, num_ctx=8192):
    body = {"model": model, "stream": False, "think": think, "keep_alive": "10m",
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": ask}],
            "options": {"num_ctx": num_ctx}}
    if model.startswith("gpt-oss"): body["think"] = "medium" if think else "low"
    if num_predict: body["options"]["num_predict"] = num_predict
    t = time.time()
    try:
        r = json.load(urllib.request.urlopen(urllib.request.Request(URL, json.dumps(body).encode(), {"Content-Type": "application/json"}), timeout=600))
    except urllib.error.HTTPError as e:
        raise SystemExit(f"{model}: HTTP {e.code}: {e.read().decode(errors='replace')[:500]}")
    return dict(wall=round(time.time() - t, 2), load=round(r.get("load_duration", 0) / 1e9, 2),
                prompt_tokens=r.get("prompt_eval_count", 0), prompt_s=round(r.get("prompt_eval_duration", 0) / 1e9, 2),
                out_tokens=r.get("eval_count", 0), out_s=round(r.get("eval_duration", 0) / 1e9, 2))

SWEEP = "--sweep" in sys.argv
out = []
for model in [a for a in sys.argv[1:] if not a.startswith("--")]:
    call(model, SYSTEM, "hi")  # load it
    ps = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=10))
    for m in ps.get("models", []):
        if m.get("name") == model or m.get("model") == model:
            r = dict(model=model, case="loaded", size_gb=round(m.get("size", 0) / 1e9, 1), vram_gb=round(m.get("size_vram", 0) / 1e9, 1),
                     context=m.get("context_length")); out.append(r); print(json.dumps(r), flush=True)
    for name, system, ask in [("first", SYSTEM + " ", ASK), ("cached", SYSTEM + " ", ASK + " Thanks."), ("terse", SYSTEM + " " + TERSE, ASK)]:
        r = dict(model=model, case=name, **call(model, system, ask)); out.append(r); print(json.dumps(r), flush=True)
    if SWEEP:
        try:
            r = dict(model=model, case="think", **call(model, SYSTEM + " ", ASK, think=True)); out.append(r); print(json.dumps(r), flush=True)
        except SystemExit as e:  # a model without thinking
            print(e, flush=True)
        r = dict(model=model, case="ctx32k", **call(model, SYSTEM + "  ", ASK, num_ctx=32768)); out.append(r); print(json.dumps(r), flush=True)
        ps = json.load(urllib.request.urlopen("http://127.0.0.1:11434/api/ps", timeout=10))
        for m in ps.get("models", []):
            if m.get("name") == model or m.get("model") == model:
                r = dict(model=model, case="loaded-32k", size_gb=round(m.get("size", 0) / 1e9, 1), context=m.get("context_length"))
                out.append(r); print(json.dumps(r), flush=True)
        call(model, SYSTEM + " ", "hi")  # back to 8k for the parallel test
    if SWEEP or model.startswith("gpt-oss") or "-mtp-" in model:
        for n in (1, 2, 4):
            t = time.time()
            with cf.ThreadPoolExecutor(n) as ex:
                rs = list(ex.map(lambda i: call(model, SYSTEM + " ", LONG + f" (#{i})", num_predict=256), range(n)))
            r = dict(model=model, case=f"parallel-{n}", wall=round(time.time() - t, 2),
                     each=[x["wall"] for x in rs], out_tokens=sum(x["out_tokens"] for x in rs))
            r["tokens_per_s"] = round(r["out_tokens"] / r["wall"], 1); r["fixed_len"] = 256
            out.append(r); print(json.dumps(r), flush=True)
import pathlib
OUT = pathlib.Path(__file__).resolve().parent.parent / "results" / "speed"
OUT.mkdir(parents=True, exist_ok=True)
json.dump(out, open(OUT / ("speed-%s.json" % time.strftime("%Y%m%d-%H%M")), "w"), indent=1)
