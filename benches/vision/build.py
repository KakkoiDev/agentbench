"""Builds the image-recognition benches benches/vision-v1 (25 tasks) and the held-out benches/vision-v2 (10 tasks).

Every image is generated here (HTML/SVG rendered by headless Chromium), so there are no licensing questions and the
ground truth is exact. vision-v2 uses other seeds and partly other templates; it was written before any vision result
was seen.

    .venv/bin/python benches/vision/build.py            # writes benches/vision-v1/ and benches/vision-v2/

Each bench folder gets tasks.json ({id, category, question, answer, score, tol?, image}) and images/<id>.png.
Scoring (vision_bench.py): 'text' = normalized exact match (NFKC, case, whitespace and surrounding punctuation),
'number' = the last number in the answer within tol, 'choice' = normalized exact match of a one-word answer.
"""
import html, json, random, shutil, subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = '/Applications/Chromium.app/Contents/MacOS/Chromium'
SANS = "'Helvetica Neue', Helvetica, Arial, sans-serif"
JP = '"Hiragino Sans", "Hiragino Kaku Gothic ProN", sans-serif'  # double quotes: used inside single-quoted style attributes
HANDS = ['"Bradley Hand"', '"Noteworthy"', '"Chalkboard SE"']
COLORS = {'red': '#d62728', 'blue': '#1f5fd6', 'green': '#2ca02c', 'yellow': '#f2c40f', 'purple': '#8e44ad', 'orange': '#ff7f0e', 'black': '#111111'}
NUM = "\nAnswer with the number only."
WORD = "\nAnswer with one word only."
TEXT = "\nAnswer with the exact text only."


def page(body, w, h, bg='#ffffff'):
    return (f"<!doctype html><html><head><meta charset='utf-8'><style>html,body{{margin:0;width:{w}px;height:{h}px;"
            f"background:{bg};overflow:hidden;font-family:{SANS};color:#111}}</style></head><body>{body}</body></html>", w, h)


# --- templates: each returns (html, w, h), question, answer, score, tol --------------------------------------------

def sign(r):
    words = r.choice([('NO PARKING', 'AFTER 6 PM'), ('PLATFORM 4', 'EXPRESS TRAINS'), ('STAFF ONLY', 'KEEP DOOR CLOSED'),
                      ('FRESH BREAD', 'OPEN 7 DAYS'), ('WET FLOOR', 'PLEASE TAKE CARE'), ('EXIT B2', 'CITY HALL')])
    bg = r.choice(['#0b5d1e', '#123c8c', '#8c1212', '#222222'])
    body = (f"<div style='margin:30px;padding:30px;border-radius:14px;background:{bg};color:#fff;text-align:center;"
            f"font-weight:700;font-size:54px;line-height:1.2'>{words[0]}<br><span style='font-size:34px'>{words[1]}</span></div>")
    return page(body, 640, 260), "What does the sign say? Give both lines, separated by a space." + TEXT, f"{words[0]} {words[1]}", 'text', None


def receipt(r, with_total=True):
    names = r.sample(['Coffee', 'Bagel', 'Orange juice', 'Muffin', 'Salad', 'Sandwich', 'Tea', 'Cookie', 'Soup', 'Water'], r.randint(4, 6))
    prices = [r.randint(150, 1290) / 100 for _ in names]
    total = round(sum(prices), 2)
    rows = ''.join(f"<tr><td>{n}</td><td style='text-align:right'>{p:.2f}</td></tr>" for n, p in zip(names, prices))
    tail = (f"<tr><td colspan=2><hr style='border:0;border-top:2px dashed #555'></td></tr><tr style='font-weight:700'><td>TOTAL</td>"
            f"<td style='text-align:right'>{total:.2f}</td></tr>") if with_total else ''
    store = r.choice(['CORNER CAFE', 'BLUE DOOR DELI', 'MORNING BAKERY'])
    body = (f"<div style='margin:20px auto;width:340px;padding:20px;background:#fbfaf4;box-shadow:0 0 6px #999;font-family:Menlo,monospace;font-size:17px'>"
            f"<div style='text-align:center;font-weight:700'>{store}</div><div style='text-align:center;font-size:13px'>2026-11-0{r.randint(1, 9)} 12:{r.randint(10, 59)}</div>"
            f"<table style='width:100%;margin-top:10px;font-size:17px'>{rows}{tail}</table></div>")
    h = 150 + 28 * len(names) + (60 if with_total else 0)
    if with_total:
        return page(body, 400, h, '#ddd'), "What is the TOTAL printed on the receipt?" + NUM, total, 'number', 0.005
    return page(body, 400, h, '#ddd'), "Add up the prices of all items on the receipt. What is the sum?" + NUM, total, 'number', 0.005


def japanese(r):
    line = r.choice(['東京駅は左です', '本日は休業日です', '駐車場は二階です', '出口はこちら', '営業時間は午前十時から'])
    body = f"<div style='margin:40px;font-family:{JP};font-size:46px'>{line}</div>"
    return page(body, 640, 150), "Transcribe the Japanese text in the image." + TEXT, line, 'text', None


def french(r):
    line = r.choice(['Fermé le dimanche', 'Café crème à emporter', 'Sortie de secours à gauche', 'Prière de ne pas fumer', 'Billets en vente à la gare'])
    body = f"<div style='margin:40px;font-family:Georgia,serif;font-size:44px'>{line}</div>"
    return page(body, 760, 150), "Transcribe the French text in the image, with its accents." + TEXT, line, 'text', None


def bars(r, horizontal=False):
    cats = r.sample(['Apples', 'Pears', 'Plums', 'Grapes', 'Kiwis', 'Lemons', 'Figs'], r.randint(4, 6))
    vals = [r.randrange(10, 95, 5) for _ in cats]
    while len(set(vals)) < len(vals):
        vals = [r.randrange(10, 95, 5) for _ in cats]
    W, H, L, B, T = 640, 420, 60, 360, 40
    span = 100
    parts = [f"<text x='{W // 2}' y='24' text-anchor='middle' font-size='18' font-weight='700'>Boxes sold</text>"]
    if not horizontal:
        for g in range(0, 101, 10):
            y = B - (B - T) * g / span
            parts.append(f"<line x1='{L}' x2='{W - 20}' y1='{y}' y2='{y}' stroke='#ccc'/><text x='{L - 8}' y='{y + 5}' text-anchor='end' font-size='13'>{g}</text>")
        bw = (W - L - 40) / len(cats)
        for i, (c, v) in enumerate(zip(cats, vals)):
            x = L + 10 + i * bw
            parts.append(f"<rect x='{x}' y='{B - (B - T) * v / span}' width='{bw * 0.7}' height='{(B - T) * v / span}' fill='#3b6fc4'/>"
                         f"<text x='{x + bw * 0.35}' y='{B + 20}' text-anchor='middle' font-size='14'>{c}</text>")
    else:
        L2, R2 = 90, W - 30
        for g in range(0, 101, 10):
            x = L2 + (R2 - L2) * g / span
            parts.append(f"<line y1='{T}' y2='{B}' x1='{x}' x2='{x}' stroke='#ccc'/><text y='{B + 20}' x='{x}' text-anchor='middle' font-size='13'>{g}</text>")
        bh = (B - T) / len(cats)
        for i, (c, v) in enumerate(zip(cats, vals)):
            y = T + 6 + i * bh
            parts.append(f"<rect y='{y}' x='{L2}' height='{bh * 0.7}' width='{(R2 - L2) * v / span}' fill='#c4703b'/>"
                         f"<text y='{y + bh * 0.45}' x='{L2 - 8}' text-anchor='end' font-size='14'>{c}</text>")
    svg = f"<svg width='{W}' height='{H}' font-family='Helvetica'>{''.join(parts)}</svg>"
    return page(svg, W, H), cats, vals


def bar_value(r, horizontal=False):
    p, cats, vals = bars(r, horizontal)
    i = r.randrange(len(cats))
    return p, f"Read the chart: how many boxes of {cats[i]} were sold?" + NUM, vals[i], 'number', 2.5


def bar_max(r, horizontal=False):
    p, cats, vals = bars(r, horizontal)
    return p, "Which fruit has the largest value in the chart?" + WORD, cats[vals.index(max(vals))], 'choice', None


def bar_diff(r):
    p, cats, vals = bars(r)
    return p, "What is the difference between the largest and the smallest value in the chart?" + NUM, max(vals) - min(vals), 'number', 2.5


def line_chart(r):
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug']
    vals = [r.randrange(20, 90, 5) for _ in months]
    while vals.count(min(vals)) > 1 or vals.count(max(vals)) > 1:
        vals = [r.randrange(20, 90, 5) for _ in months]
    W, H, L, B, T = 640, 400, 60, 340, 40
    parts = [f"<text x='{W // 2}' y='24' text-anchor='middle' font-size='18' font-weight='700'>Visitors (thousands)</text>"]
    for g in range(0, 101, 10):
        y = B - (B - T) * g / 100
        parts.append(f"<line x1='{L}' x2='{W - 20}' y1='{y}' y2='{y}' stroke='#ddd'/><text x='{L - 8}' y='{y + 5}' text-anchor='end' font-size='13'>{g}</text>")
    pts = []
    for i, (m, v) in enumerate(zip(months, vals)):
        x = L + 20 + i * (W - L - 60) / (len(months) - 1)
        y = B - (B - T) * v / 100
        pts.append(f"{x},{y}")
        parts.append(f"<circle cx='{x}' cy='{y}' r='5' fill='#c0392b'/><text x='{x}' y='{B + 20}' text-anchor='middle' font-size='14'>{m}</text>")
    parts.insert(1, f"<polyline points='{' '.join(pts)}' fill='none' stroke='#c0392b' stroke-width='3'/>")
    svg = f"<svg width='{W}' height='{H}' font-family='Helvetica'>{''.join(parts)}</svg>"
    if r.random() < 0.5:
        return page(svg, W, H), "In which month is the value lowest? Answer with the month as written on the chart." + WORD, months[vals.index(min(vals))], 'choice', None
    i = r.randrange(len(months))
    return page(svg, W, H), f"What is the value for {months[i]}?" + NUM, vals[i], 'number', 2.5


def shapes(r, kinds=('circle', 'square'), ask=None):
    W, H = 600, 400
    n = {k: r.randint(2, 9) for k in kinds}
    placed, parts = [], []
    for k in kinds:
        for _ in range(n[k]):
            for _ in range(500):
                x, y = r.randint(40, W - 40), r.randint(40, H - 40)
                if all((x - a) ** 2 + (y - b) ** 2 > 60 ** 2 for a, b in placed):
                    break
            placed.append((x, y))
            col = r.choice(list(COLORS.values()))
            if k == 'circle':
                parts.append(f"<circle cx='{x}' cy='{y}' r='20' fill='{col}'/>")
            elif k == 'square':
                parts.append(f"<rect x='{x - 18}' y='{y - 18}' width='36' height='36' fill='{col}'/>")
            else:
                parts.append(f"<polygon points='{x},{y - 22} {x - 21},{y + 16} {x + 21},{y + 16}' fill='{col}'/>")
    ask = ask or r.choice(kinds)
    svg = f"<svg width='{W}' height='{H}'>{''.join(parts)}</svg>"
    return page(svg, W, H), f"How many {ask}s are in the image?" + NUM, n[ask], 'number', 0


def left_of(r):
    names = r.sample(list(COLORS), 5)
    shapes_ = [r.choice(['circle', 'square', 'triangle']) for _ in names]
    i = r.randrange(1, 5)
    shapes_[i] = 'square'
    target = names[i]
    parts = []
    for j, (c, s) in enumerate(zip(names, shapes_)):
        x, y = 70 + j * 115, 110
        f = COLORS[c]
        parts.append({'circle': f"<circle cx='{x}' cy='{y}' r='38' fill='{f}'/>", 'square': f"<rect x='{x - 36}' y='{y - 36}' width='72' height='72' fill='{f}'/>",
                      'triangle': f"<polygon points='{x},{y - 40} {x - 40},{y + 32} {x + 40},{y + 32}' fill='{f}'/>"}[s])
    svg = f"<svg width='640' height='220'>{''.join(parts)}</svg>"
    q = f"What color is the shape directly to the left of the {target} square?" + WORD
    return page(svg, 640, 220), q, names[i - 1], 'choice', None


def above(r):
    names = r.sample(list(COLORS), 4)
    parts = [f"<circle cx='160' cy='{60 + j * 90}' r='32' fill='{COLORS[c]}'/>" for j, c in enumerate(names)]
    i = r.randrange(1, 4)
    svg = f"<svg width='320' height='400'>{''.join(parts)}</svg>"
    return page(svg, 320, 400), f"Which color is the circle directly above the {names[i]} circle?" + WORD, names[i - 1], 'choice', None


def ui_dialog(r):
    title, primary, secondary = r.choice([('Delete project?', 'Delete forever', 'Cancel'), ('Save changes?', 'Save and close', 'Discard'),
                                           ('Upgrade plan', 'Start free trial', 'Not now'), ('Share file', 'Send invite', 'Copy link')])
    body = (f"<div style='margin:40px auto;width:460px;border-radius:12px;box-shadow:0 4px 24px rgba(0,0,0,.25);padding:28px;background:#fff'>"
            f"<div style='font-size:22px;font-weight:700'>{title}</div><div style='margin:14px 0 26px;color:#555;font-size:15px'>"
            f"This action applies to everyone in the workspace.</div><div style='text-align:right'>"
            f"<span style='display:inline-block;padding:10px 18px;border:1px solid #bbb;border-radius:8px;font-size:15px;margin-right:10px'>{secondary}</span>"
            f"<span style='display:inline-block;padding:10px 18px;background:#2563eb;color:#fff;border-radius:8px;font-size:15px;font-weight:600'>{primary}</span>"
            f"</div></div>")
    return page(body, 560, 300, '#e9edf3'), "What does the primary (blue, filled) button say?" + TEXT, primary, 'text', None


def ui_toggle(r):
    opts = r.sample(['Dark mode', 'Email alerts', 'Auto backup', 'Location', 'Sound', 'Two-factor login'], 4)
    on = r.randrange(4)
    rows = ''.join(f"<div style='display:flex;justify-content:space-between;align-items:center;padding:14px 0;border-bottom:1px solid #eee;font-size:17px'>"
                   f"<span>{o}</span><span style='width:46px;height:26px;border-radius:13px;background:{'#22c55e' if i == on else '#cbd5e1'};position:relative'>"
                   f"<span style='position:absolute;top:3px;{'right' if i == on else 'left'}:3px;width:20px;height:20px;border-radius:10px;background:#fff'></span></span></div>"
                   for i, o in enumerate(opts))
    body = f"<div style='margin:30px auto;width:380px;background:#fff;padding:10px 24px;border-radius:12px'><div style='font-weight:700;font-size:20px;padding:10px 0'>Settings</div>{rows}</div>"
    return page(body, 460, 340, '#f1f5f9'), "Which setting is switched on (green toggle)? Give its label." + TEXT, opts[on], 'text', None


def table(r, ask_sum=False):
    items = r.sample(['Pens', 'Paper', 'Stapler', 'Folders', 'Tape', 'Markers', 'Scissors'], 5)
    qty = r.sample(range(2, 15), 5)
    price = [r.randint(1, 30) for _ in items]
    rows = ''.join(f"<tr><td>{a}</td><td>{q}</td><td>${p}</td></tr>" for a, q, p in zip(items, qty, price))
    body = (f"<table style='margin:30px;border-collapse:collapse;font-size:18px' border='1' cellpadding='8'>"
            f"<tr style='background:#eee'><th>Item</th><th>Qty</th><th>Unit price</th></tr>{rows}</table>")
    p = page(body, 420, 330)
    if ask_sum:
        return p, "Using the table, what is the total of Qty for all items?" + NUM, sum(qty), 'number', 0
    i = r.randrange(5)
    if r.random() < 0.5:
        return p, f"What is the unit price of {items[i]}, in dollars?" + NUM, price[i], 'number', 0
    return p, f"Which item has a quantity of {qty[i]}?" + WORD, items[i], 'choice', None


def handwriting(r):
    name = r.choice(['Maya', 'Tom', 'Lena', 'Raj', 'Sofia'])
    phone = f"{r.randint(200, 989)}-{r.randint(1000, 9999)}"
    font = r.choice(HANDS)
    body = (f"<div style='margin:30px;padding:24px;background:#fff8b5;font-family:{font};font-size:34px;transform:rotate(-2deg);"
            f"box-shadow:2px 3px 8px #aaa'>Call {name} back<br>at {phone}<br>before Friday!</div>")
    return page(body, 520, 280, '#e8e8e8'), "What phone number is written on the note? Write it as shown." + TEXT, phone, 'text', None


def hand_word(r):
    word = r.choice(['tomatoes', 'umbrella', 'library', 'birthday', 'printer'])
    font = r.choice(HANDS)
    body = (f"<div style='margin:30px;padding:24px;background:#fff;font-family:{font};font-size:38px;border-left:4px solid #e66'>"
            f"Shopping: milk, eggs,<br>{word}</div>")
    return page(body, 520, 220), "What is the last word written on the note?" + WORD, word, 'choice', None


def price_tag(r):
    item = r.choice(['Desk lamp', 'Garden hose', 'Rain jacket', 'Teapot'])
    old, new = r.randint(30, 90), None
    new = round(old * r.choice([0.5, 0.7, 0.8]), 2)
    body = (f"<div style='margin:30px;width:300px;padding:20px;border:3px solid #111;border-radius:10px;text-align:center'>"
            f"<div style='font-size:22px'>{item}</div><div style='font-size:20px;color:#777;text-decoration:line-through'>${old:.2f}</div>"
            f"<div style='font-size:44px;font-weight:800;color:#c00'>${new:.2f}</div></div>")
    return page(body, 380, 240), "What is the sale price (the large red number) on the tag?" + NUM, new, 'number', 0.005


V1 = [  # 25 tasks, seed 1
    ('ocr', sign), ('ocr', sign), ('ocr-receipt', receipt), ('ocr-receipt', receipt),
    ('ocr-japanese', japanese), ('ocr-japanese', japanese), ('ocr-french', french), ('ocr-french', french),
    ('chart', bar_value), ('chart', bar_value), ('chart', bar_max), ('chart', line_chart), ('chart', line_chart),
    ('count', shapes), ('count', shapes), ('count', shapes),
    ('position', left_of), ('position', left_of),
    ('ui', ui_dialog), ('ui', ui_dialog),
    ('table', table), ('table', table),
    ('handwriting', handwriting),
    ('multi-step', lambda r: receipt(r, with_total=False)), ('multi-step', bar_diff),
]
V2 = [  # held out: 10 tasks, seed 2, some templates only here
    ('ocr', price_tag), ('ocr-receipt', receipt), ('ocr-japanese', japanese), ('chart', lambda r: bar_value(r, horizontal=True)),
    ('chart', lambda r: bar_max(r, horizontal=True)), ('count', lambda r: shapes(r, ('circle', 'square', 'triangle'), 'triangle')),
    ('position', above), ('ui', ui_toggle), ('handwriting', hand_word), ('multi-step', lambda r: table(r, ask_sum=True)),
]


def render(items, out):
    with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as profile:
        for name, (doc, w, h) in items:
            src = Path(tmp, name + '.html'); src.write_text(doc)
            subprocess.run([CHROMIUM, '--headless=new', '--disable-gpu', '--no-first-run', '--no-default-browser-check', '--hide-scrollbars',
                            f'--user-data-dir={profile}', '--force-device-scale-factor=1', f'--window-size={w},{h}',
                            f'--screenshot={out / (name + ".png")}', src.as_uri()], capture_output=True, timeout=120)
            if not (out / (name + '.png')).exists():
                raise SystemExit(f'render failed: {name}')


def build(name, templates, seed):
    r = random.Random(seed)
    folder = ROOT / name
    if folder.exists():
        shutil.rmtree(folder)
    (folder / 'images').mkdir(parents=True)
    tasks, docs, seen = [], [], {}
    for cat, fn in templates:
        seen[cat] = seen.get(cat, 0) + 1
        tid = f'{cat}-{seen[cat]}'
        doc, q, ans, score, tol = fn(r)
        docs.append((tid, doc))
        tasks.append(dict(id=tid, category=cat, question=q, answer=ans, score=score, tol=tol, image=f'images/{tid}.png'))
    render(docs, folder / 'images')
    (folder / 'tasks.json').write_text(json.dumps({'note': f'{name}: generated by benches/vision/build.py (seed {seed}).', 'tasks': tasks},
                                                  ensure_ascii=False, indent=1))
    print(f'wrote {folder} ({len(tasks)} tasks)')


if __name__ == '__main__':
    build('vision-v1', V1, 1)
    build('vision-v2', V2, 2)
