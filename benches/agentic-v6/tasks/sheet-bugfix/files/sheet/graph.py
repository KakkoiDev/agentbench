"""Which cells read which, and in what order to recalculate.

Every cell key is (col, row). precedents[k] are the cells k reads (single references and every cell of
its ranges); dependents[k] is the reverse; refs[k] the cells k names directly. The algorithms here are
iterative: sheets can hold chains of thousands of formulas, deeper than Python's recursion limit.
"""


class Graph:
    def __init__(self):
        self.precedents = {}
        self.dependents = {}
        self.refs = {}

    def set_precedents(self, key, keys, refs=()):
        """keys: every cell `key` reads; refs: the cells it names directly (not through a range)."""
        for p in self.precedents.pop(key, ()):
            deps = self.dependents.get(p)
            if deps is not None:
                deps.discard(key)
                if not deps:
                    del self.dependents[p]
        self.refs.pop(key, None)
        keys = set(keys)
        if keys:
            self.precedents[key] = keys
            for p in keys:
                self.dependents.setdefault(p, set()).add(key)
        if refs:
            self.refs[key] = set(refs)

    def clear(self):
        self.precedents.clear()
        self.dependents.clear()
        self.refs.clear()

    def in_cycle(self, key):
        """True if following references from `key` leads back to it."""
        stack, seen = list(self.refs.get(key, ())), set()
        while stack:
            k = stack.pop()
            if k == key:
                return True
            if k in seen:
                continue
            seen.add(k)
            stack.extend(self.refs.get(k, ()))
        return False

    def plan(self, starts):
        """Recalculation plan after the cells `starts` changed: a list of (cells, cyclic) groups, one cell each:
        the changed cells and everything that depends on them, each once."""
        order, seen = [], set()
        stack = sorted(starts, reverse=True)
        while stack:
            k = stack.pop()
            if k in seen:
                continue
            seen.add(k)
            order.append(({k}, self.in_cycle(k)))
            stack.extend(sorted(self.dependents.get(k, ()), reverse=True))
        return order
