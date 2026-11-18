#!/usr/bin/env python3
"""Collect the local-AI bench results (coding agent, assistant, family jobs, routing, translation,
speech, vision) from results/ into public data, and refresh the generated tables in
docs/21-local-ai-playbook.md.

    .venv/bin/python scripts/collect_local_ai.py

Writes site/data/local-ai/:
  runs.json / runs.csv   one row per bench run (the unit you repeat), home paths removed
  summary.json           runs grouped by bench + model + setup, with repeats (n, mean, min, max)
  schema.json            what each field means
Numbers in the playbook come only from here: never retype them.
"""
import importlib.util
import csv, json, os, re, statistics, subprocess, sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "results"
OUT = ROOT / "site" / "data" / "local-ai"
DOC = ROOT / "docs" / "21-local-ai-playbook.md"
HOME = str(Path.home())
ROWS = []

# Runs kept out of the summaries, with the reason (they stay in runs.json, marked).
SET_ASIDE = {
    "memorystarved": "ran while another 26 GB model was loaded; the Mac swapped",
    "partial": "stopped part way",
    "superseded": "first version of mini's turn checks, replaced the same day",
    "causeprobe": "diagnostic run, not a setup",
    "iso11500": "concurrency experiment on a second Ollama",
}
# What each coding-agent setup tag means. Setups not listed are earlier development builds.
SETUPS = {
    "off": "pi with no extensions",
    "medium": "pi, no extensions, reasoning effort medium",
    "guard": "pi + local-model-guard (repairs broken tool calls, stops loops)",
    "guard1": "local-model-guard, build 1",
    "guard2-terse": "local-model-guard build 2 + a terse system prompt",
    "guard2": "local-model-guard, build 2",
    "gb0": "guard + coding-boost, development build 0",
    "gb1": "guard + coding-boost, development build 1",
    "gb1med": "guard + coding-boost build 1, reasoning effort medium",
    "gb2": "guard + coding-boost, development build 2",
    "gb2-terse": "guard + coding-boost build 2 + terse prompt",
    "cut": "guard + coding-boost; cut-off replies are continued at once",
    "auto": "as cut, plus 'auto' effort: low for quick replies, default once a tool runs",
    "flat": "final extensions: as auto, plus a flat edit tool for gpt-oss",
    "tuned": "same extensions as flat (named tuned on the held-out v3 suite)",
    "bare": "pi with no extensions (same as off)",
    "esc": "flat + one more run at high effort when visible tests still fail",
    "att2": "two tries per task at once, keep the first that passes the visible tests",
    "att2esc": "att2 + escalate to high effort if both fail",
    "att2pick": "two tries, keep the one passing most model-written tests",
    "att4": "four tries, keep the first that passes",
    "pair": "two runs at once that each see the other's work and revise (2 rounds)",
    "review": "flat + self-review pass (contract, correctness, validation, scope reviewers)",
    "mini": "the prompt mini (DropBox's assistant) uses",
    "low-ext": "extensions, reasoning effort low",
    "medium-ext": "extensions, reasoning effort medium",
    "high-ext": "extensions, reasoning effort high",
}
MODEL_RE = (r"(gpt-oss-20b|qwen3-8-27b-mlx|qwen3-8-27b-dflash2-nvfp4|qwen3-8-27b-mtp-q4_K_M|qwen3-6-35b-mlx|gemma4-26b-mlx|gemma4-e4b-mlx|"
            r"gemma4-12b-mlx|muse-glimmer-30b-nvfp4-dflash|nemotron-3-5-lightning-30b-a3b-mlx)")
PRETTY = {"gpt-oss-20b": "gpt-oss:20b", "qwen3-8-27b-mlx": "qwen3.8:27b-mlx", "gemma4-26b-mlx": "gemma4:26b-mlx",
          "gemma4-e4b-mlx": "gemma4:e4b-mlx", "gemma4-12b-mlx": "gemma4:12b-mlx"}


def clean(v):
    """Remove the owner's home folder from any text."""
    if isinstance(v, str):
        return v.replace(HOME, "~")
    if isinstance(v, dict):
        return {k: clean(x) for k, x in v.items()}
    if isinstance(v, list):
        return [clean(x) for x in v]
    return v


def load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def model_name(m):
    m = (m or "").removeprefix("ollama/").removeprefix("mlx-community/")
    return m


def set_aside(name):
    for k, why in SET_ASIDE.items():
        if k in name:
            return why
    return None


def hidden_steps(task_dir, hidden_src):
    """Each hidden test of a task as [plain-words name, passed], from the task's hidden.txt (unittest -v)."""
    names = re.findall(r"^    def (test_\w+)", hidden_src, re.M)
    out = (task_dir / "hidden.txt").read_text(errors="replace") if (task_dir / "hidden.txt").exists() else ""
    res = dict(re.findall(r"^(test_\w+) \(.*?\)\s*\.\.\.\s*(ok|FAIL|ERROR|skipped)", out, re.M))
    return [[n.removeprefix("test_").replace("_", " "), res.get(n) == "ok"] for n in names]


def coding_runs():
    rows = []
    v4 = None
    for d in sorted(RES.glob("agentic-v*")):
        m = re.match(rf"agentic-(v\d)-{MODEL_RE}-(.+)$", d.name)
        meta = load(d / "meta.json")
        if not m or not meta:
            continue
        suite, mkey, rest = m.groups()
        if rest in ("off", "medium") or re.match(r"(off|medium)$", rest):
            thinking, setup_tag, rep = rest, rest, 1
        else:
            thinking = rest.split("-")[0]
            tag = rest[len(thinking) + 1:]
            rm = re.search(r"-r(\d)(?:-.*)?$", tag) or re.search(r"^r(\d)$", tag)
            rep = int(rm.group(1)) if rm else 1
            setup_tag = re.sub(r"-?r\d(-.*)?$", "", tag) or thinking
            if rest.startswith(("low-ext", "medium-ext", "high-ext")):
                thinking, setup_tag = rest.split("-")[0], rest.rsplit("-r", 1)[0]
        tasks = [load(f) for f in sorted(d.glob("*/result.json"))]
        tasks = [t for t in tasks if t]
        if not tasks:
            continue
        extra = {}
        if suite == "v4":  # graded in hidden tests passed: partial credit, with each test's name
            if v4 is None:
                spec = importlib.util.spec_from_file_location("agentic_v4_tasks", ROOT / "benches" / "agentic-v4" / "tasks.py")
                v4 = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(v4)
            jobs = []
            for t in tasks:
                st = hidden_steps(d / t["task"], v4.TASKS[t["task"]]["hidden"]) if t["task"] in v4.TASKS else []
                jobs.append({"id": t["task"], "points": sum(ok for _, ok in st), "out_of": len(st), "steps": st,
                             "error": "timed out" if t.get("timed_out") else None})
            extra = {"jobs": jobs, "score": sum(j["points"] for j in jobs), "out_of": sum(j["out_of"] for j in jobs),
                     "tasks_solved": sum(bool(t.get("solved")) for t in tasks)}
        rows.append(clean({
            "bench": f"coding-{suite}", "run": d.name, "model": model_name(meta.get("model")),
            "setup": setup_tag, "setup_meaning": SETUPS.get(setup_tag, "development build"), "repeat": rep,
            "thinking": thinking, "date": (meta.get("started") or "")[:10],
            "score": sum(bool(t.get("solved")) for t in tasks), "out_of": len(tasks),
            "hidden_tests": f"{sum((t.get('hidden_passed') or 0) for t in tasks)}/{sum((t.get('hidden_total') or 0) for t in tasks)}",
            "minutes": round(sum((t.get("wall_s") or 0) for t in tasks) / 60, 1),
            "tool_calls": sum((t.get("tool_calls") or 0) for t in tasks),
            "output_tokens": sum((t.get("output_tokens") or 0) for t in tasks),
            "timeouts": sum(bool(t.get("timed_out")) for t in tasks),
            "tests_tampered": sum(bool(t.get("original_tests_changed")) for t in tasks),
            "versions": {"pi": meta.get("pi_version"), "ollama": (meta.get("ollama_version") or "").split()[-1]},
            "extensions": [Path(e).parent.parent.name + "/" + Path(e).parent.name for e in (meta.get("extension") or "").split(",") if e],
            "per_task": {t["task"]: {"solved": t.get("solved"), "seconds": t.get("wall_s")} for t in tasks},
            "set_aside": set_aside(d.name) or (None if len(tasks) == 5 else "incomplete run"),
        } | extra))
    return rows


def dropbox_runs():
    rows = []
    for d in sorted(RES.glob("dropbox-*")):
        s = (load(d / "results.json") or {}).get("summary")
        if not s:
            continue
        job, model = s.get("job"), model_name(s.get("model"))
        tag = s.get("tag") or ""
        rep = int(re.search(r"r(\d)$", tag).group(1)) if re.search(r"-r\d$", tag) else 1
        base = re.sub(r"-r\d$", "", tag)
        row = {"run": d.name, "model": model, "repeat": rep, "set_aside": set_aside(d.name),
               "date": __import__("datetime").date.fromtimestamp((d / "results.json").stat().st_mtime).isoformat()}
        # mini.py harness versions of 2026-11-17/18: v2 = rules + after-turn checks, v3 = + reads, language, HOME,
        # v3.1 = v3 + "clean up" means ask first
        # v3.2 = v3.1 + read-only clean-ups, language and lost-line checks, retry empty turns, fallback answer
        checks = ", harness v3.2" if "checks32" in base else ", harness v3.1" if "checks31" in base else ", harness v3" if "checks3" in base else ", harness v2" if "checks" in base else ""
        if "smoke" in base:
            row["set_aside"] = "smoke test of a new harness version"
        if job == "mini":
            if s.get("total") == 12:
                row["set_aside"] = "older 12-case version of the test"
            if base == "sandbox":
                row["set_aside"] = "sandbox check run"
            cases = "lang" if "lang" in d.name else "en"
            row |= {"bench": f"assistant-{cases}", "setup": ("routed to " + model_name(s["tools_model"]) if s.get("tools_model") else "one model")
                    + (f", {s.get('concurrency')} at once" if (s.get("concurrency") or 1) > 1 else "") + checks,
                    "score": s["passed"], "out_of": s["total"], "seconds_each": s.get("seconds_per_request"),
                    "slowest_seconds": s.get("slowest_request"), "stayed_quiet_right": f"{s.get('silence_right')}/{s.get('ambient_total')}"}
        elif job == "family":
            if base in ("sandbox", "sandbox2"):
                row["set_aside"] = "sandbox check run"
            row |= {"bench": "family-jobs", "setup": ("routed to " + model_name(s["tools_model"]) if s.get("tools_model") else "one model")
                    + (f", {s.get('concurrency')} at once" if (s.get("concurrency") or 1) > 1 else "") + checks,
                    "score": s["passed"], "out_of": s["total"], "seconds_each": s.get("seconds_per_job"), "slowest_seconds": s.get("slowest_job")}
        elif job == "family-hard":
            r = load(d / "results.json") or {}
            row |= {"bench": "family-hard", "setup": "one model" + checks, "score": s["passed"], "out_of": s["total"],
                    "jobs_complete": s.get("jobs_complete"), "seconds_each": s.get("seconds_per_job"), "slowest_seconds": s.get("slowest_job"),
                    "jobs": [{"id": x["id"], "points": x.get("points"), "out_of": x.get("out_of"), "steps": x.get("steps"),
                              "error": next((w for w in x.get("why") or [] if str(w).startswith("error")), None)} for x in r.get("rows", [])]}
        elif job == "translate":
            row |= {"bench": "translation", "setup": f"thinking {s.get('think')}", "score": s["chrf"], "out_of": 100, "metric": "chrF (higher is better)",
                    "by_direction": s.get("by_direction"), "seconds_each": s.get("seconds_per_message"), "failed_answers": s.get("errors")}
        elif job == "captions":
            row |= {"bench": "speech-to-text", "setup": "Whisper via mlx-whisper" if "whisper" in model else f"audio in, via Ollama, thinking {s.get('think') or 'off'}",
                    "score": round(100 * s["error_rate_all"], 1), "out_of": 100, "metric": "word/character error % (lower is better)",
                    "by_condition": {k: round(100 * v, 1) for k, v in s["error_rate"].items()},
                    "language_detected_right": s.get("language_detected_right"), "compute_per_audio_second": s.get("compute_per_audio_second"),
                    "failed_answers": s.get("errors")}
        else:
            continue
        rows.append(clean(row))
    return rows


def routing_runs():
    rows = []
    for f in sorted((RES / "route-bench").glob("*.json")):
        s = (load(f) or {}).get("summary") or {}
        a = s.get("all") or {}
        for how, val in a.items():
            got, total = map(int, val.split("/"))
            rows.append({"bench": "routing", "run": f"route-bench/{f.name}", "model": f.stem, "setup": how, "repeat": 1,
                         "score": got, "out_of": total, "needs_tools": (s.get("needs tools") or {}).get(how),
                         "chat": (s.get("chat") or {}).get(how), "ms_median": s.get("model_ms_median") or s.get("ms_median"), "set_aside": None})
    return rows


def vision_runs():
    rows = []
    for d in sorted(RES.glob("vision-v*")):
        r = load(d / "results.json")
        if not r or not r.get("rows"):
            continue
        rs = r["rows"]
        groups = defaultdict(lambda: [0, 0])
        for x in rs:
            groups[x.get("group") or x.get("category")][0] += bool(x.get("correct"))
            groups[x.get("group") or x.get("category")][1] += 1
        rows.append({"bench": r.get("bench", "vision"), "run": d.name, "model": model_name(r.get("model")), "setup": f"thinking {r.get('think')}",
                     "repeat": 1, "score": sum(bool(x.get("correct")) for x in rs), "out_of": len(rs),
                     "seconds_each": round(statistics.mean(x.get("wall_s") or 0 for x in rs), 2),
                     "by_group": {k: f"{a}/{b}" for k, (a, b) in groups.items()}, "set_aside": None})
    return rows


def summarize(rows):
    groups = defaultdict(list)
    for r in rows:
        if not r.get("set_aside"):
            groups[(r["bench"], r["model"], r["setup"])].append(r)
    out = []
    for (bench, model, setup), rs in sorted(groups.items()):
        sc = [r["score"] for r in rs]
        g = {"bench": bench, "model": model, "setup": setup, "setup_meaning": rs[0].get("setup_meaning"), "runs": len(rs),
             "out_of": rs[0]["out_of"], "scores": sc, "mean": round(statistics.mean(sc), 2), "min": min(sc), "max": max(sc)}
        for k in ("minutes", "seconds_each"):
            vals = [r[k] for r in rs if r.get(k) is not None]
            if vals:
                g[k] = round(statistics.mean(vals), 2)
        if rs[0].get("metric"):
            g["metric"] = rs[0]["metric"]
        out.append({k: v for k, v in g.items() if v is not None})
    return out


def model_sizes():
    try:
        txt = subprocess.run(["ollama", "list"], capture_output=True, text=True, timeout=20).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    sizes = {}
    for line in txt.splitlines()[1:]:
        p = line.split()
        if len(p) >= 4 and p[3] == "GB":
            sizes[p[0]] = float(p[2])
    return sizes


def machine():
    def sh(*a):
        try:
            return subprocess.run(a, capture_output=True, text=True, timeout=10).stdout.strip()
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return {"chip": sh("sysctl", "-n", "machdep.cpu.brand_string"),
            "memory_gb": round(int(sh("sysctl", "-n", "hw.memsize") or 0) / 2**30),
            "macos": sh("sw_vers", "-productVersion"), "ollama": sh("ollama", "--version").split()[-1:] and sh("ollama", "--version").split()[-1]}


# ---------- tables for the playbook ----------
def table(head, rows):
    return "\n".join(["| " + " | ".join(head) + " |", "|" + "---|" * len(head)] + ["| " + " | ".join(map(str, r)) + " |" for r in rows])


def scores(g):
    s = "/".join(map(str, g["scores"]))
    return f"{s} of {g['out_of']}" if g["runs"] > 1 else f"{s}/{g['out_of']}"


def render(summary, sizes):
    S = lambda bench: [g for g in summary if g["bench"] == bench]
    parts = {}
    # coding: main models on their best-understood setups
    keep = {("gpt-oss:20b", "flat"), ("gpt-oss:20b", "tuned"), ("gpt-oss:20b", "off"), ("qwen3.8:27b-mlx", "off"), ("qwen3.8:27b-mlx", "guard"),
            ("qwen3.8:27b-mlx", "guard2"), ("qwen3.8:27b-mlx", "guard2-terse"), ("gemma4:26b-mlx", "off"), ("gemma4:26b-mlx", "guard"), ("gemma4:26b-mlx", "guard2"),
            ("gemma4:e4b-mlx", "guard")}
    rows = []
    for suite in ("coding-v1", "coding-v2", "coding-v3"):
        for g in S(suite):
            if (g["model"], g["setup"]) in keep:
                rows.append([suite.split("-")[1], g["model"], g["setup"], scores(g), g.get("minutes", "")])
    parts["coding"] = table(["Suite", "Model", "Setup", "Tasks solved (each run)", "Minutes per run"], rows)
    rows = []
    for suite in ("coding-v1", "coding-v2", "coding-v3"):
        for g in S(suite):
            if g["model"] == "gpt-oss:20b" and g["setup"] not in ("off", "medium") and not g["setup"].startswith("gb"):
                rows.append([suite.split("-")[1], g["setup"], g.get("setup_meaning", ""), scores(g), g.get("minutes", "")])
    parts["coding_strategies"] = table(["Suite", "Setup", "What it does", "Solved (each run)", "Minutes"], rows)
    rows = []
    for bench in ("assistant-en", "assistant-lang", "family-jobs"):
        for g in S(bench):
            rows.append([bench, g["model"], g["setup"], scores(g), g.get("seconds_each", "")])
    parts["assistant"] = table(["Test", "Model", "Setup", "Passed (each run)", "Seconds per request"], rows)
    rows = [[g["model"], g["setup"], g["mean"], g.get("seconds_each", "")] for g in sorted(S("translation"), key=lambda g: -g["mean"])]
    parts["translation"] = table(["Model", "Setup", "chrF, en/ja/fr (higher is better)", "Seconds per message"], rows)
    rows = []
    for g in sorted(S("speech-to-text"), key=lambda g: g["mean"]):
        r = next(x for x in ROWS if x["bench"] == "speech-to-text" and x["model"] == g["model"] and x["setup"] == g["setup"] and not x.get("set_aside"))
        c = r["by_condition"]
        rows.append([g["model"] + ("" if "whisper" in g["model"] else f" ({g['setup'].split(', ')[-1]})"), g["mean"]] + [c.get(f"{l}-pinned") for l in ("en-clean", "ja-clean", "fr-clean", "fr-noise")]
                    + [f"{round(100 * (r.get('language_detected_right') or 0))}%", r.get("compute_per_audio_second")])
    parts["speech"] = table(["Model", "Error % (all)", "en", "ja", "fr", "fr, noisy", "Language guessed right", "Seconds of compute per second of audio"], rows)
    rows = [[g["model"], g["setup"], f"{g['mean']:g}/{g['out_of']}"] for g in S("routing") if not g["model"].endswith("-mps")]
    parts["routing"] = table(["Router", "How", "Right of 53 labelled requests"], rows)
    rows = []
    for b in ("vision-v1", "vision-v2"):
        for g in S(b):
            rows.append([b, g["model"], g["setup"], f"{g['mean']:g}/{g['out_of']}", g.get("seconds_each", "")])
    parts["vision"] = table(["Suite", "Model", "Setup", "Correct", "Seconds per image"], rows)
    names = ["gemma4:e4b-mlx", "gpt-oss:20b", "qwen3.8:27b-mlx", "gemma4:26b-mlx", "gemma4:12b-mlx"]
    parts["sizes"] = table(["Model", "Download size (GB)"], [[n, sizes[n]] for n in names if n in sizes])
    return parts


def fill(doc, parts):
    text = doc.read_text()
    for k, v in parts.items():
        text = re.sub(rf"(<!-- generated:{k} -->\n)(?:.*?\n)?(<!-- /generated:{k} -->)", lambda m: m.group(1) + v + "\n" + m.group(2), text, flags=re.S)
    doc.write_text(text)


SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "colibri-poc local-AI bench run",
    "type": "object",
    "required": ["bench", "run", "model", "setup", "repeat", "score", "out_of"],
    "properties": {
        "bench": {"description": "coding-v1..v3 (agent fixes/extends code, graded by hidden tests), assistant-en / assistant-lang (chat assistant rule checks), "
                                 "family-jobs (multi-step file jobs), translation, speech-to-text, routing, vision-v1/v2", "type": "string"},
        "run": {"description": "folder name under results/ (raw files are not published)", "type": "string"},
        "model": {"description": "Ollama tag, or the Whisper model", "type": "string"},
        "setup": {"description": "the settings compared; setup_meaning explains coding setups", "type": "string"},
        "repeat": {"description": "1, 2, 3: the same bench run again", "type": "integer"},
        "score": {"description": "tasks passed; for translation chrF; for speech-to-text error % (lower is better)", "type": "number"},
        "out_of": {"type": "number"},
        "minutes": {"description": "coding: total wall-clock minutes for the suite", "type": "number"},
        "seconds_each": {"description": "mean seconds per request / message / image", "type": "number"},
        "set_aside": {"description": "why this run is excluded from summaries, or null", "type": ["string", "null"]},
    },
}


def main():
    global ROWS
    rows = ROWS = coding_runs() + dropbox_runs() + routing_runs() + vision_runs()
    summary = summarize(rows)
    sizes = model_sizes()
    OUT.mkdir(parents=True, exist_ok=True)
    meta = {"generated_by": "scripts/collect_local_ai.py", "machine": machine(), "model_download_gb": sizes}
    (OUT / "runs.json").write_text(json.dumps({"about": meta, "runs": rows}, indent=1, ensure_ascii=False) + "\n")
    (OUT / "summary.json").write_text(json.dumps({"about": meta, "groups": summary}, indent=1, ensure_ascii=False) + "\n")
    (OUT / "schema.json").write_text(json.dumps(SCHEMA, indent=1) + "\n")
    flat = ["bench", "run", "model", "setup", "repeat", "score", "out_of", "minutes", "seconds_each", "date", "set_aside"]
    with (OUT / "runs.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, flat, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    if DOC.exists():
        sys.path.insert(0, str(ROOT / "scripts"))
        import local_ai_findings, md_page, datetime
        parts = render(summary, sizes) | local_ai_findings.build(summary, rows, RES, sizes)
        kept = [r for r in rows if not r.get("set_aside")]
        dates = sorted(r["date"] for r in rows if r.get("date"))
        coding_h = sum(r.get("minutes") or 0 for r in rows) / 60
        parts["stats"] = (f"> **{len(kept)} bench runs** ({len(rows) - len(kept)} more set aside, with the reason) across "
                          f"{len({r['bench'] for r in kept})} benches and {len({r['model'] for r in kept})} models, "
                          f"{dates[0] if dates else '?'} to {dates[-1] if dates else '?'}. The coding-agent runs alone took "
                          f"{coding_h:.0f} hours.")
        m = meta["machine"]
        pis = sorted({(r.get("versions") or {}).get("pi") for r in rows if (r.get("versions") or {}).get("pi")})
        parts["machine"] = table(["Chip", "Memory", "macOS", "Ollama", "pi (coding agent)"],
                                 [[m.get("chip"), f"{m.get('memory_gb')} GB", m.get("macos"), m.get("ollama"), ", ".join(pis)]])
        fill(DOC, parts)
        (ROOT / "site" / "local-ai.html").write_text(md_page.page(DOC.read_text(), datetime.date.today().isoformat()))
        import site_build
        n = site_build.build(ROOT, summary, rows, local_ai_findings.Data(summary, rows, RES), sizes,
                             local_ai_findings.load_times(RES), meta["machine"], DOC.read_text(), datetime.date.today().isoformat())
        print(f"site: home, models, learnings and {n} model pages")
    print(f"{len(rows)} runs, {len(summary)} groups -> {OUT.relative_to(ROOT)}" + (f"; tables refreshed in {DOC.relative_to(ROOT)}" if DOC.exists() else ""))


if __name__ == "__main__":
    main()
