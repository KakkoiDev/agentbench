"""A limit order book with price-time priority, IOC/FOK, self-trade prevention, amends and stop orders."""
import heapq
import re
from collections import deque

_PRICE = re.compile(r'\d+(\.\d{1,2})?\Z')


def _cents(text):
    if not isinstance(text, str) or not _PRICE.match(text):
        return None
    whole, _, frac = text.partition('.')
    v = int(whole) * 100 + int((frac + '00')[:2])
    return v if v > 0 else None


def _fmt(c):
    return f'{c // 100}.{c % 100:02d}'


class _Order:
    __slots__ = ('id', 'side', 'price', 'qty', 'owner', 'seq', 'live')

    def __init__(self, oid, side, price, qty, owner):
        self.id, self.side, self.price, self.qty, self.owner = oid, side, price, qty, owner
        self.seq, self.live = 0, True


class Book:
    def __init__(self):
        self._levels = {'buy': {}, 'sell': {}}      # price -> deque of orders (may hold dead ones)
        self._level_qty = {'buy': {}, 'sell': {}}   # price -> (total live qty, live count)
        self._heaps = {'buy': [], 'sell': []}       # buy: -price, sell: price (lazy)
        self._resting = {}                          # id -> live resting order
        self._stops = {}                            # id -> (seq, side, stop cents, qty, owner) of pending stops
        self._stop_heaps = {'buy': [], 'sell': []}  # (stop or -stop, seq, id): the closest to triggering first (lazy)
        self._fired = []                            # (seq, id) triggered and waiting to run
        self._ids = set()
        self._seq = 0
        self.last_price = None
        self._last = None

    # ---- book structure
    def _best(self, side):
        h = self._heaps[side]
        lv = self._level_qty[side]
        while h:
            p = -h[0] if side == 'buy' else h[0]
            if lv.get(p, (0, 0))[1] > 0:
                return p
            heapq.heappop(h)
            self._levels[side].pop(p, None)
            lv.pop(p, None)
        return None

    def _add(self, o):
        self._seq += 1
        o.seq = self._seq
        side, p = o.side, o.price
        if p not in self._levels[side]:
            self._levels[side][p] = deque()
            heapq.heappush(self._heaps[side], -p if side == 'buy' else p)
        self._levels[side][p].append(o)
        q, c = self._level_qty[side].get(p, (0, 0))
        self._level_qty[side][p] = (q + o.qty, c + 1)
        self._resting[o.id] = o

    def _remove(self, o):
        o.live = False
        del self._resting[o.id]
        q, c = self._level_qty[o.side][o.price]
        self._level_qty[o.side][o.price] = (q - o.qty, c - 1)

    def _front(self, side, p):
        dq = self._levels[side][p]
        while dq and not dq[0].live:
            dq.popleft()
        return dq[0]

    @staticmethod
    def _crosses(side, limit, p):
        return limit is None or (p <= limit if side == 'buy' else p >= limit)

    # ---- matching
    def _available(self, side, limit, owner, need):
        """Quantity a taker could fill now (own orders excluded), counting only until `need` is reached."""
        opp = 'sell' if side == 'buy' else 'buy'
        total = 0
        for p in sorted((p for p, (q, c) in self._level_qty[opp].items() if c > 0), reverse=(opp == 'buy')):
            if not self._crosses(side, limit, p):
                break
            for o in self._levels[opp][p]:
                if o.live and not (owner is not None and o.owner == owner):
                    total += o.qty
                    if total >= need:
                        return total
        return total

    def _match(self, taker_id, side, limit, qty, owner, ev):
        opp = 'sell' if side == 'buy' else 'buy'
        while qty > 0:
            p = self._best(opp)
            if p is None or not self._crosses(side, limit, p):
                break
            m = self._front(opp, p)
            if owner is not None and m.owner == owner:
                self._remove(m)
                ev.append(('cancelled', m.id, m.qty, 'stp'))
                continue
            n = min(qty, m.qty)
            ev.append(('trade', taker_id, m.id, _fmt(p), n))
            self._last = p
            self.last_price = _fmt(p)
            qty -= n
            m.qty -= n
            lq, lc = self._level_qty[opp][p]
            if m.qty == 0:
                m.live = False
                del self._resting[m.id]
                self._level_qty[opp][p] = (lq - n, lc - 1)
            else:
                self._level_qty[opp][p] = (lq - n, lc)
        return qty

    def _triggers(self, ev):
        """Move stops whose condition holds to the triggered queue; run the oldest triggered one; repeat."""
        while True:
            if self._last is not None:
                for heap, side in ((self._stop_heaps['buy'], 'buy'), (self._stop_heaps['sell'], 'sell')):
                    while heap:
                        key, seq, sid = heap[0]
                        if sid not in self._stops:
                            heapq.heappop(heap)
                            continue
                        stop = key if side == 'buy' else -key
                        if (side == 'buy' and self._last >= stop) or (side == 'sell' and self._last <= stop):
                            heapq.heappop(heap)
                            heapq.heappush(self._fired, (seq, sid))
                        else:
                            break
            if not self._fired:
                return
            seq, sid = heapq.heappop(self._fired)
            _, side, stop, qty, owner = self._stops.pop(sid)
            ev.append(('triggered', sid))
            left = self._match(sid, side, None, qty, owner, ev)
            if left:
                ev.append(('cancelled', sid, left, 'market'))

    # ---- API
    def submit(self, order):
        oid = order.get('id') if isinstance(order, dict) else None
        if not self._valid(order):
            return [('rejected', oid, 'invalid')]
        side, kind, qty, owner = order['side'], order['type'], order['qty'], order.get('owner')
        limit = _cents(order['price']) if kind == 'limit' else None
        tif = order.get('tif') or ('GTC' if kind == 'limit' else 'IOC')
        if tif == 'FOK' and kind != 'stop' and self._available(side, limit, owner, qty) < qty:
            return [('rejected', oid, 'fok')]
        self._ids.add(oid)
        ev = [('accepted', oid)]
        if kind == 'stop':
            self._seq += 1
            stop = _cents(order['stop'])
            self._stops[oid] = (self._seq, side, stop, qty, owner)
            heapq.heappush(self._stop_heaps[side], (stop if side == 'buy' else -stop, self._seq, oid))
            self._triggers(ev)
            return ev
        left = self._match(oid, side, limit, qty, owner, ev)
        if left:
            if kind == 'limit' and tif == 'GTC':
                self._add(_Order(oid, side, limit, left, owner))
                ev.append(('rested', oid, _fmt(limit), left))
            else:
                ev.append(('cancelled', oid, left, 'ioc' if kind == 'limit' else 'market'))
        self._triggers(ev)
        return ev

    def _valid(self, o):
        if not isinstance(o, dict):
            return False
        oid = o.get('id')
        if not isinstance(oid, str) or not oid or oid in self._ids:
            return False
        if o.get('side') not in ('buy', 'sell'):
            return False
        q = o.get('qty')
        if not isinstance(q, int) or isinstance(q, bool) or q <= 0:
            return False
        owner = o.get('owner')
        if owner is not None and not isinstance(owner, str):
            return False
        kind = o.get('type')
        tif = o.get('tif')
        if kind == 'limit':
            return _cents(o.get('price')) is not None and tif in (None, 'GTC', 'IOC', 'FOK') and 'stop' not in o
        if kind == 'market':
            return 'price' not in o and 'stop' not in o and tif in (None, 'IOC', 'FOK')
        if kind == 'stop':
            return _cents(o.get('stop')) is not None and 'price' not in o and tif is None
        return False

    def cancel(self, oid):
        o = self._resting.get(oid)
        if o is not None:
            self._remove(o)
            return [('cancelled', oid, o.qty, 'user')]
        if oid in self._stops:
            st = self._stops.pop(oid)
            return [('cancelled', oid, st[3], 'user')]
        return [('rejected', oid, 'unknown')]

    def amend(self, oid, qty=None, price=None):
        o = self._resting.get(oid)
        if o is None:
            return [('rejected', oid, 'unknown')]
        new_qty = o.qty if qty is None else qty
        new_price = o.price if price is None else _cents(price)
        if new_price is None or not isinstance(new_qty, int) or isinstance(new_qty, bool) or new_qty <= 0:
            return [('rejected', oid, 'invalid')]
        ev = [('amended', oid, _fmt(new_price), new_qty)]
        if new_price == o.price and new_qty <= o.qty:
            lq, lc = self._level_qty[o.side][o.price]
            self._level_qty[o.side][o.price] = (lq - (o.qty - new_qty), lc)
            o.qty = new_qty
            return ev
        self._remove(o)
        left = self._match(oid, o.side, new_price, new_qty, o.owner, ev)
        if left:
            n = _Order(oid, o.side, new_price, left, o.owner)
            self._add(n)
            ev.append(('rested', oid, _fmt(new_price), left))
        self._triggers(ev)
        return ev

    def depth(self, side, levels=5):
        lv = self._level_qty[side]
        prices = sorted((p for p, (q, c) in lv.items() if c > 0), reverse=(side == 'buy'))[:levels]
        return [(_fmt(p), lv[p][0], lv[p][1]) for p in prices]
