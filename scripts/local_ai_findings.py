"""Evidence tables for the findings in docs/21-local-ai-playbook.md, built from the collected data.

Every table here is computed from summary groups, run rows, the speed test (results/speed/) and the
`ollama ps` lines the queue logs keep. Nothing is typed in by hand.
"""
import json, re
from pathlib import Path


def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join("" if c is None else str(c) for c in r) + " |" for r in rows])


def runs_str(g):
    if not g:
        return "—"
    s = "/".join(f"{x:g}" if isinstance(x, float) else str(x) for x in g["scores"])
    return f"{s} of {g['out_of']}" if g["runs"] > 1 else f"{s}/{g['out_of']}"


def short(m):
    return m.replace("-mlx", "").replace("qwen3.8:27b", "qwen3.8 27B").replace("gemma4:", "gemma4 ").replace("gpt-oss:20b", "gpt-oss 20B")


class Data:
    def __init__(self, summary, rows, res):
        self.summary, self.rows, self.res = summary, rows, Path(res)

    def g(self, bench, model, setup):
        return next((x for x in self.summary if x["bench"] == bench and x["model"] == model and x["setup"] == setup), None)

    def all(self, bench):
        return [x for x in self.summary if x["bench"] == bench]

    def row(self, bench, model, setup):
        return next((r for r in self.rows if r["bench"] == bench and r["model"] == model and r["setup"] == setup and not r.get("set_aside")), None)

    # ---- speed test and loaded memory ----
    def speed(self):
        out = []
        for f in sorted((self.res / "speed").glob("speed-*.json")):
            try:
                out += json.loads(f.read_text())
            except ValueError:
                pass
        return out

    def loaded(self):
        """Loaded size per model: the speed test's /api/ps reading, else `ollama ps` lines in queue logs."""
        sizes = {}
        pat = re.compile(r"^(?:PS )?(\S+:\S+)\s+[0-9a-f]{12}\s+(\d+(?:\.\d+)?) GB\s+\S+ GPU\s+(\d+)", re.M)
        for f in sorted(self.res.glob("*.out")):
            for name, gb, ctx in pat.findall(f.read_text(errors="ignore")):
                sizes[name] = (float(gb), int(ctx), f"`ollama ps` in {f.name}")
        for x in self.speed():
            if x.get("case") == "loaded":
                sizes[x["model"]] = (x["size_gb"], x.get("context"), "speed test")
        return sizes


def f_thinking_lang(D):
    rows = []
    for g in sorted(D.all("translation"), key=lambda g: (g["model"], g["setup"])):
        if len([x for x in D.all("translation") if x["model"] == g["model"]]) > 1:
            r = D.row("translation", g["model"], g["setup"]) or {}
            rows.append(["Translation", g["model"], g["setup"].replace("thinking ", ""), f"chrF {g['mean']:g}", f"{g.get('seconds_each', '')} s per message", r.get("failed_answers")])
    for g in sorted(D.all("speech-to-text"), key=lambda g: (g["model"], g["setup"])):
        if "whisper" in g["model"]:
            continue
        r = D.row("speech-to-text", g["model"], g["setup"]) or {}
        rows.append(["Speech to text", g["model"], g["setup"].split("thinking ")[-1], f"{g['mean']:g}% errors",
                     f"{r.get('compute_per_audio_second')} s per s of audio", r.get("failed_answers")])
    return table(["Task", "Model", "Thinking", "Score (chrF higher is better; errors lower is better)", "Time", "Answers that failed"], rows)


def f_thinking_vision(D):
    rows = []
    for b in ("vision-v1", "vision-v2"):
        for m in sorted({g["model"] for g in D.all(b)}):
            off, on = D.g(b, m, "thinking off"), D.g(b, m, "thinking on")
            if off and on:
                rows.append([b, m, f"{off['mean']:g}/{off['out_of']}", f"{on['mean']:g}/{on['out_of']}", off.get("seconds_each"), on.get("seconds_each")])
    return table(["Suite", "Model", "Correct, thinking off", "Correct, thinking on", "Seconds per image, off", "Seconds per image, on"], rows)


def f_effort_coding(D):
    rows = []
    for suite in ("coding-v1", "coding-v2"):
        for e in ("low", "medium", "high"):
            g = D.g(suite, "gpt-oss:20b", f"{e}-ext")
            if g:
                rows.append([suite.split("-")[1], e, runs_str(g), g.get("minutes")])
    return table(["Suite", "gpt-oss reasoning effort (with extensions)", "Tasks solved", "Minutes per suite"], rows)


def f_one_vs_two(D):
    setups = [("qwen3.8:27b-mlx", "one model", "qwen3.8 27B alone"), ("gemma4:12b-mlx", "one model", "gemma4 12B alone"),
              ("gemma4:e4b-mlx", "routed to gpt-oss:20b", "e4b for chat + gpt-oss for files"),
              ("gpt-oss:20b", "one model", "gpt-oss 20B alone"), ("gemma4:e4b-mlx", "one model", "gemma4 e4b alone")]
    rows = []
    for m, s, label in setups:
        fam = D.g("family-jobs", m, s)
        rows.append([label, runs_str(fam), runs_str(D.g("assistant-en", m, s)), runs_str(D.g("assistant-lang", m, s)), (fam or {}).get("seconds_each", "—")])
    return table(["Setup (no harness)", "Family jobs", "Assistant, English", "Assistant, Japanese/French", "Seconds per family job"], rows)


def f_12b(D):
    loaded = D.loaded()
    a, b = "gemma4:12b-mlx", "qwen3.8:27b-mlx"

    def coding(m):
        gs = [D.g(f"coding-{v}", m, "off") for v in ("v1", "v2", "v3")]
        gs = [g for g in gs if g]
        if not gs:
            return "—"
        return f"{sum(g['scores'][-1] for g in gs)}/{sum(g['out_of'] for g in gs)} ({', '.join(f'v{i+1}' for i, g in enumerate(gs))}, latest run each)"

    def speech(m):
        g = D.g("speech-to-text", m, "audio in, via Ollama, thinking off")
        return f"{g['mean']:g}% errors" if g else "cannot hear"

    rows = [
        ["Family jobs", runs_str(D.g("family-jobs", a, "one model")), runs_str(D.g("family-jobs", b, "one model"))],
        ["Assistant, English", runs_str(D.g("assistant-en", a, "one model")), runs_str(D.g("assistant-en", b, "one model"))],
        ["Assistant, Japanese/French", runs_str(D.g("assistant-lang", a, "one model")), runs_str(D.g("assistant-lang", b, "one model"))],
        ["Seconds per family job", (D.g("family-jobs", a, "one model") or {}).get("seconds_each"), (D.g("family-jobs", b, "one model") or {}).get("seconds_each")],
        ["Coding, no extensions", coding(a), coding(b)],
        ["Speech to text", speech(a), speech(b)],
        ["Images (vision-v1, thinking off)", runs_str(D.g("vision-v1", a, "thinking off")), runs_str(D.g("vision-v1", b, "thinking off"))],
        ["Memory once loaded", f"{loaded[a][0]:g} GB" if a in loaded else "—", f"{loaded[b][0]:g} GB" if b in loaded else "not yet measured"],
    ]
    return table(["", "gemma4:12b", "qwen3.8:27b"], rows)


def f_harness(D):
    models = [("gpt-oss:20b", "one model", "gpt-oss 20B"), ("gemma4:e4b-mlx", "routed to gpt-oss:20b", "e4b + gpt-oss"),
              ("qwen3.8:27b-mlx", "one model", "qwen3.8 27B"), ("gemma4:12b-mlx", "one model", "gemma4 12B")]
    rows = []
    for m, s, label in models:
        rows.append([label] + [runs_str(D.g("family-jobs", m, s + v)) for v in ("", ", harness v2", ", harness v3", ", harness v3.1")])
    return table(["Family jobs passed", "No harness", "v2", "v3", "v3.1"], rows)


def f_extensions(D):
    pairs = [("gemma4:26b-mlx", ["guard", "guard2"]), ("gpt-oss:20b", ["flat", "tuned"]), ("qwen3.8:27b-mlx", ["guard", "guard2", "flat"])]
    rows = []
    for suite in ("coding-v1", "coding-v2", "coding-v3"):
        for m, ext in pairs:
            bare = D.g(suite, m, "off") or D.g(suite, m, "bare")
            withx = [(e, D.g(suite, m, e)) for e in ext if D.g(suite, m, e)]
            if bare or withx:
                rows.append([suite.split("-")[1], m, runs_str(bare), "; ".join(f"{e}: {runs_str(g)}" for e, g in withx) or "—"])
    return table(["Suite", "Model", "No extensions", "With extensions (setup: tasks solved)"], rows)


def f_review(D):
    rows = []
    for suite in ("coding-v1", "coding-v2", "coding-v3"):
        f, r = D.g(suite, "qwen3.8:27b-mlx", "flat"), D.g(suite, "qwen3.8:27b-mlx", "review")
        if f or r:
            rows.append([suite.split("-")[1], runs_str(f), runs_str(r), (f or {}).get("minutes"), (r or {}).get("minutes")])
    return table(["Suite", "qwen, extensions", "qwen, extensions + self-review", "Minutes, without review", "Minutes, with review"], rows)


def f_tries(D):
    rows = []
    for suite in ("coding-v1", "coding-v2", "coding-v3"):
        for s in ("flat", "tuned", "att2", "att2pick", "att4", "pair"):
            g = D.g(suite, "gpt-oss:20b", s)
            if g:
                rows.append([suite.split("-")[1], s, g.get("setup_meaning"), runs_str(g), g.get("minutes")])
    return table(["Suite", "Setup", "What it does", "Tasks solved", "Minutes per suite"], rows)


def f_speech(D):
    rows = []
    for g in sorted(D.all("speech-to-text"), key=lambda g: g["mean"]):
        if "thinking on" in g["setup"]:
            continue
        r = D.row("speech-to-text", g["model"], g["setup"]) or {}
        c = r.get("by_condition") or {}
        rows.append([g["model"], g["mean"]] + [c.get(k) for k in ("en-clean-pinned", "ja-clean-pinned", "fr-clean-pinned", "fr-noise-pinned")])
    return table(["Model (thinking off)", "Error % (all clips)", "English", "Japanese", "French", "French, noisy"], rows)


def f_translation(D):
    rows = [[g["model"], g["mean"], g.get("seconds_each")] for g in sorted(D.all("translation"), key=lambda g: -g["mean"]) if g["setup"] in ("thinking off", "thinking low")]
    return table(["Model (thinking off; gpt-oss at low)", "chrF", "Seconds per message"], rows)


def f_routing(D):
    rows = [[g["model"], g["setup"], f"{g['mean']:g}/{g['out_of']}"] for g in D.all("routing")
            if g["model"].startswith("ollama-") or (g["setup"] == "rules+simple" and "router" in g["model"])]
    return table(["Router", "How", "Right"], rows)


def load_times(res):
    """Seconds from a stopped model to its first token, from the LOAD lines queue logs keep."""
    t = {}
    for f in sorted(Path(res).glob("*.out")):
        for m, s in re.findall(r"LOAD (\S+) ([\d.]+) s", f.read_text(errors="ignore")):
            t[m] = round(float(s), 1)
    return t


def f_memory(D, sizes):
    loaded, lt = D.loaded(), load_times(D.res)
    rows = []
    for m in sorted(set(loaded) | set(lt)):
        gb, ctx, src = loaded.get(m, ("—", "—", ""))
        rows.append([m, sizes.get(m, "—"), gb, ctx, lt.get(m, "—")])
    return (table(["Model", "Download (GB)", "Loaded (GB)", "Context (tokens)", "Seconds to load and answer one word"], rows)
            if rows else "_Not measured yet._")


def f_speed(D):
    sp = D.speed()
    if not sp:
        return "_Measurement queued; this section fills in when it lands._"
    latest = {}  # newest file wins per model and case
    for x in sp:
        if x.get("case") in ("first", "cached", "terse") or x.get("fixed_len"):
            latest[(x["model"], x["case"])] = x
    rows, par = [], []
    names = {"first": "first request", "cached": "same system prompt again", "terse": "asked for a terse answer"}
    for (model, case), x in latest.items():
        if case in names:
            rows.append([model, names[case], x["prompt_tokens"], x["prompt_s"], x["out_tokens"], x["out_s"], x["wall"]])
        else:
            par.append([model, case.split("-")[1], x["wall"], x["out_tokens"], x["tokens_per_s"]])
    out = table(["Model", "Request", "Prompt tokens read", "Seconds reading", "Tokens written", "Seconds writing", "Total seconds"], rows)
    out += "\n\nSeveral requests at once, each writing exactly 256 tokens:\n\n"
    if par:
        out += table(["Model", "Requests at once", "Seconds until all done", "Tokens written in all", "Tokens per second in all"], sorted(par, key=lambda r: (r[0], int(r[1]))))
    else:
        out += "_Measurement queued. A first try let each reply run to its own length, so totals could not be compared; it is set aside._"
    return out


def build(summary, rows, res, sizes):
    D = Data(summary, rows, res)
    return {
        "f_thinking_lang": f_thinking_lang(D), "f_thinking_vision": f_thinking_vision(D), "f_effort_coding": f_effort_coding(D),
        "f_one_vs_two": f_one_vs_two(D), "f_12b": f_12b(D), "f_harness": f_harness(D), "f_extensions": f_extensions(D),
        "f_review": f_review(D), "f_tries": f_tries(D), "f_speech": f_speech(D), "f_translation": f_translation(D),
        "f_routing": f_routing(D), "f_memory": f_memory(D, sizes), "f_speed": f_speed(D),
    }
