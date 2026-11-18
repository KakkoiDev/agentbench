#!/usr/bin/env python3
"""Re-grade saved family-job runs after a grader fix, for checks that depend only on the reply text.

    .venv/bin/python scripts/regrade_family.py

2026-11-17: contract-notice accepted only "3 months" ("3-month notice" is right too); ask-delete did not
count "let me know …" as asking. Changed rows keep their old verdict in ok_before_regrade / why_before_regrade.
Checks that read the job's files (gone after the run) are left as they were.
"""
import importlib.util, json, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("family", ROOT / "benches/dropbox/family.py")
fam = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fam)
ASK = "did not ask before deleting"

import re
FACT = "should not contain /may\\s*3\\b(?!.*17)/"
NEW_LACK = r"^(?![\s\S]*17)[\s\S]*may\s*3\b"

# mini (assistant) runs: fact-lookup rejected "May 17 (moved from May 3)" (fixed 2026-11-17)
for f in sorted((ROOT / "results").glob("dropbox-mini-*/results.json")):
    data = json.loads(f.read_text())
    changed = 0
    for r in data.get("rows", []):
        if r["id"] == "fact-lookup" and FACT in (r.get("why") or []):
            why = [w for w in r["why"] if w != FACT]
            if re.search(NEW_LACK, r.get("reply") or "", re.I):
                why.append(f"should not contain /{NEW_LACK}/")
            if not why:
                r.setdefault("ok_before_regrade", r["ok"]); r.setdefault("why_before_regrade", r["why"])
                r["ok"], r["why"], r["regraded"] = True, why, "2026-11-17 grader fix"
                changed += 1
    if changed:
        data["summary"]["passed"] = sum(bool(r["ok"]) for r in data["rows"])
        data["summary"]["regraded"] = "2026-11-17 grader fix (fact-lookup)"
        f.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        print(f"{f.parent.name}: {changed} row(s) changed, now {data['summary']['passed']}/{data['summary']['total']}")

for f in sorted((ROOT / "results").glob("dropbox-family-*/results.json")):
    data = json.loads(f.read_text())
    changed = 0
    for r in data.get("rows", []):
        if r["id"] == "contract-notice":
            why = fam.check_contract(None, r.get("reply") or "")
        elif r["id"] == "ask-delete" and ASK in (r.get("why") or []):
            why = [w for w in r["why"] if w != ASK] + [w for w in fam.check_ask_delete(Path("/nonexistent"), r.get("reply") or "") if w == ASK]
        else:
            continue
        if bool(why) != (not r["ok"]):
            r.setdefault("ok_before_regrade", r["ok"]); r.setdefault("why_before_regrade", r["why"])
            r["ok"], r["why"], r["regraded"] = not why, why, "2026-11-17 grader fix"
            changed += 1
    if changed:
        data["summary"]["passed"] = sum(bool(r["ok"]) for r in data["rows"])
        data["summary"]["regraded"] = "2026-11-17 grader fix (contract-notice, ask-delete)"
        f.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
        print(f"{f.parent.name}: {changed} row(s) changed, now {data['summary']['passed']}/{data['summary']['total']}")
