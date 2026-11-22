"""Build the site's main pages from the bench data: Home (top picks), Models (every model tested),
one page per model, and Learnings. Called by scripts/collect_local_ai.py after it writes the data.

Every score, time and memory figure is computed here from summary.json, runs.json, the speed test
and the queue logs. Hand-written text (what a model is, how we run it, observations) lives in
scripts/model_facts.py and in LEARNINGS below, and carries no numbers from the benches.
"""
import html, json, re, statistics
from pathlib import Path

import sys

import md_page
from model_facts import MODELS, CODE, REPO

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "benches" / "dropbox"))
import family_hard  # noqa: E402  (task titles for the hard jobs)

E = lambda s: html.escape(str(s), quote=True)
inline = md_page.inline

# ---------------------------------------------------------------- data helpers
PLAIN = ("off", "bare")  # pi with no extensions
SINGLE = PLAIN + ("flat", "tuned", "guard", "guard1", "guard2", "guard2-terse", "medium", "cut", "auto", "gb0", "gb1", "gb2",
                  "gb1med", "gb2-terse", "low-ext", "medium-ext")
BENCH = {
    "family-hard": ("Hard family jobs", "10 harder multi-step jobs (reconcile receipts with a bank statement, car clashes across agendas in three languages, "
                    "fix links after renaming, latest decisions from meetings that changed their minds...), graded in 56 steps. Made so that no model gets everything."),
    "family-jobs": ("Family jobs", "12 multi-step jobs in a made-up family's files (sort, rename, total, summarize, ask before deleting), in English, Japanese and French. Graded on the files left behind."),
    "assistant-en": ("Assistant, English", "14 chat requests to DropBox's assistant: answer the asker, save where asked, stay quiet on chatter."),
    "assistant-lang": ("Assistant, Japanese and French", "8 requests in Japanese and French: answer in the asker's language, do the job."),
    "coding-v1": ("Coding agent, suite v1", "5 tasks for the pi agent (bug fixes, features, refactors); a task counts when hidden tests pass."),
    "coding-v2": ("Coding agent, suite v2", "5 harder tasks, same rules."),
    "coding-v3": ("Coding agent, suite v3", "5 tasks held out while the extensions were tuned."),
    "coding-v4": ("Hard coding, suite v4", "5 hard tasks (expression evaluator with exact error positions, iCalendar recurrence across DST, "
                  "multi-currency across a 4-file package, JSON Patch, overlap counting for 200,000 intervals) graded in 52 hidden tests. "
                  "Made so that no model solves everything; the score is tests passed."),
    "vision-v1": ("Images, suite v1", "25 screenshots, documents, photos and charts with expected answers."),
    "vision-v2": ("Images, suite v2", "10 harder images."),
    "translation": ("Translation", "Short chat messages between English, Japanese and French; chrF against references (higher is better)."),
    "speech-to-text": ("Speech to text", "Spoken clips in English, Japanese and French, clean and noisy; error rate (lower is better)."),
    "routing": ("Routing", "53 labelled chat messages: should each go to the chat model or the tool model?"),
}


class Site:
    def __init__(self, summary, rows, data, sizes, load_times, machine, findings_slugs):
        self.summary, self.rows, self.D, self.sizes = summary, rows, data, sizes
        self.load_times, self.machine, self.fslug = load_times, machine, findings_slugs
        self.loaded = data.loaded()
        self.speed = {}
        self.oct, self.par, self.glm = {}, [], {}
        for x in data.speed():
            self.speed.setdefault(x["model"], {})[x["case"]] = x

    def quiz(self, m):
        """Last month's 24-question quiz: the best setting by correct answers, then by speed."""
        best = None
        for name in m.get("oct", []):
            d = self.oct.get(name)
            for r in (d["summary"] if d else []):
                key = (r["correct"], r["config"] in ("exact", "native"), -r["answer_s"])
                if best is None or key > best[0]:
                    best = (key, dict(r, bench=d))
        return best and best[1]

    def tested(self, m):
        return bool(self.groups(m) or any(n in self.oct for n in m.get("oct", [])))

    def groups(self, m, bench=None):
        keys = m["keys"]
        return [g for g in self.summary if g["model"] in keys and (bench is None or g["bench"] == bench)]

    def g(self, m, bench, setup):
        return next((g for g in self.groups(m, bench) if g["setup"] == setup), None)

    # ---- scores used for the picks and the at-a-glance tiles ----
    def daily(self, m):
        """Family jobs + both assistant tests, one model alone (no routing), best harness setup."""
        best = None
        for fam in self.groups(m, "family-jobs"):
            s = fam["setup"]
            if "routed" in s or "at once" in s:
                continue
            en = self.g(m, "assistant-en", s) or self._best(m, "assistant-en")
            la = self.g(m, "assistant-lang", s) or self._best(m, "assistant-lang")
            if not (en and la):
                continue
            q = statistics.mean([fam["mean"] / fam["out_of"], en["mean"] / en["out_of"], la["mean"] / la["out_of"]])
            secs = fam.get("seconds_each") or 0
            key = q - 0.002 * secs
            if best is None or key > best["key"]:
                best = dict(key=key, q=q, secs=secs, setup=s, fam=fam, en=en, la=la,
                            early=min(fam["runs"], en["runs"], la["runs"]) < 2)
        return best

    def _best(self, m, bench):
        gs = [g for g in self.groups(m, bench) if "routed" not in g["setup"] and "at once" not in g["setup"]]
        return max(gs, key=lambda g: (g["mean"], g["runs"]), default=None)

    def coding(self, m):
        """pi with no extensions where measured, else the best single-run setup; at least 2 suites."""
        got, total, mins, parts, early = 0, 0, 0, [], False
        for suite in ("coding-v1", "coding-v2", "coding-v3"):
            gs = [g for g in self.groups(m, suite) if g["setup"] in SINGLE]
            plain = [g for g in gs if g["setup"] in PLAIN]
            pick = max(plain or gs, key=lambda g: (g["mean"], -(g.get("minutes") or 0)), default=None)
            if not pick:
                continue
            got += pick["mean"]; total += pick["out_of"]; mins += pick.get("minutes") or 0
            parts.append((suite, pick)); early |= pick["runs"] < 2 and not plain
        hard = max(self.groups(m, "coding-v4"), key=lambda g: g["mean"], default=None)   # the hard suite, in tests passed
        if hard:
            parts.append(("coding-v4", hard))
            mins += hard.get("minutes") or 0
        if len(parts) < 2:
            return None
        # v1-v3 (tasks solved) and the hard suite (tests passed) count half each, so the suite that separates strong models weighs in
        q = (got / total + hard["mean"] / hard["out_of"]) / 2 if hard and total else hard["mean"] / hard["out_of"] if hard else got / total
        return dict(q=q, got=got, total=total, minutes=mins, parts=parts, hard=hard, early=early or len(parts) < 3)

    def vision(self, m):
        a, b = self._best(m, "vision-v1"), self._best(m, "vision-v2")
        if not (a and b):
            return None
        return dict(q=(a["mean"] / a["out_of"] + b["mean"] / b["out_of"]) / 2, v1=a, v2=b)

    def translation(self, m):
        gs = [g for g in self.groups(m, "translation") if g["setup"] in ("thinking off", "thinking low")]
        return max(gs, key=lambda g: g["mean"], default=None)

    def speech(self, m):
        gs = [g for g in self.groups(m, "speech-to-text") if "thinking on" not in g["setup"]]
        return min(gs, key=lambda g: g["mean"], default=None)

    def hard(self, m, bench="family-hard"):
        """A hard suite (family-hard or coding-v4): the best setup's mean points as a fraction of the maximum."""
        gs = self.groups(m, bench)
        g = max(gs, key=lambda g: g["mean"], default=None)
        return dict(q=g["mean"] / g["out_of"], g=g) if g else None

    def smartest(self, m):
        # coding is required (it includes the hard suite): without it, the areas nearly every model aces would decide
        c = self.coding(m) or (self.hard(m, "coding-v4") and dict(q=self.hard(m, "coding-v4")["q"]))
        parts = [x["q"] for x in (self.daily(m), c, self.vision(m), self.hard(m)) if x]
        return dict(q=statistics.mean(parts), n=len(parts)) if c and len(parts) >= 3 else None

    def mem(self, m):
        for k in m["keys"]:
            if k in self.loaded:
                return self.loaded[k]
        return None

    def download(self, m):
        return next((self.sizes[k] for k in m["keys"] if k in self.sizes), None)

    def write_speed(self, m):
        sp = next((self.speed[k] for k in m["keys"] if k in self.speed), {})
        f = sp.get("first")
        return round(f["out_tokens"] / f["out_s"]) if f and f.get("out_s") else None


def fmt(x):
    return f"{x:g}" if isinstance(x, (int, float)) else str(x)


def runs(g):
    if not g:
        return "—"
    if g["bench"] in ("translation", "speech-to-text"):
        return ", ".join(fmt(x) for x in g["scores"])
    if g["runs"] == 1:
        return f"{fmt(g['scores'][0])}/{g['out_of']}"
    return ", ".join(fmt(x) for x in g["scores"]) + f" of {g['out_of']}"


def pct(q):
    return f"{round(q * 100)}%"


# ---------------------------------------------------------------- page shell
NAV = [("index.html", "Home"), ("models.html", "Models"), ("speed.html", "Speed"), ("intelligence.html", "Intelligence"), ("learnings.html", "Learnings")]


def shell(title, body, current, depth=0, desc=""):
    up = "../" * depth
    nav = "".join(f'<a href="{up}{h}"{" aria-current=page" if h == current else ""}>{t}</a>' for h, t in NAV)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{E(title)}</title>
<meta name="description" content="{E(desc or title)}">
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Source+Serif+4:ital,opsz,wght@0,8..60,400;0,8..60,600;0,8..60,700;1,8..60,400&amp;family=IBM+Plex+Mono:wght@400;500&amp;display=swap">
<link rel="stylesheet" href="{up}site.css">
<link rel="stylesheet" href="{up}v2.css">
<script src="{up}site.js"></script>
</head>
<body class="pp" data-theme="auto">
<script>try{{var t=localStorage.getItem('theme');if(t==='light'||t==='dark')document.body.setAttribute('data-theme',t)}}catch(e){{}}</script>
<header class="sh">
<div class="col-wide sh-in">
<a class="sh-name" href="{up}index.html"><strong>Mac mini M6 lab</strong><span>Local AI on one 32 GB Mac: what to run, measured</span></a>
<nav class="sh-nav" aria-label="Site">{nav}<a href="https://github.com/KakkoiDev/mac-mini-m6-local-ai">Code<span class="sh-ext" aria-hidden="true"> ↗</span></a></nav>
<div class="theme" role="group" aria-label="Colour theme"><button type="button" data-t="auto">Auto</button><button type="button" data-t="light">Light</button><button type="button" data-t="dark">Dark</button></div>
</div>
</header>
<main class="col-wide v2" >
{body}
<footer class="foot">
<span>Every number is generated from the bench results by <a href="{REPO}scripts/collect_local_ai.py">collect_local_ai.py</a>; none is typed by hand.</span>
<span>Data: <a href="{up}data/local-ai/summary.json">summary.json</a> · <a href="{up}data/local-ai/runs.json">runs.json</a> · <a href="{up}data/local-ai/runs.csv">runs.csv</a> · <a href="{up}llms.txt">llms.txt</a></span>
<span><a href="{up}local-ai.html">Full research note</a> · <a href="{up}glm.html">Earlier GLM / Colibri experiments</a></span>
</footer>
</main>
</body>
</html>
"""


def pull_box(cmd):
    return (f'<div class="pull"><code>{E(cmd)}</code>'
            f'<button type="button" class="copy" data-cmd="{E(cmd)}" onclick="navigator.clipboard&&navigator.clipboard.writeText(this.dataset.cmd).then(()=>{{this.textContent=\'Copied\'}})">Copy</button></div>')


def chips(items, cls="chip"):
    return "".join(f'<span class="{cls}">{E(x)}</span>' for x in items)


def table(head, rows, num_cols=()):
    h = ['<div class="tblbox"><table class="mdt"><thead><tr>'] + [f'<th scope="col">{E(c)}</th>' for c in head] + ["</tr></thead><tbody>"]
    for r in rows:
        h.append("<tr>" + "".join((f'<th scope="row">{c}</th>' if i == 0 else f'<td class="{"n" if i in num_cols else ""}">{c}</td>') for i, c in enumerate(r)) + "</tr>")
    return "".join(h) + "</tbody></table></div>"


# ---------------------------------------------------------------- home
def home(S):
    rank = lambda f, key, rev=True: sorted([(m, f(m)) for m in MODELS if f(m)], key=lambda t: key(t[1]), reverse=rev)
    daily = rank(S.daily, lambda d: d["key"])[:3]
    coding = rank(S.coding, lambda c: (c["q"], -c["minutes"]))[:3]
    smart = rank(S.smartest, lambda s: s["q"])[:3]

    def card(i, m, lines, early):
        mem = S.mem(m)
        return (f'<li class="pick{" first" if i == 0 else ""}"><a class="pick-a" href="models/{m["slug"]}.html">'
                f'<span class="rank">{i + 1}</span><span class="pick-n">{E(m["name"])}</span>'
                f'<code class="pick-t">{E(m["tag"])}</code>'
                f'<span class="pick-l">{"".join(f"<span>{l}</span>" for l in lines)}</span>'
                f'<span class="pick-m">{" · ".join(x for x in [mem and f"{mem[0]:g} GB loaded", early and "early: one run of this setup"] if x)}</span></a></li>')

    def sec(h, lede, how, cards):
        return (f'<section class="sec podium" aria-labelledby="{h[0]}"><div class="sec-h"><h2 id="{h[0]}">{h[1]}</h2></div>'
                f'<p class="dek2">{lede}</p><ol class="picks">{"".join(cards)}</ol><p class="note">How it is picked: {how}</p></section>')

    d_cards = [card(i, m, [f"Family jobs {runs(d['fam'])}", f"Assistant {runs(d['en'])} · {runs(d['la'])}",
                           f"{fmt(d['secs'])} s per family job · {E(d['setup'])}"], d["early"]) for i, (m, d) in enumerate(daily)]
    c_cards = [card(i, m, [f"Coding score {pct(c['q'])}" + (f" · {fmt(c['got'])} of {c['total']} tasks" if c['total'] else ""), " · ".join(f"{s.split('-')[1]}: {fmt(p['mean'])}/{p['out_of']}" + (" tests" if s == "coding-v4" else "") for s, p in c["parts"]),
                           f"{c['minutes']:.0f} min for the suites"], c["early"]) for i, (m, c) in enumerate(coding)]
    s_cards = []
    for i, (m, s) in enumerate(smart):
        d, c, v = S.daily(m), S.coding(m), S.vision(m)
        s_cards.append(card(i, m, [f"Overall {pct(s['q'])} across {s['n']} areas",
                                   " · ".join(x for x in [d and f"assistant {pct(d['q'])}", c and f"coding {pct(c['q'])}", v and f"images {pct(v['q'])}"] if x)],
                            (d and d["early"]) or (c and c["early"])))

    spec = []
    sp = min(((m, S.speech(m)) for m in MODELS if S.speech(m)), key=lambda t: t[1]["mean"], default=None)
    tr = max(((m, S.translation(m)) for m in MODELS if S.translation(m)), key=lambda t: t[1]["mean"], default=None)
    vi = max(((m, S.vision(m)) for m in MODELS if S.vision(m)), key=lambda t: (t[1]["q"], -(t[1]["v1"].get("seconds_each") or 0)), default=None)
    rt = max(((m, g) for m in MODELS for g in S.groups(m, "routing")), key=lambda t: t[1]["mean"], default=None)
    turbo = next((g for g in S.summary if g["model"] == "whisper-large-v3-turbo"), None)
    if sp:
        spec.append(("Speech to text", sp[0], f"{fmt(sp[1]['mean'])}% errors ({E(sp[1]['model'])})" + (f"; large-v3-turbo {fmt(turbo['mean'])}% and much faster" if turbo and sp[1]["model"] != turbo["model"] else "")))
    if tr:
        spec.append(("Translation", tr[0], f"chrF {fmt(tr[1]['mean'])}, {fmt(tr[1].get('seconds_each'))} s per message, {E(tr[1]['setup'])}"))
    if vi:
        spec.append(("Images", vi[0], f"{runs(vi[1]['v1'])} and {runs(vi[1]['v2'])} ({E(vi[1]['v1']['setup'])})"))
    if rt:
        spec.append(("Routing chat vs tools", rt[0], f"{runs(rt[1])} right ({E(rt[1]['setup'])})"))
    spec_html = "".join(f'<li><span class="sp-k">{k}</span><a href="models/{m["slug"]}.html">{E(m["name"])}</a><span class="note">{v}</span></li>' for k, m, v in spec)

    mc = S.machine
    kept = [r for r in S.rows if not r.get("set_aside")]
    body = f"""
<div class="ptitle"><p class="sc note">A reference for Mac mini M6 · 32 GB owners · updated {S.today}</p>
<h1>Which local AI to run on a 32 GB Mac mini, and how</h1>
<p class="dek">We ran {len(kept)} bench runs on one Mac mini ({E(mc.get('chip'))}, {mc.get('memory_gb')} GB, Ollama {E(mc.get('ollama'))}) on the jobs a
home assistant and a coding agent really do. These are the models to pick, and the settings that make them work.</p></div>
{sec(("daily", "Daily use: the house assistant"), "Chat, answer from your files, sort, rename, total, summarize, in English, Japanese and French. Speed matters: you wait for every reply.",
     "assistant tests and family jobs averaged (one model alone, its best harness setup), minus a little for each second per job.", d_cards)}
{sec(("coding", "Coding tasks"), "An agent (pi) that fixes bugs, adds features and refactors in a real repository, graded by hidden tests.",
     "half tasks solved in coding suites v1-v3, half hidden tests passed in the hard suite v4, with pi and no extensions where we ran it; ties go to the faster model.", c_cards)}
{sec(("smartest", "Smartest, any speed"), "The most capable model this Mac can run when you can wait: the closest to frontier quality you get locally.",
     "the average of assistant, coding (with the hard suite), image and hard-job scores; a model needs coding and at least three areas. Speed is ignored.", s_cards)}
<p class="note">Beyond 32 GB: <a href="models/glm-5.3.html">GLM-5.3 (744B)</a> runs on this Mac by streaming its experts from the SSD. It is rated well above anything here, and writes well under one token per second.</p>
<section class="sec" aria-labelledby="spec"><div class="sec-h"><h2 id="spec">Specialists</h2></div>
<ul class="specs">{spec_html}</ul></section>
<section class="sec" aria-labelledby="start"><div class="sec-h"><h2 id="start">Set it up in five minutes</h2></div>
<ol class="list">
<li>Install <a href="https://ollama.com/download">Ollama</a>, then pull the daily pick: {pull_box(daily[0][0]['pull'] if daily else 'ollama pull gemma4:12b-mlx')}</li>
<li>Prefer <code>-mlx</code> tags on Apple Silicon. They answer one request at a time; for several users at once, a non-MLX model with <code>OLLAMA_NUM_PARALLEL</code> set.</li>
<li>Thinking off by default (<code>"think": false</code>); turn it on only for exact image reading. It makes translation and transcription worse.</li>
<li>Keep the system prompt identical between requests so Ollama's prompt cache skips re-reading it, and put "answer briefly" in it.</li>
<li>Budget memory by the loaded size on each model page, not the download: one big model plus one small one fits in 32 GB, two big ones do not.</li>
<li>For speech, run <a href="models/whisper.html">Whisper</a> beside it, not a chat model that hears.</li>
</ol>
<p>Why each of these: <a href="learnings.html">Learnings</a>. Every model we tried: <a href="models.html">Models</a>.</p></section>
"""
    return shell("Local AI on a 32 GB Mac mini M6: what to run", body, "index.html",
                 desc="Top local AI models for a Mac mini M6 with 32 GB, measured: daily use, coding, smartest; plus every model's bench results and setup.")


# ---------------------------------------------------------------- models list
KIND = {"chat": "Chat and agents", "vision": "Images", "speech": "Speech", "router": "Router", "ssd": "Too big for RAM"}


def models_page(S):
    cards = []
    for m in MODELS:
        d, c, v, t, sp = S.daily(m), S.coding(m), S.vision(m), S.translation(m), S.speech(m)
        mem, dl, ws = S.mem(m), S.download(m), S.write_speed(m)
        tested, qz = S.tested(m), S.quiz(m)
        stats = [x for x in [d and f"Assistant {pct(d['q'])}", c and f"Coding {pct(c['q'])}", v and f"Images {pct(v['q'])}",
                             t and f"Translation chrF {fmt(t['mean'])}", sp and f"Speech {fmt(sp['mean'])}% errors", S.hard(m) and f"Hard jobs {pct(S.hard(m)['q'])}",
                             S.hard(m, "coding-v4") and f"Hard coding {pct(S.hard(m, 'coding-v4')['q'])}",
                             qz and f"October quiz {qz['correct']}/{qz['graded']}"] if x]
        sizes = [x for x in [dl and f"{fmt(dl)} GB download", mem and f"{mem[0]:g} GB loaded", ws and f"{ws} tokens/s"] if x]
        cards.append(
            f'<li class="mcard" data-kind="{m["kind"]}" data-name="{E(m["name"].lower())}" data-daily="{d["q"] if d else -1}" '
            f'data-coding="{c["q"] if c else -1}" data-mem="{mem[0] if mem else 999}" data-tested="{int(tested)}">'
            f'<a href="models/{m["slug"]}.html"><span class="mc-h"><span class="mc-n">{E(m["name"])}</span>'
            f'{"" if tested else "<span class=chip-w>benches running</span>"}</span>'
            f'<span class="mc-s">{E(m["summary"])}</span>'
            f'<span class="mc-c">{chips(m["abilities"])}<span class="chip chip-k">{E(KIND[m["kind"]])}</span><span class="chip">{E(m["arch"].split(",")[0])}</span></span>'
            f'<span class="mc-x">{" · ".join(E(x) for x in stats + sizes)}</span>'
            f'<span class="mc-t"><code>{E(m["tag"])}</code> · {E(m["maker"])}</span></a></li>')
    body = f"""
<div class="ptitle"><h1>Models we tested</h1>
<p class="dek">{len(MODELS)} models on the same Mac mini, from 1B-active test beds to a 744B model streamed from the SSD, with what each is good for, how we run it, and every bench result since October.</p></div>
<div class="filters" role="group" aria-label="Filter and sort">
<div class="fld"><span class="lbl">Show</span><div class="seg" id="kind">
<button type="button" aria-pressed="true" data-k="all">All</button><button type="button" aria-pressed="false" data-k="chat">Chat and agents</button><button type="button" aria-pressed="false" data-k="vision">Images</button><button type="button" aria-pressed="false" data-k="speech">Speech</button><button type="button" aria-pressed="false" data-k="router">Router</button><button type="button" aria-pressed="false" data-k="ssd">Too big for RAM</button></div></div>
<div class="fld"><label for="sort">Sort by</label><select id="sort"><option value="daily">Assistant score</option><option value="coding">Coding score</option><option value="mem">Memory, smallest first</option><option value="name">Name</option></select></div>
</div>
<ul class="mlist" id="mlist">{"".join(cards)}</ul>
<p class="note">Assistant: family jobs and assistant tests, best harness setup. Coding: hidden-test tasks solved, pi with no extensions where run. Images: both image suites. Percentages are of the maximum score. October quiz: last month's 24 graded questions, best setting; it is easy, so most models get it all right.</p>
<script>
(function(){{var L=document.getElementById('mlist'),k='all';
function apply(){{var s=document.getElementById('sort').value,c=[].slice.call(L.children);
c.sort(function(a,b){{if(s==='name')return a.dataset.name<b.dataset.name?-1:1;if(s==='mem')return a.dataset.mem-b.dataset.mem;return b.dataset[s]-a.dataset[s]||b.dataset.tested-a.dataset.tested}});
c.forEach(function(x){{x.hidden=k!=='all'&&x.dataset.kind!==k;L.appendChild(x)}})}}
document.querySelectorAll('#kind button').forEach(function(b){{b.onclick=function(){{k=b.dataset.k;document.querySelectorAll('#kind button').forEach(function(x){{x.setAttribute('aria-pressed',String(x===b))}});apply()}}}});
document.getElementById('sort').onchange=apply;apply()}})();
</script>
"""
    return shell("Models tested on a 32 GB Mac mini M6", body, "models.html", desc="Every local model we benched on a Mac mini M6 32 GB, like a model library with our own results.")


CODING_V4_TITLES = {"calc-evaluator": "Expression evaluator with exact error positions", "ics-recurrence": "iCalendar recurrence, time zones and DST",
                    "money-multicurrency": "Multi-currency money across a four-file package", "json-patch": "JSON Patch (RFC 6902), atomic",
                    "busy-intervals": "Overlap counting, correct and fast for 200,000 intervals"}


def managed_section(S, m, bench, titles, sid, heading, unit, how):
    """Per job of a hard suite: the steps (or hidden tests) a model got, and the ones it missed, over every run."""
    hruns = [r for r in S.rows if r["model"] in m["keys"] and r.get("bench") == bench and not r.get("set_aside") and r.get("jobs")]
    if not hruns:
        return ""
    items = []
    for jid, title in titles.items():
        js = [j for r in hruns for j in r["jobs"] if j["id"] == jid]
        if not js:
            continue
        n, steps = len(js), {}
        for j in js:
            for st, ok in j.get("steps") or []:
                steps[st] = steps.get(st, 0) + bool(ok)
        got = [E(st) + (f" ({k} of {n} runs)" if k < n else "") for st, k in steps.items() if k]
        miss = [E(st) + (f" ({n - k} of {n} runs)" if k else "") for st, k in steps.items() if k < n]
        errs = sorted({str(j["error"]).removeprefix("error: ") for j in js if j.get("error")})
        pts = ", ".join(f"{j['points']}/{j['out_of']}" for j in js)
        items.append(f'<li class="hjob"><strong>{E(title)}</strong> <span class="note">{pts}</span>'
                     + (f'<div><span class="ok">Managed:</span> {"; ".join(got)}</div>' if got else "")
                     + (f'<div><span class="ko">Fell short:</span> {"; ".join(miss)}</div>' if miss else "")
                     + (f'<div class="note">Stopped by: {E("; ".join(errs))}</div>' if errs else "") + "</li>")
    tot = ", ".join(f"{fmt(r['score'])}/{r['out_of']}" for r in hruns)
    return (f'<section class="sec" aria-labelledby="{sid}"><div class="sec-h"><h2 id="{sid}">{E(heading)}</h2></div>'
            f'<p class="note">{E(BENCH[bench][1])} {E(unit.capitalize())} per run: {tot}. {E(how)}</p>'
            f'<ul class="hjobs">{"".join(items)}</ul></section>')


# ---------------------------------------------------------------- model page
def model_page(S, m):
    d, c, v, t, sp = S.daily(m), S.coding(m), S.vision(m), S.translation(m), S.speech(m)
    mem, dl, ws = S.mem(m), S.download(m), S.write_speed(m)
    lt = next((S.load_times[k] for k in m["keys"] if k in S.load_times), None)
    tiles = [("Assistant", f"{pct(d['q'])}" if d else "—", d and f"family {runs(d['fam'])}"),
             ("Coding", f"{fmt(c['got'])}/{c['total']}" if c else "—", c and "tasks solved"),
             ("Images", pct(v["q"]) if v else "—", v and f"{runs(v['v1'])}, {runs(v['v2'])}"),
             ("Hard jobs", pct(S.hard(m)["q"]) if S.hard(m) else "—", S.hard(m) and f"{runs(S.hard(m)['g'])} steps"),
             ("Hard coding", pct(S.hard(m, "coding-v4")["q"]) if S.hard(m, "coding-v4") else "—",
              S.hard(m, "coding-v4") and f"{runs(S.hard(m, 'coding-v4')['g'])} tests"),
             ("Translation", f"chrF {fmt(t['mean'])}" if t else "—", t and t["setup"]),
             ("Speech errors", f"{fmt(sp['mean'])}%" if sp else "—", sp and len(m["keys"]) > 1 and sp["model"]),
             ("Download", f"{fmt(dl)} GB" if dl else "—", None),
             ("Loaded", f"{mem[0]:g} GB" if mem else "—", mem and (f"at {mem[1]} tokens of context" if mem[1] else "")),
             ("Writing speed", f"{ws} tok/s" if ws else "—", "one request"),
             ("Load and first word", f"{fmt(lt)} s" if lt else "—", lt and "file cache warm")]
    qz = S.quiz(m)
    if qz:
        tiles.append(("October quiz", f"{qz['correct']}/{qz['graded']}", f"best setting: {qz['style']}, {qz['bench']['labels'].get(qz['config'], qz['config'])}"))
    tiles = [t for t in tiles if t[1] != "—"]
    tiles_html = "".join(f'<div class="tile"><span class="tl">{k}</span><span class="tv">{E(val)}</span><span class="note">{E(sub or "")}</span></div>' for k, val, sub in tiles)
    facts = [("Maker", m["maker"]), ("Architecture", m["arch"]), ("Parameters", m["params"]), ("Context", m["context"]),
             ("Quantization", m["quant"]), ("Runs on", m["runner"]), ("License", m["license"])]
    facts_html = "".join(f"<div><dt>{k}</dt><dd>{E(val)}</dd></div>" for k, val in facts)
    links = " · ".join(f'<a href="{E(u)}">{E(l)}</a>' for l, u in m["links"])
    lst = lambda xs: '<ul class="list">' + "".join(f"<li>{inline(x)}</li>" for x in xs) + "</ul>" if xs else '<p class="note">Not yet known.</p>'

    # bench results, one table per bench
    res = []
    order = list(BENCH)
    for bench in sorted({g["bench"] for g in S.groups(m)}, key=order.index):
        gs = sorted(S.groups(m, bench), key=lambda g: (g["model"], -g["mean"] if bench != "speech-to-text" else g["mean"]))
        multi = len({g["model"] for g in gs}) > 1
        rows = []
        for g in gs:
            time = (f"{fmt(g['minutes'])} min" if g.get("minutes") else f"{fmt(g['seconds_each'])} s each" if g.get("seconds_each") else "")
            setup = (f"<code>{E(g['model'])}</code> · " if multi else "") + E(g["setup"]) + (f'<span class="note"> — {E(g["setup_meaning"])}</span>' if g.get("setup_meaning") else "")
            rows.append([setup, E(runs(g)), E(time), E(g["runs"])])
        name, what = BENCH.get(bench, (bench, ""))
        unit = {"translation": "chrF", "speech-to-text": "Error %"}.get(bench, "Score (each run)")
        res.append(f'<h3 id="{bench}">{E(name)}</h3><p class="note">{E(what)}</p>' + table(["Setup", unit, "Time", "Runs"], rows, (1, 2, 3)))
    set_aside = [r for r in S.rows if r["model"] in m["keys"] and r.get("set_aside")]
    if set_aside:
        res.append("<h3>Runs set aside</h3>" + table(["Run", "Why"], [[E(r["run"]), E(r["set_aside"])] for r in set_aside]))

    hard_html = managed_section(S, m, "family-hard", {t["id"]: t["title"] for t in family_hard.TASKS}, "hard",
                                "Hard jobs: what it managed and where it fell short", "points",
                                '"Kept"/"did not" steps only count when the job itself got somewhere.')
    hard_html += managed_section(S, m, "coding-v4", CODING_V4_TITLES, "hardcode",
                                 "Hard coding: what it managed and where it fell short", "hidden tests",
                                 "Each line is one hidden test, named after what it checks.")

    # last month's quiz
    oct_html = ""
    qrows = []
    for name in m.get("oct", []):
        d = S.oct.get(name)
        if not d:
            continue
        for r in sorted(d["summary"], key=lambda r: (r["style"], r["config"] != "exact", r["config"])):
            build = (f"{E(d['model'])} · " if len(m["oct"]) > 1 else "")
            qrows.append([build + E(r["style"]) + " · " + E(d["labels"].get(r["config"], r["config"])), f"{r['correct']}/{r['graded']}",
                          fmt(round(r["answer_s"], 1)), fmt(round(r["decode_tok_s"], 2))])
    if qrows:
        oct_html = ('<section class="sec" aria-labelledby="quiz"><div class="sec-h"><h2 id="quiz">October quiz: 24 graded questions</h2></div>'
                    '<p class="note">Last month\'s first bench: 24 short questions (facts, arithmetic, a little code and Japanese) graded automatically, asked two ways: '
                    '<em>normal</em> and <em>terse</em> (answer as briefly as possible). For Ollama, <em>exact</em> is thinking off, temperature zero and a short output budget; '
                    '<em>Ollama defaults</em> is thinking on with up to 4,096 tokens. For GLM, <em>exact</em> computes every expert; <em>skip 0.3</em> and <em>skip 0.5</em> skip experts '
                    'whose routing weight is below that value, which is faster but changes the output. One run per setting on a busy Mac.</p>'
                    + table(["Prompt · setting", "Correct", "Seconds per answer", "Writing tokens/s"], qrows, (1, 2, 3))
                    + f'<p class="note">Answers and grading: <a href="../bench.html">Benchmarks</a> · data: '
                    + " · ".join(f'<a href="../data/bench-{E(n)}.json">{E(n)}</a>' if n != "v1" else '<a href="../data/bench-v1.json">v1</a>' for n in m["oct"] if n in S.oct)
                    + "</p></section>")
    par_rows = [[E(x["model"]), "yes" if x["status"] == "supported" else "no", fmt(round(x["capability"]["wall_s"], 1)), fmt(round(x["capability"]["total_tok_s"], 1))]
                for x in S.par if x["model"].removesuffix("-mlx") == m["tag"].removesuffix("-mlx")]
    if par_rows:
        oct_html += ('<section class="sec" aria-labelledby="par"><div class="sec-h"><h2 id="par">Two requests at once (October)</h2></div>'
                     '<p class="note">Two clients sent a request at the same moment. "Overlapped" means the server wrote both replies at the same time instead of one after the other.</p>'
                     + table(["Model", "Overlapped", "Seconds for both", "Tokens per second, both"], par_rows, (2, 3)) + "</section>")
    if m.get("extra") == "glm" and S.glm:
        g = S.glm
        prog = [[E(g["starting_point"]["measured_label"]), fmt(g["starting_point"]["measured_tok_s"]), fmt(round(g["starting_point"]["measured_prefill_s"]))]]
        prog += [[E(x["label"]), fmt(x["tok_s"]), fmt(round(x["prefill_s"]))] for x in g["exact_progression"]]
        neg = [[E(x["label"]), fmt(x["tok_s"]) if x["tok_s"] is not None else E(x["status"].replace("_", " "))] for x in g["exact_negative"]]
        lossy = [[E(x["label"]), fmt(x["tok_s"]), E(x.get("quality", ""))] for x in g.get("lossy_threshold_curve", [])]
        h, c = g["headline"], g["ceiling"]
        oct_html += (f'<section class="sec" aria-labelledby="glmspeed"><div class="sec-h"><h2 id="glmspeed">Making it faster</h2></div>'
                     f'<p>Each step below was kept because it helped; exact output throughout. The engine as first installed wrote {fmt(g["starting_point"]["measured_tok_s"])} tokens per second; '
                     f'the best exact setup wrote {fmt(h["exact_tok_s"])}, {fmt(h["exact_speedup_vs_start"])} times faster. Skipping low-weight experts reached {fmt(h["lossy_tok_s_at_0_5"])} tokens per second '
                     f'with changed output. The ceiling: about {fmt(c["expert_bytes_per_token_gb"])} GB of experts per token over an SSD that delivers about {fmt(c["ssd_max_gbs"])} GB/s '
                     f'caps exact decoding near {E(c["exact_ceiling_tok_s"])} tokens per second; {fmt(c["target_tok_s"])} tokens per second would need about {fmt(c["needed_gbs_at_5_tok_s"])} GB/s.</p>'
                     + table(["Step (exact)", "Tokens/s", "Seconds reading the prompt"], prog, (1, 2))
                     + "<h3>Tried and not kept</h3>" + table(["Change", "Tokens/s"], neg, (1,))
                     + ("<h3>Skipping experts: speed against output</h3>" + table(["Setting", "Tokens/s", "Output"], lossy, (1,)) if lossy else "")
                     + f'<p class="note">{" ".join(E(x) for x in g["caveats"][:2])} Full detail: <a href="../glm.html">GLM report</a> · <a href="../glm-speed.html">every speed run</a> · <a href="../findings.html">findings</a>.</p></section>')

    # speed
    spd = next((S.speed[k] for k in m["keys"] if k in S.speed), None)
    speed_html = ""
    if spd:
        names = {"first": "First request", "cached": "Same system prompt again", "terse": "Asked for a terse answer"}
        rows = [[names[k], fmt(x["prompt_tokens"]), fmt(x["prompt_s"]), fmt(x["out_tokens"]), fmt(x["out_s"]), fmt(x["wall"])] for k, x in spd.items() if k in names]
        par = [[f"{k.split('-')[1]} at once", fmt(x["wall"]), fmt(x["out_tokens"]), fmt(x["tokens_per_s"])] for k, x in sorted(spd.items()) if x.get("fixed_len")]
        speed_html = ('<section class="sec" aria-labelledby="speed"><div class="sec-h"><h2 id="speed">Speed</h2></div>'
                      '<p class="note">A system prompt the size of the assistant\'s (about 4,700 tokens) and one everyday question.</p>'
                      + table(["Request", "Prompt tokens", "Seconds reading", "Tokens written", "Seconds writing", "Total seconds"], rows, (1, 2, 3, 4, 5))
                      + (table(["Requests", "Seconds until all done", "Tokens written", "Tokens per second, all"], par, (1, 2, 3)) if par else "")
                      + "</section>")

    body = f"""
<nav class="crumb note" aria-label="Breadcrumb"><a href="../models.html">Models</a> / {E(m["name"])}</nav>
<div class="ptitle"><h1>{E(m["name"])}</h1><p class="dek">{E(m["summary"])}</p>
<div class="mc-c">{chips(m["abilities"])}<span class="chip chip-k">{E(KIND[m["kind"]])}</span></div></div>
<section class="sec" aria-label="Get it">{pull_box(m["pull"])}<p class="note">{links}</p></section>
{f'<section class="sec" aria-labelledby="glance"><div class="sec-h"><h2 id="glance">At a glance</h2></div><div class="tiles">{tiles_html}</div></section>' if tiles else ""}
<div class="two">
<section class="sec" aria-labelledby="good"><div class="sec-h"><h2 id="good">Good for</h2></div>{lst(m["good"])}</section>
<section class="sec" aria-labelledby="weak"><div class="sec-h"><h2 id="weak">Not good for</h2></div>{lst(m["weak"])}</section>
</div>
<section class="sec" aria-labelledby="setup"><div class="sec-h"><h2 id="setup">How to run it well</h2></div>{lst(m["setup"])}
<p class="note">The code behind these setups: <a href="{REPO}harness/dropbox-mini/mini.py">assistant harness</a> · <a href="https://github.com/KakkoiDev/pi-core/tree/cutoff-cause">pi extensions</a> · <a href="../learnings.html#setup">how our setup works</a></p></section>
{f'<section class="sec" aria-labelledby="obs"><div class="sec-h"><h2 id="obs">What we saw</h2></div>{lst(m["notes"])}</section>' if m["notes"] else ""}
<section class="sec" aria-labelledby="facts"><div class="sec-h"><h2 id="facts">Model facts</h2></div><dl class="facts">{facts_html}</dl></section>
{hard_html}
{speed_html}
{f'<section class="sec" aria-labelledby="results"><div class="sec-h"><h2 id="results">All bench results</h2></div>{"".join(res)}</section>' if res else ("" if oct_html else '<section class="sec"><p>Benches are running; results appear here as they land.</p></section>')}
{oct_html}
"""
    return shell(f"{m['name']} on a 32 GB Mac mini M6", body, "models.html", depth=1, desc=m["summary"])


# ---------------------------------------------------------------- the two bench modes
def speed_page(S):
    rows = []
    for m in MODELS:
        sp = next((S.speed[k] for k in m["keys"] if k in S.speed), None)
        if not sp or "first" not in sp:
            continue
        f, c, t = sp["first"], sp.get("cached"), sp.get("terse")
        th, x32, l8, l32 = sp.get("think"), sp.get("ctx32k"), sp.get("loaded"), sp.get("loaded-32k")
        write = f["out_tokens"] / f["out_s"] if f.get("out_s") else None
        read = f["prompt_tokens"] / f["prompt_s"] if f.get("prompt_s") else None
        par = {int(k.split("-")[1]): v for k, v in sp.items() if k.startswith("parallel-") and v.get("fixed_len")}
        best = []
        if c and f.get("prompt_s") and c["prompt_s"] < f["prompt_s"] / 2:
            best.append(f"fixed system prompt: reading {fmt(f['prompt_s'])} s → {fmt(c['prompt_s'])} s")
        if t:
            best.append(f"terse replies: {fmt(f['wall'])} s → {fmt(t['wall'])} s")
        if th:
            best.append("thinking off" + (f" (on: {fmt(th['wall'])} s)" if th["wall"] > f["wall"] * 1.2 else ": on costs little"))
        if l8 and l32:
            best.append(f"8k context: {l8['size_gb']:g} GB loaded, 32k: {l32['size_gb']:g} GB")
        if par:
            one = par.get(1, {}).get("tokens_per_s")
            top = max(par.items(), key=lambda kv: kv[1]["tokens_per_s"])
            best.append(f"{top[0]} requests at once: {fmt(top[1]['tokens_per_s'])} tok/s in all" + (f" (1 alone: {fmt(one)})" if one and top[0] != 1 else "")
                        if one and top[1]["tokens_per_s"] > one * 1.3 else "one request at a time: parallel requests just queue")
        rows.append([f'<a href="models/{m["slug"]}.html">{E(m["name"])}</a>', fmt(round(write)) if write else "—", fmt(round(read)) if read else "—",
                     fmt(f["wall"]), fmt(t["wall"]) if t else "—", f"{l8['size_gb']:g}" if l8 else "—",
                     "<br>".join(E(b) for b in best)])
    rows.sort(key=lambda r: -float(r[1]) if r[1] != "—" else 0)
    glm = ""
    if S.glm:
        h = S.glm["headline"]
        glm = (f'<p>Beyond memory: <a href="models/glm-5.3.html">GLM-5.3 744B</a> streamed from the SSD writes {fmt(h["exact_tok_s"])} tokens/s exact, '
               f'and {fmt(h["lossy_tok_s_at_0_5"])} tokens/s when low-weight experts are skipped (the output changes). See <a href="models/glm-5.3.html#glmspeed">how we got there</a>.</p>')
    body = f"""
<div class="ptitle"><h1>Speed</h1>
<p class="dek">How fast each model is on the Mac mini M6, and which settings make it faster. Speed mode: <code>lab.py speed MODEL</code>, minutes per model.</p></div>
<section class="sec">{table(["Model", "Writing tok/s", "Prompt reading tok/s", "Seconds, first reply", "Seconds, terse reply", "GB loaded (8k)", "Best speed settings, measured"], rows, (1, 2, 3, 4, 5))}
<p class="note">One request with a system prompt the size of the assistant's (about 4,700 tokens) and an everyday question; then the same prompt again, a terse reply, thinking on, a 32k context, and 1, 2 and 4 requests at once with 256-token replies. Models without a row have not run speed mode yet.</p>
{glm}</section>
"""
    return shell("Speed: local models on a 32 GB Mac mini M6", body, "speed.html", desc="Writing speed, prompt reading, memory and the fastest settings for each local model on a Mac mini M6 32 GB.")


def intelligence_page(S):
    cols = [("Assistant", lambda m: S.daily(m) and pct(S.daily(m)["q"])), ("Hard jobs", lambda m: S.hard(m) and pct(S.hard(m)["q"])),
            ("Coding", lambda m: S.coding(m) and pct(S.coding(m)["q"])), ("Hard coding", lambda m: S.hard(m, "coding-v4") and pct(S.hard(m, "coding-v4")["q"])),
            ("Images", lambda m: S.vision(m) and pct(S.vision(m)["q"])), ("Translation chrF", lambda m: S.translation(m) and fmt(S.translation(m)["mean"])),
            ("Speech errors", lambda m: S.speech(m) and f"{fmt(S.speech(m)['mean'])}%"), ("October quiz", lambda m: S.quiz(m) and f"{S.quiz(m)['correct']}/24")]
    rows = []
    for m in MODELS:
        vals = [f(m) or "—" for _, f in cols]
        if all(v == "—" for v in vals):
            continue
        sm = S.smartest(m)
        rows.append((sm["q"] if sm else -1, [f'<a href="models/{m["slug"]}.html">{E(m["name"])}</a>', pct(sm["q"]) if sm else "—"] + [E(v) for v in vals]))
    rows.sort(key=lambda r: -r[0])
    body = f"""
<div class="ptitle"><h1>Intelligence</h1>
<p class="dek">What each model can actually do, with the harness it would run in. Intelligence mode: <code>lab.py intelligence MODEL</code>, hours per model.</p></div>
<section class="sec">{table(["Model", "Overall"] + [c for c, _ in cols], [r for _, r in rows], tuple(range(1, len(cols) + 2)))}
<p class="note">Overall: the average of assistant, coding (suites v1-v3 and the hard suite, half each), hard jobs and images, where run (at least two). The hard suites are made so that no model gets everything, so they spread the strong models apart; each model's page says what it managed and where it fell short. Percentages are of the maximum score.</p></section>
"""
    return shell("Intelligence: local models on a 32 GB Mac mini M6", body, "intelligence.html", desc="What each local model can do on a Mac mini M6 32 GB: assistant, hard jobs, coding, images, translation, speech.")


# ---------------------------------------------------------------- learnings
LEARNINGS = [
    ("Thinking", [
        ("Turn thinking off for translation and transcription.", "Every model translated worse with thinking on and took many times longer; some never answered. Speech recognition by models that hear got worse too.", [1]),
        ("Thinking helps image reading a little, at a high price.", "Image scores went up or stayed the same with thinking on, but each image took many times longer. Use it only when exactness matters.", [2]),
        ("More reasoning effort did not make a better coding agent.", "gpt-oss at high effort solved fewer tasks than at medium, and took longer. Medium for coding, low for chat.", [3]),
    ]),
    ("Choosing models", [
        ("One capable model beats a pair of small ones for file work.", "Sending chat to a small fast model and file work to a tool model passed fewer family jobs than one mid-size model doing everything, and was not faster.", [4]),
        ("A 12B can be your butler if it does not need to code.", "gemma4:12b matched Qwen 3.8 27B on assistant and family jobs, faster and in less than half the memory. It cannot code and hears poorly.", [5]),
        ("Use Whisper for speech.", "Whisper made far fewer errors than any chat model that accepts audio, in every language. Noise hurts French most.", [10]),
        ("The smallest models translate short messages best.", "Translation did not improve with size: the small Gemma models led, and were also the fastest.", [11]),
        ("Route with rules first, then a one-word question.", "Simple rules (a file name, an attachment, tool words) route most messages with no model at all. For the rest, asking a small model for one word (CHAT or TOOLS) did best.", [12]),
        ("Fast writing is not fast or safe file work.", "Qwen 3.6 35B-A3B (mixture of experts) writes several times faster than the dense 27B, yet several family jobs hit the 10-minute limit, and in both runs it emptied a folder it was only asked to clean out, without asking. Test the jobs you need, not tokens per second.", []),
    ]),
    ("The harness around the model", [
        ("House rules and after-turn checks lift weak models.", "Checking that a claimed save really happened, and sending the model back when it answered without looking, raised every weaker model's score.", [6]),
        ("Test every harness change on every model.", "One rule (do sort and move requests at once) made the 12B clear out a folder without asking. A follow-up rule (vague clean-up requests: list, suggest, ask) fixed it.", [6]),
        ("Coding extensions help weak tool users only.", "Repairing broken tool calls turned gemma4:26b into a working coding agent. For gpt-oss and Qwen, which call tools well, plain pi did as well or better.", [7]),
        ("Self-review and multiple tries rarely pay.", "Reviewing its own change, trying two or four times, or pairing two agents took two to five times longer and scored about the same as one plain run.", [8, 9]),
    ]),
    ("Memory and speed", [
        ("Plan memory by the loaded size, not the download.", "A loaded model takes more than its download because of the context cache and runtime buffers. Two models that fit by download can push 32 GB into swap; when that happened, an app crashed and the run scored lower.", [13]),
        ("Switching models is cheap.", "Loading a stopped model and getting its first word took seconds with a warm file cache. Swapping a butler for a builder model when the house is quiet costs little.", [13]),
        ("Keep the system prompt fixed.", "Ollama's prompt cache skips re-reading an identical system prompt. Changing even one line (say, adding \"be brief\" per request) pays the full reading cost again.", [14]),
        ("Ask for short answers.", "Writing is most of a reply's time. A two-sentence answer writes a fraction of the tokens. Put the rule in the fixed system prompt.", [14]),
        ("MLX is fastest, one request at a time.", "Ollama's MLX runner (tags ending in -mlx) answers one request at a time; several at once just queue. The standard runner can serve several with OLLAMA_NUM_PARALLEL; whether that raises total throughput is being measured.", [14]),
    ]),
    ("Giant models from the SSD (GLM-5.3, October)", [
        ("A model ten times bigger than memory runs, but slowly.", "GLM-5.3 (744B, mixture of experts) runs on the 32 GB Mac by reading the experts each token needs from the SSD. It answered our quiz correctly, at well under one token per second.", [("GLM report", "glm.html"), ("GLM-5.3 page", "models/glm-5.3.html")]),
        ("The SSD sets the speed limit.", "Each token needs about 12.7 GB of experts; the SSD delivers about 6.5 GB/s. No setting can make exact decoding faster than roughly half a token per second on this Mac.", [("GLM speed runs", "glm-speed.html")]),
        ("Use the GPU and overlap the reads.", "Moving expert math to Metal and reading the next experts while computing the current ones were the big wins: several times faster than the engine as first installed.", [("GLM report", "glm.html")]),
        ("Caching experts in RAM barely helps.", "Giving the engine more memory for an expert cache hardly raised the hit rate: the experts a prompt needs change from token to token.", [("Findings", "findings.html")]),
        ("If a model fits in memory, keep it all there.", "On the small OLMoE, streaming experts through a cache was more than twice as slow as keeping every expert loaded, even with a cache big enough for all of them: loading and packing cost time on every token. Streaming is for models that cannot fit.", [("Findings", "findings.html"), ("OLMoE page", "models/olmoe-1b-7b.html")]),
        ("Skipping experts is faster and changes the answer.", "Skipping experts with a low routing weight wrote up to four times faster, but changed the text; at higher thresholds answers broke. Treat it as a different model and check the answers.", [("GLM-5.3 page", "models/glm-5.3.html#glmspeed")]),
        ("Smaller files are not always faster.", "The 2- and 3-bit GGUF builds of GLM read less from the SSD but were slower than int4: unpacking them cost more than the reads saved.", [("GLM-5.3 page", "models/glm-5.3.html#quiz")]),
        ("Thread count depends on the core layout.", "On the M6 the engine sized itself to the two fastest cores. Twelve threads made CPU-only and lossy runs faster; exact GPU runs did not change.", [("GLM speed runs", "glm-speed.html")]),
    ]),
    ("Last month's quiz (October)", [
        ("Ask for terse answers, then check them.", "On the 24-question quiz, asking for the briefest answer cut GLM's answer time about four times and kept every GLM build and most Ollama models at or near full marks. One exception: gpt-oss with thinking off got most terse answers wrong. Check each model.", [("Comparison", "comparison.html"), ("gpt-oss page", "models/gpt-oss-20b.html#quiz")]),
        ("A short output budget hides what a model knows.", "With thinking off and a short budget, normal answers were often cut off and scored lower; with the model's defaults the same models scored near full marks.", [("Comparison", "comparison.html")]),
        ("Bigger is not better on easy questions.", "Gemma 4 31B scored no higher than the 26B mixture-of-experts Gemma, which writes about three times faster. Gemma 4 E4B got everything right fastest when asked to be terse.", [("Gemma 4 31B", "models/gemma4-31b.html"), ("Gemma 4 E4B", "models/gemma4-e4b.html")]),
        ("Not every model serves two requests at once.", "Asked twice at the same moment, gpt-oss on Ollama wrote both replies together; Gemma 4 26B and Qwen 3.8 answered one after the other.", [("Parallel tests", "concurrency.html")]),
        ("An easy quiz cannot rank strong models.", "Most models got 24/24 with the right setting, so we moved on to the harder benches above: family jobs, coding with hidden tests, images, translation and speech.", []),
    ]),
    ("New engines and models (November 2026)", [
        ("A small mixture-of-experts coder can beat the dense 27B at coding.", "Flint 40B-A2.5B, which uses only about 2.5B weights per token, topped the hard coding suite and nearly aced suites v1-v3, ahead of Qwen 3.8 27B. It reasons at length on every reply, so it is a coder, not a butler.", [("Flint", "models/flint-40b.html")]),
        ("A compressed model can keep assistant skills and lose coding.", "Ternary Bonsai 2 (Qwen 3.8 27B in about a third of the memory) stayed close on assistant and family jobs but fell far behind on hard coding. Check the jobs you need before trusting a compression claim.", [("Bonsai 2", "models/bonsai-2-27b.html")]),
        ("A faster engine is not a better coding agent by itself.", "The same Qwen 3.8 27B solved far fewer hard coding tests on MTPLX and on oMLX than on Ollama: one long reasoning turn hit the output limit on MTPLX, and oMLX throttled prompt reading under memory pressure. Bench the agent, not the decode speed.", [("MTPLX", "models/qwen3.8-27b-mtplx.html"), ("oMLX", "models/qwen3.8-27b-omlx.html")]),
        ("Models four to twelve times larger than memory run, with the right settings.", "ds4 ran GLM-5.3-Flash (97 GB), Qwen3.8-Flash-Next (147 GB) and DeepSeek V4.1 Flash (366 GB) on the 32 GB Mac by streaming experts from the SSD, after three fixes: start it from its own folder, lower GLM's built-in 32 GiB memory reserve, and give DeepSeek a smaller context and an expert cache sized by count.", [("DeepSeek V4.1", "models/deepseek-v4.1-flash.html")]),
        ("Leave memory for macOS.", "Engine memory budgets set close to 32 GB (SlotStream at 26 GB, MTPLX Optimized at 26 GB) made the engines refuse prompts mid-run. Their automatic plans, or about 23 GB, kept them working.", []),
        ("Name the engine in every run.", "The same model on two engines must not share a result folder: our Bonsai runs on MTPLX overwrote the Ollama ones until the engine became part of the name.", []),
    ]),
    ("Harness v3.2 (November 2026)", [
        ("Make risky requests read-only.", "With vague clean-ups running read-only, no model deleted or moved anything on \"clean up my old folder\" in 31 runs; 30 listed the files and asked, as the house rule says (one changed nothing but did not ask).", []),
        ("Most models now ace the assistant tests.", "On harness v3.2 almost every model scored full marks on the assistant tests in English and in Japanese and French; the language check and retrying empty turns removed most earlier failures.", []),
        ("Make hard tests harder than you think.", "The hard family jobs were meant to be beyond every model, yet the strong ones scored 54-55.5 of 56. The hard coding suite spread models far more (from about a third to over 90%). The next hard family suite needs longer, messier jobs.", []),
    ]),
    ("Testing", [
        ("Read the failures, not just the score.", "Reading failures one by one found three grader bugs that marked right answers wrong. Small benches need their failures read.", [15]),
        ("Repeat runs before believing a gap.", "Suites are small (5 coding tasks, 12 family jobs); one task can be the whole difference. Look at the repeats; a single run is an indication.", []),
    ]),
]


def learnings_page(S):
    secs = []
    for title, tips in LEARNINGS:
        items = []
        for head, text, fs in tips:
            ev = " ".join(f'<a href="{E(f[1])}">{E(f[0])}</a>' if isinstance(f, tuple) else f'<a href="local-ai.html#{S.fslug[f]}">F{f}</a>'
                          for f in fs if isinstance(f, tuple) or f in S.fslug)
            items.append(f'<li class="tip"><strong>{inline(head)}</strong> {inline(text)}{f" <span class=note>Evidence: {ev}</span>" if ev else ""}</li>')
        sid = re.sub(r"\W+", "-", title.lower()).strip("-")
        secs.append(f'<section class="sec" aria-labelledby="{sid}"><div class="sec-h"><h2 id="{sid}">{E(title)}</h2></div><ul class="tips">{"".join(items)}</ul></section>')
    code = "".join(f'<li><a href="{E(u)}">{E(n)}</a> — {E(d)}</li>' for n, u, d in CODE)
    body = f"""
<div class="ptitle"><h1>Learnings</h1>
<p class="dek">What works and what does not when you run AI on a 32 GB Mac mini, from our benches since October: running a 744B model from the SSD, last month's quiz, and this month's assistant, coding, image, translation and speech benches. Each tip links to its evidence.</p></div>
{"".join(secs)}
<section class="sec" aria-labelledby="setup"><div class="sec-h"><h2 id="setup">How our setup works</h2></div>
<ol class="list">
<li><strong>Ollama serves every chat model</strong> on the Mac. MLX tags for speed; <code>OLLAMA_NUM_PARALLEL=4</code> for the standard runner; models stay loaded for an hour after use; 8k context for chat, 32k for coding.</li>
<li><strong>The assistant, mini, is a harness around the pi agent.</strong> For each chat request it runs one pi turn in a macOS sandbox that can only see the asker's files. Its prompt carries house rules (search before answering, do the job yourself, ask before deleting, totals with a tool, reply in the asker's language). After the turn, checks look for three silent failures: a file it was asked to save is missing, it claims a change but no file changed, or it answered without looking. A failed check gets one more turn.</li>
<li><strong>Routing (optional):</strong> rules first; if unsure, a small model answers CHAT or TOOLS. Our benches found one capable model better than a split, so routing is off by default.</li>
<li><strong>The coding agent is pi</strong> with Ollama as its provider. Extensions from pi-core repair broken tool calls and stop loops; they help models that call tools poorly and are best left off for strong ones.</li>
<li><strong>Speech goes to Whisper</strong> (mlx-whisper, large-v3-turbo), outside Ollama.</li>
<li><strong>The benches drive this same code:</strong> the assistant benches run the real mini.py against a stand-in chat server, so the harness tested is the one that ships.</li>
</ol>
<h3>The code</h3><ul class="list">{code}</ul></section>
<section class="sec" aria-labelledby="more"><div class="sec-h"><h2 id="more">More</h2></div>
<p>The <a href="local-ai.html">full research note</a> has every finding with its tables, methods and limits. October's work is still published in full: the <a href="glm.html">GLM report</a>, <a href="bench.html">quiz answers</a>, <a href="comparison.html">quiz comparison</a>, <a href="glm-speed.html">GLM speed runs</a>, <a href="findings.html">GLM findings</a> and <a href="concurrency.html">requests at once</a>.</p></section>
"""
    return shell("Learnings: running local AI on a 32 GB Mac mini M6", body, "learnings.html",
                 desc="Tips and observations from benching local models on a Mac mini M6 32 GB: thinking, memory, harness, coding agents, speech, translation.")


CSS = """
/* v2 pages: home, models, model, learnings */
.v2{display:flex;flex-direction:column;gap:36px;padding-bottom:24px}
.v2 .dek2{color:var(--fg-2)}
.v2 .list{padding-left:22px;display:flex;flex-direction:column;gap:6px}
.v2 h2{font-size:24px;line-height:30px}
.v2 h3{font-size:18px;line-height:26px;margin-top:14px}
.picks{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(280px,100%),1fr));gap:14px}
.pick a.pick-a{display:flex;flex-direction:column;gap:4px;padding:14px 16px 16px;border:1px solid var(--hair);color:var(--fg);text-decoration:none;height:100%}
.pick.first a.pick-a{border:2px solid var(--fg)}
.pick a.pick-a:hover{background:var(--tint)}
.rank{font-size:13px;font-weight:700;font-variant:small-caps;color:var(--fg-2)}
.rank::before{content:"No. "}
.pick-n{font-size:21px;line-height:27px;font-weight:600}
.pick-t{color:var(--fg-2)}
.pick-l{display:flex;flex-direction:column;font-size:14.5px;line-height:21px;margin-top:4px}
.pick-m{font-size:13px;color:var(--fg-2)}
.specs{list-style:none;padding:0;display:grid;grid-template-columns:repeat(auto-fit,minmax(min(260px,100%),1fr));gap:10px 24px}
.specs li{display:flex;flex-direction:column;border-top:1px solid var(--hair);padding-top:8px}
.sp-k{font-size:13px;font-weight:700;font-variant:small-caps;letter-spacing:.04em;color:var(--fg-2)}
.pull{display:flex;align-items:center;gap:8px;flex-wrap:wrap;background:var(--tint);padding:8px 12px;max-width:100%}
.pull code{font-size:14px;overflow-wrap:anywhere}
.chip,.chip-w{display:inline-block;font-size:12px;line-height:16px;padding:2px 7px;border:1px solid var(--hair);margin:0 6px 4px 0;white-space:nowrap}
.chip-k{border-color:var(--fg)}
.chip-w{border-style:dashed;color:var(--fg-2)}
.mlist{list-style:none;padding:0;display:flex;flex-direction:column}
.mcard a{display:flex;flex-direction:column;gap:4px;padding:16px 4px;border-bottom:1px solid var(--hair);color:var(--fg);text-decoration:none}
.mcard a:hover{background:var(--tint)}
.mc-h{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap}
.mc-n{font-size:20px;line-height:26px;font-weight:600}
.mc-s{color:var(--fg-2)}
.mc-x{font-size:14px;line-height:20px;font-variant-numeric:tabular-nums}
.mc-t{font-size:13px;color:var(--fg-2)}
.tiles{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(170px,100%),1fr));border-top:1px solid var(--hair);border-left:1px solid var(--hair)}
.tile{display:flex;flex-direction:column;padding:10px 12px;border-right:1px solid var(--hair);border-bottom:1px solid var(--hair)}
.tl{font-size:13px;color:var(--fg-2)}
.tv{font-size:22px;line-height:30px;font-weight:600;font-variant-numeric:tabular-nums}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(340px,100%),1fr));gap:24px 40px}
.facts{display:grid;grid-template-columns:repeat(auto-fill,minmax(min(230px,100%),1fr));gap:8px 24px}
.facts dt{font-size:13px;color:var(--fg-2)}
.facts dd{margin:0}
.tips{list-style:none;padding:0;display:flex;flex-direction:column;gap:12px;max-width:820px}
.tip{border-left:2px solid var(--fg);padding-left:12px}
.hjobs{list-style:none;padding:0;display:flex;flex-direction:column;gap:12px;max-width:900px}
.hjob{border-left:2px solid var(--hair);padding-left:12px;display:flex;flex-direction:column;gap:2px}
.hjob .ok,.hjob .ko{font-weight:600}
.crumb{padding-top:20px;margin-bottom:-24px}
.v2 .tblbox{overflow-x:auto}
.mdt{border-collapse:collapse;width:100%;font-size:14.5px;line-height:21px;border-top:2px solid var(--fg);border-bottom:2px solid var(--fg)}
.mdt th,.mdt td{text-align:left;padding:6px 12px 6px 0;vertical-align:top}
.mdt thead th{border-bottom:1px solid var(--fg);font-weight:600}
.mdt tbody tr+tr th,.mdt tbody tr+tr td{border-top:1px solid var(--hair)}
.mdt tbody th{font-weight:400}
.mdt td.n{font-variant-numeric:tabular-nums;white-space:nowrap}
"""


def build(root, summary, rows, data, sizes, load_times, machine, doc_text, today):
    site = Path(root) / "site"
    fslug = {}
    for line in doc_text.splitlines():
        mm = re.match(r"### (F(\d+)\..*)", line)
        if mm:
            fslug[int(mm.group(2))] = md_page.slug(mm.group(1))
    S = Site(summary, rows, data, sizes, load_times, machine, fslug)
    S.today = today
    dd = site / "data"
    for b in json.loads((dd / "benches.json").read_text())["benches"]:
        f = dd / Path(b["file"]).name
        if f.exists():
            S.oct[b["name"]] = json.loads(f.read_text())
    if (dd / "parallel-capability.json").exists():
        S.par = json.loads((dd / "parallel-capability.json").read_text())
    if (dd / "glm-performance.json").exists():
        S.glm = json.loads((dd / "glm-performance.json").read_text())
    (site / "v2.css").write_text(CSS.lstrip())
    (site / "index.html").write_text(home(S))
    (site / "models.html").write_text(models_page(S))
    (site / "learnings.html").write_text(learnings_page(S))
    (site / "speed.html").write_text(speed_page(S))
    (site / "intelligence.html").write_text(intelligence_page(S))
    (site / "models").mkdir(exist_ok=True)
    for m in MODELS:
        (site / "models" / f"{m['slug']}.html").write_text(model_page(S, m))
    return len(MODELS)
