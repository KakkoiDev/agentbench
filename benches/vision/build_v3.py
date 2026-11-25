"""Builds benches/vision-v3: 30 hard image tasks with exact answers (seed 3).

v1/v2 were saturated by a strong hosted model (25/25, 9/10). v3 makes the images dense (1024-1600 px, small labels,
many similar objects, near-colour distractors, rotated / low-contrast text, 20+ row tables) and most questions need a
lookup plus arithmetic. Rendering is the same headless Chromium as build.py.

    .venv/bin/python benches/vision/build_v3.py      # writes benches/vision-v3/{tasks.json,data.json,images/}
    .venv/bin/python benches/vision-v3/check.py      # re-derives every answer from data.json and tests grade()

Each template returns (doc, question, answer, score, tol, data). `data` is the exact content drawn in the image and is
saved to data.json so check.py can recompute the answer independently.
"""
import json, math, random, shutil, sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build import page, render, ROOT  # noqa: E402

JP = '"Hiragino Sans", "Hiragino Kaku Gothic ProN", sans-serif'
JP_SERIF = '"Hiragino Mincho ProN", serif'
HANDS = ['"Bradley Hand"', 'Noteworthy', '"Chalkboard SE"']
NUM = ' Reply with only the number.'
MONEY = ' Reply with only the number (dot as decimal separator, no currency sign).'


def esc(s):
    return s.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')


def svgdoc(parts, w, h, bg='#ffffff'):
    return page(f"<svg width='{w}' height='{h}' font-family='Helvetica'>{''.join(parts)}</svg>", w, h, bg)


def noise_bg(r, w, h, n=160, palette=('#e9e4da', '#ddd6c8', '#efe9df', '#d8d1c2')):
    return ''.join(f"<rect x='{r.randint(-50, w)}' y='{r.randint(-50, h)}' width='{r.randint(40, 260)}' height='{r.randint(20, 160)}' "
                   f"fill='{r.choice(palette)}' opacity='0.6' transform='rotate({r.randint(-30, 30)})'/>" for _ in range(n))


# ---------------------------------------------------------------------------------------------------------- OCR ----

GROCERIES = ['Oat milk 1L', 'Sourdough loaf', 'Free-range eggs 12', 'Greek yogurt', 'Cheddar 400g', 'Bananas', 'Tomatoes vine',
             'Cherry tomatoes', 'Spinach 200g', 'Olive oil 750ml', 'Basmati rice 1kg', 'Penne 500g', 'Tinned chickpeas',
             'Dish soap', 'Paper towels', 'Toothpaste', 'Coffee beans', 'Green tea 40', 'Dark chocolate', 'Butter 250g',
             'Apples Gala', 'Lemons', 'Garlic', 'Red onions', 'Frozen peas', 'Chicken thighs', 'Salmon fillet',
             'Laundry pods', 'Sparkling water', 'Peanut butter', 'Honey 350g', 'Oranges']


def receipt_long(r):
    names = r.sample(GROCERIES, 26)
    lines = []
    for n in names:
        q = r.choice([1, 1, 1, 2, 2, 3, 4])
        u = r.randint(79, 1299) / 100
        lines.append(dict(name=n, qty=q, unit=u, amount=round(q * u, 2), taxable=r.random() < 0.38))
    while not 8 <= sum(l['taxable'] for l in lines) <= 11:
        for l in lines:
            l['taxable'] = r.random() < 0.38
    sub = round(sum(l['amount'] for l in lines), 2)
    rows = ''.join(
        f"<div style='display:flex;white-space:pre'><span style='width:70px'>{l['qty']} x</span><span style='flex:1'>{esc(l['name'])}"
        f"<span style='color:#555'>  @{l['unit']:.2f}</span></span><span style='width:70px;text-align:right'>{l['amount']:.2f}</span>"
        f"<span style='width:22px;text-align:right'>{'T' if l['taxable'] else ''}</span></div>" for l in lines)
    tax = round(sum(l['amount'] for l in lines if l['taxable']) * 0.08, 2)
    body = (f"<svg style='position:absolute;left:0;top:0' width='1100' height='1050'>{noise_bg(r, 1100, 1050)}</svg>"
            f"<div style='position:absolute;left:300px;top:40px;width:470px;padding:22px 24px;background:#fbfaf3;box-shadow:0 2px 10px #777;"
            f"font-family:Menlo,monospace;font-size:13px;line-height:19px;color:#333;transform:rotate(-1deg)'>"
            f"<div style='text-align:center;font-weight:700;font-size:16px'>GREENLEAF MARKET</div>"
            f"<div style='text-align:center'>14 Harbour Rd · Till 3 · 2026-11-18 17:42</div><div>{'-' * 56}</div>{rows}<div>{'-' * 56}</div>"
            f"<div style='display:flex'><span style='flex:1'>SUBTOTAL</span><span>{sub:.2f}</span></div>"
            f"<div style='display:flex'><span style='flex:1'>TAX 8% on T items</span><span>{tax:.2f}</span></div>"
            f"<div style='display:flex;font-weight:700'><span style='flex:1'>TOTAL</span><span>{sub + tax:.2f}</span></div>"
            f"<div style='text-align:center;margin-top:8px'>T = taxable item · thank you!</div></div>")
    ans = round(sum(l['amount'] for l in lines if l['taxable']), 2)
    q = ("On this receipt some lines are marked with a T at the far right. Add up the line amounts (the right-hand price column, "
         "not the @ unit price) of every line marked T. What is that sum?" + MONEY)
    return page(body, 1100, 1050, '#cfc8ba'), q, ans, 'number', 0.005, dict(kind='receipt_taxable', lines=lines)


CITIES = ['Linz', 'Lienz', 'Graz', 'Wels', 'Steyr', 'Krems', 'Leoben', 'Lindau', 'Bregenz', 'Villach', 'Dornbirn', 'Innsbruck']


def shipping_labels(r):
    cities = r.sample(CITIES, 12)
    target = r.choice(['Linz', 'Lienz', 'Lindau'])
    if target not in cities:
        cities[r.randrange(12)] = target
    labels = []
    for c in cities:
        code = f"{r.choice('RCLEV')}{r.choice('BXQK')} {r.randint(1000, 9999)} {r.randint(1000, 9999)} {r.randint(0, 9)}AT"
        rot = 180 if c == target else r.choice([-90, -7, -3, 0, 4, 8, 90, 180])
        labels.append(dict(city=c, code=code, rot=rot, zip=r.randint(1010, 9999)))
    cells = []
    for i, l in enumerate(labels):
        x, y = 40 + (i % 4) * 340, 40 + (i // 4) * 330
        cells.append(f"<div style='position:absolute;left:{x}px;top:{y}px;width:290px;height:250px;display:flex;align-items:center;justify-content:center'>"
                     f"<div style='width:250px;padding:12px;background:#fff;border:1px solid #999;font-size:12px;color:#222;transform:rotate({l['rot']}deg)'>"
                     f"<div style='font-weight:700;font-size:13px'>PRIORITY · EXPRES</div><div style='margin-top:6px'>TO: Firma Huber GmbH</div>"
                     f"<div>Hauptstraße {r.randint(2, 88)}</div><div style='font-size:15px;font-weight:700'>{l['zip']} {l['city']}</div>"
                     f"<div style='margin-top:8px;height:34px;background:repeating-linear-gradient(90deg,#000 0 2px,#fff 2px 4px,#000 4px 7px,#fff 7px 8px)'></div>"
                     f"<div style='font-family:Menlo,monospace;font-size:11px;letter-spacing:1px'>{l['code']}</div></div></div>")
    body = f"<div style='position:relative;width:1400px;height:1040px;background:#b9a27f'>{''.join(cells)}</div>"
    q = (f"Twelve parcel labels are shown, some rotated or upside down. What is the tracking number printed under the barcode on the "
         f"label addressed to {target}? Reply with only the tracking number, exactly as printed.")
    ans = next(l['code'] for l in labels if l['city'] == target)
    return page(body, 1400, 1040), q, ans, 'text', None, dict(kind='label_code', labels=labels, target=target)


def low_contrast(r):
    ref = r.randint(1000000, 9999999)
    inv, phone = r.randint(100000, 999999), f"0{r.randint(100, 999)} {r.randint(100000, 999999)}"
    filler = ' '.join(r.choice(['account', 'balance', 'period', 'service', 'meter', 'reading', 'tariff', 'payment', 'direct', 'debit', 'estimated',
                                'customer', 'number', 'standing', 'charge', 'unit', 'rate', 'credit']) for _ in range(260))
    body = (f"<div style='margin:40px;width:1100px;height:820px;background:#f4f4f2;position:relative;padding:40px;box-sizing:border-box;"
            f"font-size:14px;color:#444'><div style='font-size:28px;font-weight:700;color:#222'>Energy statement</div>"
            f"<div style='margin-top:8px'>Invoice no. {inv} · Customer line {phone}</div><p style='line-height:1.7;color:#666'>{filler}</p>"
            f"<div style='position:absolute;right:46px;bottom:34px;font-size:13px;color:#dcdcd8'>Ref. {ref}</div>"
            f"<div style='position:absolute;left:40px;bottom:34px;font-size:13px;color:#555'>Page 1 of 2 · printed 2026-11-03</div></div>")
    q = ("Near the bottom right corner of this statement a 7-digit number follows 'Ref.' in very faint light-gray print. "
         "What is that number?" + NUM)
    return page(body, 1180, 900, '#d0d0cc'), q, ref, 'number', 0, dict(kind='plain', value=ref)


JP_MENU = [('醤油ラーメン', 'しょうゆ'), ('味噌ラーメン', 'みそ'), ('塩ラーメン', 'しお'), ('豚骨ラーメン', 'とんこつ'), ('味噌つけ麺', ''),
           ('醤油つけ麺', ''), ('担々麺', ''), ('冷やし中華', '夏季限定'), ('餃子（6個）', ''), ('餃子（12個）', ''), ('焼き飯', ''),
           ('半チャーハン', ''), ('チャーシュー丼', ''), ('ミニチャーシュー丼', ''), ('唐揚げ', '3個'), ('生ビール', '中'), ('瓶ビール', ''),
           ('ウーロン茶', ''), ('味玉', 'トッピング'), ('メンマ', 'トッピング')]


def jp_menu(r):
    prices = r.sample(range(15, 125), len(JP_MENU))
    items = [dict(name=n, note=note, price=p * 10) for (n, note), p in zip(JP_MENU, prices)]
    r.shuffle(items)
    a, b = r.choice([('味噌つけ麺', '半チャーハン'), ('醤油つけ麺', 'ミニチャーシュー丼'), ('塩ラーメン', '餃子（12個）')])
    def row(it):
        size = r.choice([20, 22, 26])
        return (f"<div style='display:flex;align-items:baseline;border-bottom:1px dotted #8a7;padding:7px 0'><span style='font-size:{size}px'>{it['name']}</span>"
                f"<span style='font-size:13px;color:#cdb;margin-left:8px'>{it['note']}</span><span style='flex:1'></span>"
                f"<span style='font-size:22px;color:#ffd866'>{it['price']}円</span></div>")
    col1, col2 = ''.join(row(i) for i in items[:10]), ''.join(row(i) for i in items[10:])
    body = (f"<div style='margin:30px;width:1220px;height:900px;box-sizing:border-box;padding:30px 40px;background:#1f3a2c;color:#f4efe0;"
            f"font-family:{JP};border:14px solid #6b4a2b'><div style='text-align:center;font-size:40px;font-weight:700'>らーめん 龍月</div>"
            f"<div style='text-align:center;font-size:15px;color:#cdb;margin-bottom:16px'>税込価格 · 大盛り無料 · 現金のみ</div>"
            f"<div style='display:flex;gap:60px'><div style='flex:1'>{col1}</div><div style='flex:1'>{col2}</div></div></div>")
    q = (f"このメニューで「{a}」と「{b}」を1つずつ注文すると合計で何円ですか？ "
         f"(Using this menu, how many yen do one {a} and one {b} cost together?)" + NUM)
    ans = sum(i['price'] for i in items if i['name'] in (a, b))
    return page(body, 1280, 960), q, ans, 'number', 0, dict(kind='sum_named', items=items, names=[a, b], key='price')


JP_LINES = ['本日の特売品は三階です', '午後五時より営業開始', '会員様限定の割引あり', '駅前店は改装中です', '傘のお忘れにご注意',
            '整理券は受付でどうぞ', '催事場は屋上にあります', 'お会計は一階レジへ']


def jp_vertical(r):
    lines = r.sample(JP_LINES, 6)
    colors = ['#c0392b', '#1d3f8f', '#222222', '#1e6b3a', '#6b3a8f', '#8a5a00']
    order = list(range(6)); r.shuffle(order)
    cols = []
    for i, k in enumerate(order):
        size = r.choice([30, 34, 38])
        cols.append(f"<div style='writing-mode:vertical-rl;font-family:{JP_SERIF};font-size:{size}px;color:{colors[k]};padding:20px 14px;"
                    f"background:rgba(255,255,255,0.55);border:2px solid {colors[k]};margin-top:{r.randint(0, 260)}px;height:520px'>{lines[k]}</div>")
    stripes = 'repeating-linear-gradient(45deg,#f3e3c4 0 18px,#ead5ae 18px 36px)'
    body = (f"<div style='width:1200px;height:1000px;background:{stripes};display:flex;gap:44px;justify-content:center;padding-top:60px;"
            f"box-sizing:border-box;flex-direction:row-reverse'>{''.join(cols)}</div>")
    q = ("Six vertical Japanese signs are shown, each in a different colour. Transcribe the text of the red sign, reading top to bottom. "
         "Reply with only that Japanese text.")
    return page(body, 1200, 1000), q, lines[0], 'text', None, dict(kind='plain', value=lines[0])


FR_MENU = ['Tarte aux pommes', 'Tarte aux poires', 'Tarte au citron meringuée', 'Crème brûlée', 'Mousse au chocolat', 'Île flottante',
           'Crêpe au sucre', 'Crêpe beurre-sucre', 'Croque-monsieur', 'Croque-madame', 'Quiche lorraine', 'Soupe à l’oignon',
           'Café crème', 'Café noisette', 'Chocolat chaud', 'Chocolat viennois', 'Thé à la menthe', 'Jus d’orange pressé']


def fr_menu(r):
    items = [dict(name=n, price=r.randrange(250, 1490, 10) / 100) for n in FR_MENU]
    while len({i['price'] for i in items}) < len(items):
        for i in items:
            i['price'] = r.randrange(250, 1490, 10) / 100
    r.shuffle(items)
    rows = ''.join(f"<div style='display:flex;padding:6px 0;font-size:{r.choice([19, 21, 23])}px'><span style='font-style:italic'>{esc(i['name'])}</span>"
                   f"<span style='flex:1;border-bottom:2px dotted #a98;margin:0 8px 6px'></span><span>{i['price']:.2f}".replace('.', ',') + " €</span></div>"
                   for i in items)
    body = (f"<svg style='position:absolute' width='1200' height='1150'>{noise_bg(r, 1200, 1150, 90, ('#d9c9a8', '#c9b48a', '#e4d6b8'))}</svg>"
            f"<div style='position:absolute;left:250px;top:40px;width:700px;padding:30px 44px;background:#fffaf0;font-family:Georgia,serif;"
            f"color:#3a2a1a;box-shadow:0 3px 14px #876;transform:rotate(1.5deg)'><div style='text-align:center;font-size:38px'>Café des Lilas</div>"
            f"<div style='text-align:center;font-size:15px;margin-bottom:14px'>Service continu · prix nets en euros</div>{rows}</div>")
    a, b = r.choice([('Tarte aux pommes', 'Chocolat chaud'), ('Crêpe au sucre', 'Café crème'), ('Croque-monsieur', 'Thé à la menthe')])
    q = (f"D'après la carte, combien coûtent ensemble un « {a} » et un « {b} » ? "
         f"(According to the menu, what do one {a} and one {b} cost together, in euros?)" + MONEY)
    ans = round(sum(i['price'] for i in items if i['name'] in (a, b)), 2)
    return page(body, 1200, 1150, '#c8b993'), q, ans, 'number', 0.005, dict(kind='sum_named', items=items, names=[a, b], key='price')


FR_LINES = ['Le parking sera fermé du lundi 3 au vendredi 7.', 'Les vélos doivent être rangés au sous-sol.',
            "Merci de ne pas déposer d'objets encombrants.", 'Une réunion aura lieu à 18 h dans le hall.',
            "L'ascenseur côté cour est hors service.", 'Les colis non réclamés seront renvoyés.', "Pensez à fermer la porte d'entrée à clé."]


def fr_notice(r):
    lines = r.sample(FR_LINES, 5)
    others = ''.join(f"<div style='position:absolute;left:{x}px;top:{y}px;width:330px;padding:18px;background:{bg};font-size:15px;"
                     f"transform:rotate({rot}deg);box-shadow:1px 2px 6px #555'><b>{t}</b><br>{esc(r.choice(FR_LINES))}<br>{esc(r.choice(FR_LINES))}</div>"
                     for x, y, rot, bg, t in [(40, 60, -6, '#fff6a8', 'À VENDRE'), (760, 40, 5, '#d8f0ff', 'PERDU : CHAT GRIS'),
                                              (60, 700, 4, '#ffe0e8', 'COURS DE PIANO'), (780, 690, -4, '#e6ffe0', 'VIDE-GRENIER')])
    body = (f"<div style='position:relative;width:1200px;height:1050px;background:#a67c52'>{others}"
            f"<div style='position:absolute;left:300px;top:300px;width:600px;padding:22px 26px;background:#fdfdf8;font-family:Georgia,serif;"
            f"font-size:16px;line-height:1.7;color:#555;transform:rotate(-3deg);box-shadow:1px 3px 10px #333'>"
            f"<div style='text-align:center;font-weight:700;font-size:20px;color:#333'>AVIS AUX RÉSIDENTS</div>"
            + ''.join(f"<div style='white-space:nowrap'>{esc(l)}</div>" for l in lines) +
            "<div style='text-align:right;font-size:13px;margin-top:6px'>Le syndic</div></div></div>")
    q = ("On the noticeboard, find the white notice titled « AVIS AUX RÉSIDENTS ». Transcribe the fourth sentence below its title "
         "exactly, with its accents and punctuation. Reply with only that sentence.")
    return page(body, 1200, 1050), q, lines[3], 'text', None, dict(kind='nth', items=lines, n=4)


HAND_ITEMS = ['taxi', 'lunch', 'printer ink', 'parking', 'train', 'hotel bar', 'stamps', 'coffee', 'cables', 'flowers', 'snacks', 'tolls']


def hand_expenses(r):
    items = [dict(item=n, amount=r.randint(4, 96), crossed=False) for n in r.sample(HAND_ITEMS, 11)]
    for i in r.sample(range(11), 3):
        items[i]['crossed'] = True
    font = r.choice(HANDS)
    rows = ''.join(f"<div style='height:52px;line-height:52px;transform:rotate({r.uniform(-2.5, 2.5):.1f}deg);margin-left:{r.randint(0, 30)}px'>"
                   f"<span style=\"{'text-decoration:line-through;text-decoration-thickness:3px' if it['crossed'] else ''}\">"
                   f"{it['item']} &nbsp;&nbsp; {it['amount']}</span></div>" for it in items)
    body = (f"<div style='margin:50px auto;width:760px;height:1000px;background:repeating-linear-gradient(#fffdf2 0 51px,#9ec3e6 51px 52px);"
            f"padding:40px 60px;box-sizing:border-box;font-family:{font};font-size:34px;color:#1b2a6b;transform:rotate(1.5deg);"
            f"box-shadow:2px 4px 12px #777;border-left:3px solid #e88'><div style='font-size:38px'>Expenses - Nov trip</div>{rows}</div>")
    q = "Add up the amounts on this handwritten expense list, skipping every line that is crossed out." + NUM
    ans = sum(it['amount'] for it in items if not it['crossed'])
    return page(body, 1100, 1100, '#cfd4d8'), q, ans, 'number', 0, dict(kind='sum_where', rows=items, key='amount', where=('crossed', False))


# -------------------------------------------------------------------------------------------------------- charts ----

def grid_axis(parts, L, R, T, B, vmax, step, label_every=None, font=11):
    for g in range(0, vmax + 1, step):
        y = B - (B - T) * g / vmax
        parts.append(f"<line x1='{L}' x2='{R}' y1='{y:.1f}' y2='{y:.1f}' stroke='{'#bbb' if g % (label_every or step) == 0 else '#e4e4e4'}'/>")
        if g % (label_every or step) == 0:
            parts.append(f"<text x='{L - 6}' y='{y + 4:.1f}' text-anchor='end' font-size='{font}'>{g}</text>")


def chart_grouped(r):
    regions = ['North', 'South', 'East', 'West', 'Central', 'Coast', 'Hills', 'Metro']
    years = ['2022', '2023', '2024', '2025']
    cols = ['#9ecae1', '#6baed6', '#3182bd', '#08519c']
    vals = {y: [r.randint(12, 98) for _ in regions] for y in years}
    W, H, L, R, T, B = 1400, 820, 70, 1380, 80, 740
    parts = ["<text x='700' y='34' text-anchor='middle' font-size='20' font-weight='700'>Service calls per region</text>"]
    grid_axis(parts, L, R, T, B, 100, 10)
    gw = (R - L) / len(regions)
    for i, reg in enumerate(regions):
        for j, y in enumerate(years):
            v = vals[y][i]; x = L + i * gw + 18 + j * 34; h = (B - T) * v / 100
            parts.append(f"<rect x='{x:.1f}' y='{B - h:.1f}' width='30' height='{h:.1f}' fill='{cols[j]}'/>"
                         f"<text x='{x + 15:.1f}' y='{B - h - 3:.1f}' text-anchor='middle' font-size='9' fill='#333'>{v}</text>")
        parts.append(f"<text x='{L + i * gw + gw / 2:.1f}' y='{B + 22}' text-anchor='middle' font-size='13'>{reg}</text>")
    for j, y in enumerate(years):
        parts.append(f"<rect x='{1060 + j * 80}' y='50' width='14' height='14' fill='{cols[j]}'/><text x='{1078 + j * 80}' y='62' font-size='12'>{y}</text>")
    q = ("In this grouped bar chart the small number above each bar is its value. Add up the 2024 values (the third bar of each group, "
         "see the legend) for all eight regions." + NUM)
    return svgdoc(parts, W, H), q, sum(vals['2024']), 'number', 0, dict(kind='series_sum', vals=vals, series='2024')


def chart_lines(r):
    months = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
    series = [('Harbor', '#1f77b4', ''), ('Delta', '#1f77b4', '8,5'), ('Summit', '#ff7f0e', ''), ('Prairie', '#ff7f0e', '8,5'),
              ('Canyon', '#2ca02c', ''), ('Mesa', '#d62728', '')]
    vals = {s: [r.randrange(10, 101, 10) for _ in months] for s, _, _ in series}
    m = r.randrange(3, 11)
    a, b = r.sample(['Harbor', 'Delta', 'Summit', 'Prairie'], 2)
    while vals[a][m] - vals[b][m] < 20:
        vals[a][m], vals[b][m] = r.randrange(50, 101, 10), r.randrange(10, 51, 10)
    W, H, L, R, T, B = 1400, 860, 70, 1250, 80, 770
    parts = ["<text x='700' y='36' text-anchor='middle' font-size='20' font-weight='700'>Weekly bookings by site (hundreds)</text>"]
    grid_axis(parts, L, R, T, B, 100, 10)
    xs = [L + 30 + i * (R - L - 60) / 11 for i in range(12)]
    for i, mo in enumerate(months):
        parts.append(f"<text x='{xs[i]:.1f}' y='{B + 22}' text-anchor='middle' font-size='13'>{mo}</text>")
    for s, c, dash in series:
        pts = ' '.join(f"{xs[i]:.1f},{B - (B - T) * v / 100:.1f}" for i, v in enumerate(vals[s]))
        parts.append(f"<polyline points='{pts}' fill='none' stroke='{c}' stroke-width='2.5' stroke-dasharray='{dash}'/>")
        for i, v in enumerate(vals[s]):
            parts.append(f"<circle cx='{xs[i]:.1f}' cy='{B - (B - T) * v / 100:.1f}' r='4' fill='{c}'/>")
    for k, (s, c, dash) in enumerate(series):
        parts.append(f"<line x1='1270' x2='1310' y1='{110 + k * 30}' y2='{110 + k * 30}' stroke='{c}' stroke-width='2.5' stroke-dasharray='{dash}'/>"
                     f"<text x='1318' y='{115 + k * 30}' font-size='14'>{s}</text>")
    q = (f"Six lines are plotted (solid and dashed lines share colours; use the legend on the right). Every point lies exactly on a "
         f"gridline. In {months[m]}, what is {a}'s value minus {b}'s value?" + NUM)
    return svgdoc(parts, W, H), q, vals[a][m] - vals[b][m], 'number', 0, dict(kind='diff', vals=vals, a=a, b=b, i=m, labels=months)


def chart_stacked(r):
    stores = [f'S{i}' for i in range(1, 13)]
    segs = [('Online', '#4e79a7'), ('In-store', '#59a14f'), ('Refunds', '#f28e2b'), ('Gift cards', '#edc948')]
    vals = {s: [r.randrange(5, 45, 5) for _ in stores] for s, _ in segs}
    W, H, L, R, T, B = 1300, 900, 70, 1100, 80, 820
    vmax = 180
    parts = ["<text x='650' y='36' text-anchor='middle' font-size='20' font-weight='700'>Transactions by store (stacked)</text>"]
    grid_axis(parts, L, R, T, B, vmax, 5, 10)
    bw = (R - L) / len(stores)
    for i, st in enumerate(stores):
        base = 0
        for s, c in segs:
            v = vals[s][i]; y0 = B - (B - T) * (base + v) / vmax
            parts.append(f"<rect x='{L + i * bw + 14:.1f}' y='{y0:.1f}' width='{bw - 28:.1f}' height='{(B - T) * v / vmax:.1f}' fill='{c}' stroke='#fff' stroke-width='0.6'/>")
            base += v
        parts.append(f"<text x='{L + i * bw + bw / 2:.1f}' y='{B + 22}' text-anchor='middle' font-size='13'>{st}</text>")
    for k, (s, c) in enumerate(segs):
        parts.append(f"<rect x='1130' y='{120 + k * 30}' width='16' height='16' fill='{c}'/><text x='1152' y='{133 + k * 30}' font-size='14'>{s}</text>")
    i = r.randrange(3, 12)
    q = (f"In this stacked bar chart, segment boundaries fall exactly on the light gridlines (every 5). How large is the Refunds "
         f"segment of store {stores[i]}?" + NUM)
    return svgdoc(parts, W, H), q, vals['Refunds'][i], 'number', 0, dict(kind='pick', vals=vals, series='Refunds', i=i)


def chart_scatter(r):
    pts, kinds = [], [('tri', '#1f5fd6')] * 22 + [('circ', '#1f5fd6')] * 22 + [('tri', '#ff7f0e')] * 18
    for k, c in kinds:
        for _ in range(1000):
            x, y = r.uniform(3, 97), r.uniform(3, 97)
            if abs(y - 60) >= 3 and all((x - a) ** 2 + (y - b) ** 2 > 3.2 ** 2 for a, b, _, _ in pts):
                break
        pts.append((round(x, 2), round(y, 2), k, c))
    W, H, L, R, T, B = 1200, 1000, 70, 1160, 60, 940
    parts = []
    grid_axis(parts, L, R, T, B, 100, 10)
    for g in range(0, 101, 10):
        x = L + (R - L) * g / 100
        parts.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{T}' y2='{B}' stroke='#eee'/><text x='{x:.1f}' y='{B + 18}' text-anchor='middle' font-size='11'>{g}</text>")
    y60 = B - (B - T) * 0.6
    parts.append(f"<line x1='{L}' x2='{R}' y1='{y60}' y2='{y60}' stroke='#333' stroke-width='1.5' stroke-dasharray='10,6'/>")
    for x, y, k, c in pts:
        px, py = L + (R - L) * x / 100, B - (B - T) * y / 100
        parts.append(f"<circle cx='{px:.1f}' cy='{py:.1f}' r='7' fill='{c}' opacity='0.85'/>" if k == 'circ' else
                     f"<polygon points='{px:.1f},{py - 8:.1f} {px - 7.5:.1f},{py + 6:.1f} {px + 7.5:.1f},{py + 6:.1f}' fill='{c}' opacity='0.85'/>")
    q = "How many blue triangles lie above the dashed horizontal line? (Ignore blue circles and orange triangles.)" + NUM
    ans = sum(1 for x, y, k, c in pts if k == 'tri' and c == '#1f5fd6' and y > 60)
    data = dict(kind='count_pts', pts=[list(p) for p in pts], shape='tri', color='#1f5fd6', above=60)
    return svgdoc(parts, W, H), q, ans, 'number', 0, data


REGIONS = ['Aldmoor', 'Briarfield', 'Calder', 'Dunmore', 'Eastvale', 'Fenwick', 'Glenrock', 'Harlow', 'Ivydale', 'Juniper', 'Kestrel',
           'Larkspur', 'Millbrook', 'Northcote', 'Oakhurst', 'Pembury', 'Quarry Hill', 'Redcliff', 'Stonebridge', 'Thornbury', 'Upton', 'Westmere']


def chart_hbar(r):
    vals = []
    for _ in REGIONS:
        v = r.randint(20, 98)
        while 65 <= v <= 75:
            v = r.randint(20, 98)
        vals.append(v)
    W, H, L, R, T, B = 1100, 1000, 120, 1060, 70, 960
    parts = ["<text x='550' y='34' text-anchor='middle' font-size='18' font-weight='700'>Survey score by district</text>"]
    for g in range(0, 101, 10):
        x = L + (R - L) * g / 100
        parts.append(f"<line x1='{x:.1f}' x2='{x:.1f}' y1='{T}' y2='{B}' stroke='#ddd'/><text x='{x:.1f}' y='{T - 8}' text-anchor='middle' font-size='11'>{g}</text>")
    bh = (B - T) / len(REGIONS)
    for i, (n, v) in enumerate(zip(REGIONS, vals)):
        y = T + i * bh
        parts.append(f"<rect x='{L}' y='{y + 5:.1f}' width='{(R - L) * v / 100:.1f}' height='{bh - 10:.1f}' fill='#7a5195'/>"
                     f"<text x='{L - 6}' y='{y + bh / 2 + 4:.1f}' text-anchor='end' font-size='11'>{n}</text>")
    q = "How many of the 22 districts have a score greater than 70?" + NUM
    return svgdoc(parts, W, H), q, sum(v > 70 for v in vals), 'number', 0, dict(kind='count_gt', vals=vals, gt=70)


# ------------------------------------------------------------------------------------------------------ counting ----

def place(r, n, w, h, margin, mind, placed=None):
    placed = placed if placed is not None else []
    out = []
    for _ in range(n):
        for _ in range(3000):
            x, y = r.uniform(margin, w - margin), r.uniform(margin, h - margin)
            if all((x - a) ** 2 + (y - b) ** 2 > mind ** 2 for a, b in placed):
                break
        else:
            raise SystemExit('placement failed')
        placed.append((x, y)); out.append((round(x, 1), round(y, 1)))
    return out


def count_red(r):
    W, H = 1200, 900
    n_red = r.randint(30, 40)
    kinds = ['#d62728'] * n_red + ['#f28e2b'] * r.randint(18, 25) + ['#e377c2'] * r.randint(15, 22)
    r.shuffle(kinds)
    pos = place(r, len(kinds), W, H, 30, 24)   # radius 15-19: neighbours may overlap a little
    parts = [f"<circle cx='{x}' cy='{y}' r='{r.randint(15, 19)}' fill='{c}' stroke='#fff' stroke-width='1.5'/>" for (x, y), c in zip(pos, kinds)]
    q = "How many red circles are there? (Orange and pink circles do not count; some circles overlap.)" + NUM
    return svgdoc(parts, W, H, '#f7f7f7'), q, n_red, 'number', 0, dict(kind='count_eq', items=kinds, value='#d62728')


def star(cx, cy, n, R, rot):
    pts = []
    for k in range(2 * n):
        rad = R if k % 2 == 0 else R * 0.45
        a = rot + math.pi * k / n
        pts.append(f"{cx + rad * math.sin(a):.1f},{cy - rad * math.cos(a):.1f}")
    return ' '.join(pts)


def count_stars(r):
    W, H = 1200, 900
    kinds = [5] * r.randint(26, 38) + [6] * r.randint(18, 26) + [7] * r.randint(6, 10)
    r.shuffle(kinds)
    pos = place(r, len(kinds), W, H, 30, 52)
    parts = [f"<polygon points='{star(x, y, n, r.randint(17, 24), r.uniform(0, 6.3))}' fill='#d4a017' stroke='#7a5a00' stroke-width='1'/>"
             for (x, y), n in zip(pos, kinds)]
    q = "How many stars have exactly five points? (Six- and seven-pointed stars do not count.)" + NUM
    return svgdoc(parts, W, H, '#1c2541'), q, kinds.count(5), 'number', 0, dict(kind='count_eq', items=kinds, value=5)


def count_letters(r):
    cols, rows = 26, 18
    pool = ['8', 'R', 'P', 'E', '3', 'D', 'H', '6']
    grid = [[('B' if r.random() < 0.085 else r.choice(pool)) for _ in range(cols)] for _ in range(rows)]
    text = ''.join(f"<text x='{30 + c * 44}' y='{50 + rr * 48}' font-size='26' font-family='Menlo' font-weight='700' fill='#222'>{grid[rr][c]}</text>"
                   for rr in range(rows) for c in range(cols))
    flat = ''.join(''.join(g) for g in grid)
    q = "How many times does the capital letter B appear in this grid? (The digit 8 and other letters do not count.)" + NUM
    return svgdoc([text], 1180, 900, '#fffef8'), q, flat.count('B'), 'number', 0, dict(kind='count_eq', items=list(flat), value='B')


def count_hollow(r):
    W, H = 1300, 950
    kinds = ['hollow_sq'] * r.randint(24, 34) + ['filled_sq'] * r.randint(18, 24) + ['hollow_circ'] * r.randint(14, 20) + ['hollow_hex'] * r.randint(8, 12)
    r.shuffle(kinds)
    pos = place(r, len(kinds), W, H, 34, 50)
    parts = []
    for (x, y), k in zip(pos, kinds):
        s, rot = r.randint(26, 36), r.randint(0, 89)
        if k.endswith('sq'):
            fill = '#2d6a4f' if k == 'filled_sq' else 'none'
            parts.append(f"<rect x='{x - s / 2}' y='{y - s / 2}' width='{s}' height='{s}' fill='{fill}' stroke='#2d6a4f' stroke-width='3' "
                         f"transform='rotate({rot} {x} {y})'/>")
        elif k == 'hollow_circ':
            parts.append(f"<circle cx='{x}' cy='{y}' r='{s / 2}' fill='none' stroke='#2d6a4f' stroke-width='3'/>")
        else:
            hexp = ' '.join(f"{x + s / 1.8 * math.cos(math.pi / 3 * i + rot / 57.3):.1f},{y + s / 1.8 * math.sin(math.pi / 3 * i + rot / 57.3):.1f}" for i in range(6))
            parts.append(f"<polygon points='{hexp}' fill='none' stroke='#2d6a4f' stroke-width='3'/>")
    q = ("How many hollow (outline-only) squares are there? Squares may be rotated. Filled squares, circles and hexagons do not count." + NUM)
    return svgdoc(parts, W, H), q, kinds.count('hollow_sq'), 'number', 0, dict(kind='count_eq', items=kinds, value='hollow_sq')


# ------------------------------------------------------------------------------------------------------ position ----

def nearest_star(r):
    W, H = 1300, 950
    for _ in range(500):
        pos = place(r, 31, W, H, 40, 70)
        sx, sy = pos[0]
        d = sorted((math.hypot(x - sx, y - sy), i) for i, (x, y) in enumerate(pos[1:], 1))
        if d[1][0] >= 1.3 * d[0][0] and d[0][0] > 75:
            break
    labels = r.sample(range(10, 99), 30)
    parts = [f"<polygon points='{star(sx, sy, 5, 22, 0)}' fill='#e02020'/>"]
    for (x, y), lab in zip(pos[1:], labels):
        parts.append(f"<circle cx='{x}' cy='{y}' r='20' fill='#e8eef7' stroke='#345' stroke-width='2'/>"
                     f"<text x='{x}' y='{y + 6}' text-anchor='middle' font-size='16' font-weight='700' fill='#123'>{lab}</text>")
    q = "Which numbered circle is closest to the red star (centre to centre)? Reply with only the number written in that circle."
    ans = labels[d[0][1] - 1]
    return svgdoc(parts, W, H), q, ans, 'number', 0, dict(kind='nearest', star=[sx, sy], circles=[[x, y, l] for (x, y), l in zip(pos[1:], labels)])


def grid_offset(r):
    n = 9
    nums = [[r.randint(10, 99) for _ in range(n)] for _ in range(n)]
    tr, tc = r.randrange(0, n - 3), r.randrange(3, n)
    dr, dc = 3, -2
    marks = {(tr, tc): 'green_tri'}
    free = [(a, b) for a in range(n) for b in range(n) if (a, b) != (tr, tc) and (a, b) != (tr + dr, tc + dc)]
    for (a, b), m in zip(r.sample(free, 3), ['green_circ', 'blue_tri', 'blue_tri']):
        marks[(a, b)] = m
    cell = 108
    parts = []
    for a in range(n):
        for b in range(n):
            x, y = 40 + b * cell, 40 + a * cell
            parts.append(f"<rect x='{x}' y='{y}' width='{cell}' height='{cell}' fill='{'#f5f5f0' if (a + b) % 2 else '#ffffff'}' stroke='#999'/>")
            m = marks.get((a, b))
            cx, cy = x + cell / 2, y + cell / 2
            if m == 'green_tri':
                parts.append(f"<polygon points='{cx},{cy - 30} {cx - 28},{cy + 22} {cx + 28},{cy + 22}' fill='#2ca02c'/>")
            elif m == 'green_circ':
                parts.append(f"<circle cx='{cx}' cy='{cy}' r='28' fill='#2ca02c'/>")
            elif m == 'blue_tri':
                parts.append(f"<polygon points='{cx},{cy - 30} {cx - 28},{cy + 22} {cx + 28},{cy + 22}' fill='#1f5fd6'/>")
            else:
                parts.append(f"<text x='{cx}' y='{cy + 10}' text-anchor='middle' font-size='28' fill='#222'>{nums[a][b]}</text>")
    q = ("Find the cell holding the green triangle. Move 3 cells down and 2 cells to the left from it. What number is in that cell?" + NUM)
    ans = nums[tr + dr][tc + dc]
    return svgdoc(parts, 1052, 1052), q, ans, 'number', 0, dict(kind='grid_offset', nums=nums, start=[tr, tc], move=[dr, dc])


def inside_rect(r):
    W, H = 1300, 950
    rx, ry, rw, rh = 360, 240, 560, 430
    kinds = ['blue_sq'] * 46 + ['blue_circ'] * 22 + ['teal_sq'] * 20
    r.shuffle(kinds)
    items, placed = [], []
    for k in kinds:
        for _ in range(3000):
            x, y = r.uniform(30, W - 30), r.uniform(30, H - 30)
            inside = rx + 22 < x < rx + rw - 22 and ry + 22 < y < ry + rh - 22
            outside = x < rx - 22 or x > rx + rw + 22 or y < ry - 22 or y > ry + rh + 22
            if (inside or outside) and all((x - a) ** 2 + (y - b) ** 2 > 46 ** 2 for a, b in placed):
                break
        placed.append((x, y)); items.append([round(x, 1), round(y, 1), k, inside])
    parts = [f"<rect x='{rx}' y='{ry}' width='{rw}' height='{rh}' fill='none' stroke='#111' stroke-width='2.5' stroke-dasharray='12,8'/>"]
    for x, y, k, _ in items:
        if k == 'blue_circ':
            parts.append(f"<circle cx='{x}' cy='{y}' r='13' fill='#1f5fd6'/>")
        else:
            parts.append(f"<rect x='{x - 12}' y='{y - 12}' width='24' height='24' fill='{'#1f5fd6' if k == 'blue_sq' else '#178f8f'}'/>")
    q = "How many blue squares are inside the dashed rectangle? (Blue circles and teal squares do not count.)" + NUM
    ans = sum(1 for x, y, k, ins in items if k == 'blue_sq' and ins)
    return svgdoc(parts, W, H), q, ans, 'number', 0, dict(kind='inside', items=items, rect=[rx, ry, rw, rh], shape='blue_sq')


BOX_COLS = {'yellow': '#f2c40f', 'orange': '#f28e2b', 'red': '#d62728', 'blue': '#4e79a7', 'green': '#59a14f', 'gray': '#9a9a9a'}


def shelves(r):
    shelves_ = []
    for s in range(4):
        cols = [r.choice(['orange', 'red', 'blue', 'green', 'gray', 'yellow']) for _ in range(9)]
        shelves_.append(cols)
    shelves_[1] = [c if c != 'yellow' else 'orange' for c in shelves_[1]]
    y_at = r.randrange(1, 8)
    shelves_[1][y_at] = 'yellow'
    for s in (0, 2, 3):
        shelves_[s][r.randrange(9)] = 'yellow'
    labels = r.sample(range(100, 999), 36)
    parts = []
    for s in range(4):
        base = 80 + s * 230 + 190
        parts.append(f"<rect x='40' y='{base}' width='1300' height='16' fill='#7b5a3a'/>")
        x = 60
        for b in range(9):
            w, h = r.randint(95, 120), r.randint(110, 175)
            parts.append(f"<rect x='{x}' y='{base - h}' width='{w}' height='{h}' fill='{BOX_COLS[shelves_[s][b]]}' stroke='#333'/>"
                         f"<rect x='{x + w / 2 - 30}' y='{base - h + 20}' width='60' height='28' fill='#fff'/>"
                         f"<text x='{x + w / 2}' y='{base - h + 41}' text-anchor='middle' font-size='18' font-family='Menlo'>{labels[s * 9 + b]}</text>")
            x += w + r.randint(8, 18)
    q = ("Look at the second shelf from the top. It has exactly one yellow box. What number is on the label of the box immediately "
         "to the right of that yellow box?" + NUM)
    ans = labels[9 + y_at + 1]
    return svgdoc(parts, 1380, 1020, '#efe7da'), q, ans, 'number', 0, dict(kind='shelf', shelves=shelves_, labels=labels, shelf=1)


# ------------------------------------------------------------------------------------------------------------ UI ----

SETTINGS = {'Privacy': ['Share usage data', 'Personalised ads', 'Location history', 'Contact sync', 'Read receipts', 'Show online status',
                        'Search indexing', 'Crash reports', 'Face grouping'],
            'Notifications': ['Email digest', 'Push alerts', 'Sound', 'Badges', 'Weekly summary', 'Mentions only', 'Calendar reminders',
                              'Marketing', 'Do not disturb']}


def ui_toggles(r):
    rows = {sec: [dict(label=l, on=r.random() < 0.5, disabled=r.random() < 0.3) for l in labs] for sec, labs in SETTINGS.items()}
    def tog(t):
        bg = '#2563eb' if t['on'] else '#c7ccd4'
        return (f"<div style='display:flex;align-items:center;justify-content:space-between;padding:10px 0;border-bottom:1px solid #eef;"
                f"opacity:{0.38 if t['disabled'] else 1}'><span style='font-size:14px'>{t['label']}</span>"
                f"<span style='width:40px;height:22px;border-radius:11px;background:{bg};position:relative;display:inline-block'>"
                f"<span style='position:absolute;top:2px;{'right' if t['on'] else 'left'}:2px;width:18px;height:18px;border-radius:9px;background:#fff'></span></span></div>")
    secs = ''.join(f"<div style='flex:1;background:#fff;border-radius:10px;padding:14px 22px'><div style='font-weight:700;font-size:17px;margin-bottom:6px'>{sec}</div>"
                   + ''.join(tog(t) for t in ts) + "</div>" for sec, ts in rows.items())
    body = (f"<div style='padding:30px;font-family:-apple-system,Helvetica'><div style='font-size:24px;font-weight:700;margin-bottom:18px'>Account settings</div>"
            f"<div style='display:flex;gap:30px'>{secs}</div></div>")
    q = ("In the Privacy section only, how many toggles are switched on (blue, knob on the right)? Count faded (disabled) toggles too." + NUM)
    ans = sum(t['on'] for t in rows['Privacy'])
    return page(body, 1100, 640, '#eef1f6'), q, ans, 'number', 0, dict(kind='count_flag', rows=rows['Privacy'], flag='on')


BTNS = ['Import', 'Export', 'Merge', 'Split', 'Archive', 'Restore', 'Duplicate', 'Rename', 'Publish', 'Preview', 'Compare', 'Sync',
        'Lock', 'Unlock', 'Tag', 'Pin', 'Share', 'Print', 'Move', 'Copy', 'Upload', 'Review', 'Approve', 'Reject']


def ui_disabled(r):
    labels = r.sample(BTNS, 24)
    for _ in range(1000):   # 6 per row; at most one disabled in the first row so the answer is not simply in row 1
        state = [r.choice(['primary', 'secondary', 'secondary', 'muted', 'disabled']) for _ in labels]
        if 6 <= state.count('disabled') <= 8 and state[:6].count('disabled') <= 1:
            break
    st = {'primary': 'background:#2563eb;color:#fff;border:1px solid #2563eb',
          'secondary': 'background:#fff;color:#374151;border:1px solid #9ca3af',
          'muted': 'background:#f3f4f6;color:#4b5563;border:1px solid #d1d5db',     # enabled "ghost" button: gray but dark text
          'disabled': 'background:#f3f4f6;color:#b8bcc4;border:1px solid #e5e7eb'}
    btns = ''.join(f"<span style='display:inline-block;width:150px;text-align:center;margin:8px;padding:10px 0;border-radius:7px;font-size:14px;{st[s]}'>{l}</span>"
                   for l, s in zip(labels, state))
    body = (f"<div style='margin:30px;padding:24px;background:#fff;border-radius:12px;width:1020px'><div style='font-size:20px;font-weight:700;margin-bottom:10px'>"
            f"Document actions</div><div style='width:1020px'>{btns}</div></div>")
    q = ("Some buttons are disabled: very light gray text on a light gray background (gray buttons with dark text are enabled). "
         "Reading left to right, top row first, what is the label of the third disabled button? Reply with only the label.")
    ans = [l for l, s in zip(labels, state) if s == 'disabled'][2]
    return page(body, 1124, 480, '#e5e7eb'), q, ans, 'text', None, dict(kind='nth_where', items=labels, flags=[s == 'disabled' for s in state], n=3)


def ui_checked(r):
    rows = []
    for i in range(20):
        rows.append(dict(order=f"#{r.randint(40000, 49999)}", customer=r.choice(['A. Moreau', 'J. Okafor', 'L. Chen', 'P. Silva', 'K. Novak', 'R. Haddad', 'M. Berg']),
                         amount=r.randint(2000, 48000) / 100, checked=False))
    for i in r.sample(range(20), 6):
        rows[i]['checked'] = True
    def cb(c):
        return ("<span style='display:inline-block;width:16px;height:16px;border-radius:3px;background:#2563eb;color:#fff;font-size:12px;"
                "line-height:16px;text-align:center'>✓</span>" if c else
                "<span style='display:inline-block;width:14px;height:14px;border-radius:3px;border:1px solid #9ca3af;background:#fff'></span>")
    trs = ''.join(f"<tr style='background:{'#eef4ff' if i % 2 else '#fff'}'><td style='padding:6px 10px'>{cb(x['checked'])}</td><td>{x['order']}</td>"
                  f"<td>{x['customer']}</td><td style='text-align:right;padding-right:16px'>${x['amount']:,.2f}</td></tr>" for i, x in enumerate(rows))
    body = (f"<div style='margin:30px;background:#fff;border-radius:10px;padding:20px;width:900px;font-size:14px'>"
            f"<div style='font-size:20px;font-weight:700;margin-bottom:10px'>Pending refunds</div>"
            f"<table style='width:100%;border-collapse:collapse'><tr style='text-align:left;color:#555'><th></th><th>Order</th><th>Customer</th>"
            f"<th style='text-align:right;padding-right:16px'>Amount</th></tr>{trs}</table></div>")
    q = "What is the sum of the Amount column for the rows whose checkbox is ticked?" + MONEY
    ans = round(sum(x['amount'] for x in rows if x['checked']), 2)
    return page(body, 1024, 820, '#e5e7eb'), q, ans, 'number', 0.005, dict(kind='sum_where', rows=rows, key='amount', where=('checked', True))


APPS = ['Mail', 'Chat', 'Notes', 'Photos', 'Maps', 'Music', 'Files', 'Tasks', 'News', 'Bank', 'Shop', 'Fit', 'Clock', 'Wallet', 'Books',
        'Docs', 'Drive', 'Meet', 'Radio', 'Video', 'Games', 'Food', 'Ride', 'Home', 'Camera', 'Notes+', 'Pay', 'Weather', 'Stocks', 'Podcasts']


def ui_badges(r):
    apps = []
    for a in APPS:
        kind = r.choice(['red', 'red', 'gray', 'none'])
        apps.append(dict(app=a, badge=kind, n=r.randint(1, 29) if kind != 'none' else 0))
    hues = ['#5b8def', '#f59e0b', '#10b981', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#64748b']
    cells = ''.join(f"<div style='width:150px;height:150px;position:relative;text-align:center'><div style='margin:16px auto 6px;width:84px;height:84px;"
                    f"border-radius:20px;background:{r.choice(hues)}'></div><div style='font-size:13px;color:#fff'>{a['app']}</div>"
                    + (f"<div style='position:absolute;left:95px;top:6px;min-width:30px;height:30px;border-radius:15px;padding:0 4px;box-sizing:border-box;border:2px solid #fff;"
                       f"background:{'#ff3b30' if a['badge'] == 'red' else '#8e8e93'};color:#fff;font-size:14px;font-weight:700;line-height:26px'>{a['n']}</div>"
                       if a['badge'] != 'none' else '') + "</div>" for a in apps)
    body = f"<div style='padding:30px 40px;display:flex;flex-wrap:wrap;width:950px'>{cells}</div>"
    q = "Add up the numbers on all red badges. Ignore the gray badges." + NUM
    ans = sum(a['n'] for a in apps if a['badge'] == 'red')
    return page(body, 1030, 840, 'linear-gradient(160deg,#1e3a5f,#4a2a5f)'), q, ans, 'number', 0, dict(kind='sum_where', rows=apps, key='n', where=('badge', 'red'))


# --------------------------------------------------------------------------------------------------------- table ----

FIRST = ['Ana', 'Ben', 'Chloé', 'Dev', 'Elif', 'Femi', 'Gus', 'Hana', 'Ivo', 'Jon', 'Kira', 'Liam', 'Mina', 'Nils', 'Omar', 'Pia', 'Quinn',
         'Rosa', 'Sami', 'Tara', 'Uma', 'Vic', 'Wen', 'Xavi', 'Yara', 'Zoe']


def table_hours(r):
    rows = [dict(name=f"{n} {r.choice('BCDFGHKLMNPRSTVW')}.", dept=r.choice(['Sales', 'Support', 'Ops', 'Finance', 'Sales Ops']),
                 hours=r.randint(18, 48), rate=r.randint(18, 45)) for n in FIRST]
    trs = ''.join(f"<tr style='background:{'#f4f6f8' if i % 2 else '#fff'}'><td>{x['name']}</td><td>{x['dept']}</td><td style='text-align:right'>{x['hours']}</td>"
                  f"<td style='text-align:right'>{x['rate']}.00</td></tr>" for i, x in enumerate(rows))
    body = (f"<table style='margin:24px;font-size:13px;border-collapse:collapse;width:700px' cellpadding='5'><tr style='background:#334;color:#fff'>"
            f"<th align='left'>Employee</th><th align='left'>Department</th><th align='right'>Hours</th><th align='right'>Rate</th></tr>{trs}</table>")
    q = "What is the total of the Hours column for employees whose department is exactly 'Sales' (not 'Sales Ops')?" + NUM
    ans = sum(x['hours'] for x in rows if x['dept'] == 'Sales')
    return page(body, 1024, 1024), q, ans, 'number', 0, dict(kind='sum_where', rows=rows, key='hours', where=('dept', 'Sales'))


STATIONS = ['Ashford', 'Birch Lane', 'Coldwater', 'Dunmore', 'Elm Park', 'Oakridge', 'Fairview', 'Glen Cross', 'Pine Hill', 'Westgate']


def table_timetable(r):
    trains = [str(n) for n in sorted(r.sample(range(1401, 1460), 9))]
    times = {}
    for k, t in enumerate(trains):
        cur = 6 * 60 + 15 + k * 37 + r.randint(0, 9)
        col = []
        for s in STATIONS:
            stop = r.random() > 0.25 or s in ('Ashford', 'Westgate')
            col.append(cur if stop else None)
            cur += r.randint(4, 13)
        times[t] = col
    target = r.choice(trains)
    times[target][STATIONS.index('Oakridge')] = times[target][STATIONS.index('Oakridge')] or (times[target][4] or times[target][0] + 30) + 6
    oi, pi = STATIONS.index('Oakridge'), STATIONS.index('Pine Hill')
    if times[target][pi] is None:
        times[target][pi] = times[target][oi] + 23
    if times[target][pi] <= times[target][oi]:
        times[target][pi] = times[target][oi] + 19
    for t in trains:   # keep each column increasing after the fixes above
        last = -1
        for i, v in enumerate(times[t]):
            if v is not None:
                if v <= last:
                    times[t][i] = v = last + 4
                last = v
    fmt = lambda v: '—' if v is None else f"{v // 60:02d}:{v % 60:02d}"
    head = ''.join(f"<th style='padding:6px 8px'>{t}</th>" for t in trains)
    trs = ''.join(f"<tr style='background:{'#f6f3ea' if i % 2 else '#fff'}'><td style='padding:6px 10px;font-weight:600'>{s}</td>"
                  + ''.join(f"<td style='text-align:center;font-family:Menlo;font-size:13px'>{fmt(times[t][i])}</td>" for t in trains) + "</tr>"
                  for i, s in enumerate(STATIONS))
    body = (f"<div style='margin:30px'><div style='font-size:22px;font-weight:700;margin-bottom:10px'>Weekday departures · Line 4</div>"
            f"<table style='border-collapse:collapse;font-size:14px' border='1' bordercolor='#ccc'><tr style='background:#7a1f1f;color:#fff'>"
            f"<th>Station \\ Train</th>{head}</tr>{trs}</table><div style='font-size:12px;margin-top:8px'>— = does not stop</div></div>")
    q = f"In this timetable, how many minutes does train {target} take from Oakridge to Pine Hill?" + NUM
    ans = times[target][pi] - times[target][oi]
    return page(body, 1100, 640), q, ans, 'number', 0, dict(kind='timetable', times=times, stations=STATIONS, train=target, a='Oakridge', b='Pine Hill')


PRODUCTS = ['Hex bolts M6', 'Hex bolts M8', 'Wing nuts M6', 'Washers 8mm', 'Washers 10mm', 'Wood screws 4x40', 'Wood screws 4x50',
            'Wall plugs 6mm', 'Wall plugs 8mm', 'Cable ties 200', 'Cable ties 300', 'Hinges 75mm', 'Hinges 100mm', 'Brackets L50',
            'Brackets L75', 'Sandpaper P120', 'Sandpaper P240', 'Wood glue 250ml', 'Masking tape 25', 'Duct tape 50', 'Drill bit 6mm',
            'Drill bit 8mm', 'Hook screws', 'Chain 5mm']


def table_reorder(r):
    rows = []
    for p in PRODUCTS:
        lvl = r.randrange(20, 120, 5)
        stock = r.randint(0, 200)
        while abs(stock - lvl) < 3:
            stock = r.randint(0, 200)
        rows.append(dict(sku=f"HW-{r.randint(1000, 9999)}", product=p, stock=stock, reorder=lvl))
    trs = ''.join(f"<tr style='background:{'#f2f7f2' if i % 2 else '#fff'}'><td>{x['sku']}</td><td>{x['product']}</td>"
                  f"<td style='text-align:right'>{x['stock']}</td><td style='text-align:right'>{x['reorder']}</td></tr>" for i, x in enumerate(rows))
    body = (f"<table style='margin:24px;font-size:13px;border-collapse:collapse;width:760px' cellpadding='5'><tr style='background:#264d26;color:#fff'>"
            f"<th align='left'>SKU</th><th align='left'>Product</th><th align='right'>In stock</th><th align='right'>Reorder level</th></tr>{trs}</table>")
    q = "How many products have an 'In stock' value lower than their 'Reorder level'?" + NUM
    ans = sum(x['stock'] < x['reorder'] for x in rows)
    return page(body, 1024, 1000), q, ans, 'number', 0, dict(kind='count_lt', rows=rows, a='stock', b='reorder')


# ---------------------------------------------------------------------------------------------------- multi-step ----

def ms_chart_table(r):
    prods = ['Kettle', 'Toaster', 'Blender', 'Juicer', 'Mixer', 'Grill', 'Fryer', 'Steamer']
    units = r.sample(range(20, 141, 10), len(prods))
    top = sorted(units)
    while top[-1] - top[-2] < 20:
        units = r.sample(range(20, 141, 10), len(prods)); top = sorted(units)
    prices = [r.randint(1999, 15999) / 100 for _ in prods]
    W, L, R, T, B = 820, 60, 800, 70, 700
    parts = ["<text x='420' y='34' text-anchor='middle' font-size='18' font-weight='700'>Units sold in October</text>"]
    grid_axis(parts, L, R, T, B, 150, 10, 10, 11)
    bw = (R - L) / len(prods)
    for i, (p, u) in enumerate(zip(prods, units)):
        h = (B - T) * u / 150
        parts.append(f"<rect x='{L + i * bw + 12:.1f}' y='{B - h:.1f}' width='{bw - 24:.1f}' height='{h:.1f}' fill='#e15759'/>"
                     f"<text x='{L + i * bw + bw / 2:.1f}' y='{B + 20}' text-anchor='middle' font-size='13'>{p}</text>")
    order = list(range(len(prods))); r.shuffle(order)
    trs = ''.join(f"<tr><td style='padding:6px 12px'>{prods[i]}</td><td style='text-align:right;padding:6px 12px'>${prices[i]:.2f}</td></tr>" for i in order)
    body = (f"<div style='display:flex;align-items:flex-start'><svg width='{W}' height='760' font-family='Helvetica'>{''.join(parts)}</svg>"
            f"<table style='margin-top:80px;font-size:15px;border-collapse:collapse' border='1' bordercolor='#bbb'><tr style='background:#eee'>"
            f"<th>Product</th><th>Unit price</th></tr>{trs}</table></div>")
    i = units.index(max(units))
    q = ("Find the product with the most units sold in the chart, then multiply its units sold by its unit price from the table. "
         "What is the revenue?" + MONEY)
    return page(body, 1180, 760), q, round(units[i] * prices[i], 2), 'number', 0.01, dict(kind='argmax_mul', names=prods, units=units, prices=prices)


PRICE_LIST = ['Espresso beans 1kg', 'Filter papers', 'Oat milk 1L', 'Paper cups 12oz', 'Paper cups 8oz', 'Lids 12oz', 'Lids 8oz', 'Stirrers',
              'Sugar sticks', 'Napkins', 'Cocoa powder', 'Chai syrup', 'Vanilla syrup', 'Caramel syrup', 'Decaf beans 1kg', 'Cleaning tabs']


def ms_order_note(r):
    prices = {p: r.randint(150, 3990) / 100 for p in PRICE_LIST}
    order = [(p, r.randint(2, 9)) for p in r.sample(['Paper cups 12oz', 'Lids 12oz', 'Oat milk 1L', 'Vanilla syrup', 'Decaf beans 1kg',
                                                     'Napkins', 'Caramel syrup'], 4)]
    trs = ''.join(f"<tr style='background:{'#f6f6f6' if i % 2 else '#fff'}'><td style='padding:5px 10px'>{p}</td>"
                  f"<td style='text-align:right;padding:5px 10px'>{prices[p]:.2f}</td></tr>" for i, p in enumerate(PRICE_LIST))
    note = ''.join(f"<div>{q} × {p}</div>" for p, q in order)
    body = (f"<div style='display:flex;gap:40px;padding:40px'><table style='font-size:14px;border-collapse:collapse' border='1' bordercolor='#ccc'>"
            f"<tr style='background:#333;color:#fff'><th>Supplier price list</th><th>Unit price</th></tr>{trs}</table>"
            f"<div style='margin-top:120px;width:420px;padding:26px;background:#fff59d;font-family:\"Bradley Hand\";font-size:28px;line-height:1.6;"
            f"transform:rotate(3deg);box-shadow:2px 4px 10px #999'><div style='font-size:30px'>Order for Friday:</div>{note}</div></div>")
    q = ("Using the unit prices in the price list, what is the total cost of the order written on the yellow note "
         "(quantity × unit price, summed)?" + MONEY)
    ans = round(sum(prices[p] * q for p, q in order), 2)
    return page(body, 1100, 860, '#e8e8e8'), q, ans, 'number', 0.01, dict(kind='order_total', prices=prices, order=order)


V3 = [
    ('ocr-receipt', receipt_long), ('ocr', shipping_labels), ('ocr', low_contrast), ('ocr-japanese', jp_menu), ('ocr-japanese', jp_vertical),
    ('ocr-french', fr_menu), ('ocr-french', fr_notice), ('handwriting', hand_expenses),
    ('chart', chart_grouped), ('chart', chart_lines), ('chart', chart_stacked), ('chart', chart_scatter), ('chart', chart_hbar),
    ('count', count_red), ('count', count_stars), ('count', count_letters), ('count', count_hollow),
    ('position', nearest_star), ('position', grid_offset), ('position', inside_rect), ('position', shelves),
    ('ui', ui_toggles), ('ui', ui_disabled), ('ui', ui_checked), ('ui', ui_badges),
    ('table', table_hours), ('table', table_timetable), ('table', table_reorder),
    ('multi-step', ms_chart_table), ('multi-step', ms_order_note),
]


def build(name='vision-v3', seed=3):
    r = random.Random(seed)
    folder = ROOT / name
    (folder / 'images').mkdir(parents=True, exist_ok=True)
    for old in (folder / 'images').glob('*.png'):
        old.unlink()
    tasks, docs, data, seen = [], [], {}, {}
    for cat, fn in V3:
        seen[cat] = seen.get(cat, 0) + 1
        tid = f'{cat}-{seen[cat]}'
        doc, q, ans, score, tol, d = fn(r)
        docs.append((tid, doc))
        tasks.append(dict(id=tid, category=cat, question=q, answer=ans, score=score, tol=tol, image=f'images/{tid}.png'))
        data[tid] = d
    for tid, doc in docs:   # one Chromium run per image, retried: headless screenshots occasionally come back empty
        for attempt in range(3):
            try:
                render([(tid, doc)], folder / 'images')
                break
            except SystemExit:
                if attempt == 2:
                    raise
    (folder / 'tasks.json').write_text(json.dumps({'note': f'{name}: generated by benches/vision/build_v3.py (seed {seed}).', 'tasks': tasks},
                                                  ensure_ascii=False, indent=1))
    (folder / 'data.json').write_text(json.dumps(data, ensure_ascii=False, indent=1))
    print(f'wrote {folder} ({len(tasks)} tasks)')


if __name__ == '__main__':
    build()
