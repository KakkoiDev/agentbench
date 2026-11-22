"""Hard family jobs: the same made-up family's files, with jobs that take several careful steps.

Built (2026-11-18) because the strong models reached 12/12 on the family jobs in family.py, and two
perfect scores cannot say which model is better. These jobs are meant to be hard enough that no
model we have gets everything right, so scores spread out (70% vs 80%) and each failure says where
a model falls short.

Each job is graded in steps worth one point each. `check(folder, reply)` returns a list of
(step, passed) so a page can say what the model managed and what it missed. Expected answers are
computed from the seed data below, never typed by hand. `reference(folder)` does each job the way a
careful person would; the tests in test_family_hard.py check that it scores every point.
"""
import csv
import hashlib
import io
import re
from datetime import date
from pathlib import Path

TODAY = date(2026, 11, 18)

# ---------------------------------------------------------------- seed data

RECEIPTS = {  # file -> (shop, date, amount in euros, how the receipt writes it)
    'receipts/2026-10-02 boulangerie.txt': ('Boulangerie Martin', '2026-10-02', 12.50, 'BOULANGERIE MARTIN\n02/10/2026\nPain, croissants\nTotal : 12,50 €\n'),
    'receipts/oct03-pharmacy.txt': ('City Pharmacy', '2026-10-03', 23.80, 'City Pharmacy\n3 Oct 2026\nTOTAL EUR 23.80\nThank you!\n'),
    'receipts/supermarket 1010.txt': ('FreshMart', '2026-10-10', 87.35, 'FreshMart supermarket\n2026-10-10 18:42\n12 items\nAmount due: 87.35 EUR\n'),
    'receipts/IMG_2210.txt': ('Garage Dupont', '2026-10-14', 145.00, 'Garage Dupont\nVidange + filtre\n14.10.2026\nMontant TTC : 145,00 €\n'),
    'receipts/bookshop.txt': ('Page & Co', '2026-10-17', 31.90, 'Page & Co Books\nOct 17, 2026\n2 books\nTotal 31.90 EUR\n'),
    'receipts/2026-10-21.txt': ('FreshMart', '2026-10-21', 64.10, 'FreshMart supermarket\n2026-10-21 12:05\nAmount due: 64.10 EUR\n'),
    'receipts/cinema.txt': ('Cine Lumiere', '2026-10-24', 22.00, 'CINE LUMIERE\n24/10/2026 20:30\n2 tickets\nTotal : 22,00 €\n'),
    'receipts/hardware 28 oct.txt': ('BricoPlus', '2026-10-28', 18.75, 'BricoPlus\n28 October 2026\nScrews, glue\nTOTAL 18.75 EUR\n'),
}
STATEMENT = [  # (date, description, amount): the bank's October card charges
    ('2026-10-02', 'CB BOULANGERIE MARTIN', -12.50),
    ('2026-10-03', 'CB CITY PHARMACY', -23.80),
    ('2026-10-10', 'CB FRESHMART 0412', -87.35),
    ('2026-10-14', 'CB GARAGE DUPONT', -145.00),
    ('2026-10-15', 'CB NETFLIX', -13.49),
    ('2026-10-21', 'CB FRESHMART 0412', -64.10),
    ('2026-10-24', 'CB CINE LUMIERE', -22.00),
    ('2026-10-26', 'CB PETROL STATION A6', -58.20),
]
# receipts with no charge: Page & Co (31.90) and BricoPlus (18.75); charges with no receipt: Netflix, petrol
NO_CHARGE = ['Page & Co', 'BricoPlus']
NO_RECEIPT = ['NETFLIX', 'PETROL']

LEASE = ("# Lease agreement: flat 3B, 14 rue des Lilas\n\n"
         + "".join(f"## Clause {i}\n\n" + {
             4: "Rent: 1,120 euros per month, due on the 5th of each month.\n\n",
             9: "Pets: allowed with the landlord's written consent.\n\n",
             14: "Notice period: the tenant gives three months' notice in writing.\n\n",
             17: "Rent review: once a year, on 1 January, at most by the reference index.\n\n",
         }.get(i, f"The tenant and the landlord agree to the standard terms of section {i} of the housing code, "
                   "including upkeep, insurance, access for repairs with 24 hours' notice, and the inventory.\n\n")
           for i in range(1, 25)))
AMENDMENT = ("# Amendment to the lease of flat 3B, signed 2 September 2026\n\n"
             "1. Clause 14 is replaced: the tenant gives two months' notice in writing.\n"
             "2. From 1 January 2027 the rent is 1,180 euros per month.\n"
             "3. All other clauses stay as they are.\n")

TRANSACTIONS = [  # date, description, category, amount (negative = spent, positive = refund)
    ('2026-10-01', 'Rent October', 'housing', -1120.00), ('2026-10-02', 'FreshMart', 'food', -54.20),
    ('2026-10-04', 'Electricity', 'utilities', -88.40), ('2026-10-05', 'Bus pass', 'transport', -75.00),
    ('2026-10-06', 'Market', 'food', -23.15), ('2026-10-08', 'Internet', 'utilities', -45.00),
    ('2026-10-09', 'Cinema', 'leisure', -22.00), ('2026-10-11', 'FreshMart', 'food', -96.70),
    ('2026-10-12', 'Petrol', 'transport', -61.30), ('2026-10-14', 'Concert', 'leisure', -48.00),
    ('2026-10-15', 'FreshMart refund', 'food', 12.40), ('2026-10-17', 'Bakery', 'food', -9.80),
    ('2026-10-19', 'Water', 'utilities', -31.25), ('2026-10-20', 'Taxi', 'transport', -18.60),
    ('2026-10-22', 'FreshMart', 'food', -71.05), ('2026-10-24', 'Books', 'leisure', -31.90),
    ('2026-10-25', 'Concert refund', 'leisure', 48.00), ('2026-10-27', 'Home insurance', 'housing', -24.50),
    ('2026-10-29', 'Market', 'food', -17.45), ('2026-10-30', 'Petrol', 'transport', -55.10),
]


def category_totals():
    tot = {}
    for _, _, c, a in TRANSACTIONS:
        tot[c] = round(tot.get(c, 0) - a, 2)  # spending as positive numbers, refunds lower it
    return dict(sorted(tot.items(), key=lambda kv: -kv[1]))


AGENDAS = {
    'agendas/aiko.md': "# Aiko - week of 23 November\n\n- Mon 23 Nov 09:00-10:00 yoga\n- Tue 24 Nov 15:00-16:30 dentist (car)\n"
                       "- Thu 26 Nov 14:00-15:00 school meeting (car)\n- Sat 28 Nov 10:00-12:00 market\n",
    'agendas/ken.md': "# ケンの予定（11月23日の週）\n\n- 11月24日(火) 16:00〜17:30 取引先訪問（車）\n- 11月25日(水) 19:00〜21:00 飲み会\n"
                      "- 11月28日(土) 09:30〜11:00 ゴルフ練習（車）\n",
    'agendas/marc.md': "# Agenda de Marc - semaine du 23 novembre\n\n- jeudi 26 nov. 15h00-16h00 kiné (voiture)\n"
                       "- vendredi 27 nov. 18h-19h piscine\n- samedi 28 nov. 10h30-12h00 déchetterie (voiture)\n",
}
# car clashes: Tue 24 Aiko 15:00-16:30 vs Ken 16:00-17:30; Sat 28 Ken 09:30-11:00 vs Marc 10:30-12:00.
# Thu 26 Aiko 14:00-15:00 then Marc 15:00-16:00 only touch: not a clash.

PHOTOS = {  # name -> content id; same id = same bytes
    'photos/2026/IMG_1001.jpg': 'a', 'photos/2026/IMG_1002.jpg': 'b', 'photos/2026/IMG_1003.jpg': 'c',
    'photos/2026/beach day.jpg': 'a', 'photos/2026/IMG_1004.jpg': 'd', 'photos/2026/IMG_1002 (1).jpg': 'b',
    'photos/2026/copy of IMG_1004.jpg': 'd', 'photos/2026/IMG_1005.jpg': 'e',
}


def photo_bytes(cid):
    return f'(photo {cid}) '.encode() * (40 + ord(cid))


JOURNAL = {  # current name -> (date it is about, text)
    'notes/journal/Nov 3 2026.md': ('2026-11-03', 'Trip to Nara: deer, Todai-ji, mochi at the station.\n'),
    'notes/journal/2026_11_07 market.md': ('2026-11-07', 'Saturday market: chestnuts and persimmons.\n'),
    'notes/journal/journal 12-11-2026.md': ('2026-11-12', '12 November: first cold morning, heating on.\n'),
    'notes/journal/Sunday 15th November.md': ('2026-11-15', 'Sunday 15 November 2026: long walk by the river.\n'),
}
INDEX = ("# Notes\n\nJournal:\n"
         "- [Nara trip](journal/Nov%203%202026.md)\n- [Market](<journal/2026_11_07 market.md>)\n"
         "- [Cold morning](journal/journal%2012-11-2026.md)\n- [River walk](<journal/Sunday 15th November.md>)\n\n"
         "Other: [shopping](../shopping.md)\n")

KYOTO = ("京都旅行の計画\n\n"
         "1日目（12月5日）\n- 8:00 伏見稲荷大社（朝は人が少ない）\n- 12:00 錦市場で昼ごはん\n- 15:00 清水寺\n\n"
         "2日目（12月6日）\n- 9:00 金閣寺\n- 11:30 龍安寺\n- 18:00 祇園を散歩\n\n"
         "3日目（12月7日）\n- 8:30 嵐山の竹林\n- 10:30 天龍寺\n- 14:00 京都駅から新幹線\n")

BILLS = [  # name, due (as written), due date, amount, paid
    ('Electricity', '2026-11-02', date(2026, 11, 2), 88.40, 'yes'),
    ('Water', '11/20/2026', date(2026, 11, 20), 31.25, 'no'),
    ('Phone', '5 Nov 2026', date(2026, 11, 5), 29.99, 'no'),
    ('Insurance', '2026年11月30日', date(2026, 11, 30), 24.50, 'no'),
    ('Internet', '2026-11-08', date(2026, 11, 8), 45.00, 'no'),
    ('School trip', '14/11/2026', date(2026, 11, 14), 60.00, 'no'),
    ('Gym', 'Nov 1, 2026', date(2026, 11, 1), 35.00, 'yes'),
]
OVERDUE = sorted([b for b in BILLS if b[4] == 'no' and b[2] < TODAY], key=lambda b: b[2])  # Phone, Internet, School trip

PHONES = "name,phone\nKen Sato,090-1234-5678\nAiko Sato,080 2222 3333\nMarc Dubois,+33 6 12 34 56 78\nYuki Tanaka,070-9999-0000\n"
EMAILS = "name,email\nken sato,ken@example.jp\nMARC DUBOIS,marc.dubois@example.fr\nLea Martin,lea@example.fr\nAiko Sato,aiko@example.jp\n"
PEOPLE = {'ken sato': ('09012345678', 'ken@example.jp'), 'aiko sato': ('08022223333', 'aiko@example.jp'),
          'marc dubois': ('33612345678', 'marc.dubois@example.fr'), 'yuki tanaka': ('07099990000', None),
          'lea martin': (None, 'lea@example.fr')}

MEETINGS = {
    'meetings/club/2026-09-12.md': "Club meeting 12 September\n- Venue for the end-of-year party: the community hall.\n- Budget: 400 euros.\n",
    'meetings/club/2026-10-03.md': "Club meeting 3 October\n- Party date: Saturday 12 December.\n- Ken asks the bakery for a quote.\n",
    'meetings/club/2026-10-17.md': "Club meeting 17 October\n- The community hall is booked that day. New venue: the Riverside café.\n",
    'meetings/club/2026-10-31.md': "Club meeting 31 October\n- Budget raised to 550 euros (the café needs a deposit).\n",
    'meetings/club/2026-11-14.md': "Club meeting 14 November\n- Party moved to Saturday 19 December: the café was double-booked on the 12th.\n- Venue unchanged.\n",
}


def seed():
    s = {}
    for rel, (_, _, _, text) in RECEIPTS.items():
        s[rel] = text
    s['bank/october.csv'] = 'date,description,amount\n' + ''.join(f'{d},{t},{a:.2f}\n' for d, t, a in STATEMENT)
    s['documents/lease.md'] = LEASE
    s['documents/lease-amendment-2026.md'] = AMENDMENT
    s['budget/transactions-october.csv'] = 'date,description,category,amount\n' + ''.join(f'{d},{t},{c},{a:.2f}\n' for d, t, c, a in TRANSACTIONS)
    s.update(AGENDAS)
    for rel, cid in PHOTOS.items():
        s[rel] = photo_bytes(cid)
    for rel, (_, text) in JOURNAL.items():
        s[rel] = text
    s['notes/index.md'] = INDEX
    s['shopping.md'] = '# Shopping\n\n- milk\n'
    s['旅行/京都計画.md'] = KYOTO
    s['bills/bills.csv'] = 'bill,due,amount,paid\n' + ''.join(f'{n},{d},{a:.2f},{p}\n' for n, d, _, a, p in BILLS)
    s['contacts/phone.csv'] = PHONES
    s['contacts/email.csv'] = EMAILS
    s.update(MEETINGS)
    return s


SEED = seed()


def write_seed(folder):
    for rel, value in SEED.items():
        p = Path(folder, rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        (p.write_bytes if isinstance(value, bytes) else p.write_text)(value)


# ---------------------------------------------------------------- helpers

def _read(folder, rel):
    p = Path(folder, rel)
    return p.read_text(errors='replace') if p.is_file() else None


def _files(folder, under=''):
    base = Path(folder, under)
    return [p for p in base.rglob('*') if p.is_file()] if base.is_dir() else []


def _num(text, value):
    """The amount appears, written 1,180 / 1180 / 1 180 / 1180.00 / 1.180,00 / 87,35."""
    whole, cents = f'{value:.2f}'.split('.')
    w = re.sub(r'(\d)(?=(\d{3})+$)', r'\1[,.\\s ]?', whole)
    c = cents if not cents.endswith('0') else cents[0] + '0?'  # 53.9 is 53.90
    rx = rf'(?<![\d.,]){w}(?:[.,]00)?(?![\d.,]?\d)' if cents == '00' else rf'(?<![\d.,]){w}[.,]{c}(?!\d)'
    return bool(re.search(rx, text or ''))


def _digits(s):
    return re.sub(r'\D', '', s or '')


def _csv_rows(text):
    try:
        return [r for r in csv.reader(io.StringIO(text or '')) if any(c.strip() for c in r)]
    except csv.Error:
        return []


# ---------------------------------------------------------------- checks: each returns [(step, passed)]

def check_reconcile(folder, reply):
    s = _read(folder, 'bank/reconcile.md')
    t = (s or '') + '\n' + (reply or '')
    return [('wrote bank/reconcile.md', s is not None),
            ('found the receipt from Page & Co with no charge', bool(re.search(r'page\s*&?\s*(and\s*)?co|bookshop', s or '', re.I))),
            ('found the receipt from BricoPlus with no charge', bool(re.search(r'brico', s or '', re.I))),
            ('found the Netflix charge with no receipt', bool(re.search(r'netflix', s or '', re.I))),
            ('found the petrol charge with no receipt', bool(re.search(r'petrol', s or '', re.I))),
            ('flagged nothing that matches', not re.search(r'boulangerie|pharmacy|garage|cine', _unmatched_part(s), re.I))]


def _unmatched_part(s):
    """The report minus any section that lists matched items, so "matched: Garage Dupont" is not a false flag."""
    if not s:
        return ''
    keep, skip = [], False
    for line in s.splitlines():
        if re.match(r'\s*#+\s', line) or re.match(r'\s*\*\*.*\*\*\s*$', line):
            skip = bool(re.search(r'\bmatch(ed|es|ing)?\b|both|ok\b|fine|reconciled', line, re.I)) and not re.search(r'un|no |not|missing|without', line, re.I)
            continue
        if not skip:
            keep.append(line)
    return '\n'.join(keep)


def check_lease(folder, reply):
    r = reply or ''
    return [('notice period from the amendment: two months', bool(re.search(r'\b(2|two)\s*months?', r, re.I))),
            ('did not give the old three months as current', not re.search(r'\b(3|three)\s*months?', r, re.I)
             or bool(re.search(r'(was|replac|amend|old|previous|instead|no longer)', r, re.I))),
            ('rent in February 2027: 1,180 euros', _num(r, 1180)),
            ('said the answer comes from the amendment', bool(re.search(r'amend', r, re.I)))]


def check_categories(folder, reply):
    s = _read(folder, 'budget/october-by-category.csv')
    rows = _csv_rows(s)
    want = category_totals()
    got = {}
    for r in rows:
        name = next((c.strip().lower() for c in r if c.strip().lower() in want), None)
        nums = [c for c in r if re.fullmatch(r'\s*-?[\d.,\s€]+\s*', c) and re.search(r'\d', c)]
        if name and nums:
            got[name] = nums[0]
    right = [c for c, v in want.items() if c in got and _num(got[c].replace('-', ''), v)]
    order = [c for c in got if c in want]
    grand = round(sum(want.values()), 2)
    return [('wrote budget/october-by-category.csv', s is not None)]\
        + [(f'{c} total right ({v:.2f})', c in right) for c, v in want.items()]\
        + [('sorted from biggest to smallest', len(order) == len(want) and order == list(want)),
           (f'gave the grand total ({grand:.2f}) in the reply', _num(reply, grand))]


def check_car(folder, reply):
    s = _read(folder, 'agendas/car-clashes.md')
    t = s or ''
    lines = [l for l in t.splitlines() if l.strip()]
    def clash(day, a, b):
        return any(re.search(day, l, re.I) and re.search(a, l, re.I) and re.search(b, l, re.I) for l in lines) or \
            bool(re.search(rf'(?s){day}.{{0,300}}{a}.{{0,300}}{b}|{day}.{{0,300}}{b}.{{0,300}}{a}', t, re.I))
    return [('wrote agendas/car-clashes.md', s is not None),
            ('Tuesday 24: Aiko (dentist) and Ken (client visit)', clash(r'\b24\b|tue', r'aiko|dentist', r'ken|client|取引先')),
            ('Saturday 28: Ken (golf) and Marc (recycling centre)', clash(r'\b28\b|sat', r'ken|golf|ゴルフ', r'marc|déchetterie|recycl|dump')),
            ('did not call Thursday 26 a clash (15:00 end, 15:00 start)', s is not None and not _thursday_flagged(t))]


NOT_CLASH = re.compile(r"no clash|not a clash|no overlap|not overlap|n.t overlap|does not overlap|don't overlap|back[- ]to[- ]back|near[- ]miss|"
                       r"near[- ]clash|tight|ends? exactly|just as|touch|no conflict|not a conflict|fine|\bok\b|pas de conflit|重ならない", re.I)


def _thursday_flagged(t):
    """Thursday 26 is named as a clash: a mention with nothing near it, or in its heading, saying it does not overlap
    (fixed 2026-11-22: "tight but not overlapping" and a "Near misses (no overlap)" heading are right)."""
    for mm in re.finditer(r'(?i)\b26\b(?!\d)|thu', t):
        if re.match(r'(?i)\d', t[mm.end():mm.end() + 1] or 'x'):
            continue
        heading = next((l for l in reversed(t[:mm.start()].splitlines()) if l.lstrip().startswith('#')), '')
        near = t[max(0, mm.start() - 250):mm.end() + 250]
        if not (NOT_CLASH.search(near) or NOT_CLASH.search(heading)):
            return True
    return False


def check_duplicates(folder, reply):
    keep = _files(folder, 'photos/2026')
    dup = _files(folder, 'photos/duplicates')
    h = lambda p: hashlib.sha1(p.read_bytes()).hexdigest()
    want = {hashlib.sha1(photo_bytes(c)).hexdigest() for c in set(PHOTOS.values())}
    kept = [h(p) for p in keep]
    all_now = kept + [h(p) for p in dup] + [h(p) for p in _files(folder) if p.suffix.lower() == '.jpg' and 'photos/2026' not in str(p) and 'photos/duplicates' not in str(p)]
    return [('one copy of each photo left in photos/2026', sorted(kept) == sorted(want)),
            ('the 3 extra copies are in photos/duplicates', len(dup) == 3 and all(x in want for x in map(h, dup))),
            ('no photo lost', len(all_now) == len(PHOTOS) and set(all_now) == want)]


def check_journal(folder, reply):
    names = sorted(p.name for p in _files(folder, 'notes/journal'))
    want = sorted(f'{d}.md' for d, _ in JOURNAL.values())
    idx = _read(folder, 'notes/index.md') or ''
    links = re.findall(r'\]\(<?([^)>]+)>?\)', idx)
    from urllib.parse import unquote
    jl = [l for l in links if 'journal' in l]
    resolves = [Path(folder, 'notes', unquote(l)).is_file() for l in jl]
    texts = {(p.read_text(errors='replace')) for p in _files(folder, 'notes/journal')}
    return [('renamed all 4 to YYYY-MM-DD.md', names == want),
            ('kept every journal text', texts == {t for _, t in JOURNAL.values()}),
            ('all 4 journal links in notes/index.md point to the renamed files', len(jl) == 4 and all(resolves)
             and all(re.search(r'\d{4}-\d{2}-\d{2}\.md$', unquote(l)) for l in jl)),
            ('kept the other link and the link titles', '[shopping](../shopping.md)' in idx and all(x in idx for x in ('Nara trip', 'Market', 'Cold morning', 'River walk')))]


def check_kyoto(folder, reply):
    s = _read(folder, 'voyage/kyoto.md') or ''
    fr = len(re.findall(r'\b(le|la|les|du|des|au|et|jour|matin|déjeuner|promenade|visite|départ|dîner)\b', s, re.I))
    return [('wrote voyage/kyoto.md', bool(s)),
            ('three days, in order', all(re.search(rf'(jour\s*{i}|{d}\s*d[ée]c)', s, re.I) for i, d in ((1, 5), (2, 6), (3, 7)))),
            ('all 9 places', all(re.search(rx, s, re.I) for rx in (r'inari', r'nishiki', r'kiyomizu', r'kinkaku', r'ry[oō]an', r'gion',
                                                               r'arashiyama', r'tenry[uū]', r'shinkansen|gare|station'))),
            ('times kept (8h, 12h, 15h ...)', len(re.findall(r'\b0?(8|12|15|9|11|18|10|14)\s*[h:]\s*(00|30)?', s)) >= 8),
            ('written in French', fr >= 6)]


def check_overdue(folder, reply):
    s = _read(folder, 'bills/overdue.md') or ''
    names = [b[0] for b in OVERDUE]
    pos = [s.lower().find(n.lower()) for n in names]
    total = round(sum(b[3] for b in OVERDUE), 2)
    return [('wrote bills/overdue.md', bool(s)),
            ('listed Phone, Internet and School trip', all(p >= 0 for p in pos)),
            ('left out bills not yet due or already paid', not any(re.search(r'water|insurance|electricity|gym', l, re.I)
                                                                   and not re.search(r'not (yet )?(due|overdue)|paid|exclud|upcoming|later|future|ok\b', l, re.I)
                                                                   for l in s.splitlines())),   # naming them as excluded is right (fixed 2026-11-22)
            ('sorted by due date', all(p >= 0 for p in pos) and pos == sorted(pos)),
            (f'total owed on them ({total:.2f})', _num(s + '\n' + (reply or ''), total))]


def check_contacts(folder, reply):
    rows = _csv_rows(_read(folder, 'contacts/all.csv'))
    if not rows:
        return [('wrote contacts/all.csv', False), ('one row per person (5)', False), ('phones and emails joined to the right person', False)]
    body = rows[1:] if not re.search(r'@', ','.join(rows[0])) else rows
    found = {}
    for r in body:
        line = ','.join(r)
        name = next((n for n in PEOPLE if re.search(r'\s*'.join(n.split()), line, re.I)), None)
        if name:
            found.setdefault(name, []).append(line)
    right = all(name in found and len(found[name]) == 1
                and (ph is None or ph[-8:] in _digits(found[name][0]))
                and (em is None or em in found[name][0].lower()) for name, (ph, em) in PEOPLE.items())
    return [('wrote contacts/all.csv', True),
            ('one row per person (5)', len(body) == 5 and all(len(v) == 1 for v in found.values()) and len(found) == 5),
            ('phones and emails joined to the right person', right)]


def check_club(folder, reply):
    r = reply or ''
    return [('venue: the Riverside café', bool(re.search(r'riverside', r, re.I))),
            ('date: Saturday 19 December', bool(re.search(r'19\s*(th)?\s*dec|dec\w*\s*19', r, re.I))),
            ('budget: 550 euros', _num(r, 550)),
            ('did not give an older decision as current', not re.search(r'community hall|12\s*(th)?\s*dec|dec\w*\s*12\b|\b400\b', r, re.I)
             or bool(re.search(r'(was|moved|changed|replac|instead|earlier|previous|raised|from)', r, re.I)))]


TASKS = [
    dict(id='h-reconcile', title='Match receipts against the bank statement', lang='en', may_change=['bank/reconcile.md'], check=check_reconcile,
         text='@mini check my October receipts in receipts/ against the bank statement bank/october.csv: which receipts have no '
              'charge on the statement, and which charges have no receipt? Write it to bank/reconcile.md'),
    dict(id='h-lease', title='Notice and rent from a lease and its amendment', lang='en', may_change=[], check=check_lease,
         text='@mini how much notice do I have to give on my flat now, and what will my rent be in February 2027?'),
    dict(id='h-categories', title='October spending per category, with refunds', lang='en', may_change=['budget/october-by-category.csv'], check=check_categories,
         text='@mini make budget/october-by-category.csv with how much I spent per category in October (refunds count against '
              'their category), biggest first, and tell me the total'),
    dict(id='h-car', title='Car clashes across three agendas (English, Japanese, French)', lang='en', may_change=['agendas/car-clashes.md'], check=check_car,
         text='@mini we have one car. Look at our agendas: which appointments next week need the car at the same time? '
              'Write them to agendas/car-clashes.md'),
    dict(id='h-duplicates', title='Find duplicate photos and move the extra copies', lang='en', may_change=['photos'], check=check_duplicates,
         text='@mini some photos in photos/2026 are the same picture saved twice. Keep one of each and move the extra copies to photos/duplicates'),
    dict(id='h-journal', title='Rename journal notes by date and fix the links', lang='en', may_change=['notes/journal', 'notes/index.md'], check=check_journal,
         text='@mini rename my journal notes in notes/journal to the date they are about, like 2026-11-03.md, and fix the links '
              'in notes/index.md so they still work'),
    dict(id='h-kyoto', title='Japanese trip plan to a French itinerary', lang='fr', may_change=['voyage'], check=check_kyoto,
         text="@mini à partir de mon plan de voyage à Kyoto (en japonais, dans 旅行/), écris un itinéraire en français jour par "
              "jour avec les horaires dans voyage/kyoto.md"),
    dict(id='h-overdue', title='Overdue bills, with due dates in four formats', lang='en', may_change=['bills/overdue.md'], check=check_overdue,
         text="@mini today is 18 November 2026. Which of my unpaid bills in bills/bills.csv are overdue, and how much do I owe on "
              "them? List them by due date in bills/overdue.md"),
    dict(id='h-contacts', title='Merge two contact lists', lang='en', may_change=['contacts/all.csv'], check=check_contacts,
         text='@mini merge contacts/phone.csv and contacts/email.csv into contacts/all.csv with name, phone and email, one row per person'),
    dict(id='h-club', title='Latest decisions from five meetings that changed their minds', lang='en', may_change=[], check=check_club,
         text="@mini from the club's meeting notes, where and when is the end-of-year party, and what's the budget? Only the latest decisions."),
]


def untouched(folder, task):
    bad = []
    for rel, value in SEED.items():
        if any(rel == m or rel.startswith(m.rstrip('/') + '/') for m in task['may_change']):
            continue
        p = Path(folder, rel)
        now = p.read_bytes() if p.is_file() else None
        if now != (value if isinstance(value, bytes) else value.encode()):
            bad.append(rel)
    return bad


GUARD = re.compile(r'^(did not|left |kept |no photo lost|flagged nothing)')  # steps about not breaking things


def score(task, folder, reply):
    """[(step, passed)] including the "did not touch other files" step. Steps about not breaking
    things count only when the job itself got somewhere: doing nothing earns no points."""
    steps = task['check'](folder, reply)
    steps = steps + [('left every other file as it was', not untouched(folder, task))]
    if not any(ok for step, ok in steps if not GUARD.match(step)):
        steps = [(step, False) for step, _ in steps]
    return steps


# ---------------------------------------------------------------- reference solutions (for the tests)

def reference(task_id, folder):
    """Do the job as a careful person would; returns the reply."""
    f = Path(folder)
    if task_id == 'h-reconcile':
        (f / 'bank/reconcile.md').write_text('# October reconciliation\n\n## Receipts with no charge\n- Page & Co Books, 17 Oct, 31.90 EUR\n'
                                             '- BricoPlus, 28 Oct, 18.75 EUR\n\n## Charges with no receipt\n- 15 Oct NETFLIX 13.49\n- 26 Oct PETROL STATION A6 58.20\n')
        return 'Written to bank/reconcile.md: 2 receipts with no charge (Page & Co, BricoPlus), 2 charges with no receipt (Netflix, petrol).'
    if task_id == 'h-lease':
        return 'Two months, in writing: the 2026 amendment replaced clause 14 (it was three months). From 1 January 2027 the rent is 1,180 euros, so 1,180 in February.'
    if task_id == 'h-categories':
        tot = category_totals()
        (f / 'budget/october-by-category.csv').write_text('category,total\n' + ''.join(f'{c},{v:.2f}\n' for c, v in tot.items()))
        return f'Saved budget/october-by-category.csv. Total spent: {sum(tot.values()):.2f} euros.'
    if task_id == 'h-car':
        (f / 'agendas/car-clashes.md').write_text('# Car clashes, week of 23 November\n\n- Tue 24 Nov: Aiko dentist 15:00-16:30 and Ken client visit 16:00-17:30\n'
                                                  '- Sat 28 Nov: Ken golf 09:30-11:00 and Marc recycling centre 10:30-12:00\n')
        return 'Two clashes, saved to agendas/car-clashes.md.'
    if task_id == 'h-duplicates':
        (f / 'photos/duplicates').mkdir(parents=True, exist_ok=True)
        for n in ('beach day.jpg', 'IMG_1002 (1).jpg', 'copy of IMG_1004.jpg'):
            (f / 'photos/2026' / n).rename(f / 'photos/duplicates' / n)
        return 'Moved 3 extra copies to photos/duplicates.'
    if task_id == 'h-journal':
        idx = (f / 'notes/index.md').read_text()
        for rel, (d, _) in JOURNAL.items():
            (f / rel).rename(f / 'notes/journal' / f'{d}.md')
        idx = re.sub(r'\]\(<?journal/[^)>]+>?\)', lambda m: m.group(0), idx)
        for old, new in (('journal/Nov%203%202026.md', 'journal/2026-11-03.md'), ('<journal/2026_11_07 market.md>', 'journal/2026-11-07.md'),
                         ('journal/journal%2012-11-2026.md', 'journal/2026-11-12.md'), ('<journal/Sunday 15th November.md>', 'journal/2026-11-15.md')):
            idx = idx.replace(old, new)
        (f / 'notes/index.md').write_text(idx)
        return 'Renamed 4 notes and fixed the links in notes/index.md.'
    if task_id == 'h-kyoto':
        (f / 'voyage').mkdir(exist_ok=True)
        (f / 'voyage/kyoto.md').write_text("# Kyoto\n\n## Jour 1 (5 déc.)\n- 8h00 : le sanctuaire Fushimi Inari, le matin il y a moins de monde\n"
                                           "- 12h00 : déjeuner au marché Nishiki\n- 15h00 : le temple Kiyomizu-dera\n\n"
                                           "## Jour 2 (6 déc.)\n- 9h00 : visite du Kinkaku-ji\n- 11h30 : le Ryōan-ji\n- 18h00 : promenade dans Gion\n\n"
                                           "## Jour 3 (7 déc.)\n- 8h30 : la bambouseraie d'Arashiyama\n- 10h30 : le Tenryū-ji\n- 14h00 : départ en shinkansen de la gare de Kyoto\n")
        return "Itinéraire écrit dans voyage/kyoto.md."
    if task_id == 'h-overdue':
        (f / 'bills/overdue.md').write_text('# Overdue on 18 November 2026\n\n' + ''.join(f'- {n}: due {d.isoformat()}, {a:.2f}\n' for n, _, d, a, _ in OVERDUE)
                                            + f'\nTotal: {sum(b[3] for b in OVERDUE):.2f}\n')
        return f'3 overdue bills, {sum(b[3] for b in OVERDUE):.2f} in all; listed in bills/overdue.md.'
    if task_id == 'h-contacts':
        (f / 'contacts/all.csv').write_text('name,phone,email\nKen Sato,090-1234-5678,ken@example.jp\nAiko Sato,080 2222 3333,aiko@example.jp\n'
                                            'Marc Dubois,+33 6 12 34 56 78,marc.dubois@example.fr\nYuki Tanaka,070-9999-0000,\nLea Martin,,lea@example.fr\n')
        return 'Merged 5 people into contacts/all.csv.'
    if task_id == 'h-club':
        return 'Riverside café, Saturday 19 December (moved from the 12th), budget 550 euros (raised from 400).'
    raise KeyError(task_id)
