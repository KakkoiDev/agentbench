"""Hidden tests for order-book: exact event lists, random operations against a model, speed."""
import random

# ---- grading helpers (same in every agentic-v6 hidden test file) ----
import os, re as _re_mod, signal, subprocess, sys, textwrap, unittest


class Limited(unittest.TestCase):
    """Each test gets LIMIT seconds; a solution that hangs fails that test only, not the whole suite."""
    LIMIT = 30

    def setUp(self):
        def boom(*_):
            raise TimeoutError(f'test took over {self.LIMIT} s')
        signal.signal(signal.SIGALRM, boom)
        signal.alarm(self.LIMIT)

    def tearDown(self):
        signal.alarm(0)

    def timed(self, setup, work, budget, check=''):
        """Run setup (untimed), then work (timed), then check in a fresh interpreter in this folder.
        Fails if work takes over budget seconds; the process is killed at budget + 20 s."""
        prog = ('import sys, time\nsys.path.insert(0, %r)\n' % os.getcwd() + textwrap.dedent(setup)
                + '\n_t0 = time.perf_counter()\n' + textwrap.dedent(work)
                + '\n_elapsed = time.perf_counter() - _t0\n' + textwrap.dedent(check) + '\nprint("ELAPSED", _elapsed)\n')
        signal.alarm(0)
        try:
            r = subprocess.run([sys.executable, '-I', '-c', prog], capture_output=True, text=True, timeout=budget + 20)
        except subprocess.TimeoutExpired:
            self.fail(f'over {budget} s (stopped after {budget + 20} s)')
        self.assertEqual(r.returncode, 0, r.stderr[-3000:])
        took = float(_re_mod.search(r'ELAPSED (\S+)', r.stdout)[1])
        sys.stderr.write(f'TIMING {self.id().split(".")[-1]} {took:.3f} {budget}\n')
        self.assertLessEqual(took, budget, f'took {took:.2f} s, the limit is {budget} s')


def banned_source(path, names):
    """Names from `names` that the solution file imports or mentions."""
    import ast
    src = open(path).read()
    found = set()
    for node in ast.walk(ast.parse(src)):
        if isinstance(node, ast.Import):
            found |= {a.name.split('.')[0] for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            found.add(node.module.split('.')[0])
    hits = {n for n in names if n in found}
    hits |= {s for s in ('__import__', 'importlib', 'sys.modules') if s in src}
    return sorted(hits)
# ---- end of helpers ----

# ---- a slow, simple model of the specification (lists and linear scans) ----
def o_cents(text):
    import re as _r
    if not isinstance(text, str) or not _r.fullmatch(r'\d+(\.\d{1,2})?', text):
        return None
    whole, _, frac = text.partition('.')
    v = int(whole) * 100 + int((frac + '00')[:2])
    return v if v > 0 else None


def o_fmt(c):
    return '%d.%02d' % (c // 100, c % 100)


class OracleBook:
    def __init__(self):
        self.resting = []    # dicts: id side price qty owner seq
        self.stops = []      # dicts: id side stop qty owner seq (pending, submission order)
        self.ids = set()
        self.seq = 0
        self.last = None

    def valid(self, o):
        if not isinstance(o, dict) or not isinstance(o.get('id'), str) or not o['id'] or o['id'] in self.ids:
            return False
        q = o.get('qty')
        if o.get('side') not in ('buy', 'sell') or type(q) is not int or q <= 0:
            return False
        if o.get('owner') is not None and not isinstance(o.get('owner'), str):
            return False
        t, tif = o.get('type'), o.get('tif')
        if t == 'limit':
            return o_cents(o.get('price')) is not None and tif in (None, 'GTC', 'IOC', 'FOK') and 'stop' not in o
        if t == 'market':
            return 'price' not in o and 'stop' not in o and tif in (None, 'IOC', 'FOK')
        if t == 'stop':
            return o_cents(o.get('stop')) is not None and 'price' not in o and tif is None
        return False

    def ok_price(self, side, limit, p):
        return limit is None or (p <= limit if side == 'buy' else p >= limit)

    def makers(self, side, limit):
        opp = 'sell' if side == 'buy' else 'buy'
        ms = [r for r in self.resting if r['side'] == opp and self.ok_price(side, limit, r['price'])]
        return sorted(ms, key=lambda r: ((r['price'] if opp == 'sell' else -r['price']), r['seq']))

    def match(self, tid, side, limit, qty, owner, ev):
        while qty > 0:
            ms = self.makers(side, limit)
            if not ms:
                break
            m = ms[0]
            if owner is not None and m['owner'] == owner:
                self.resting.remove(m)
                ev.append(('cancelled', m['id'], m['qty'], 'stp'))
                continue
            n = min(qty, m['qty'])
            ev.append(('trade', tid, m['id'], o_fmt(m['price']), n))
            self.last = m['price']
            qty -= n
            m['qty'] -= n
            if m['qty'] == 0:
                self.resting.remove(m)
        return qty

    def rest(self, oid, side, price, qty, owner):
        self.seq += 1
        self.resting.append(dict(id=oid, side=side, price=price, qty=qty, owner=owner, seq=self.seq))

    def run_stops(self, ev):
        fired = []
        while True:
            if self.last is not None:
                for s in list(self.stops):
                    if (s['side'] == 'buy' and self.last >= s['stop']) or (s['side'] == 'sell' and self.last <= s['stop']):
                        self.stops.remove(s)
                        fired.append(s)
            if not fired:
                return
            fired.sort(key=lambda s: s['seq'])
            s = fired.pop(0)
            ev.append(('triggered', s['id']))
            left = self.match(s['id'], s['side'], None, s['qty'], s['owner'], ev)
            if left:
                ev.append(('cancelled', s['id'], left, 'market'))

    def submit(self, o):
        oid = o.get('id') if isinstance(o, dict) else None
        if not self.valid(o):
            return [('rejected', oid, 'invalid')]
        side, t, qty, owner = o['side'], o['type'], o['qty'], o.get('owner')
        limit = o_cents(o['price']) if t == 'limit' else None
        tif = o.get('tif') or ('GTC' if t == 'limit' else 'IOC')
        if t != 'stop' and tif == 'FOK':
            avail = sum(m['qty'] for m in self.makers(side, limit) if not (owner is not None and m['owner'] == owner))
            if avail < qty:
                return [('rejected', oid, 'fok')]
        self.ids.add(oid)
        ev = [('accepted', oid)]
        if t == 'stop':
            self.seq += 1
            self.stops.append(dict(id=oid, side=side, stop=o_cents(o['stop']), qty=qty, owner=owner, seq=self.seq))
            self.run_stops(ev)
            return ev
        left = self.match(oid, side, limit, qty, owner, ev)
        if left:
            if t == 'limit' and tif == 'GTC':
                self.rest(oid, side, limit, left, owner)
                ev.append(('rested', oid, o_fmt(limit), left))
            else:
                ev.append(('cancelled', oid, left, 'ioc' if t == 'limit' else 'market'))
        self.run_stops(ev)
        return ev

    def cancel(self, oid):
        for r in self.resting:
            if r['id'] == oid:
                self.resting.remove(r)
                return [('cancelled', oid, r['qty'], 'user')]
        for s in self.stops:
            if s['id'] == oid:
                self.stops.remove(s)
                return [('cancelled', oid, s['qty'], 'user')]
        return [('rejected', oid, 'unknown')]

    def amend(self, oid, qty=None, price=None):
        r = next((r for r in self.resting if r['id'] == oid), None)
        if r is None:
            return [('rejected', oid, 'unknown')]
        nq = r['qty'] if qty is None else qty
        np_ = r['price'] if price is None else o_cents(price)
        if np_ is None or type(nq) is not int or nq <= 0:
            return [('rejected', oid, 'invalid')]
        ev = [('amended', oid, o_fmt(np_), nq)]
        if np_ == r['price'] and nq <= r['qty']:
            r['qty'] = nq
            return ev
        self.resting.remove(r)
        left = self.match(oid, r['side'], np_, nq, r['owner'], ev)
        if left:
            self.rest(oid, r['side'], np_, left, r['owner'])
            ev.append(('rested', oid, o_fmt(np_), left))
        self.run_stops(ev)
        return ev

    def depth(self, side, levels=5):
        agg = {}
        for r in self.resting:
            if r['side'] == side:
                q, c = agg.get(r['price'], (0, 0))
                agg[r['price']] = (q + r['qty'], c + 1)
        ps = sorted(agg, reverse=(side == 'buy'))[:levels]
        return [(o_fmt(p), agg[p][0], agg[p][1]) for p in ps]

    def last_price(self):
        return None if self.last is None else o_fmt(self.last)


def random_action(rnd, n, live_ids):
    """One random action: ('submit', order) / ('cancel', id) / ('amend', id, qty, price)."""
    r = rnd.random()
    price = lambda: rnd.choice(['99.50', '99.75', '100', '100.00', '100.25', '100.5', '100.75', '101.00', '99.9', '100.10'])
    if r < 0.12 and live_ids:
        return ('cancel', rnd.choice(live_ids + ['nope']))
    if r < 0.24 and live_ids:
        return ('amend', rnd.choice(live_ids), rnd.choice([None, rnd.randint(1, 12), rnd.randint(1, 3)]),
                rnd.choice([None, None, price()]))
    o = {'id': f'o{n}', 'side': rnd.choice(['buy', 'sell']), 'qty': rnd.randint(1, 12)}
    if rnd.random() < 0.7:
        o['owner'] = rnd.choice(['A', 'B', 'C'])
    k = rnd.random()
    if k < 0.6:
        o['type'], o['price'] = 'limit', price()
        if rnd.random() < 0.3:
            o['tif'] = rnd.choice(['GTC', 'IOC', 'FOK'])
    elif k < 0.75:
        o['type'] = 'market'
        if rnd.random() < 0.3:
            o['tif'] = rnd.choice(['IOC', 'FOK'])
    elif k < 0.92:
        o['type'], o['stop'] = 'stop', price()
    else:
        o['type'] = 'limit'
        bad = rnd.choice(['price0', 'price3dec', 'qty0', 'qtybool', 'side', 'dup', 'marketprice', 'stoptif', 'tif'])
        o['price'] = {'price0': '0.00', 'price3dec': '100.001'}.get(bad, '100.00')
        if bad == 'qty0':
            o['qty'] = 0
        if bad == 'qtybool':
            o['qty'] = True
        if bad == 'side':
            o['side'] = 'hold'
        if bad == 'dup':
            o['id'] = 'o1'
        if bad == 'marketprice':
            o['type'] = 'market'
        if bad == 'stoptif':
            o['type'], o['stop'] = 'stop', '100'
            del o['price']
            o['tif'] = 'IOC'
        if bad == 'tif':
            o['tif'] = 'DAY'
    return ('submit', o)
# ---- end of model ----

from book import Book


def L(i, side, qty, price, owner=None, tif=None):
    o = {'id': i, 'side': side, 'type': 'limit', 'qty': qty, 'price': price}
    if owner:
        o['owner'] = owner
    if tif:
        o['tif'] = tif
    return o


def M(i, side, qty, owner=None, tif=None):
    o = {'id': i, 'side': side, 'type': 'market', 'qty': qty}
    if owner:
        o['owner'] = owner
    if tif:
        o['tif'] = tif
    return o


def S(i, side, qty, stop, owner=None):
    o = {'id': i, 'side': side, 'type': 'stop', 'qty': qty, 'stop': stop}
    if owner:
        o['owner'] = owner
    return o


def book_with(*orders):
    b = Book()
    for o in orders:
        b.submit(o)
    return b


class Matching(Limited):
    def test_rest_and_price_formatting(self):
        b = Book()
        self.assertEqual(b.submit(L('s1', 'sell', 5, '101')), [('accepted', 's1'), ('rested', 's1', '101.00', 5)])
        self.assertEqual(b.submit(L('s2', 'sell', 3, '100.5')), [('accepted', 's2'), ('rested', 's2', '100.50', 3)])
        self.assertEqual(b.submit(L('s3', 'sell', 4, '0100.50')), [('accepted', 's3'), ('rested', 's3', '100.50', 4)])
        self.assertEqual(b.submit(L('b0', 'buy', 1, '0.01')), [('accepted', 'b0'), ('rested', 'b0', '0.01', 1)])
        self.assertEqual(b.depth('sell'), [('100.50', 7, 2), ('101.00', 5, 1)])
        self.assertIsNone(b.last_price)

    def test_price_time_priority_and_maker_price(self):
        b = book_with(L('s1', 'sell', 5, '101'), L('s2', 'sell', 3, '100.5'), L('s3', 'sell', 4, '100.50'), L('s4', 'sell', 2, '102'))
        self.assertEqual(b.submit(L('b1', 'buy', 10, '101')),
                         [('accepted', 'b1'), ('trade', 'b1', 's2', '100.50', 3), ('trade', 'b1', 's3', '100.50', 4),
                          ('trade', 'b1', 's1', '101.00', 3)])
        self.assertEqual((b.depth('sell'), b.depth('buy'), b.last_price), ([('101.00', 2, 1), ('102.00', 2, 1)], [], '101.00'))

    def test_sell_side_mirror(self):
        b = book_with(L('b1', 'buy', 2, '99'), L('b2', 'buy', 2, '100'), L('b3', 'buy', 2, '100'), L('b4', 'buy', 2, '98'))
        self.assertEqual(b.submit(L('s1', 'sell', 7, '99')),
                         [('accepted', 's1'), ('trade', 's1', 'b2', '100.00', 2), ('trade', 's1', 'b3', '100.00', 2),
                          ('trade', 's1', 'b1', '99.00', 2), ('rested', 's1', '99.00', 1)])
        self.assertEqual((b.depth('buy'), b.depth('sell')), ([('98.00', 2, 1)], [('99.00', 1, 1)]))

    def test_partial_maker_keeps_priority(self):
        b = book_with(L('s1', 'sell', 5, '10'), L('s2', 'sell', 5, '10'))
        b.submit(L('b1', 'buy', 2, '10'))
        self.assertEqual(b.submit(L('b2', 'buy', 4, '10')),
                         [('accepted', 'b2'), ('trade', 'b2', 's1', '10.00', 3), ('trade', 'b2', 's2', '10.00', 1)])
        self.assertEqual(b.depth('sell'), [('10.00', 4, 1)])

    def test_ioc(self):
        b = book_with(L('s1', 'sell', 2, '10'))
        self.assertEqual(b.submit(L('i1', 'buy', 5, '10', tif='IOC')),
                         [('accepted', 'i1'), ('trade', 'i1', 's1', '10.00', 2), ('cancelled', 'i1', 3, 'ioc')])
        self.assertEqual(b.submit(L('i2', 'buy', 1, '9', tif='IOC')), [('accepted', 'i2'), ('cancelled', 'i2', 1, 'ioc')])
        self.assertEqual(b.depth('buy'), [])

    def test_market_orders(self):
        b = book_with(L('s1', 'sell', 2, '10'), L('s2', 'sell', 2, '50'))
        self.assertEqual(b.submit(M('m1', 'buy', 3)), [('accepted', 'm1'), ('trade', 'm1', 's1', '10.00', 2), ('trade', 'm1', 's2', '50.00', 1)])
        self.assertEqual(b.submit(M('m2', 'buy', 3)), [('accepted', 'm2'), ('trade', 'm2', 's2', '50.00', 1), ('cancelled', 'm2', 2, 'market')])
        self.assertEqual(b.submit(M('m3', 'sell', 4, tif='IOC')), [('accepted', 'm3'), ('cancelled', 'm3', 4, 'market')])
        self.assertEqual(b.last_price, '50.00')

    def test_fok(self):
        b = book_with(L('s1', 'sell', 5, '10'), L('s2', 'sell', 5, '11'), L('s3', 'sell', 5, '12'))
        self.assertEqual(b.submit(L('f1', 'buy', 11, '11', tif='FOK')), [('rejected', 'f1', 'fok')])
        self.assertEqual(b.depth('sell'), [('10.00', 5, 1), ('11.00', 5, 1), ('12.00', 5, 1)])
        self.assertEqual(b.submit(L('f1', 'buy', 10, '11', tif='FOK')),
                         [('accepted', 'f1'), ('trade', 'f1', 's1', '10.00', 5), ('trade', 'f1', 's2', '11.00', 5)])
        self.assertEqual(b.submit(M('f2', 'buy', 6, tif='FOK')), [('rejected', 'f2', 'fok')])
        self.assertEqual(b.submit(M('f2', 'buy', 5, tif='FOK')), [('accepted', 'f2'), ('trade', 'f2', 's3', '12.00', 5)])

    def test_fok_ignores_own_orders_and_rejection_changes_nothing(self):
        b = book_with(L('a1', 'sell', 5, '10', 'A'), L('b1', 'sell', 5, '10', 'B'), L('a2', 'sell', 5, '11', 'A'))
        self.assertEqual(b.submit(L('f1', 'buy', 6, '11', 'A', tif='FOK')), [('rejected', 'f1', 'fok')])
        self.assertEqual(b.depth('sell'), [('10.00', 10, 2), ('11.00', 5, 1)])
        self.assertEqual(b.submit(L('f2', 'buy', 5, '11', 'A', tif='FOK')),
                         [('accepted', 'f2'), ('cancelled', 'a1', 5, 'stp'), ('trade', 'f2', 'b1', '10.00', 5)])
        self.assertEqual(b.depth('sell'), [('11.00', 5, 1)])
        self.assertEqual(b.submit(L('f1', 'buy', 1, '9', tif='IOC')), [('accepted', 'f1'), ('cancelled', 'f1', 1, 'ioc')])

    def test_self_trade_prevention(self):
        b = book_with(L('a1', 'sell', 2, '10', 'A'), L('b1', 'sell', 2, '10', 'B'), L('a2', 'sell', 2, '10', 'A'), L('n1', 'sell', 2, '11'))
        self.assertEqual(b.submit(L('x', 'buy', 3, '11', 'A')),
                         [('accepted', 'x'), ('cancelled', 'a1', 2, 'stp'), ('trade', 'x', 'b1', '10.00', 2),
                          ('cancelled', 'a2', 2, 'stp'), ('trade', 'x', 'n1', '11.00', 1)])
        b = book_with(L('a1', 'sell', 2, '10', 'A'), L('n1', 'sell', 2, '10'))
        self.assertEqual(b.submit(L('y', 'buy', 3, '10')),
                         [('accepted', 'y'), ('trade', 'y', 'a1', '10.00', 2), ('trade', 'y', 'n1', '10.00', 1)])
        b = book_with(L('a1', 'sell', 2, '10', 'A'))
        self.assertEqual(b.submit(M('m', 'buy', 2, 'A')), [('accepted', 'm'), ('cancelled', 'a1', 2, 'stp'), ('cancelled', 'm', 2, 'market')])
        b = book_with(L('a1', 'sell', 2, '10', 'A'))
        self.assertEqual(b.submit(L('g', 'buy', 2, '10', 'A')), [('accepted', 'g'), ('cancelled', 'a1', 2, 'stp'), ('rested', 'g', '10.00', 2)])


class Validation(Limited):
    def test_invalid_orders(self):
        b = book_with(L('used', 'sell', 1, '10'))
        b.submit(L('filled', 'buy', 1, '10'))
        bad = [
            ('n1', {'id': 'n1', 'side': 'buy', 'type': 'limit', 'qty': 0, 'price': '10'}),
            ('n2', {'id': 'n2', 'side': 'buy', 'type': 'limit', 'qty': -1, 'price': '10'}),
            ('n3', {'id': 'n3', 'side': 'buy', 'type': 'limit', 'qty': True, 'price': '10'}),
            ('n4', {'id': 'n4', 'side': 'buy', 'type': 'limit', 'qty': 1.0, 'price': '10'}),
            ('n5', {'id': 'n5', 'side': 'hold', 'type': 'limit', 'qty': 1, 'price': '10'}),
            ('n6', {'id': 'n6', 'side': 'buy', 'type': 'limit', 'qty': 1, 'price': '10.001'}),
            ('n7', {'id': 'n7', 'side': 'buy', 'type': 'limit', 'qty': 1, 'price': '0.00'}),
            ('n8', {'id': 'n8', 'side': 'buy', 'type': 'limit', 'qty': 1, 'price': '-1'}),
            ('n9', {'id': 'n9', 'side': 'buy', 'type': 'limit', 'qty': 1, 'price': '1.'}),
            ('n10', {'id': 'n10', 'side': 'buy', 'type': 'limit', 'qty': 1, 'price': '.5'}),
            ('n11', {'id': 'n11', 'side': 'buy', 'type': 'limit', 'qty': 1, 'price': 10}),
            ('n12', {'id': 'n12', 'side': 'buy', 'type': 'limit', 'qty': 1}),
            ('n13', {'id': 'n13', 'side': 'buy', 'type': 'limit', 'qty': 1, 'price': '10', 'tif': 'DAY'}),
            ('n14', {'id': 'n14', 'side': 'buy', 'type': 'market', 'qty': 1, 'price': '10'}),
            ('n15', {'id': 'n15', 'side': 'buy', 'type': 'market', 'qty': 1, 'tif': 'GTC'}),
            ('n16', {'id': 'n16', 'side': 'buy', 'type': 'stop', 'qty': 1, 'stop': '10', 'tif': 'IOC'}),
            ('n17', {'id': 'n17', 'side': 'buy', 'type': 'stop', 'qty': 1, 'stop': '10', 'price': '10'}),
            ('n18', {'id': 'n18', 'side': 'buy', 'type': 'stop', 'qty': 1}),
            ('n19', {'id': 'n19', 'side': 'buy', 'type': 'limit', 'qty': 1, 'price': '10', 'stop': '10'}),
            ('n20', {'id': 'n20', 'side': 'buy', 'type': 'iceberg', 'qty': 1, 'price': '10'}),
            ('n21', {'id': 'n21', 'side': 'buy', 'type': 'limit', 'qty': 1, 'price': '10', 'owner': 7}),
            ('used', L('used', 'buy', 1, '9')),
            ('filled', L('filled', 'buy', 1, '9')),
            ('', L('', 'buy', 1, '9')),
            (None, {'side': 'buy', 'type': 'limit', 'qty': 1, 'price': '10'}),
        ]
        for oid, o in bad:
            self.assertEqual(b.submit(o), [('rejected', oid, 'invalid')], o)
        self.assertEqual(b.depth('buy') + b.depth('sell'), [])

    def test_rejected_ids_can_be_used_again(self):
        b = Book()
        b.submit({'id': 'r', 'side': 'buy', 'type': 'limit', 'qty': 0, 'price': '10'})
        self.assertEqual(b.submit(L('r', 'buy', 1, '10')), [('accepted', 'r'), ('rested', 'r', '10.00', 1)])
        self.assertEqual(b.submit(L('r', 'buy', 1, '10')), [('rejected', 'r', 'invalid')])

    def test_tif_none_and_owner_none_are_defaults(self):
        b = book_with(L('s', 'sell', 1, '10', 'A'))
        self.assertEqual(b.submit({'id': 'x', 'side': 'buy', 'type': 'limit', 'qty': 2, 'price': '10', 'tif': None, 'owner': None}),
                         [('accepted', 'x'), ('trade', 'x', 's', '10.00', 1), ('rested', 'x', '10.00', 1)])
        self.assertEqual(b.submit({'id': 'y', 'side': 'sell', 'type': 'stop', 'qty': 1, 'stop': '5', 'tif': None}), [('accepted', 'y')])


class CancelAmend(Limited):
    def test_cancel(self):
        b = book_with(L('s1', 'sell', 5, '10'), S('t1', 'buy', 2, '20'))
        self.assertEqual(b.cancel('s1'), [('cancelled', 's1', 5, 'user')])
        self.assertEqual(b.cancel('s1'), [('rejected', 's1', 'unknown')])
        self.assertEqual(b.cancel('t1'), [('cancelled', 't1', 2, 'user')])
        self.assertEqual(b.cancel('zz'), [('rejected', 'zz', 'unknown')])
        self.assertEqual(b.depth('sell'), [])

    def test_amend_down_keeps_priority(self):
        b = book_with(L('s1', 'sell', 5, '10'), L('s2', 'sell', 5, '10'))
        self.assertEqual(b.amend('s1', qty=2), [('amended', 's1', '10.00', 2)])
        self.assertEqual(b.amend('s1', price='10.00'), [('amended', 's1', '10.00', 2)])
        self.assertEqual(b.amend('s1'), [('amended', 's1', '10.00', 2)])
        self.assertEqual(b.depth('sell'), [('10.00', 7, 2)])
        self.assertEqual(b.submit(L('b', 'buy', 3, '10')), [('accepted', 'b'), ('trade', 'b', 's1', '10.00', 2), ('trade', 'b', 's2', '10.00', 1)])

    def test_amend_up_or_new_price_loses_priority(self):
        b = book_with(L('s1', 'sell', 5, '10'), L('s2', 'sell', 5, '10'), L('s3', 'sell', 1, '11'))
        self.assertEqual(b.amend('s1', qty=6), [('amended', 's1', '10.00', 6), ('rested', 's1', '10.00', 6)])
        self.assertEqual(b.amend('s3', price='10'), [('amended', 's3', '10.00', 1), ('rested', 's3', '10.00', 1)])
        self.assertEqual(b.submit(L('b', 'buy', 12, '10')),
                         [('accepted', 'b'), ('trade', 'b', 's2', '10.00', 5), ('trade', 'b', 's1', '10.00', 6), ('trade', 'b', 's3', '10.00', 1)])

    def test_amend_that_crosses_trades(self):
        b = book_with(L('s1', 'sell', 3, '10', 'A'), L('s2', 'sell', 3, '11', 'B'), L('b1', 'buy', 5, '9', 'B'))
        self.assertEqual(b.amend('b1', price='11'),
                         [('amended', 'b1', '11.00', 5), ('trade', 'b1', 's1', '10.00', 3), ('cancelled', 's2', 3, 'stp'),
                          ('rested', 'b1', '11.00', 2)])
        self.assertEqual((b.depth('buy'), b.depth('sell'), b.last_price), ([('11.00', 2, 1)], [], '10.00'))

    def test_amend_rejections(self):
        b = book_with(L('s1', 'sell', 5, '10'), S('t', 'buy', 1, '50'))
        b.submit(L('f', 'buy', 5, '10'))
        for args in [('s1', {}), ('t', {'qty': 2}), ('nope', {'qty': 1})]:
            self.assertEqual(b.amend(args[0], **args[1]), [('rejected', args[0], 'unknown')])
        b.submit(L('s2', 'sell', 5, '12'))
        for kw in [{'qty': 0}, {'qty': -2}, {'qty': True}, {'price': '0'}, {'price': '12.345'}, {'price': 12}]:
            self.assertEqual(b.amend('s2', **kw), [('rejected', 's2', 'invalid')], kw)
        self.assertEqual(b.depth('sell'), [('12.00', 5, 1)])


class Stops(Limited):
    def test_trigger_order_and_cascade(self):
        b = book_with(L('s1', 'sell', 2, '100'), L('s2', 'sell', 2, '101'), L('s3', 'sell', 2, '102'), L('s4', 'sell', 5, '103'))
        self.assertEqual(b.submit(S('t1', 'buy', 2, '101')), [('accepted', 't1')])
        self.assertEqual(b.submit(S('t2', 'buy', 3, '100')), [('accepted', 't2')])
        self.assertEqual(b.submit(S('t3', 'sell', 1, '50')), [('accepted', 't3')])
        self.assertEqual(b.submit(L('b1', 'buy', 2, '100')),
                         [('accepted', 'b1'), ('trade', 'b1', 's1', '100.00', 2), ('triggered', 't2'), ('trade', 't2', 's2', '101.00', 2),
                          ('trade', 't2', 's3', '102.00', 1), ('triggered', 't1'), ('trade', 't1', 's3', '102.00', 1),
                          ('trade', 't1', 's4', '103.00', 1)])
        self.assertEqual((b.depth('sell'), b.last_price), ([('103.00', 4, 1)], '103.00'))
        self.assertEqual(b.submit(S('t4', 'buy', 1, '99')), [('accepted', 't4'), ('triggered', 't4'), ('trade', 't4', 's4', '103.00', 1)])

    def test_several_triggered_run_earliest_submitted_first(self):
        b = book_with(L('s1', 'sell', 1, '10'), L('s2', 'sell', 9, '20'))
        b.submit(S('late_low', 'buy', 1, '15'))
        b.submit(S('early_high', 'buy', 1, '18'))
        b.cancel('late_low')
        b.submit(S('t3', 'buy', 1, '19'))
        b.submit(S('t4', 'buy', 1, '12'))
        self.assertEqual(b.submit(L('b', 'buy', 2, '20')),
                         [('accepted', 'b'), ('trade', 'b', 's1', '10.00', 1), ('trade', 'b', 's2', '20.00', 1),
                          ('triggered', 'early_high'), ('trade', 'early_high', 's2', '20.00', 1),
                          ('triggered', 't3'), ('trade', 't3', 's2', '20.00', 1),
                          ('triggered', 't4'), ('trade', 't4', 's2', '20.00', 1)])

    def test_stop_without_liquidity_and_sell_trigger(self):
        b = book_with(L('s1', 'sell', 1, '10'), L('bb', 'buy', 5, '5'))
        b.submit(S('up', 'buy', 2, '10'))
        b.submit(S('down', 'sell', 3, '9'))
        self.assertEqual(b.submit(L('b', 'buy', 1, '10')),
                         [('accepted', 'b'), ('trade', 'b', 's1', '10.00', 1), ('triggered', 'up'), ('cancelled', 'up', 2, 'market')])
        self.assertEqual(b.submit(L('x', 'sell', 1, '5')),
                         [('accepted', 'x'), ('trade', 'x', 'bb', '5.00', 1), ('triggered', 'down'), ('trade', 'down', 'bb', '5.00', 3)])

    def test_sell_stops_and_cascade_down(self):
        b = book_with(L('b1', 'buy', 1, '50'), L('b2', 'buy', 1, '48'), L('b3', 'buy', 5, '40'))
        b.submit(S('st1', 'sell', 1, '50'))
        b.submit(S('st2', 'sell', 2, '48'))
        b.submit(S('st3', 'sell', 1, '30'))
        self.assertEqual(b.submit(M('m', 'sell', 1)),
                         [('accepted', 'm'), ('trade', 'm', 'b1', '50.00', 1), ('triggered', 'st1'), ('trade', 'st1', 'b2', '48.00', 1),
                          ('triggered', 'st2'), ('trade', 'st2', 'b3', '40.00', 2)])
        self.assertEqual(b.cancel('st3'), [('cancelled', 'st3', 1, 'user')])

    def test_stop_self_trade_prevention_and_no_depth(self):
        b = book_with(L('a', 'sell', 2, '10', 'A'), L('n', 'sell', 2, '11'))
        b.submit(S('t', 'buy', 3, '10', 'A'))
        self.assertEqual(b.depth('buy'), [])
        self.assertEqual(b.submit(L('x', 'buy', 1, '10', 'B')),
                         [('accepted', 'x'), ('trade', 'x', 'a', '10.00', 1), ('triggered', 't'), ('cancelled', 'a', 1, 'stp'),
                          ('trade', 't', 'n', '11.00', 2), ('cancelled', 't', 1, 'market')])

    def test_amend_can_trigger_stops(self):
        b = book_with(L('s', 'sell', 1, '10'), L('s2', 'sell', 1, '12'), L('b', 'buy', 1, '9'))
        b.submit(S('t', 'buy', 1, '10'))
        self.assertEqual(b.amend('b', price='10'),
                         [('amended', 'b', '10.00', 1), ('trade', 'b', 's', '10.00', 1), ('triggered', 't'), ('trade', 't', 's2', '12.00', 1)])


class AgainstModel(Limited):
    LIMIT = 120

    def run_seeds(self, seeds):
        for seed in seeds:
            rnd = random.Random(seed)
            b, m = Book(), OracleBook()
            for n in range(1, 301):
                live = [r['id'] for r in m.resting] + [s['id'] for s in m.stops]
                a = random_action(rnd, n, live)
                if a[0] == 'submit':
                    got, exp = b.submit(dict(a[1])), m.submit(dict(a[1]))
                elif a[0] == 'cancel':
                    got, exp = b.cancel(a[1]), m.cancel(a[1])
                else:
                    got, exp = b.amend(a[1], qty=a[2], price=a[3]), m.amend(a[1], qty=a[2], price=a[3])
                where = (seed, n, a)
                self.assertEqual(got, exp, where)
                self.assertEqual(b.depth('buy', 10), m.depth('buy', 10), where)
                self.assertEqual(b.depth('sell', 10), m.depth('sell', 10), where)
                self.assertEqual(b.last_price, m.last_price(), where)

    def test_random_operations_1(self):
        self.run_seeds(range(0, 80))

    def test_random_operations_2(self):
        self.run_seeds(range(1000, 1080))

    def test_random_operations_3(self):
        self.run_seeds(range(2000, 2080))


class Speed(Limited):
    LIMIT = 120

    def test_200000_operations(self):
        setup = '''
import random
from book import Book
rnd = random.Random(7)
ops, ids = [], []
for n in range(200000):
    r = rnd.random()
    if r < 0.2 and ids:
        ops.append(('cancel', ids[rnd.randrange(len(ids))]))
        continue
    if r < 0.25 and ids:
        ops.append(('amend', ids[rnd.randrange(len(ids))], rnd.randint(1, 50)))
        continue
    side = rnd.choice(['buy', 'sell'])
    off = rnd.randint(-1000, 1000) + (150 if side == 'buy' else -150) * (rnd.random() < 0.1)
    p = 10000 + off if side == 'buy' else 10000 - off
    o = {'id': f'x{n}', 'side': side, 'type': 'limit', 'qty': rnd.randint(1, 100), 'price': '%d.%02d' % (p // 100, p % 100),
         'owner': rnd.choice('ABCDEFGH')}
    if r > 0.97:
        o = {'id': f'x{n}', 'side': side, 'type': 'market', 'qty': rnd.randint(1, 300)}
    ids.append(o['id'])
    ops.append(('submit', o))
b = Book()
'''
        work = '''
trades = 0
for op in ops:
    if op[0] == 'submit':
        ev = b.submit(op[1])
    elif op[0] == 'cancel':
        ev = b.cancel(op[1])
    else:
        ev = b.amend(op[1], qty=op[2])
    trades += sum(1 for e in ev if e[0] == 'trade')
'''
        check = '''
assert trades == 121081, trades
assert b.depth('buy', 3) == [('97.12', 56, 1), ('96.88', 10, 1), ('96.49', 23, 1)], b.depth('buy', 3)
assert b.last_price == '106.33', b.last_price
'''
        self.timed(setup, work, 6, check)


if __name__ == '__main__':
    unittest.main()
