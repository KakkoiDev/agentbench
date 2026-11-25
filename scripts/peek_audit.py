"""Which agent_bench runs read outside their own workspace into the bench's answers (2026-11-25: the coding sandbox
allowed reads of benches/ and results/, and DeepSeek V4.1 Flash read agentic-v5 reference solutions there).

    .venv/bin/python scripts/peek_audit.py        # writes results/peek-audit.json, prints a summary
"""
import json, re, sys
from pathlib import Path

RESULTS = Path(__file__).resolve().parents[1] / 'results'
ANSWERS = re.compile(r'colibri-poc/benches/|colibri-poc/results/(?!agentic-pi-home)|\.meta/example|hidden_test|test_hidden_grade|\.claude/projects')


def audit():
    out = {}
    for f in sorted(RESULTS.glob('*/*/pi.jsonl')):
        run, task = f.parent.parent.name, f.parent.name
        ws = str(f.parent / 'workspace')
        hits = []
        for line in f.open(errors='replace'):
            if '"tool_execution_start"' not in line:
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            a = json.dumps(e.get('args'), ensure_ascii=False).replace(ws, 'WS').replace(str(f.parent), 'TASK')
            if ANSWERS.search(a):
                hits.append(a[:300])
        if hits:
            out.setdefault(run, {})[task] = hits
    return out


if __name__ == '__main__':
    a = audit()
    (RESULTS / 'peek-audit.json').write_text(json.dumps(a, indent=1, ensure_ascii=False))
    for run, tasks in a.items():
        print(f'{run}: {len(tasks)} task(s): {", ".join(f"{t} ({len(h)})" for t, h in tasks.items())}')
    sys.exit(0)
