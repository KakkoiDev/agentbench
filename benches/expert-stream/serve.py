"""An OpenAI-compatible server for Inkling 2 Mini with its experts streamed from the SSD (2026-11-27), so the benches
and agentbox can run it like any local engine (benches/local_engines.py, engine "stream").

    ~/Code/mlx-lm-inkling2/.venv/bin/python benches/expert-stream/serve.py --port 8097 --model-id inkling-s32 \
        --slots 32 --loop-weighted --policy lfu --prefetch [--lossy sub --sub-alpha 0.8]

- The prompt is rendered as Ollama renders Inkling 2 (model/renderers/inkling2.go: tool declaration, messages, thinking
  effort line); the output frames are parsed as Ollama parses them (model/parsers/inkling2.go): thinking, text, and
  JSON tool calls. Thinking goes out as reasoning_content, tool calls as OpenAI tool_calls, streamed (SSE) or not.
- reasoning_effort: none, minimal, low, medium, high (default, as Ollama), xhigh, max.
- Sampling: the model's own temperature 1.0 over the whole distribution unless the request sets temperature/top_p.
- Prompt cache: an agent re-sends the whole conversation every turn; reading it again from the SSD would take minutes.
  After each prompt the cache is snapshotted (attention offsets, sliding-window and convolution state); a request
  whose tokens start with that prompt goes back to the snapshot and runs only the new part.
- One request at a time (a lock); others wait.
"""
import argparse
import json
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import mlx.core as mx
import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import stream  # noqa: E402
from mlx_lm.models.cache import KVCache, RotatingKVCache  # noqa: E402

EFFORT = {"none": "0", "false": "0", "minimal": "0.1", "low": "0.2", "medium": "0.7", "high": "0.9", "true": "0.9",
          "xhigh": "0.99", "max": "0.99"}
MODEL, TEXT, THOUGHT, TOOL = "<|message_model|>", "<|content_text|>", "<|content_thinking|>", "<|content_invoke_tool_json|>"
TOOL_TEXT, TOOL_ERROR, END, END_SAMPLING = "<|content_invoke_tool_text|>", "<|content_tool_error|>", "<|end_message|>", \
    "<|content_model_end_sampling|>"
PROMPT_CHUNK = 2048


def pyjson(v):
    """Python's json.dumps with compact separators and sorted keys (Ollama's PythonJSONCompact)."""
    return json.dumps(v, separators=(",", ":"), sort_keys=True)


def text_of(content):
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    return "".join(p if isinstance(p, str) else (p.get("text") or "") for p in content
                   if isinstance(p, str) or p.get("type") in (None, "text", "input_text"))


def render(messages, tools, effort):
    """Ollama's Inkling 2 renderer, for OpenAI-style messages (text only)."""
    out = []
    if tools:
        specs = []
        for t in tools:
            fn = t.get("function", t)
            params = dict(fn.get("parameters") or {})
            if not params.get("type"):
                params.pop("type", None)
            if params.get("properties") is None:
                params.pop("properties", None)
            specs.append({"description": fn.get("description") or "", "name": fn["name"], "parameters": params,
                          "type": t.get("type") or "function"})
        out.append("<|message_system|>tool_declare<|content_xml|>" + pyjson(specs) + END)
    effort_line = "<|message_system|><|content_text|>Thinking effort level: " + effort + END
    names = {}
    for m in messages:
        for c in (m.get("tool_calls") or []) if m.get("role") == "assistant" else []:
            if c.get("id"):
                names[c["id"]] = names.get(c["id"], "") + c["function"]["name"]
    emitted = False
    for m in messages:
        role = m["role"]
        if role == "developer":
            role = "system"
        head = {"user": "<|message_user|>", "assistant": MODEL + (m.get("name") or ""), "system": "<|message_system|>",
                "tool": "<|message_tool|>" + (m.get("name") or names.get(m.get("tool_call_id"), ""))}.get(role)
        if head is None:
            raise ValueError(f"unknown role {role}")
        if not emitted and role != "system":
            out.append(effort_line)
            emitted = True
        thinking = m.get("reasoning_content") or m.get("reasoning") or ""
        content = text_of(m.get("content"))
        calls = m.get("tool_calls") or [] if role == "assistant" else []
        if role == "assistant" and thinking:
            out.append(head + THOUGHT + thinking + END)
        if content or not (role == "assistant" and (calls or thinking)):
            out.append(head + TEXT + content + END)
        if role == "assistant":
            for c in calls:
                fn = c["function"]
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    args = json.loads(args) if args.strip() else {}
                out.append(MODEL + fn["name"] + TOOL + '{"name":' + json.dumps(fn["name"]) + ',"args":' + pyjson(args) + "}" + END)
            out.append(END_SAMPLING)
    if not emitted:
        out.append(effort_line)
    return "".join(out)


class Frames:
    """Ollama's Inkling 2 output parser, token by token: thinking, text and tool calls."""

    def __init__(self, tok):
        self.tok = tok
        self.special = {tok.convert_tokens_to_ids(t): t for t in (MODEL, TEXT, THOUGHT, TOOL, TOOL_TEXT, TOOL_ERROR, END, END_SAMPLING)}
        self.mode, self.name, self.ids, self.sent = None, "", [], ""
        self.calls = []

    def _flush(self):
        text = self.tok.decode(self.ids)
        if text.endswith("�"):  # half of a character: wait for the rest
            return ""
        new, self.sent = text[len(self.sent):], text
        return new

    def add(self, token):
        """Returns (kind, text) pieces to stream: kind is 'thinking' or 'content'."""
        out = []
        marker = self.special.get(token)
        if marker is None:
            self.ids.append(token)
            if self.mode in (THOUGHT, TEXT, None):
                piece = self._flush()
                if piece:
                    out.append(("thinking" if self.mode == THOUGHT else "content", piece))
            return out
        body = self.tok.decode(self.ids)
        if self.mode == MODEL:
            self.name = body
        elif self.mode == TOOL:
            try:
                call = json.loads(body)
                self.calls.append({"name": call.get("name") or self.name, "arguments": call.get("args") or {}})
            except json.JSONDecodeError:
                self.calls.append({"name": self.name, "arguments": {}, "error": body[:200]})
        elif self.mode in (THOUGHT, TEXT, None):
            rest = body[len(self.sent):]
            if rest:
                out.append(("thinking" if self.mode == THOUGHT else "content", rest))
        self.ids, self.sent = [], ""
        if marker != TOOL:
            self.name = "" if marker != MODEL else self.name
        self.mode = marker
        return out


class Engine:
    def __init__(self, a):
        stream.LOSSY, stream.SUB_ALPHA, stream.TOPK, stream.SKIP_SHARE = a.lossy, a.sub_alpha, a.topk, a.skip_share
        stream.GUESS_FIRST, stream.AHEAD, stream.LFU_DECAY = a.guess_first, a.ahead, a.lfu_decay
        self.model, layers, self.stats, _, _ = stream.load(a)
        self.tok = stream.tokenizer(layers, Path(__file__).parent / ".tokenizer")
        self.eos = {200006}
        self.cache = None
        self.prompt = []  # tokens whose state is in the snapshot
        self.snap = None
        self.lock = threading.Lock()

    def _snapshot(self):
        snap = []
        for c in self.cache:
            kv, conv = c[0], c[1]
            if isinstance(kv, RotatingKVCache):
                keys = None if kv.keys is None else kv.keys + 0
                values = None if kv.values is None else kv.values + 0
                kvs = ("rot", keys, values, kv.offset, kv._idx)
            else:
                kvs = ("kv", kv.offset)
            snap.append((kvs, list(conv.cache)))
        mx.eval([s[0][1:3] for s in snap if s[0][0] == "rot" and s[0][1] is not None])
        return snap

    def _restore(self):
        for c, (kvs, conv) in zip(self.cache, self.snap):
            kv = c[0]
            if kvs[0] == "rot":
                kv.keys = None if kvs[1] is None else kvs[1] + 0
                kv.values = None if kvs[2] is None else kvs[2] + 0
                kv.offset, kv._idx = kvs[3], kvs[4]
            else:
                kv.offset = kvs[1]
            c[1].cache = list(conv)

    def generate(self, ids, max_tokens, temperature, top_p):
        """Yields token ids. Continues from the snapshot when `ids` starts with the last prompt."""
        n = len(self.prompt)
        if self.cache is not None and self.snap is not None and n and ids[:n] == self.prompt and len(ids) > n:
            self._restore()
            start = n
        else:
            self.cache, start = self.model.make_cache(), 0
        t = time.perf_counter()
        logits = None
        for i in range(start, len(ids), PROMPT_CHUNK):
            logits = self.model(mx.array(ids[i:i + PROMPT_CHUNK])[None], cache=self.cache)[:, -1]
            mx.eval(logits)
        self.prompt_s, self.prompt_new = time.perf_counter() - t, len(ids) - start
        self.prompt, self.snap = list(ids), self._snapshot()
        for _ in range(max_tokens):
            token = self._sample(logits, temperature, top_p)
            yield token
            if token in self.eos:
                return
            logits = self.model(mx.array([[token]]), cache=self.cache)[:, -1]

    @staticmethod
    def _sample(logits, temperature, top_p):
        if temperature <= 0:
            return int(mx.argmax(logits, axis=-1).item())
        logits = logits.astype(mx.float32) / temperature
        if top_p < 1.0:
            probs = mx.softmax(logits, axis=-1)
            order = mx.argsort(-probs, axis=-1)
            sorted_p = mx.take_along_axis(probs, order, axis=-1)
            keep = (mx.cumsum(sorted_p, axis=-1) - sorted_p) < top_p
            masked = mx.where(keep, mx.log(sorted_p), -mx.inf)
            return int(mx.take_along_axis(order, mx.random.categorical(masked)[..., None], axis=-1).item())
        return int(mx.random.categorical(logits).item())


def make_handler(engine, model_id):
    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"  # one thread: close after each answer so an idle client cannot hold the server

        def log_message(self, *args):
            pass

        def _json(self, code, obj):
            body = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            if self.path.rstrip("/").endswith("/models"):
                return self._json(200, {"object": "list", "data": [{"id": model_id, "object": "model", "owned_by": "local"}]})
            self._json(404, {"error": "not found"})

        def do_POST(self):
            if not self.path.rstrip("/").endswith("/chat/completions"):
                return self._json(404, {"error": "not found"})
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            effort = req.get("reasoning_effort")
            if effort is None and req.get("enable_thinking") is False:
                effort = "none"
            effort = EFFORT.get(str(effort if effort is not None else "high").strip().lower())
            if effort is None:
                return self._json(400, {"error": {"message": f"unknown reasoning_effort {req.get('reasoning_effort')}"}})
            try:
                prompt = render(req.get("messages") or [], req.get("tools") or [], effort)
            except Exception as e:
                return self._json(400, {"error": {"message": f"render: {e}"}})
            ids = engine.tok.encode(prompt, add_special_tokens=False)
            max_tokens = int(req.get("max_completion_tokens") or req.get("max_tokens") or 32768)
            temperature = float(req.get("temperature", 1.0) if req.get("temperature") is not None else 1.0)
            top_p = float(req.get("top_p", 1.0) if req.get("top_p") is not None else 1.0)
            streaming = bool(req.get("stream"))
            rid, created = "chatcmpl-" + uuid.uuid4().hex[:24], int(time.time())
            with engine.lock:
                frames = Frames(engine.tok)
                if streaming:
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    self.close_connection = True

                def emit(delta, finish=None, usage=None):
                    chunk = {"id": rid, "object": "chat.completion.chunk", "created": created, "model": model_id,
                             "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
                    if usage is not None:
                        chunk["usage"] = usage
                    self.wfile.write(b"data: " + json.dumps(chunk).encode() + b"\n\n")
                    self.wfile.flush()

                thinking, content, n_out, sent_calls, finish = [], [], 0, 0, "length"
                t0 = time.perf_counter()
                try:
                    if streaming:
                        emit({"role": "assistant", "content": ""})
                    for token in engine.generate(ids, max_tokens, temperature, top_p):
                        n_out += 1
                        if token in engine.eos:
                            finish = "stop"
                            break
                        for kind, piece in frames.add(token):
                            (thinking if kind == "thinking" else content).append(piece)
                            if streaming:
                                emit({"reasoning_content": piece} if kind == "thinking" else {"content": piece})
                        while streaming and sent_calls < len(frames.calls):
                            c = frames.calls[sent_calls]
                            emit({"tool_calls": [{"index": sent_calls, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                                                  "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}]})
                            sent_calls += 1
                    for kind, piece in frames.add(200010):  # close an open frame
                        (thinking if kind == "thinking" else content).append(piece)
                        if streaming:
                            emit({"reasoning_content": piece} if kind == "thinking" else {"content": piece})
                    if frames.calls and finish == "stop":
                        finish = "tool_calls"
                    usage = {"prompt_tokens": len(ids), "completion_tokens": n_out, "total_tokens": len(ids) + n_out}
                    sys.stderr.write(json.dumps({"prompt": len(ids), "prompt_new": engine.prompt_new,
                                                 "prompt_s": round(engine.prompt_s, 1), "out": n_out,
                                                 "decode_tok_s": round(n_out / max(time.perf_counter() - t0 - engine.prompt_s, 1e-6), 2),
                                                 "finish": finish}) + "\n")
                    if streaming:
                        while sent_calls < len(frames.calls):
                            c = frames.calls[sent_calls]
                            emit({"tool_calls": [{"index": sent_calls, "id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                                                  "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}]})
                            sent_calls += 1
                        emit({}, finish=finish, usage=usage)
                        self.wfile.write(b"data: [DONE]\n\n")
                        self.wfile.flush()
                        return
                    msg = {"role": "assistant", "content": "".join(content) or None}
                    if thinking:
                        msg["reasoning_content"] = "".join(thinking)
                    if frames.calls:
                        msg["tool_calls"] = [{"id": f"call_{uuid.uuid4().hex[:12]}", "type": "function",
                                              "function": {"name": c["name"], "arguments": json.dumps(c["arguments"])}}
                                             for c in frames.calls]
                    self._json(200, {"id": rid, "object": "chat.completion", "created": created, "model": model_id,
                                     "choices": [{"index": 0, "message": msg, "finish_reason": finish}], "usage": usage})
                except (BrokenPipeError, ConnectionResetError):
                    engine.snap = None  # the client left mid-answer: the cache holds a half answer, start fresh next time
                    engine.prompt = []
    return Handler


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8097)
    ap.add_argument("--model-id", default="inkling-stream")
    ap.add_argument("--slots", type=int, default=32)
    ap.add_argument("--resident", action="store_true")
    ap.add_argument("--loop-weighted", action="store_true")
    ap.add_argument("--policy", choices=("lru", "lfu"), default="lru")
    ap.add_argument("--prefetch", action="store_true")
    ap.add_argument("--guess-first", action="store_true")
    ap.add_argument("--ahead", type=int, default=1)
    ap.add_argument("--lfu-decay", type=float, default=0.995)
    ap.add_argument("--lossy", choices=("", "skip", "skiplow", "sub", "topk"), default="")
    ap.add_argument("--skip-share", type=float, default=0.1)
    ap.add_argument("--sub-alpha", type=float, default=0.8)
    ap.add_argument("--topk", type=int, default=8)
    ap.add_argument("--workers", type=int, default=16)
    a = ap.parse_args()
    engine = Engine(a)
    # One thread: MLX's GPU streams belong to the thread that loaded the model (a request thread fails with
    # "There is no Stream(cpu, 1) in current thread"); requests are one at a time anyway.
    server = HTTPServer(("127.0.0.1", a.port), make_handler(engine, a.model_id))
    sys.stderr.write(f"serving {a.model_id} on 127.0.0.1:{a.port}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
