"""The report's overview chart: intelligence against writing speed and against memory, one point per
model, plus a table with whether each model handles 4 requests at once (scripts/concurrency_smoke.py).

Two scatter plots side by side share the vertical axis (overall score), so three measures show
without a second y-axis. Color says where the model runs (3 categories, validated all-pairs for
color-vision deficiency in light and dark); the top models are labelled on the chart and every
point has a tooltip; the table repeats everything for screen readers and print.
"""
import html, json
from pathlib import Path

E = lambda s: html.escape(str(s), quote=True)

KINDS = [('ollama', 'Ollama, in memory'), ('engine', 'Other engines, in memory'), ('ssd', 'Streamed from the SSD')]
VERDICT = {'parallel': 'yes, together', 'partly': 'partly', 'queued': 'one after another', 'failed': 'failed'}


def kind_of(key):
    e = key.split('/', 1)[0] if '/' in key else ''
    return 'ssd' if e in ('slotstream', 'ds4') else 'engine' if e in ('mtplx', 'omlx', 'mlxlm') else 'ollama'


def concurrency(root):
    out = {}
    for f in sorted((Path(root) / 'results' / 'concurrency').glob('concurrency-*.json')):
        try:
            r = json.loads(f.read_text())
            out[r['model']] = r   # sorted by date: the latest wins
        except (OSError, ValueError, KeyError):
            pass
    return out


def speed_of(S, m):
    """Writing speed (tokens/s) of the model's first key that has a speed test."""
    for k in m['keys']:
        f = S.speed.get(k, {}).get('first')
        if f and f.get('out_s'):
            return round(f['out_tokens'] / f['out_s'], 1), k
    return None, None


def rows(S, MODELS, root):
    conc = concurrency(root)
    out = []
    for m in MODELS:
        if not m['keys']:
            continue
        sm = S.smartest(m)
        spd, sk = speed_of(S, m)
        mem = S.mem(m)
        key = sk or m['keys'][0]
        c = next((conc[k] for k in m['keys'] if k in conc), None)
        if not (sm or spd or c):
            continue
        out.append(dict(slug=m['slug'], name=m['name'], kind=kind_of(key), q=round(sm['q'] * 100) if sm else None,
                        speed=spd, mem=mem[0] if mem else None, conc=c['verdict'] if c else None,
                        ratio=c.get('ratio') if c else None))
    return out


def scatter(points, xkey, xlabel, xmax, cid, top, ymin=40):
    W, H, L, R, T, B = 460, 340, 46, 16, 14, 44
    pw, ph = W - L - R, H - T - B
    placed = []   # label boxes already drawn, so labels never overlap
    sx = lambda v: L + pw * v / xmax
    sy = lambda v: T + ph * (1 - (v - ymin) / (100 - ymin))
    g = [f'<svg viewBox="0 0 {W} {H}" class="vz-svg" role="img" aria-labelledby="{cid}-t"><title id="{cid}-t">Overall score against {E(xlabel)}</title>']
    for v in range(ymin, 101, 10 if 100 - ymin > 30 else 5):
        g.append(f'<line x1="{L}" x2="{W - R}" y1="{sy(v):.1f}" y2="{sy(v):.1f}" class="vz-grid"/><text x="{L - 6}" y="{sy(v) + 4:.1f}" class="vz-tick" text-anchor="end">{v}%</text>')
    step = 10 if xmax <= 60 else 20 if xmax <= 140 else 50
    for v in range(0, int(xmax) + 1, step):
        g.append(f'<text x="{sx(v):.1f}" y="{H - B + 16}" class="vz-tick" text-anchor="middle">{v}</text>')
    g.append(f'<line x1="{L}" x2="{W - R}" y1="{T + ph}" y2="{T + ph}" class="vz-axis"/>')
    g.append(f'<text x="{L + pw / 2}" y="{H - 6}" class="vz-label" text-anchor="middle">{E(xlabel)}</text>')
    for p in points:   # every point is an obstacle for labels too
        if p['q'] is not None and p[xkey] is not None:
            px, py = sx(min(p[xkey], xmax)), sy(max(p['q'], ymin))
            placed.append((px - 6, py - 6, px + 6, py + 6))
    for p in points:
        if p['q'] is None or p[xkey] is None:
            continue
        x, y = sx(min(p[xkey], xmax)), sy(max(p['q'], ymin))
        tip = (f"{p['name']} · overall {p['q']}% · {p['speed'] if p['speed'] is not None else '—'} tok/s · "
               f"{(str(p['mem']) + ' GB') if p['mem'] is not None else 'memory not measured'} · 4 at once: {VERDICT.get(p['conc'], 'not tested')}")
        g.append(f'<a href="models/{E(p["slug"])}.html" class="vz-pt" data-tip="{E(tip)}" tabindex="0" aria-label="{E(tip)}">'
                 f'<circle cx="{x:.1f}" cy="{y:.1f}" r="11" class="vz-hit"/><circle cx="{x:.1f}" cy="{y:.1f}" r="5" class="vz-dot vz-{p["kind"]}"/></a>')
        if p['slug'] in top:
            label = p["name"].split(" (")[0].replace("Qwen 3.8 27B, ", "Qwen 27B ").replace(" build", "")
            w, lx, ly = 6.2 * len(label), x + 8, y - 7
            if lx + w > W - R:   # near the right edge: put it on the left of the point
                lx = x - 8 - w
            own = (x - 6, y - 6, x + 6, y + 6)
            box = (lx, ly - 11, lx + w, ly + 2)
            if not any(b != own and box[0] < b[2] and b[0] < box[2] and box[1] < b[3] and b[1] < box[3] for b in placed):
                placed.append(box)
                g.append(f'<text x="{lx:.1f}" y="{ly:.1f}" class="vz-dl">{E(label)}</text>')
    g.append('</svg>')
    return ''.join(g)


def ratio_note(p):
    return f" (4 took {p['ratio']}× one)" if p.get('ratio') else ''


def section(S, MODELS, root):
    pts = rows(S, MODELS, root)
    if not pts:
        return ''
    ranked = sorted([p for p in pts if p['q'] is not None], key=lambda p: -p['q'])
    top = {p['slug'] for p in ranked[:6]}
    qs = [p['q'] for p in pts if p['q'] is not None]
    ymin = max(0, min(qs) // 10 * 10 - 5) if qs else 40   # start just below the lowest score
    smax = max([p['speed'] for p in pts if p['speed']] + [10])
    smax = 20 * (int(smax // 20) + 1)
    legend = ''.join(f'<span class="vz-key"><i class="vz-sw vz-{k}"></i>{E(n)}</span>' for k, n in KINDS)
    table_rows = ''.join(
        f'<tr><th scope="row"><a href="models/{E(p["slug"])}.html">{E(p["name"])}</a></th><td>{E(dict(KINDS)[p["kind"]])}</td>'
        f'<td class="n">{str(p["q"]) + "%" if p["q"] is not None else "—"}</td><td class="n">{p["speed"] if p["speed"] is not None else "—"}</td>'
        f'<td class="n">{p["mem"] if p["mem"] is not None else "—"}</td>'
        f'<td>{E(VERDICT.get(p["conc"], "not tested"))}{ratio_note(p)}</td></tr>'
        for p in sorted(pts, key=lambda p: -(p['q'] or -1)))
    return f"""
<section class="sec vz-root" aria-labelledby="overview">
<div class="sec-h"><h2 id="overview">Intelligence, speed and memory</h2></div>
<p class="dek2">Each point is a model: higher is smarter, further right is faster (left chart) or takes more memory (right chart). Hover or tap a point for its numbers; the table below has them all, with whether the model can take 4 requests at once.</p>
<div class="vz-legend">{legend}</div>
<div class="vz-grid2">
<figure class="vz-fig">{scatter(pts, 'speed', 'Writing speed (tokens per second)', smax, 'vzs', top, ymin)}</figure>
<figure class="vz-fig">{scatter(pts, 'mem', 'Memory loaded (GB, of 32)', 32, 'vzm', top, ymin)}</figure>
</div>
<div class="vz-tip" role="tooltip" hidden></div>
<details class="vz-table"><summary>Table: every model's score, speed, memory and 4-at-once result</summary>
<div class="tblbox"><table class="mdt"><thead><tr><th scope="col">Model</th><th scope="col">Runs on</th><th scope="col">Overall</th><th scope="col">Tokens/s</th><th scope="col">GB loaded</th><th scope="col">4 requests at once</th></tr></thead>
<tbody>{table_rows}</tbody></table></div></details>
<p class="note">Overall: the Smartest score (assistant, coding with the hard suite, hard jobs and images, where run). Speed: one request with an assistant-sized system prompt. Memory: loaded size reported by Ollama; other engines' memory is not measured. 4 at once: four short requests sent together; "together" when all four took under twice one alone (scripts/concurrency_smoke.py).</p>
<script>
(function(){{var r=document.currentScript.closest('.vz-root'),t=r.querySelector('.vz-tip');
function show(e){{var a=e.target.closest('.vz-pt');if(!a)return;t.textContent=a.dataset.tip;t.hidden=false;var b=a.getBoundingClientRect(),rb=r.getBoundingClientRect();
t.style.left=Math.max(0,Math.min(b.left-rb.left-100,rb.width-260))+'px';t.style.top=(b.top-rb.top-48)+'px'}}
function hide(){{t.hidden=true}}
r.addEventListener('mouseover',show);r.addEventListener('focusin',show);r.addEventListener('mouseout',hide);r.addEventListener('focusout',hide);}})();
</script>
</section>"""


CSS = """
.vz-root{--vz-ollama:#2a78d6;--vz-engine:#eb6834;--vz-ssd:#1baf7a;position:relative}
body[data-theme="dark"] .vz-root{--vz-ollama:#3987e5;--vz-engine:#d95926;--vz-ssd:#199e70}
@media (prefers-color-scheme:dark){body:not([data-theme="light"]) .vz-root{--vz-ollama:#3987e5;--vz-engine:#d95926;--vz-ssd:#199e70}}
.vz-grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(min(100%,340px),1fr));gap:16px}
.vz-fig{margin:0}.vz-svg{width:100%;height:auto;display:block;overflow:visible}
.vz-grid{stroke:var(--hair);stroke-width:1}.vz-axis{stroke:var(--fg-2);stroke-width:1}
.vz-tick{font-size:11px;fill:var(--fg-2);font-variant-numeric:tabular-nums}.vz-label{font-size:12px;fill:var(--fg-2)}
.vz-dl{font-size:11.5px;fill:var(--fg);paint-order:stroke;stroke:var(--bg);stroke-width:3px}
.vz-dot{stroke:var(--bg);stroke-width:2}.vz-hit{fill:transparent}
.vz-ollama{fill:var(--vz-ollama);background:var(--vz-ollama)}.vz-engine{fill:var(--vz-engine);background:var(--vz-engine)}.vz-ssd{fill:var(--vz-ssd);background:var(--vz-ssd)}
.vz-pt:focus-visible .vz-dot{stroke:var(--fg);stroke-width:2.5}
.vz-legend{display:flex;flex-wrap:wrap;gap:6px 18px;font-size:13px;color:var(--fg-2)}
.vz-key{display:inline-flex;align-items:center;gap:6px}.vz-sw{width:10px;height:10px;border-radius:50%;display:inline-block}
.vz-tip{position:absolute;z-index:3;max-width:260px;padding:6px 9px;background:var(--bg);border:1px solid var(--hair);font-size:12.5px;line-height:17px;color:var(--fg);box-shadow:0 2px 8px rgba(0,0,0,.12);pointer-events:none}
.vz-table summary{cursor:pointer;font-size:14px;color:var(--link)}
"""


# ---------------------------------------------------------------- best model per use case
RAMP = ['#cde2fb', '#b7d3f6', '#9ec5f4', '#86b6ef', '#6da7ec', '#5598e7', '#3987e5', '#2a78d6', '#256abf', '#1c5cab', '#184f95']


def _pct(x):
    return None if x is None else round(100 * x)


def usecase_rows(S, MODELS, root):
    """One row per model: every use-case measure the benches give (None where not run)."""
    conc = concurrency(root)
    out = []
    for m in MODELS:
        if not m['keys']:
            continue
        d, c, v, t, sp = S.daily(m), S.coding(m), S.vision(m), S.translation(m), S.speech(m)
        h, h4, h5, hx = S.hard(m), S.hard(m, 'coding-v4'), S.hard(m, 'coding-v5'), S.hard(m, 'family-expert')
        spd, _ = speed_of(S, m)
        base = [p for s, p in (c['parts'] if c else []) if s in ('coding-v1', 'coding-v2', 'coding-v3')]
        ntask = sum(p['out_of'] for p in base)
        runs4 = [r for r in S.rows if r['model'] in m['keys'] and r.get('bench') == 'coding-v4' and not r.get('set_aside') and r.get('output_tokens')]
        cc = next((conc[k] for k in m['keys'] if k in conc), None)
        row = dict(slug=m['slug'], name=m['name'], kind=kind_of(m['keys'][0]),
                   assistant=_pct(d['q']) if d else None,
                   family=_pct(d['fam']['mean'] / d['fam']['out_of']) if d and d.get('fam') else None,
                   hard=_pct(h['q']) if h else None, expert=_pct(hx['q']) if hx else None,
                   coding=_pct(sum(p['mean'] for p in base) / ntask) if ntask else None,
                   v4=_pct(h4['q']) if h4 else None, v5=_pct(h5['q']) if h5 else None,
                   images=_pct(v['q']) if v else None, chrf=round(t['mean'], 1) if t else None,
                   speech=round(sp['mean'], 1) if sp else None, tps=spd,
                   job_s=round(d['secs'], 1) if d and d.get('secs') else None,
                   task_min=round(sum(p.get('minutes') or 0 for p in base) / ntask, 1) if ntask and any(p.get('minutes') for p in base) else None,
                   task_tokens=round(sum(r['output_tokens'] for r in runs4) / len(runs4) / 5) if runs4 else None,
                   conc=cc['verdict'] if cc else None)
        if any(row[k] is not None for k, *_ in USECASES):
            out.append(row)
    return out


# key, column label, unit, higher is better, what it measures
USECASES = [
    ('assistant', 'Assistant', '%', True, 'family jobs and assistant tests, averaged'),
    ('family', 'Family jobs', '%', True, '12 multi-step file jobs'),
    ('hard', 'Hard jobs', '%', True, '10 harder file jobs, 56 steps'),
    ('expert', 'Expert jobs', '%', True, 'expert file jobs, graded in steps'),
    ('coding', 'Coding v1-v3', '%', True, '15 coding-agent tasks'),
    ('v4', 'Hard coding', '%', True, 'suite v4, 52 hidden tests'),
    ('v5', 'Polyglot coding', '%', True, 'suite v5 (Aider polyglot, Exercism), 312 hidden tests'),
    ('images', 'Images', '%', True, 'two image suites'),
    ('chrf', 'Translation', 'chrF', True, 'chat translation EN/JA/FR'),
    ('speech', 'Speech errors', '%', False, 'speech to text error rate'),
    ('tps', 'Writing speed', 'tok/s', True, 'tokens per second, one request'),
    ('job_s', 'Time per family job', 's', False, 'seconds per family job'),
    ('task_min', 'Time per coding task', 'min', False, 'minutes per coding task (v1-v3)'),
    ('task_tokens', 'Tokens per coding task', 'tokens', False, 'output tokens written per hard coding task'),
]


def _shade(vals, v, higher):
    xs = [x for x in vals if x is not None]
    if v is None or not xs:
        return None
    lo, hi = min(xs), max(xs)
    f = 1.0 if hi == lo else (v - lo) / (hi - lo)
    f = f if higher else 1 - f
    return RAMP[round(f * (len(RAMP) - 1))]


def usecase_section(S, MODELS, root):
    rows_ = usecase_rows(S, MODELS, root)
    cols = [u for u in USECASES if any(r[u[0]] is not None for r in rows_)]
    if not rows_ or not cols:
        return ''
    rows_.sort(key=lambda r: -(S.smartest(next(m for m in MODELS if m['slug'] == r['slug'])) or {'q': -1})['q'])
    head = ''.join(f'<th scope="col" title="{E(u[4])}"><span>{E(u[1])}</span><small>{E(u[2])}{" ↓" if not u[3] else ""}</small></th>' for u in cols)
    body = []
    for r in rows_:
        cells = []
        for k, label, unit, higher, _ in cols:
            v = r[k]
            bg = _shade([x[k] for x in rows_], v, higher)
            dark = bg in RAMP[7:] if bg else False   # white text from #2a78d6 on (4.5:1); #3987e5 keeps dark text
            best = v is not None and v == (max if higher else min)(x[k] for x in rows_ if x[k] is not None)
            cells.append(f'<td class="n{" hm-best" if best else ""}" style="{f"background:{bg};" if bg else ""}{"color:#fff;" if dark else "color:#111;" if bg else ""}"'
                         f' title="{E(r["name"])} · {E(label)}: {v if v is not None else "not run"}{" " + unit if v is not None else ""}">'
                         f'{"" if v is None else (str(v) + ("%" if unit == "%" else ""))}{" ★" if best else ""}</td>')
        body.append(f'<tr><th scope="row"><a href="models/{E(r["slug"])}.html">{E(r["name"])}</a></th>{"".join(cells)}'
                    f'<td>{E(VERDICT.get(r["conc"], "—"))}</td></tr>')
    data = json.dumps({'rows': rows_, 'cols': [dict(key=k, label=l, unit=u, higher=h, what=w) for k, l, u, h, w in cols]})
    options = ''.join(f'<option value="{E(k)}">{E(l)} ({E(w)})</option>' for k, l, u, h, w in cols)
    return f"""
<section class="sec vz-root uc" aria-labelledby="usecases">
<div class="sec-h"><h2 id="usecases">Best model for each use case</h2></div>
<p class="dek2">Every bench at a glance. In each column the darker the cell, the better (for time and errors, lower is better: marked ↓); ★ marks the best. Rows are ordered by the overall score. Pick a use case below to rank the models on it.</p>
<div class="hm-wrap"><table class="hm"><thead><tr><th scope="col">Model</th>{head}<th scope="col"><span>4 at once</span><small>smoke</small></th></tr></thead><tbody>{''.join(body)}</tbody></table></div>
<div class="uc-pick"><label for="uc-metric">Rank models by</label> <select id="uc-metric">{options}</select></div>
<div class="uc-bars" id="uc-bars" aria-live="polite"></div>
<script type="application/json" id="uc-data">{data.replace('</', '<\\/')}</script>
<script>
(function(){{var D=JSON.parse(document.getElementById('uc-data').textContent),sel=document.getElementById('uc-metric'),box=document.getElementById('uc-bars');
function draw(){{var c=D.cols.filter(function(x){{return x.key===sel.value}})[0],rs=D.rows.filter(function(r){{return r[c.key]!=null}});
rs.sort(function(a,b){{return c.higher?b[c.key]-a[c.key]:a[c.key]-b[c.key]}});var mx=Math.max.apply(null,rs.map(function(r){{return r[c.key]}}))||1;
box.innerHTML='';var cap=document.createElement('p');cap.className='note';cap.textContent=c.label+': '+c.what+(c.higher?'; higher is better.':'; lower is better.');box.appendChild(cap);
rs.forEach(function(r,i){{var row=document.createElement('a');row.href='models/'+r.slug+'.html';row.className='uc-row';
var n=document.createElement('span');n.className='uc-n';n.textContent=(i+1)+'. '+r.name;var tr=document.createElement('span');tr.className='uc-track';
var b=document.createElement('span');b.className='uc-bar vz-'+r.kind;b.style.width=Math.max(2,100*r[c.key]/mx)+'%';tr.appendChild(b);
var v=document.createElement('span');v.className='uc-v';v.textContent=r[c.key]+(c.unit==='%'?'%':' '+c.unit);row.appendChild(n);row.appendChild(tr);row.appendChild(v);box.appendChild(row)}});}}
sel.addEventListener('change',draw);draw();}})();
</script>
<div class="vz-legend">{''.join(f'<span class="vz-key"><i class="vz-sw vz-{k}"></i>{E(n)}</span>' for k, n in KINDS)}</div>
<p class="note">Bars are colored by where the model runs. Tokens per coding task: output tokens per hard-suite task, thinking included; fewer means the model gets there with less writing. Every number links back to the model's page and the raw data.</p>
</section>"""


CSS += """
.hm-wrap{overflow-x:auto;-webkit-overflow-scrolling:touch;border-top:2px solid var(--fg)}
.hm{border-collapse:separate;border-spacing:2px;font-size:13px;line-height:17px;min-width:100%}
.hm thead th{font-weight:600;vertical-align:bottom;text-align:left;padding:6px 4px;min-width:64px}
.hm thead th small{display:block;font-weight:400;color:var(--fg-2)}
.hm tbody th{font-weight:400;text-align:left;padding:4px 8px 4px 0;white-space:nowrap;position:sticky;left:0;background:var(--bg)}
.hm td{padding:5px 6px;text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap;border-radius:3px;color:var(--fg)}
.hm td.hm-best{font-weight:700}
.uc-pick{display:flex;gap:8px;align-items:center;flex-wrap:wrap;margin-top:8px}
.uc-pick select{font:inherit;font-size:14px;padding:4px 6px;max-width:100%}
.uc-bars{display:flex;flex-direction:column;gap:4px}
.uc-row{display:grid;grid-template-columns:minmax(120px,240px) 1fr 76px;gap:10px;align-items:center;color:var(--fg);text-decoration:none;font-size:13.5px}
.uc-row:hover{background:var(--tint)}
.uc-n{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.uc-track{height:12px;background:transparent}
.uc-bar{display:block;height:12px;border-radius:0 4px 4px 0}
.uc-v{text-align:right;font-variant-numeric:tabular-nums}
"""
