"""Bench failures as GitHub issues on agentbench (asked for on 2026-11-25), so agentbox's progress and regressions are
tracked task by task, next to the real-life reports `agentbox report` files.

Reads every run made through agentbox (`agent_bench.py run --harness agentbox`: meta.json has a "harness"). For each
task, model and thinking level, its latest run decides:
- failed, no open issue   -> open one (or reopen the closed one: a regression);
- failed, issue open      -> comment once per new run;
- passed, issue open      -> comment and close.
Issues carry scores and versions only, never hidden tests or the model's code (the repo is public). What was done is
kept in results/bench-issues.json.

    .venv/bin/python bench_issues.py            # print what it would do
    .venv/bin/python bench_issues.py --apply    # do it (needs gh signed in)
"""
import argparse, json, re, subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RES = ROOT / 'results'
STATE = RES / 'bench-issues.json'
REPO = 'KakkoiDev/agentbench'
LABEL = 'bench-failure'


def runs():
    """{(bench, task, model, thinking): [run, ...]} oldest first, for runs through agentbox (smoke tests left out)."""
    out = {}
    for meta_f in RES.glob('*/meta.json'):
        try:
            meta = json.loads(meta_f.read_text())
        except ValueError:
            continue
        h = meta.get('harness')
        if not h or 'smoke' in meta_f.parent.name or meta_f.parent.name.startswith('_'):
            continue
        for rf in meta_f.parent.glob('*/result.json'):
            r = json.loads(rf.read_text())
            key = (meta.get('bench'), r['task'], meta.get('model'), meta.get('thinking'))
            out.setdefault(key, []).append(dict(run=meta_f.parent.name, started=meta.get('started'), agentbox=h.get('version'),
                                                parts=[e.get('part') for e in h.get('extensions', [])], solved=r['solved'],
                                                hidden=f"{r['hidden_passed']}/{r['hidden_total']}", timed_out=r.get('timed_out'),
                                                wall=r.get('wall_s')))
    for v in out.values():
        v.sort(key=lambda x: x['started'] or '')
    return out


def line(x):
    return (f"- {x['started'][:16].replace('T', ' ')} `{x['run']}`: {'**passed**' if x['solved'] else '**failed**'}, hidden tests {x['hidden']}"
            f"{', timed out' if x['timed_out'] else ''}, {round(x['wall'] or 0)} s, agentbox {x['agentbox']} ({', '.join(x['parts']) or 'no parts'})")


def body(key, history):
    bench, task, model, thinking = key
    return '\n'.join([f'`{task}` on bench **{bench}** fails with **{model}** (thinking {thinking}) run through agentbox.', '',
                      'Filed by `bench_issues.py` from the bench results; it closes itself when a later run passes. '
                      'Scores only: the hidden tests stay out of issues.', '', '## Runs', ''] + [line(x) for x in history])


def gh(*args):
    r = subprocess.run(['gh', *args], capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f'gh {" ".join(args[:3])}: {r.stderr.strip()}')
    return r.stdout.strip()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--apply', action='store_true')
    a = ap.parse_args()
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if a.apply:
        subprocess.run(['gh', 'label', 'create', LABEL, '-R', REPO, '--color', 'B60205',
                        '--description', 'A bench task that fails when run through agentbox'], capture_output=True)
        subprocess.run(['gh', 'label', 'create', 'real-life', '-R', REPO, '--color', '0E8A16',
                        '--description', 'A real session that went wrong (agentbox report)'], capture_output=True)
    actions = 0
    for key, history in sorted(runs().items(), key=str):
        k = ' | '.join(map(str, key))
        latest, s = history[-1], state.get(k, {})
        title = f'{key[0]}/{key[1]} fails with {key[2]} ({key[3]})'
        if not latest['solved']:
            if not s.get('issue'):
                print(f'open    {title}')
                if a.apply:
                    url = gh('issue', 'create', '-R', REPO, '--title', title, '--body', body(key, history), '--label', LABEL)
                    s = dict(issue=int(re.search(r'/(\d+)$', url)[1]), open=True, last_run=latest['run'])
            elif not s.get('open'):
                print(f'reopen  {title} (#{s["issue"]}: a regression)')
                if a.apply:
                    gh('issue', 'reopen', str(s['issue']), '-R', REPO, '--comment', 'Fails again (a regression):\n\n' + line(latest))
                    s.update(open=True, last_run=latest['run'])
            elif s.get('last_run') != latest['run']:
                print(f'comment {title} (#{s["issue"]}: still failing)')
                if a.apply:
                    gh('issue', 'comment', str(s['issue']), '-R', REPO, '--body', 'Still failing:\n\n' + line(latest))
                    s['last_run'] = latest['run']
            else:
                continue
        elif s.get('open'):
            print(f'close   {title} (#{s["issue"]}: passes now)')
            if a.apply:
                gh('issue', 'close', str(s['issue']), '-R', REPO, '--comment', 'Passes now:\n\n' + line(latest))
                s.update(open=False, last_run=latest['run'])
        else:
            continue
        actions += 1
        state[k] = s
    if a.apply:
        STATE.write_text(json.dumps(state, indent=1))
    print(f'{actions} action(s){"" if a.apply else " (dry run: --apply to do them)"}')


if __name__ == '__main__':
    main()
