"""Tests for the hard family jobs' grading: the reference solution scores every point, doing nothing
scores none, and correct answers written another way still pass.  python3 benches/dropbox/test_family_hard.py"""
import tempfile
from pathlib import Path

import family_hard as H


def run(tid, files=None, reply=''):
    t = next(x for x in H.TASKS if x['id'] == tid)
    d = tempfile.mkdtemp()
    H.write_seed(d)
    for rel, txt in (files or {}).items():
        Path(d, rel).parent.mkdir(parents=True, exist_ok=True)
        Path(d, rel).write_text(txt)
    return H.score(t, d, reply)


def test_reference_and_nothing():
    for t in H.TASKS:
        d = tempfile.mkdtemp()
        H.write_seed(d)
        assert all(ok for _, ok in H.score(t, d, H.reference(t['id'], d))), t['id']
        assert not any(ok for _, ok in run(t['id'])), t['id']


def test_other_formats():
    full = lambda s: all(ok for _, ok in s)
    assert full(run('h-reconcile', {'bank/reconcile.md': "**Matched**\n- Boulangerie Martin 12.50\n- Garage Dupont\n\n**Receipts without a charge**\n- Page & Co Books 31.90\n- BricoPlus 18.75\n\n**Charges without a receipt**\n- Netflix 13.49\n- Petrol station A6 58.20\n"}))
    assert full(run('h-categories', {'budget/october-by-category.csv': 'Category,Amount (€)\nHousing,"1,144.50"\nFood,259.95\nTransport,210.00\nUtilities,164.65\nLeisure,53.9\n'}, 'Total: €1,833.00'))
    assert full(run('h-car', {'agendas/car-clashes.md': "| Tuesday 24 November | Aiko (dentist) / Ken (client visit) |\n| Saturday 28 November | Ken (golf) / Marc (déchetterie) |\n\nThursday 26: back-to-back, not a clash.\n"}))
    assert full(run('h-lease', reply="2 months' notice (the 2026 amendment replaced the 3-month clause). Rent from Jan 2027: €1,180."))
    assert full(run('h-kyoto', {'voyage/kyoto.md': "## Jour 1 — 5 décembre\n- 08:00 Fushimi Inari\n- 12:00 déjeuner au marché Nishiki\n- 15:00 Kiyomizu-dera\n## Jour 2 — 6 décembre\n- 09:00 Kinkaku-ji\n- 11:30 Ryoan-ji\n- 18:00 promenade à Gion\n## Jour 3 — 7 décembre\n- 08:30 Arashiyama\n- 10:30 Tenryu-ji\n- 14:00 départ en shinkansen de la gare\n"}))


def test_numbers():
    for txt, v, want in [('53.9', 53.9, True), ('153.90', 53.9, False), ('1,180', 1180, True), ('1180.5', 1180, False),
                         ('5500', 550, False), ('€1,833.00', 1833, True), ('29.99', 134.99, False)]:
        assert H._num(txt, v) == want, (txt, v)


if __name__ == '__main__':
    test_reference_and_nothing(); test_other_formats(); test_numbers()
    print('ok')
