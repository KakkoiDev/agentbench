"""Validates benches/vision-v3 without calling any model.

For every task: the image exists and is 1024-1600 px on its long side; the category is a key of GROUPS in vision_bench.py;
the answer re-derived here from data.json (the exact content drawn by benches/vision/build_v3.py, solved by code written
separately from the generator) equals tasks.json's answer; ambiguity guards hold (margins, no near-ties); and grade()
accepts the stated answer (bare and inside a short sentence for numbers) and rejects a nearby wrong one.

    .venv/bin/python benches/vision-v3/check.py
"""
import json, math, struct, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from vision_bench import GROUPS, grade  # noqa: E402  (import only; nothing is run)

tasks = json.loads((HERE / 'tasks.json').read_text())['tasks']
data = json.loads((HERE / 'data.json').read_text())
problems = []


def need(cond, tid, msg):
    if not cond:
        problems.append(f'{tid}: {msg}')


def solve(tid, d):
    k = d['kind']
    if k == 'receipt_taxable':
        for l in d['lines']:
            need(abs(l['qty'] * l['unit'] - l['amount']) < 0.006, tid, 'line amount != qty*unit')
        need(20 <= len(d['lines']), tid, 'receipt shorter than 20 lines')
        return round(sum(l['amount'] for l in d['lines'] if l['taxable']), 2)
    if k == 'label_code':
        hits = [l for l in d['labels'] if l['city'] == d['target']]
        need(len(hits) == 1, tid, 'target city not unique')
        need(len({l['code'] for l in d['labels']}) == len(d['labels']), tid, 'duplicate codes')
        return hits[0]['code']
    if k == 'plain':
        return d['value']
    if k == 'sum_named':
        names = [i['name'] for i in d['items']]
        need(len(set(names)) == len(names) and all(names.count(n) == 1 for n in d['names']), tid, 'names not unique')
        return round(sum(i[d['key']] for i in d['items'] if i['name'] in d['names']), 2)
    if k == 'nth':
        return d['items'][d['n'] - 1]
    if k == 'sum_where':
        f, v = d['where']
        return round(sum(x[d['key']] for x in d['rows'] if x[f] == v), 2)
    if k == 'series_sum':
        return sum(d['vals'][d['series']])
    if k == 'diff':
        return d['vals'][d['a']][d['i']] - d['vals'][d['b']][d['i']]
    if k == 'pick':
        return d['vals'][d['series']][d['i']]
    if k == 'count_pts':
        need(all(abs(y - d['above']) >= 3 for _, y, _, _ in d['pts']), tid, 'point too close to the line')
        return sum(1 for _, y, s, c in d['pts'] if s == d['shape'] and c == d['color'] and y > d['above'])
    if k == 'count_gt':
        need(all(abs(v - d['gt']) > 4 for v in d['vals']), tid, 'bar too close to the threshold')
        return sum(1 for v in d['vals'] if v > d['gt'])
    if k == 'count_eq':
        n = sum(1 for x in d['items'] if x == d['value'])
        need(25 <= n <= 60, tid, f'count {n} outside 25-60')
        return n
    if k == 'nearest':
        sx, sy = d['star']
        ds = sorted((math.hypot(x - sx, y - sy), lab) for x, y, lab in d['circles'])
        need(ds[1][0] >= 1.25 * ds[0][0], tid, 'nearest circle not clearly nearest')
        need(len({lab for _, _, lab in d['circles']}) == len(d['circles']), tid, 'duplicate labels')
        return ds[0][1]
    if k == 'grid_offset':
        r, c = d['start'][0] + d['move'][0], d['start'][1] + d['move'][1]
        need(0 <= r < len(d['nums']) and 0 <= c < len(d['nums']), tid, 'offset out of grid')
        return d['nums'][r][c]
    if k == 'inside':
        rx, ry, rw, rh = d['rect']
        n = 0
        for x, y, s, _ in d['items']:
            ins = rx < x < rx + rw and ry < y < ry + rh
            near = min(abs(x - rx), abs(x - rx - rw), abs(y - ry), abs(y - ry - rh)) < 22 and (rx - 22 < x < rx + rw + 22 and ry - 22 < y < ry + rh + 22)
            need(not near, tid, 'shape touches the rectangle border')
            n += ins and s == d['shape']
        return n
    if k == 'shelf':
        row = d['shelves'][d['shelf']]
        need(row.count('yellow') == 1, tid, 'yellow not unique on the shelf')
        i = row.index('yellow')
        need(i < len(row) - 1, tid, 'yellow box is last')
        need(len(set(d['labels'])) == len(d['labels']), tid, 'duplicate box labels')
        return d['labels'][d['shelf'] * len(row) + i + 1]
    if k == 'count_flag':
        return sum(1 for x in d['rows'] if x[d['flag']])
    if k == 'nth_where':
        sel = [x for x, f in zip(d['items'], d['flags']) if f]
        need(len(sel) >= d['n'] + 1, tid, 'too few flagged items')
        return sel[d['n'] - 1]
    if k == 'timetable':
        col = d['times'][d['train']]
        a, b = col[d['stations'].index(d['a'])], col[d['stations'].index(d['b'])]
        need(a is not None and b is not None and b > a, tid, 'train does not stop at both stations in order')
        stops = [v for v in col if v is not None]
        need(stops == sorted(stops), tid, 'times not increasing')
        return b - a
    if k == 'count_lt':
        need(all(abs(x[d['a']] - x[d['b']]) >= 3 for x in d['rows']), tid, 'near tie stock/reorder')
        return sum(1 for x in d['rows'] if x[d['a']] < x[d['b']])
    if k == 'argmax_mul':
        s = sorted(d['units'])
        need(s[-1] - s[-2] >= 20, tid, 'top product not clear')
        i = d['units'].index(s[-1])
        return round(d['units'][i] * d['prices'][i], 2)
    if k == 'order_total':
        return round(sum(d['prices'][p] * q for p, q in d['order']), 2)
    raise SystemExit(f'{tid}: unknown kind {k}')


def wrong(t):
    a = t['answer']
    if t['score'] == 'number':
        tol = t.get('tol') or 0
        return round(a + (max(0.1, 10 * tol) if tol else 1), 2)
    return a[:-1] + ('X' if a[-1] != 'X' else 'Y')


cats = {}
for t in tasks:
    tid = t['id']
    cats[t['category']] = cats.get(t['category'], 0) + 1
    need(t['category'] in GROUPS, tid, f"category {t['category']} not in GROUPS")
    img = HERE / t['image']
    need(img.exists(), tid, 'image missing')
    if img.exists():
        w, h = struct.unpack('>II', img.read_bytes()[16:24])   # PNG IHDR width/height
        need(1024 <= max(w, h) <= 1600, tid, f'image {w}x{h} outside 1024-1600')
    need('Reply with only' in t['question'], tid, 'question does not say how to answer')
    need(t['score'] in ('number', 'text', 'word'), tid, 'bad score type')
    got = solve(tid, data[tid])
    need(got == t['answer'] if not isinstance(got, float) else abs(got - t['answer']) < 1e-6, tid, f'answer {t["answer"]!r} != derived {got!r}')
    need(grade(t, str(t['answer'])), tid, 'grade() rejects the stated answer')
    if t['score'] == 'number':
        shown = f"{t['answer']:.2f}" if (t.get('tol') or 0) > 0 else str(t['answer'])
        need(grade(t, shown), tid, f'grade() rejects {shown}')
        need(grade(t, f'The answer is {shown}.'), tid, 'grade() rejects a sentence ending in the answer')
    need(not grade(t, str(wrong(t))), tid, f'grade() accepts the wrong answer {wrong(t)!r}')
    print(f"{tid:16s} {t['score']:6s} answer={t['answer']!r:28s} wrong={wrong(t)!r}")

print('\ncategories:', ', '.join(f'{c} {n}' for c, n in cats.items()), f'· total {len(tasks)}')
if problems:
    print('\nPROBLEMS:\n' + '\n'.join(problems))
    sys.exit(1)
print('all checks passed')
