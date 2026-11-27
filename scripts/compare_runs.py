"""Compare two setups on the same items with a paired difference and an error bar (2026-11-27).

Single runs on a handful of tasks are mostly noise ("3/5 vs 2/5"). Following Miller, "Adding Error Bars to Evals"
(Anthropic, arXiv:2411.00640), as livenerf does: score each item (a mini case, an expert job, a coding task) in every
run, average an item over its runs per setup, take the per-item difference B - A, and report its mean with the
standard error over items (items are the clusters; repeated runs of an item only make its own score less noisy).

    .venv/bin/python scripts/compare_runs.py --a 'results/dropbox-mini-en-*exact*' --b 'results/dropbox-mini-en-*lossy*'

Items: mini cases score 1/0 (ok), expert jobs score points/out_of, coding tasks hidden_passed/hidden_total. Coding
tasks also give output tokens per item, a second signal (thinking shrinking or growing shows there first).
Prints a summary; --json writes it.
"""
import argparse
import glob
import json
import math
from pathlib import Path


def items_of(run_dir):
    """{item: (score 0..1, output tokens or None)} for one run folder."""
    d = Path(run_dir)
    if (d / "results.json").exists():
        rows = json.loads((d / "results.json").read_text()).get("rows", [])
        out = {}
        for r in rows:
            if "points" in r and r.get("out_of"):
                out[r["id"]] = (r["points"] / r["out_of"], None)
            else:
                out[r["id"]] = (1.0 if r.get("ok") else 0.0, None)
        return out
    out = {}
    for f in sorted(d.glob("*/result.json")):
        r = json.loads(f.read_text())
        if r.get("hidden_total"):
            out[r["task"]] = (r["hidden_passed"] / r["hidden_total"], r.get("output_tokens"))
    return out


def per_item(dirs):
    """{item: [scores over runs]}, {item: [tokens over runs]}."""
    scores, tokens = {}, {}
    for d in dirs:
        for k, (s, t) in items_of(d).items():
            scores.setdefault(k, []).append(s)
            if t is not None:
                tokens.setdefault(k, []).append(t)
    return scores, tokens


T975 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145, 2.131,
        2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def t975(df):
    """Two-sided 95% Student t critical value: with few items 1.96 makes the interval far too narrow."""
    return T975[df - 1] if 1 <= df <= len(T975) else 1.96


def mean(x):
    return sum(x) / len(x)


def paired(a, b):
    """Mean of per-item differences (b - a) over items both have, and its standard error over items."""
    common = sorted(set(a) & set(b))
    diffs = [mean(b[k]) - mean(a[k]) for k in common]
    if not diffs:
        return None
    m = mean(diffs)
    se = math.sqrt(sum((d - m) ** 2 for d in diffs) / (len(diffs) - 1) / len(diffs)) if len(diffs) > 1 else float("nan")
    t = t975(len(diffs) - 1)
    return dict(items=len(common), a=mean([mean(a[k]) for k in common]), b=mean([mean(b[k]) for k in common]),
                diff=m, se=se, ci95=(m - t * se, m + t * se), significant=bool(se == se and abs(m) > t * se))


def compare(a_dirs, b_dirs):
    sa, ta = per_item(a_dirs)
    sb, tb = per_item(b_dirs)
    res = dict(a_runs=len(a_dirs), b_runs=len(b_dirs), score=paired(sa, sb))
    if ta and tb:
        # tokens as a ratio per item (log scale), so long tasks do not dominate
        la = {k: [math.log(max(t, 1)) for t in v] for k, v in ta.items()}
        lb = {k: [math.log(max(t, 1)) for t in v] for k, v in tb.items()}
        p = paired(la, lb)
        if p:
            res["tokens_ratio"] = dict(items=p["items"], ratio=math.exp(p["diff"]),
                                       ci95=(math.exp(p["ci95"][0]), math.exp(p["ci95"][1])), significant=p["significant"])
    return res


def fmt(res, label_a="A", label_b="B"):
    s = res["score"]
    if not s:
        return "no common items"
    line = (f"{label_b} vs {label_a}: {s['diff'] * 100:+.1f} ± {s['se'] * 100:.1f} points "
            f"(95% CI {s['ci95'][0] * 100:+.1f} to {s['ci95'][1] * 100:+.1f}; {s['items']} items, "
            f"{res['a_runs']} vs {res['b_runs']} runs; {label_a} {s['a'] * 100:.1f}%, {label_b} {s['b'] * 100:.1f}%)"
            + ("  SIGNIFICANT" if s["significant"] else "  not significant"))
    t = res.get("tokens_ratio")
    if t:
        line += (f"\n  output tokens x{t['ratio']:.2f} (95% CI x{t['ci95'][0]:.2f} to x{t['ci95'][1]:.2f})"
                 + ("  SIGNIFICANT" if t["significant"] else ""))
    return line


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", nargs="+", required=True, help="run folders or globs of setup A (the reference)")
    ap.add_argument("--b", nargs="+", required=True, help="run folders or globs of setup B")
    ap.add_argument("--labels", nargs=2, default=("A", "B"))
    ap.add_argument("--json", default="")
    a = ap.parse_args()
    expand = lambda pats: sorted({p for pat in pats for p in (glob.glob(pat) or [pat]) if Path(p).is_dir()})  # noqa: E731
    res = compare(expand(a.a), expand(a.b))
    print(fmt(res, *a.labels))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1))


if __name__ == "__main__":
    main()
