"""Check the streamed-Inkling server (serve.py) before benches use it: a tool call, a plain answer, a follow-up turn that
must reuse the prompt cache, and streaming. Run by the bench queue (the server is started by local_engines.serve).
    .venv/bin/python benches/expert-stream/server_check.py stream/inkling-s32
"""
import json
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import local_engines  # noqa: E402

TOOLS = [{"type": "function", "function": {"name": "read", "description": "Read a file and return its text.",
                                            "parameters": {"type": "object", "properties": {"path": {"type": "string"}},
                                                           "required": ["path"]}}}]


def post(port, body, stream=False):
    body = dict(body, stream=stream)
    req = urllib.request.Request(f"http://127.0.0.1:{port}/v1/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    t = time.time()
    with urllib.request.urlopen(req, timeout=1800) as r:
        if not stream:
            return json.loads(r.read()), time.time() - t
        chunks = [json.loads(line[6:]) for line in r.read().decode().splitlines()
                  if line.startswith("data: ") and line != "data: [DONE]"]
        return chunks, time.time() - t


def main():
    model = sys.argv[1]
    stop = local_engines.serve(model)
    port = local_engines.ENGINES["stream"]["port"]
    ok = True
    try:
        msgs = [{"role": "system", "content": "You are a coding agent. Use the tools."},
                {"role": "user", "content": "Read the file notes/todo.md and tell me what is in it."}]
        r, s = post(port, {"model": model, "messages": msgs, "tools": TOOLS, "reasoning_effort": "low", "max_tokens": 2048})
        m = r["choices"][0]["message"]
        calls = m.get("tool_calls") or []
        print("tool turn:", round(s, 1), "s, finish", r["choices"][0]["finish_reason"], "calls", json.dumps(calls)[:300],
              "thinking chars", len(m.get("reasoning_content") or ""))
        ok &= bool(calls) and calls[0]["function"]["name"] == "read" and "todo" in calls[0]["function"]["arguments"]
        msgs += [{"role": "assistant", "content": m.get("content"), "tool_calls": calls},
                 {"role": "tool", "tool_call_id": calls[0]["id"] if calls else "x", "content": "- buy milk\n- fix the bike"}]
        chunks, s = post(port, {"model": model, "messages": msgs, "tools": TOOLS, "reasoning_effort": "low", "max_tokens": 2048},
                         stream=True)
        text = "".join(c["choices"][0]["delta"].get("content") or "" for c in chunks if c.get("choices"))
        think = "".join(c["choices"][0]["delta"].get("reasoning_content") or "" for c in chunks if c.get("choices"))
        print("follow-up (streamed):", round(s, 1), "s, answer:", repr(text[:200]), "thinking chars", len(think))
        ok &= "milk" in text.lower()
        r, s = post(port, {"model": model, "messages": [{"role": "user", "content": "What is 17 * 23? Answer with the number."}],
                           "reasoning_effort": "none", "max_tokens": 256})
        ans = r["choices"][0]["message"].get("content") or ""
        print("plain, thinking none:", round(s, 1), "s, answer:", repr(ans[:100]))
        ok &= "391" in ans
    finally:
        stop()
    log = local_engines.LOGS / f"{model.replace('/', '-')}.log"
    print("server log (prompt reuse, speeds):")
    print("\n".join(l for l in log.read_text().splitlines()[-6:]))
    print("CHECK", "OK" if ok else "FAILED")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
