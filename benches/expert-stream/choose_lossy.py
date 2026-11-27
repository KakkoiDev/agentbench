"""Pick the lossy setup the real benches compare with exact (rule fixed before the results, 2026-11-27): at 16 slots
(5.4 GB), the fastest lossy run whose next-token agreement with the exact model is at least 95%. Writes
results/expert-stream-lossy-choice.json with the serve.py arguments; benches/local_engines.py reads it."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SLOTS, FLOOR = 16, 0.95
rows = [json.loads(l) for f in ("expert-stream-lossy.jsonl", "expert-stream-lossy2.jsonl")
        if (ROOT / "results" / f).exists() for l in (ROOT / "results" / f).read_text().splitlines() if l.strip()]
ok = [r for r in rows if r["slots"] == SLOTS and r.get("lossy") and (r.get("agreement") or 0) >= FLOOR]
best = max(ok, key=lambda r: r["decode_tok_s_second_half"])
args = ["--slots", str(SLOTS), "--loop-weighted", "--policy", "lfu", "--prefetch", "--lossy", best["lossy"]]
if best["lossy"] == "sub":
    args += ["--sub-alpha", str(best["sub_alpha"])]
if best["lossy"] == "topk":
    args += ["--topk", str(best["topk"])]
if best["lossy"] == "skiplow":
    args += ["--skip-share", str(best["skip_share"])]
choice = dict(args=args, rule=f"{SLOTS} slots, agreement >= {FLOOR}, fastest", picked={k: best.get(k) for k in (
    "lossy", "sub_alpha", "topk", "skip_share", "decode_tok_s_second_half", "agreement", "kl_mean", "peak_gb")})
(ROOT / "results" / "expert-stream-lossy-choice.json").write_text(json.dumps(choice, indent=1))
print(json.dumps(choice))
