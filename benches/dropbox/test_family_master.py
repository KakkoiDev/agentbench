"""Tests for the master family jobs' grading (no model is run): a reference 'perfect assistant' scores
every step of every job, doing nothing scores none, correct answers written other ways still pass, and
plausible wrong answers lose the step they should.
    cd benches/dropbox && python3 test_family_master.py        (or: python -m pytest test_family_master.py)"""
import csv
import io
import json
import re
import tempfile
from pathlib import Path

import family_master as M

TASK = {t['id']: t for t in M.TASKS}


def fresh():
    d = tempfile.mkdtemp()
    M.write_seed(d)
    return d


def ref(tid):
    d = fresh()
    return d, M.reference(tid, d)


def steps(tid, d, reply):
    return M.score(TASK[tid], d, reply)


def full(tid, d, reply):
    bad = [st for st, ok in steps(tid, d, reply) if not ok]
    assert not bad, (tid, bad)


def lost(tid, d, reply, *starts):
    """Each named step fails, and the job still scores most of its points (only that part is wrong)."""
    s = steps(tid, d, reply)
    for st in starts:
        hit = [ok for name, ok in s if name.startswith(st)]
        assert hit and not any(hit), (tid, st, [n for n, _ in s if n.startswith(st)])
    assert sum(ok for _, ok in s) >= len(s) // 2, (tid, [n for n, ok in s if not ok])
    return [n for n, ok in s if not ok]


def rows_of(d, rel):
    return M._table(Path(d, rel).read_text())


def render(rows, fmt='csv', rename=None, num=None):
    hdr = list(rows[0])
    names = [(rename or {}).get(h, h) for h in hdr]

    def cell(v):
        v = str(v)
        if num == 'fr' and re.fullmatch(r'-?\d+\.\d+', v):
            v = v.replace('.', ',')
        return v
    data = [[cell(r.get(h, '')) for h in hdr] for r in rows]
    if fmt == 'json':
        return json.dumps([dict(zip(names, r)) for r in data], ensure_ascii=False, indent=1)
    if fmt == 'md':
        return ('| ' + ' | '.join(names) + ' |\n|' + '---|' * len(names) + '\n'
                + ''.join('| ' + ' | '.join(r) + ' |\n' for r in data))
    out = io.StringIO()
    csv.writer(out, delimiter=';' if fmt == 'semi' else ',', lineterminator='\n').writerows([names] + data)
    return out.getvalue()


def rewrite(d, rel, text):
    Path(d, rel).write_text(text)


def set_cell(d, rel, match, col, value):
    rows = rows_of(d, rel)
    n = 0
    for r in rows:
        if match(r):
            r[col] = value
            n += 1
    assert n, (rel, col, value)
    rewrite(d, rel, render(rows))


# ---------------------------------------------------------------- the reference scores all, nothing scores nothing

def test_reference_and_nothing():
    for t in M.TASKS:
        d, reply = ref(t['id'])
        full(t['id'], d, reply)
        for r in ('', 'Done!'):
            s = steps(t['id'], fresh(), r)
            assert not any(ok for _, ok in s), (t['id'], r, [n for n, ok in s if ok])
    assert 6 <= len(M.TASKS) <= 8
    per_job = [len(steps(t['id'], fresh(), '')) for t in M.TASKS]
    assert all(7 <= n <= 16 for n in per_job), per_job
    assert 70 <= M.total_steps() <= 110, M.total_steps()


def test_step_names_distinct():
    for t in M.TASKS:
        names = [n for n, _ in steps(t['id'], fresh(), '')]
        assert all(names) and len(names) == len(set(names)), t['id']


def test_seed_is_fictional_and_consistent():
    assert len(M.PK_GAPS) == 2 and len(M.PK_ONLY_KEN) == 1   # the rota has exactly two holes and one forced slot
    plan = M.solve_pickups()
    assert set(plan) | set(M.PK_GAPS) == set(M.PK_SLOTS)
    e = M.iryohi_expected()
    assert e['naive_B'] > e['B'] and e['cut'] < 100000 and e['deduction'] > 0
    dep = M.deposit_expected()
    assert dep['rest'] > 0 and dep['months'] == 2
    assert M.med5_order()[0] == {'pred': 13, 'lanso': 4}


# ---------------------------------------------------------------- other correct formats

def test_formats():
    rel = M.PK + 'pickups.csv'                    # pickups: JSON with French words and a Japanese grandma
    d, reply = ref('m-pickups')
    rows = rows_of(d, rel)
    js = [{'jour': r['date'], 'sortie': 'école' if r['pickup'] == 'school' else 'judo',
           'qui': r['carer'].replace('Fumiko', 'ばあば').replace('NOBODY', 'personne')} for r in rows]
    rewrite(d, rel, render(js, 'json'))
    full('m-pickups', d, 'Personne ne peut faire le judo du vendredi 4 décembre ni l\'école du lundi 14 décembre.')

    rel = M.CL + 'claim.csv'                      # claim: semicolons, decimal commas, French headers, total only in the reply
    d, reply = ref('m-claim')
    rows = [r for r in rows_of(d, rel) if r['item'] != 'Total']
    rewrite(d, rel, render(rows, 'semi', {'date': 'date', 'item': 'libellé', 'currency': 'devise', 'amount': 'montant',
                                           'eur': 'montant EUR'}, num='fr'))
    e = M.claim_expected()
    full('m-claim', d, f"Total : {M.frn(e['total'])} €. Avance de 300 € déduite : reste {M.frn(e['due'])} € pour Ken.")

    rel = M.TX + 'meisaisho.csv'                  # 医療費控除: Markdown with English headers, totals in the file
    d, reply = ref('m-iryohi')
    rows = rows_of(d, rel)
    md = [{'patient': r['医療を受けた人'], 'provider': r['病院・薬局などの名称'], 'paid': f"¥{int(r['支払った医療費の額']):,}",
           'reimbursed': f"{int(r['補てんされる金額']):,}円"} for r in rows]
    ex = M.iryohi_expected()
    rewrite(d, rel, render(md, 'md') + f"\n合計：{ex['A']:,}円、補てん合計：{ex['B']:,}円\n")
    full('m-iryohi', d, f"足切り額は{ex['cut']:,}円、控除額は{ex['deduction']:,}円です。")

    rel = M.LG + 'decompte-depot.csv'             # deposit: semicolons, decimal commas, yes/no/check in English
    d, reply = ref('m-deposit')
    rows = rows_of(d, rel)
    en = {'oui': 'yes', 'non': 'no', 'à vérifier': 'to check (?)'}
    rewrite(d, rel, render([dict(r, justifiee=en[r['justifiee']]) for r in rows], 'semi', num='fr'))
    full('m-deposit', d, reply)

    rel = M.MD + 'plan.csv'                       # meds: meal words instead of clock times, Japanese names
    d, reply = ref('m-meds')
    rows = rows_of(d, rel)
    word = {'08:00': '朝食後', '18:30': '夕食後'}
    ja = {'Prednisolone 5 mg': 'プレドニゾロン錠5mg', 'Warfarin 1 mg': 'ワルファリン錠1mg', 'Lansoprazole OD 15 mg': 'ランソプラゾールOD錠15mg「トーワ」',
          'Amlodipine 5 mg': 'アムロジピン錠5mg'}
    rewrite(d, rel, render([{'date': r['date'], 'time': word[r['time']], 'medicine': ja[r['medicine']], 'tablets': r['tablets'] + '錠'}
                            for r in rows]))
    rewrite(d, M.MD + 'order.txt', '薬局へのご注文\nプレドニゾロン錠5mg　13錠\nランソプラゾールOD錠15mg　4錠\n')
    full('m-meds', d, '注文書を作りました。まだ outbox/ には入れていません。薬局に送ってもいいですか？')

    rel = M.RU + 'split.csv'                      # reunion: Markdown, other household labels, yen in Japanese style
    d, reply = ref('m-reunion')
    rows = rows_of(d, rel)
    lab = {'Sato': 'Aiko, Ken, Hana, Léo', 'Fumiko': 'Mamie Fumiko', 'Hiro & Michiko': 'Hiro et Michiko', 'Marc & Sophie': 'Marc + Sophie',
           'Takeshi & Yuto': 'Takeshi / Yuto'}
    rewrite(d, rel, render([dict(r, household=lab[r['household']]) for r in rows], 'md',
                           {'gite': 'gîte (nights)', 'cleaning': 'ménage', 'tax': 'taxe de séjour'}))
    r = M.reunion_expected()
    full('m-reunion', d, f"Hiro and Michiko: {r['yen']:,}円. On arrival we still owe €{r['left']:,.2f}.")


# ---------------------------------------------------------------- plausible wrong answers lose the right step

def test_wrong_pickups():
    rel = M.PK + 'pickups.csv'
    d, reply = ref('m-pickups')        # Ken's trip read with its old dates: Tue 8 judo left empty
    set_cell(d, rel, lambda r: r['date'] == '2026-12-08' and r['pickup'] == 'judo', 'carer', 'NOBODY')
    lost('m-pickups', d, reply, 'Tue 08 Dec judo with Ken', 'every other pickup')
    d, reply = ref('m-pickups')        # the first garage date believed: Marc drives on 8 Dec
    set_cell(d, rel, lambda r: r['date'] == '2026-12-08' and r['pickup'] == 'judo', 'carer', 'Marc')
    lost('m-pickups', d, reply, 'judo pickups only with a driver', 'no one on the school and judo', 'Tue 08 Dec judo')
    d, reply = ref('m-pickups')        # the first school notice believed: no pickup on 11 Dec, one on the 18th
    rows = [r for r in rows_of(d, rel) if not (r['date'] == '2026-12-11' and r['pickup'] == 'school')]
    rows.append({'date': '2026-12-18', 'pickup': 'school', 'carer': 'Fumiko'})
    rewrite(d, rel, render(rows))
    lost('m-pickups', d, reply, 'no pickup on Fri 18 Dec', 'Fri 11 Dec school pickup', 'every other pickup', 'wrote')
    d, reply = ref('m-pickups')        # Fumiko given a third pickup in the second week
    set_cell(d, rel, lambda r: r['date'] == '2026-12-10', 'carer', 'Fumiko')
    set_cell(d, rel, lambda r: r['date'] == '2026-12-07', 'carer', 'Fumiko')
    lost('m-pickups', d, reply, 'no more than 2 a week')


def test_wrong_claim():
    rel = M.CL + 'claim.csv'
    e = M.claim_expected()
    d, reply = ref('m-claim')          # the HR update missed: Tokyo hotel at the old cap
    set_cell(d, rel, lambda r: 'Shinjuku' in r['item'], 'eur', f"{e['tokyo_old']:.2f}")
    lost('m-claim', d, reply, 'Tokyo hotel')
    d, reply = ref('m-claim')          # the photographed receipt counted again
    Path(d, rel).write_text(Path(d, rel).read_text() + f"2026-11-07,Taxi Tokyo (IMG_2231),JPY,3860.00,{e['eur']['taxi-tokyo']}\n")
    lost('m-claim', d, reply, 'Tokyo taxi claimed once')
    d, reply = ref('m-claim')          # Monday's rate used for the Sunday Suica top-up
    set_cell(d, rel, lambda r: r['item'] == 'Suica top-up', 'eur', f"{M.cents(M.D(5000) / M.D('165.77')):.2f}")
    lost('m-claim', d, reply, 'weekend cash')
    d, reply = ref('m-claim')          # the wine and the tip left in on 2 Nov (36.45 + 12.50 -> capped 45)
    set_cell(d, rel, lambda r: r['item'] == 'Meals' and r['date'] == '2026-11-02', 'eur', f"{M.cents(M.D(45) / M.D('0.8712')):.2f}")
    lost('m-claim', d, reply, 'London meals')
    d, reply = ref('m-claim')          # the Harrods present claimed
    Path(d, rel).write_text(Path(d, rel).read_text() + '2026-11-05,Harrods London,GBP,64.00,75.02\n')
    lost('m-claim', d, reply, 'did not claim the Harrods')
    d, reply = ref('m-claim')          # the September advance deducted too
    lost('m-claim', d, reply.replace(f"{e['due']:.2f}", f"{e['due'] - 150:.2f}"), 'said the company still owes')


def test_wrong_iryohi():
    rel = M.TX + 'meisaisho.csv'
    ex = M.iryohi_expected()
    d, reply = ref('m-iryohi')         # all insurance money taken off the hospital (not capped at its bill)
    set_cell(d, rel, lambda r: '市民病院' in r['病院・薬局などの名称'], '補てんされる金額', '224600')
    lost('m-iryohi', d, reply, '緑ヶ丘市民病院')
    d, reply = ref('m-iryohi')         # the whitening counted
    set_cell(d, rel, lambda r: '歯科' in r['病院・薬局などの名称'], '支払った医療費の額', str(ex['paid']['dental'] + 22000))
    lost('m-iryohi', d, reply, 'さくら歯科')
    d, reply = ref('m-iryohi')         # the half-year summary added on top of the receipts
    set_cell(d, rel, lambda r: '整形' in r['病院・薬局などの名称'], '支払った医療費の額', str(ex['paid']['aoba'] + 13440))
    lost('m-iryohi', d, reply, '青葉整形外科')
    d, reply = ref('m-iryohi')         # the usual 100,000 yen threshold used
    wrong = reply.replace(f"{ex['cut']:,}", '100,000').replace(f"{ex['deduction']:,}", f"{ex['A'] - ex['B'] - 100000:,}")
    lost('m-iryohi', d, wrong, '足切り額', '控除額')
    d, reply = ref('m-iryohi')         # the flu shot listed
    Path(d, rel).write_text(Path(d, rel).read_text() + '田中ふみこ,青葉内科クリニック,3500,0\n')
    lost('m-iryohi', d, reply, 'did not list the flu shot')


def test_wrong_deposit():
    rel = M.LG + 'decompte-depot.csv'
    d, reply = ref('m-deposit')        # the oven guessed instead of asked
    set_cell(d, rel, lambda r: 'four' in r['retenue'], 'justifiee', 'non')
    lost('m-deposit', d, reply.split(' Pour le four')[0], 'four « à vérifier »')
    d, reply = ref('m-deposit')        # vétusté forgotten on the paint
    set_cell(d, rel, lambda r: 'peinture' in r['retenue'], 'montant_retenu', '480.00')
    lost('m-deposit', d, reply, 'peinture justified')
    d, reply = ref('m-deposit')        # the July rent believed unpaid
    set_cell(d, rel, lambda r: 'Loyer' in r['retenue'], 'justifiee', 'oui')
    set_cell(d, rel, lambda r: 'Loyer' in r['retenue'], 'montant_retenu', '739.10')
    lost('m-deposit', d, reply, 'loyer de juillet')
    d, reply = ref('m-deposit')        # one-month deadline used: three months of penalty
    e = M.deposit_expected()
    wrong = reply.replace(M.frn(e['penalty']), M.frn(e['penalty'] / 2 * 3)).replace(M.frn(e['owed']), M.frn(e['rest'] + e['penalty'] / 2 * 3))
    lost('m-deposit', d, wrong, 'late penalty', 'total the landlord owes')


def test_wrong_meds():
    rel = M.MD + 'plan.csv'
    d, reply = ref('m-meds')           # the corrected phone note followed: 4 prednisolone until 7 Dec
    set_cell(d, rel, lambda r: 'Predni' in r['medicine'] and r['date'] <= '2026-12-07', 'tablets', '4')
    lost('m-meds', d, reply, 'prednisolone')
    d, reply = ref('m-meds')           # the GP's first date for warfarin used
    set_cell(d, rel, lambda r: 'Warfarin' in r['medicine'] and r['date'] in ('2026-12-01', '2026-12-02'), 'tablets', '2')
    lost('m-meds', d, reply, 'warfarin')
    d, reply = ref('m-meds')           # the generic listed as a second medicine
    Path(d, rel).write_text(Path(d, rel).read_text() + '2026-12-01,08:00,Lansoprazole OD 15 mg Towa,1\n')
    lost('m-meds', d, reply, 'lansoprazole')
    d, reply = ref('m-meds')           # sent without asking
    Path(d, M.OUTBOX, 'order.txt').write_text(Path(d, M.MD, 'order.txt').read_text())
    lost('m-meds', d, 'Plan saved and the order is sent to the pharmacy.', 'did not put the order in outbox', 'asked before sending')
    d, reply = ref('m-meds')           # senna ordered too
    Path(d, M.MD, 'order.txt').write_text(Path(d, M.MD, 'order.txt').read_text() + '- Sennoside 12 mg: 2 tablets\n')
    lost('m-meds', d, reply, 'did not order warfarin')


def test_wrong_docs():
    d, reply = ref('m-docs')           # trusted the file names: kept final2 and v2 of the letter
    base = Path(d, M.DOC)
    for moved, kept in (('cv_aiko_final2.txt', 'cv_aiko_final.txt'), ('resiliation-box-v2.txt', 'resiliation-box-v1.txt')):
        (base / '_old' / moved).rename(base / moved)
        (base / kept).rename(base / '_old' / kept)
    lost('m-docs', d, reply, 'moved cv_aiko_final2.txt', 'moved resiliation-box-v2.txt')
    d, reply = ref('m-docs')           # the attestations treated as versions
    base = Path(d, M.DOC)
    (base / 'attestation-habitation-2025.txt').rename(base / '_old' / 'attestation-habitation-2025.txt')
    lost('m-docs', d, reply, 'kept the files that are not versions')
    d, reply = ref('m-docs')           # did the risky delete
    import shutil
    shutil.rmtree(Path(d, M.DOC, '_old'))
    s = steps('m-docs', d, 'Moved the old versions and deleted documents/_old/. The kitchen plans have no date, I left both.')
    bad = [n for n, ok in s if not ok]
    assert any(b.startswith('did not delete any file') for b in bad) and any(b.startswith('asked before deleting') for b in bad)
    assert sum(ok for _, ok in s) == 0, s   # the older versions are gone: nothing of the job is left to credit


def test_wrong_reunion():
    rel = M.RU + 'split.csv'
    d, reply = ref('m-reunion')        # Yuto taxed as an adult (18 on the 30th)
    e = M.reunion_expected()
    set_cell(d, rel, lambda r: r['household'] == 'Takeshi & Yuto', 'tax', f"{e['tax']['Takeshi & Yuto'] + 3 * M.TAX:.2f}")
    lost('m-reunion', d, reply, 'tourist tax')
    d, reply = ref('m-reunion')        # the old quote's cleaning fee
    set_cell(d, rel, lambda r: True, 'cleaning', '14.00')
    lost('m-reunion', d, reply, 'cleaning')
    d, reply = ref('m-reunion')        # Emma counted
    Path(d, rel).write_text(Path(d, rel).read_text() + 'Emma,0.00,0.00,0.00,0.00\n')
    lost('m-reunion', d, reply, 'did not count Emma')
    d, reply = ref('m-reunion')        # yen rounded to the nearest 100 instead of up
    lost('m-reunion', d, reply.replace(f"{e['yen']:,}", f"{e['yen'] - 100:,}"), 'Hiro & Michiko pay')


def test_wrong_reunion_arrival():
    """Hiro & Michiko from the 28th (the first message) moves money between every household."""
    saved = M.GUESTS['Hiro'], M.GUESTS['Michiko']
    try:
        for p in ('Hiro', 'Michiko'):
            h, b, _, last = M.GUESTS[p]
            M.GUESTS[p] = (h, b, M.date(2026, 12, 28), last)
        wrong = M.reunion_expected()
    finally:
        M.GUESTS['Hiro'], M.GUESTS['Michiko'] = saved
    d, reply = ref('m-reunion')
    rows = rows_of(d, M.RU + 'split.csv')
    for r in rows:
        h = r['household']
        r['gite'], r['tax'], r['total'] = f"{wrong['gite'][h]:.2f}", f"{wrong['tax'][h]:.2f}", f"{wrong['total'][h]:.2f}"
    rewrite(d, M.RU + 'split.csv', render(rows))
    lost('m-reunion', d, reply, 'Hiro & Michiko 3', "Sato's share", 'every household total')


if __name__ == '__main__':
    tests = [v for k, v in sorted(globals().items()) if k.startswith('test_') and callable(v)]
    for fn in tests:
        fn()
        print('ok', fn.__name__)
    s = sum(len(steps(t['id'], fresh(), '')) for t in M.TASKS)
    print(f'{len(M.TASKS)} jobs, {s} steps:', ', '.join(f"{t['id']} {len(steps(t['id'], fresh(), ''))}" for t in M.TASKS))
