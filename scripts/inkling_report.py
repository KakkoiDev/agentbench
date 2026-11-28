"""Inkling 2 Mini report page (2026-11-27): MLX vs GGUF vs Qwen 3.8 27B, and Inkling with its experts streamed from the
SSD. Every number is read from the bench results; none is typed here.

    .venv/bin/python scripts/inkling_report.py --results ~/Code/colibri-poc/results
writes site/reports/inkling.html and site/data/reports/inkling.json.
"""
import argparse
import glob
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from site_build import E, REPO, shell, table  # noqa: E402
import compare_runs as CR  # noqa: E402

SETUPS = {  # what was run, and where its results are
    "mlx": dict(name="Inkling MLX", detail="inkling-2-mini:26b-a5b-nvfp4, Ollama's MLX runner",
                ollama="inkling-2-mini:26b-a5b-nvfp4", speed="bench-logs/smoke-000-inkling-2-mini-26b-a5b-nvfp4-speed.log",
                coding="agentic-v4-inkling-2-mini-26b-a5b-nvfp4-smoke-agentbox-all",
                agents="agentic-v1-inkling-2-mini-26b-a5b-nvfp4-iso11500-{}-20261127T020057",
                expert="dropbox-family-expert-inkling-2-mini-26b-a5b-nvfp4-expert-smoke-inkling-2-mini-26b-a5b-nvfp4",
                mini_en="dropbox-mini-en-smoke-inkling-2-mini-26b-a5b-nvfp4",
                mini_lang="dropbox-mini-lang-smoke-inkling-2-mini-26b-a5b-nvfp4"),
    "gguf": dict(name="Inkling GGUF", detail="inkling-2-mini:26b-a5b-iq4_NL, llama.cpp runner, takes requests at once",
                 ollama="inkling-2-mini:26b-a5b-iq4_NL", speed="bench-logs/smoke-016-inkling-2-mini-26b-a5b-iq4-nl-speed.log",
                 coding="agentic-v4-inkling-2-mini-26b-a5b-iq4-nl-smoke-agentbox-all",
                 agents="agentic-v1-inkling-2-mini-26b-a5b-iq4_NL-iso11500-{}-20261127T031653",
                 expert="dropbox-family-expert-inkling-2-mini-26b-a5b-iq4-nl-expert-smoke-inkling-2-mini-26b-a5b-iq4-nl",
                 mini_en="dropbox-mini-en-smoke-inkling-2-mini-26b-a5b-iq4-nl",
                 mini_lang="dropbox-mini-lang-smoke-inkling-2-mini-26b-a5b-iq4-nl"),
    "qwen": dict(name="Qwen 3.8 27B MLX", detail="qwen3.8:27b-mlx, Ollama's MLX runner",
                 ollama="qwen3.8:27b-mlx", speed="bench-logs/055-qwen3-8-27b-mlx-speed-mode.log",
                 coding="agentic-v4-qwen3-8-27b-mlx-best-agentbox-r1", agents=None,
                 expert="dropbox-family-expert-qwen3-8-27b-mlx-expert-checks33-r1-qwen3-8-27b-mlx",
                 mini_en="dropbox-mini-checks33-r1-qwen3-8-27b-mlx", mini_lang="dropbox-mini-lang-checks33-r1-qwen3-8-27b-mlx"),
}
CONCURRENCY_LOGS = ["bench-logs/smoke-013-inkling-2-mini-26b-a5b-iq4-nl-concurrency-1-2-4-v3.log",
                    "bench-logs/smoke-016-inkling-2-mini-26b-a5b-iq4-nl-concurrency-1-2-4-others.log"]
TASKS = {"busy-intervals": "Overlap counting for 200,000 intervals", "calc-evaluator": "Expression evaluator, exact errors",
         "ics-recurrence": "iCalendar recurrence across DST", "json-patch": "JSON Patch (RFC 6902)",
         "money-multicurrency": "Multi-currency money, 4 files"}


def jlines(path):
    out = []
    for line in Path(path).read_text(errors="replace").splitlines():
        if line.startswith("{"):
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return out


def collect(res):
    data = {}
    conc = {}
    for log in CONCURRENCY_LOGS:
        for line in (res / log).read_text(errors="replace").splitlines():
            m = re.match(r"(\S+): (\d+) concurrent -> ([\d.]+) tok/s aggregate, latency p50 ([\d.]+)s max ([\d.]+)s, "
                         r"TTFT p50 ([\d.]+)s", line)
            if m:
                conc.setdefault(m[1], {})[int(m[2])] = dict(tok_s=float(m[3]), p50_s=float(m[4]), max_s=float(m[5]),
                                                            ttft_s=float(m[6]))
    for key, s in SETUPS.items():
        d = dict(name=s["name"], detail=s["detail"])
        sp = {r["case"]: r for r in jlines(res / s["speed"])}
        d["mem_gb"] = sp["loaded"]["size_gb"]
        d["mem32k_gb"] = sp["loaded-32k"]["size_gb"]
        d["prompt_tokens"] = sp["first"]["prompt_tokens"]
        d["prompt_s"] = sp["first"]["prompt_s"]
        d["parallel"] = {n: sp[f"parallel-{n}"]["tokens_per_s"] for n in (1, 2, 4)}
        d["concurrency"] = conc.get(s["ollama"])  # None: measured with the speed sweep only
        rs = [json.load(open(f)) for f in sorted(glob.glob(str(res / s["coding"] / "*" / "result.json")))]
        d["coding"] = dict(solved=sum(r["solved"] for r in rs), tasks=len(rs), hidden=sum(r["hidden_passed"] for r in rs),
                           hidden_total=sum(r["hidden_total"] for r in rs), minutes=round(sum(r["wall_s"] for r in rs) / 60, 1),
                           per_task={r["task"]: dict(solved=r["solved"], hidden=r["hidden_passed"],
                                                     total=r["hidden_total"], minutes=round(r["wall_s"] / 60, 1),
                                                     timed_out=r.get("timed_out")) for r in rs})
        if s["agents"]:
            ag = {}
            for mode in ("concurrent", "sequential"):
                rs = [json.load(open(f)) for f in glob.glob(str(res / s["agents"].format(mode) / "*" / "result.json"))]
                walls = [r["wall_s"] for r in rs]
                ag[mode] = dict(solved=sum(r["solved"] for r in rs), tasks=len(rs),
                                minutes=round((max(walls) if mode == "concurrent" else sum(walls)) / 60, 1))
            d["agents"] = ag
        for k in ("expert", "mini_en", "mini_lang"):
            sm = json.load(open(res / s[k] / "results.json"))["summary"]
            d[k] = dict(passed=sm["passed"], total=sm["total"])
        data[key] = d
    stream = {}
    for name in ("v1", "v2", "v3", "exact", "mtp"):
        files = {"v1": sorted(glob.glob(str(res / "expert-stream-inkling-*.jsonl"))),
                 "v2": [str(res / "expert-stream-v2.jsonl")], "v3": [str(res / "expert-stream-v3.jsonl")],
                 "exact": [str(res / "expert-stream-exact.jsonl")], "mtp": [str(res / "expert-stream-mtp.jsonl")]}[name]
        rows = []
        for f in files:
            if Path(f).exists():
                rows += jlines(f)
        stream[name] = rows
    for name in ("best", "lossy", "lossy2", "prefill"):
        f = res / f"expert-stream-{name}.jsonl"
        stream[name] = jlines(f) if f.exists() else []
    data["stream"] = stream

    def runs(pat):
        return sorted(x for x in glob.glob(str(res / pat)) if Path(x).is_dir() and (Path(x) / "results.json").exists())

    def minutes(dirs):
        return [round(sum(float(r.get("seconds", 0)) for r in json.load(open(Path(d) / "results.json"))["rows"]) / 60)
                for d in dirs]

    def cmp(a_pat, b_pat):
        a, b = runs(a_pat), runs(b_pat)
        if not a or not b:
            return None
        r = CR.compare(a, b)
        r.update(a_minutes=minutes(a), b_minutes=minutes(b))
        return r

    # The streamed servers' own per-request lines: decode and prompt speed, output tokens, share of time on prompts.
    eng = {}
    for m in ("s32", "lossy", "s32-p2"):
        f = res / "engine-logs" / f"stream-inkling-{m}.log"
        rs = [r for r in jlines(f) if "prompt_new" in r] if f.exists() else []
        if rs:
            dec_s = sum(r["out"] / r["decode_tok_s"] for r in rs if r["decode_tok_s"])
            pre_s = sum(r["prompt_s"] for r in rs)
            eng[m] = dict(calls=len(rs), out=sum(r["out"] for r in rs), decode_tok_s=round(sum(r["out"] for r in rs) / dec_s, 1),
                          prompt_tok_s=round(sum(r["prompt_new"] for r in rs) / pre_s, 1),
                          prompt_share=round(pre_s / (pre_s + dec_s), 2), out_per_call=round(sum(r["out"] for r in rs) / len(rs)))
    data["engine"] = eng
    # Real benches, paired by item (scripts/compare_runs.py): exact against lossy, and the faster prompt reading.
    benches = {"mini_en": ("dropbox-mini-en-stream2-r*-stream-inkling-{}", "Assistant (mini), English"),
               "mini_lang": ("dropbox-mini-lang-stream2-r*-stream-inkling-{}", "Assistant (mini), Japanese and French"),
               "expert": ("dropbox-family-expert-stream-inkling-{0}-expert-stream2-r*-stream-inkling-{0}", "Expert checks (3 jobs)")}
    data["paired"] = {
        "lossy": {k: dict(label=lab, **(cmp(pat.format("s32"), pat.format("lossy")) or {})) for k, (pat, lab) in benches.items()},
        "prefill": {k: dict(label=lab, **(cmp(pat.format("s32"), pat.format("s32-p2")) or {})) for k, (pat, lab) in benches.items()},
        # earlier single-run claims, now with error bars (hidden tests per task)
        "coding_mlx_gguf": CR.compare([str(res / SETUPS["gguf"]["coding"])], [str(res / SETUPS["mlx"]["coding"])]),
        "coding_qwen_mlx": CR.compare([str(res / SETUPS["mlx"]["coding"])], [str(res / SETUPS["qwen"]["coding"])]),
    }
    return data


def f1(x):
    return f"{x:.1f}" if isinstance(x, float) else str(x)


def best(data, keys, fn, low=False):
    vals = {k: fn(data[k]) for k in keys}
    target = (min if low else max)(vals.values())
    return {k for k, v in vals.items() if v == target}


def comparison(data):
    keys = list(SETUPS)

    def row(label, fn, fmt, low=False, keys_=keys):
        b = best(data, keys_, fn, low)
        return [E(label)] + [(f"<strong>{fmt(data[k])}</strong>" if k in b else fmt(data[k])) for k in keys]

    def conc(n):
        def fn(d):
            return d["concurrency"][n]["tok_s"] if d["concurrency"] else d["parallel"][n]
        return fn

    def conc_fmt(n):
        def fmt(d):
            if d["concurrency"]:
                return f'{f1(d["concurrency"][n]["tok_s"])} tok/s'
            return f'{f1(d["parallel"][n])} tok/s' + (' <span class="note">(one after another)</span>' if n > 1 else "")
        return fmt

    def agents(d):
        a = d.get("agents")
        if not a:
            return "not measured"
        c, s = a["concurrent"], a["sequential"]
        return f'{f1(c["minutes"])} vs {f1(s["minutes"])} min ({c["solved"]}/{c["tasks"]} and {s["solved"]}/{s["tasks"]} solved)'

    rows = [
        row("Memory when loaded, 8k / 32k context", lambda d: d["mem_gb"],
            lambda d: f'{f1(d["mem_gb"])} / {f1(d["mem32k_gb"])} GB', low=True),
        row(f'Reading a new {data["mlx"]["prompt_tokens"]:,}-token prompt', lambda d: d["prompt_s"],
            lambda d: f'{f1(d["prompt_s"])} s', low=True),
        row("Output speed, 1 request", conc(1), conc_fmt(1)),
        row("2 requests at once (total)", conc(2), conc_fmt(2)),
        row("4 requests at once (total)", conc(4), conc_fmt(4)),
        [E("3 coding agents at once vs one after another")] + [agents(data[k]) for k in keys],
        row("Hard coding (5 tasks, agentbox): hidden tests", lambda d: d["coding"]["hidden"],
            lambda d: f'{d["coding"]["solved"]}/{d["coding"]["tasks"]} solved, {d["coding"]["hidden"]}/{d["coding"]["hidden_total"]} tests, {f1(d["coding"]["minutes"])} min'),
        [E("Expert checks")] + [f'{data[k]["expert"]["passed"]}/{data[k]["expert"]["total"]}' for k in keys],
        row("Assistant (mini), English", lambda d: d["mini_en"]["passed"] / d["mini_en"]["total"],
            lambda d: f'{d["mini_en"]["passed"]}/{d["mini_en"]["total"]}'),
        row("Assistant (mini), Japanese and French", lambda d: d["mini_lang"]["passed"] / d["mini_lang"]["total"],
            lambda d: f'{d["mini_lang"]["passed"]}/{d["mini_lang"]["total"]}'),
    ]
    head = [""] + [data[k]["name"] for k in keys]
    return table(head, rows)


def concurrency_table(data):
    rows = []
    for k in ("mlx", "gguf"):
        c = data[k]["concurrency"]
        for n in (1, 2, 4):
            rows.append([E(data[k]["name"]) if n == 1 else "", str(n), f1(c[n]["tok_s"]), f1(c[n]["p50_s"]),
                         f1(c[n]["max_s"]), f1(c[n]["ttft_s"])])
    q = data["qwen"]["parallel"]
    for n in (1, 2, 4):
        rows.append([E(data["qwen"]["name"]) if n == 1 else "", str(n), f1(q[n]), "", "", ""])
    return table(["Setup", "Requests at once", "Total tok/s", "Answer time p50 (s)", "Slowest (s)", "First token p50 (s)"],
                 rows, num_cols=(1, 2, 3, 4, 5))


def coding_table(data):
    rows = []
    for t, title in TASKS.items():
        cells = [E(title)]
        for k in SETUPS:
            r = data[k]["coding"]["per_task"].get(t)
            if not r:
                cells.append("")
                continue
            mark = "solved" if r["solved"] else ("hit the 900 s limit" if r["timed_out"] else "not solved")
            cells.append(f'{r["hidden"]}/{r["total"]} <span class="note">{mark}, {f1(r["minutes"])} min</span>')
        rows.append(cells)
    return table(["Task"] + [data[k]["name"] for k in SETUPS], rows)


def stream_rows(rows, with_ref=False):
    out = []
    for r in rows:
        setup = "all in memory" if r.get("resident") else (
            "all 128, streaming path" if r["slots"] == 128 else f'{r["slots"]} per layer')
        if r.get("loop_weighted"):
            setup += ", looped layers weighted"
        if r.get("policy") == "lfu":
            setup += ", LFU"
        if r.get("prefetch"):
            setup += f', read-ahead ({round(r["prefetch_used_share"] * 100)}% of guesses used)' if r.get("prefetch_used_share") else ", read-ahead"
        cells = [E(setup), f1(r["peak_gb"]), f1(r["decode_tok_s_second_half"]), f1(r["decode_tok_s"]),
                 f1(r["prefill_tok_s"]), f'{round(r["hit_rate"] * 100)}%' if not r.get("resident") else "",
                 f1(r["mb_read_per_token"]) if not r.get("resident") else ""]
        if with_ref:
            cells.append("" if r.get("resident") else (
                "same as reference" if r.get("ref_first_argmax_mismatch") is None
                else f'differs from step {r["ref_first_argmax_mismatch"]} (max logit diff {f1(r.get("ref_max_logit_diff"))})'))
            cells.append(" / ".join(f1(r.get(k)) for k in ("ms_read", "ms_router_wait", "ms_cache_write"))
                         if not r.get("resident") else "")
        out.append(cells)
    head = ["Experts kept", "Peak GB", "Decode tok/s (warm)", "Decode tok/s (all)", "Prompt tok/s", "Cache hits",
            "MB read per token"]
    if with_ref:
        head += ["Next-token picks vs in-memory run", "ms per token: read / router wait / cache write"]
    return table(head, out, num_cols=(1, 2, 3, 4, 5, 6))


def pts(x):
    return f"{x * 100:+.1f}"


def diff_cell(c):
    sc = c.get("score")
    if not sc:
        return "", "", ""
    ci = f'{pts(sc["diff"])} &plusmn; {sc["se"] * 100:.1f} <span class="note">(95% CI {pts(sc["ci95"][0])} to {pts(sc["ci95"][1])}; '
    ci += ("significant" if sc["significant"] else "not significant") + ")</span>"
    return f'{sc["a"] * 100:.1f}%', f'{sc["b"] * 100:.1f}%', ci


def paired_table(rows, a_name, b_name):
    out = []
    for c in rows.values():
        if not c.get("score"):
            continue
        a, b, d = diff_cell(c)
        out.append([E(c["label"]), a, b, d, f'{c["a_runs"]} / {c["b_runs"]}',
                    " + ".join(map(str, c["a_minutes"])), " + ".join(map(str, c["b_minutes"]))])
    return table(["Bench", a_name, b_name, f"{b_name} minus {a_name}, points", "Runs",
                  f"{a_name}: minutes per run", f"{b_name}: minutes per run"], out, num_cols=(1, 2, 4))


LOSSY_NAMES = {"skip": "skip a missing expert, fetch it in the background",
               "skiplow": "skip missing experts under {:.0%} of the router weight, no fetch",
               "sub": "substitute a cached expert scoring at least {:.0%} of the missing one",
               "topk": "only the top {} of 8 experts"}


def lossy_rows(rows, exact):
    out = []
    for r in sorted(rows, key=lambda r: (r["slots"], r["lossy"], -(r.get("agreement") or 0))):
        name = LOSSY_NAMES[r["lossy"]]
        arg = {"skiplow": r.get("skip_share"), "sub": r.get("sub_alpha"), "topk": r.get("topk")}.get(r["lossy"])
        name = name.format(arg) if arg is not None else name
        ex = exact.get(r["slots"])
        out.append([E(name), str(r["slots"]), f1(r["decode_tok_s_second_half"]), f1(ex) if ex else "",
                    f'{r["agreement"] * 100:.1f}%', f'{r["kl_mean"]:.3f}',
                    f'{round(r["dropped_share"] * 100)}%' if r.get("dropped_share") is not None else ""])
    return table(["Lossy mode", "Experts kept", "Decode tok/s", "Exact, same cache", "Same next token as exact",
                  "KL vs exact", "Experts dropped"], out, num_cols=(1, 2, 3, 4, 5, 6))


def prefill_rows(rows):
    out = []
    for r in rows:
        if r.get("resident"):
            continue
        mode = {0: "before: through the cache, in groups", 1: "full-layer buffer",
                2: "full-layer buffer, next layer read ahead"}[r.get("prefill_scratch", 0)]
        d = r.get("ref_max_logit_diff")
        out.append([f'{r["slots"]} per layer', E(mode), f1(r["prefill_tok_s"]), f1(r["prefill_s"]),
                    f1(r["prefill_gb_read"]), f1(r["peak_gb"]), "0 (identical)" if d == 0 else f1(d)])
    return table(["Experts kept", "Prompt reading", "Prompt tok/s", "Seconds", "GB read", "Peak GB",
                  "Largest logit difference vs in-memory"], out, num_cols=(2, 3, 4, 5, 6))


def prefill_engine(e):
    if "s32" not in e or "s32-p2" not in e:
        return ""
    a, b = e["s32"], e["s32-p2"]
    return (f'<p class="note">In the assistant benches (pi with mini, 32 experts kept, exact both), from the server\'s own '
            f'request lines: prompt speed {f1(a["prompt_tok_s"])} before, {f1(b["prompt_tok_s"])} tok/s after (most new '
            f'chunks there are a few hundred tokens, which still read whole layers); prompt reading went from '
            f'{round(a["prompt_share"] * 100)}% to {round(b["prompt_share"] * 100)}% of the model\'s time, so decoding '
            f'(about {f1(b["decode_tok_s"])} tok/s) is now the limit. Paired by item, with error bars:</p>')


def lossy_engine(e):
    if "s32" not in e or "lossy" not in e:
        return ""
    a, b = e["s32"], e["lossy"]
    return (f'<p class="note">Lossy matched exact on the assistant cases but lost the medical schedule job (out of time '
            f'in both runs). Over all of each server\'s requests it was also slower: decode {f1(b["decode_tok_s"])} against {f1(a["decode_tok_s"])} tok/s, '
            f'prompts {f1(b["prompt_tok_s"])} against {f1(a["prompt_tok_s"])}, the smaller cache costing more than the '
            f'skipped reads saved. With 3 expert jobs the gap is within the error bar, but nothing argues for lossy '
            f'here.</p>')


def mtp_rows(rows):
    out = []
    for r in rows:
        setup = "all in memory" if r.get("resident") else f'{r["slots"]} per layer'
        out.append([E(setup), str(r["draft"]), f1(r["decode_tok_s"]), f1(r["tokens_per_round"]),
                    f'{round(r["accept_rate"] * 100)}%', f'{round(r["rerun_share"] * 100)}%', f1(r["peak_gb"]),
                    f1(r["mb_read_per_token"]),
                    "same" if r.get("ref_first_token_mismatch") is None else f'differs from token {r["ref_first_token_mismatch"]}'])
    return table(["Experts kept", "Draft tokens", "Decode tok/s", "Tokens per round", "Drafts accepted",
                  "Rounds needing a re-run", "Peak GB", "MB read per token", "Text vs plain greedy"], out,
                 num_cols=(1, 2, 3, 4, 5, 6, 7))


def mtp_verdict(s):
    """Best plain streamed speed vs best draft-head speed, per cache size, from the rows."""
    plain = {}
    for k in ("v1", "v2", "v3"):
        for r in s[k]:
            if not r.get("resident") and r["slots"] < 128:
                plain[r["slots"]] = max(plain.get(r["slots"], 0), r["decode_tok_s_second_half"])
    mtp, mb = {}, {}
    for r in s["mtp"]:
        if not r.get("resident") and r["decode_tok_s"] > mtp.get(r["slots"], 0):
            mtp[r["slots"]], mb[r["slots"]] = r["decode_tok_s"], r["mb_read_per_token"]
    both = sorted(set(plain) & set(mtp))
    if not both:
        return ""
    parts = "; ".join(f"{n} per layer: {f1(mtp[n])} tok/s with drafts against {f1(plain[n])} without" for n in both)
    return (f'<p>With experts on the SSD the draft head did not pay off here: {parts}. Several tokens checked together '
            f'need more different experts per layer than one token (consecutive tokens share few), so they overflow a '
            f'small cache; rejected drafts are run again (this port has no cheaper rollback); and checking several '
            f'tokens costs this port more than one even in memory. Drafted text also drifts from plain greedy after '
            f'some 60 tokens, in memory too; most likely the multi-token pass rounding differently from one token at a time (not yet checked).</p>')


def page(data):
    m, g, q = data["mlx"], data["gguf"], data["qwen"]
    s = data["stream"]
    v1 = sorted(s["v1"], key=lambda r: (not r.get("resident"), -r["slots"]))
    resident = next((r for r in v1 if r.get("resident")), None)
    mc, gc = m["concurrency"], g["concurrency"]
    ma, ga = m["agents"], g["agents"]
    bullets = [
        f'<li><strong>One user at a time: Inkling MLX.</strong> It reads a new {m["prompt_tokens"]:,}-token prompt in '
        f'{f1(m["prompt_s"])} s (GGUF {f1(g["prompt_s"])} s, Qwen {f1(q["prompt_s"])} s) and writes about '
        f'{f1(mc[1]["tok_s"])} tok/s.</li>',
        f'<li><strong>Several agents or requests at once: Inkling GGUF.</strong> With 4 requests it reaches '
        f'{f1(gc[4]["tok_s"])} tok/s in total (MLX {f1(mc[4]["tok_s"])}); three coding agents together took '
        f'{f1(ga["concurrent"]["minutes"])} min instead of {f1(ga["sequential"]["minutes"])} one after another, '
        f'while on MLX together took {f1(ma["concurrent"]["minutes"])} min against {f1(ma["sequential"]["minutes"])}.</li>',
        f'<li><strong>Hardest coding: Qwen 3.8 27B</strong> passed {q["coding"]["hidden"]}/{q["coding"]["hidden_total"]} '
        f'hidden tests against Inkling MLX {m["coding"]["hidden"]} and GGUF {g["coding"]["hidden"]}, but its output is '
        f'the slowest and it serves one request at a time.</li>',
        f'<li><strong>But none of these coding gaps is significant yet.</strong> With error bars over the 5 tasks, '
        f'Qwen against Inkling MLX is {pts(data["paired"]["coding_qwen_mlx"]["score"]["diff"])} &plusmn; '
        f'{data["paired"]["coding_qwen_mlx"]["score"]["se"] * 100:.0f} points of hidden tests, and Inkling MLX against '
        f'GGUF {pts(data["paired"]["coding_mlx_gguf"]["score"]["diff"])} &plusmn; '
        f'{data["paired"]["coding_mlx_gguf"]["score"]["se"] * 100:.0f}: single runs on 5 tasks cannot tell them apart.</li>',
        '<li><strong>Inkling loses its hard coding tasks to overthinking</strong>: single thinking turns of 60k to 100k '
        'characters with no action, until the time limit. An overthink bench is testing fixes (results to come).</li>',
    ]
    streamed = [r for k in ("v1", "v2", "v3") for r in s[k] if not r.get("resident") and r["slots"] < 128]
    if resident and streamed:
        by = {}
        for r in streamed:
            if r["slots"] not in by or r["decode_tok_s_second_half"] > by[r["slots"]]["decode_tok_s_second_half"]:
                by[r["slots"]] = r
        parts = "; ".join(f'{n} per layer: {f1(by[n]["decode_tok_s_second_half"])} tok/s at {f1(by[n]["peak_gb"])} GB'
                          for n in sorted(by))
        bullets.append(
            f'<li><strong>Inkling fits a small Mac by streaming its experts from the SSD.</strong> Best decode speed '
            f'by experts kept: {parts}. With everything in memory the same code writes '
            f'{f1(resident["decode_tok_s_second_half"])} tok/s at {f1(resident["peak_gb"])} GB.</li>')
    pf = [r for r in s["prefill"] if not r.get("resident")]
    p2 = data["paired"]["prefill"]["expert"]
    if pf and p2.get("score"):
        old = {r["slots"]: r["prefill_tok_s"] for r in pf if r.get("prefill_scratch", 0) == 0}
        new = {r["slots"]: r["prefill_tok_s"] for r in pf if r.get("prefill_scratch") == 2}
        bullets.append(
            f'<li><strong>Streamed prompts are now read {min(new[n] / old[n] for n in old if n in new):.0f} to '
            f'{max(new[n] / old[n] for n in old if n in new):.0f} times faster, exactly.</strong> A 2,048-token prompt: '
            + "; ".join(f"{n} per layer {f1(old[n])} to {f1(new[n])} tok/s" for n in sorted(set(old) & set(new)))
            + f'. In the assistant benches that cuts the time per task (expert jobs: '
            f'{" + ".join(map(str, p2["a_minutes"]))} min per run before, {" + ".join(map(str, p2["b_minutes"]))} after) '
            f'with the same scores.</li>')
    lo = data["paired"]["lossy"]["expert"]
    e = data["engine"]
    if lo.get("score") and "lossy" in e and "s32" in e:
        bullets.append(
            f'<li><strong>Exact beats lossy.</strong> Skipping low-weight experts looked faster in a speed test, but on '
            f'the expert jobs it scored {lo["score"]["b"] * 100:.0f}% against {lo["score"]["a"] * 100:.0f}% for exact, '
            f'ran out of time on one job in both runs, and was slower in use: decode {f1(e["lossy"]["decode_tok_s"])} '
            f'against {f1(e["s32"]["decode_tok_s"])} tok/s for exact with 32 experts kept.</li>')
    body = [f"""
<nav class="crumb note" aria-label="Breadcrumb"><a href="../learnings.html">Learnings</a> / Reports / Inkling 2 Mini</nav>
<div class="ptitle"><h1>Inkling 2 Mini: MLX, GGUF or Qwen, and streaming its experts</h1>
<p class="dek">Which Inkling build to run on a 32 GB Mac mini M6, how it compares with Qwen 3.8 27B, and how fast it
still is when most of it stays on the SSD. Measured November 2026.</p></div>
<section class="sec" aria-labelledby="short"><div class="sec-h"><h2 id="short">In short</h2></div><ul class="list">{"".join(bullets)}</ul></section>
<section class="sec" aria-labelledby="cmp"><div class="sec-h"><h2 id="cmp">Side by side</h2></div>
<p class="note">Bold: best in the row. {E(m["detail"])}; {E(g["detail"])}; {E(q["detail"])}. Expert checks: the Qwen run
used a larger check set, so its score is not comparable with Inkling's.</p>{comparison(data)}</section>
<section class="sec" aria-labelledby="conc"><div class="sec-h"><h2 id="conc">Requests at once</h2></div>
<p class="note">Fixed-length answers sent together; total output speed, answer times and time to the first token.
Ollama's MLX runner takes several requests but gains little; Qwen's runs them one after another (speed sweep only).</p>
{concurrency_table(data)}</section>
<section class="sec" aria-labelledby="coding"><div class="sec-h"><h2 id="coding">Hard coding, task by task</h2></div>
<p class="note">Suite v4, pi with agentbox, thinking high, 900 s per task: hidden tests passed per task.</p>{coding_table(data)}</section>
<section class="sec" aria-labelledby="stream"><div class="sec-h"><h2 id="stream">Streaming the experts from the SSD</h2></div>
<p>Inkling 2 Mini is a mixture of experts: each layer has 128 experts and uses 8 per token. The experts are most of
the model; the rest (attention, routers, embeddings) is about {f1(resident["dense_gb"]) if resident else "3"} GB.
Keeping only that rest and a small cache of experts in memory, and reading the others from the SSD when the router
picks them, lets the model run on a Mac with much less memory. Its 12 middle layers run three times per token, so a
token needs experts for 48 layer passes.</p>
<p class="note">Setup: the open mlx-lm Inkling 2 port, Ollama's model files read directly, F_NOCACHE so the macOS file
cache cannot hide the SSD; 57-token prompt, 256 tokens greedy. Speed only, no answer is scored.
Code: <a href="{REPO}benches/expert-stream/stream.py">stream.py</a>.</p>
<h3>First runs (least recently used eviction)</h3>{stream_rows(v1) if v1 else '<p class="note">No runs yet.</p>'}
"""]
    if s["v2"]:
        body.append(f"""<h3>Improved runs</h3><p class="note">Every run is fed the in-memory run's 256 tokens, so all
decode the same text and their next-token picks can be compared with it. "Looped layers weighted" gives the 12 layers
that run three times per token more cache for the same memory; LFU evicts the expert used least often.</p>
{stream_rows(s["v2"], with_ref=True)}""")
    fixed = [r for r in s["exact"] if not r.get("resident") and r["slots"] < 128 and r.get("ref_prompt_logit_diff") is not None]
    if s["v2"]:
        last = fixed[-1] if fixed else None
        body.append(f"""<p class="note">Why the smaller caches above differ from the in-memory run: a prompt chunk that
needs more experts than the cache holds is computed in groups, and the GPU's sorted expert kernel rounds slightly
differently when a group's unused rows pile onto one slot (up to 0.006 per layer in a one-layer test, which grows to
0.8 on the logits over 48 layer passes). Decoding itself was exact: fed one token at a time, 8 slots gave the same
logits as the in-memory run. Fixed by computing multi-group chunks unsorted{
f'; after the fix an {last["slots"]}-slot run matches the in-memory run exactly (largest logit difference {last["ref_max_logit_diff"]})' if last and last.get("ref_max_logit_diff") == 0 else ''}.
The speed numbers are not affected.</p>""")
    if s["v3"]:
        body.append(f"""<h3>Reading ahead</h3><p class="note">While a layer runs on the GPU, the next layer's router is
applied to the same hidden state to guess its experts, and the guessed ones are read from the SSD in the background.
Guesses that turn out wrong cost extra reads (counted in MB read per token).</p>{stream_rows(s["v3"], with_ref=True)}""")
    if s["mtp"]:
        body.append(f"""<h3>With the draft head</h3><p class="note">Inkling ships a draft head (a 3-layer block drafter
with a Markov head) that proposes up to 7 tokens; the model checks them in one pass, so each expert read serves several
tokens. Drafter ported from <a href="https://github.com/youssofal/mtplx">MTPLX</a> 2.13.3 (Apache 2.0). Code:
<a href="{REPO}benches/expert-stream/mtp.py">mtp.py</a>.</p>{mtp_rows(s["mtp"])}{mtp_verdict(s)}""")
    if s["best"]:
        body.append(f"""<h3>All exact improvements together</h3><p class="note">LFU with looped layers weighted, this
layer's own misses read first and the next layer's guesses after them, no extra copies; 512 tokens, 2 runs each. The
text is identical to the in-memory run's.</p>{stream_rows(s["best"], with_ref=True)}""")
    if s["prefill"]:
        body.append(f"""<h3>Reading the prompt</h3><p>Decoding needs 8 experts per layer, but a 2,048-token prompt chunk
needs nearly all 128. Before, the chunk went through the cache in groups of experts that fit, and each group ran the
layer on every row with the others masked: 8 times the work at 16 slots, unsorted, and a wait after each group. Now the
chunk's experts are read straight into one full-size buffer at their own rows (into the GPU's memory, no copy) and the
layer runs once with the router's own expert ids, the same call as the in-memory model. It reads every expert once per
layer pass (about 26 GB for a 2,048-token chunk), so it is limited by the SSD, not by the GPU.</p>
<p class="note">A 2,048-token prompt, then 64 tokens fed from the in-memory run. The old way was not exact on long
prompts either (the same next tokens, but logits off by up to 10.8); the new way is bit-identical.</p>{prefill_rows(s["prefill"])}
{prefill_engine(data["engine"])}
{paired_table(data["paired"]["prefill"], "Before", "After")}""")
    if s["lossy"] or s["lossy2"]:
        exact = {}
        for r in s["best"]:
            exact[r["slots"]] = max(exact.get(r["slots"], 0), r["decode_tok_s_second_half"])
        body.append(f"""<h3>Exact or lossy</h3><p>Exact streaming gives the in-memory model's answers, bit for bit. Lossy
modes change the answers to read less: they leave out or replace experts that are not in the cache. First measured
against the exact model's 256 tokens (the same text fed to both): how often it picks the same next token, and the KL
divergence of its next-token distribution.</p>{lossy_rows(s["lossy"] + s["lossy2"], exact)}
<p class="note">Only skip-low at 15% was clearly faster than exact at the same cache size, at 94% agreement. Against
exact with 32 experts kept (2 GB more memory), and on the real benches (3 runs of each mini suite, 2 of the expert
jobs, alternating the two setups, 2,400 s per task):</p>
{paired_table(data["paired"]["lossy"], "Exact, 32 kept", "Skip-low 15%, 16 kept")}
{lossy_engine(data["engine"])}""")
    body.append(f"""</section>
<section class="sec" aria-labelledby="how"><div class="sec-h"><h2 id="how">How it was measured</h2></div><ul class="list">
<li>One 32 GB Mac mini M6, one job at a time, Ollama 0.40.18.</li>
<li>Speed: a 4.7k-token prompt, then fixed 256-token answers for requests at once.</li>
<li>Coding: <a href="{REPO}benches/agentic-v4">agentic suite v4</a>, 5 tasks, 52 hidden tests. Agents at once: suite v1, 3 tasks, all started together.</li>
<li>Single runs: one run per setup unless the table says otherwise, so a task's result can change on a rerun.
From 27 November comparisons are paired by item with error bars (Miller, <a href="https://arxiv.org/abs/2411.00640">Adding
Error Bars to Evals</a>; Student's t over items): <a href="{REPO}scripts/compare_runs.py">compare_runs.py</a>,
<a href="{REPO}docs/23-error-bars.md">method</a>.</li>
<li>Streamed benches: the open mlx-lm port behind an OpenAI-compatible server
(<a href="{REPO}benches/expert-stream/serve.py">serve.py</a>), 2,400 s per task, since experts from the SSD are about
3 times slower than in memory.</li>
<li>Data: <a href="../data/reports/inkling.json">inkling.json</a>; page built by <a href="{REPO}scripts/inkling_report.py">inkling_report.py</a>.</li>
</ul></section>""")
    return shell("Inkling 2 Mini: MLX, GGUF or Qwen, and streaming its experts", "".join(body), "learnings.html", depth=1,
                 desc="Inkling 2 Mini on a 32 GB Mac mini: MLX vs GGUF vs Qwen 3.8 27B on speed, requests at once and hard "
                      "coding, and Inkling with its experts streamed from the SSD: decode and prompt speed, exact against lossy.")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default=str(ROOT / "results"))
    a = ap.parse_args()
    data = collect(Path(a.results).expanduser())
    (ROOT / "site/reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "site/data/reports").mkdir(parents=True, exist_ok=True)
    (ROOT / "site/reports/inkling.html").write_text(page(data))
    (ROOT / "site/data/reports/inkling.json").write_text(json.dumps(data, indent=1))
    print("wrote site/reports/inkling.html")


if __name__ == "__main__":
    main()
