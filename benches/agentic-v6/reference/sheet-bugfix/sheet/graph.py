"""Which cells read which, and in what order to recalculate.

Every cell key is (col, row). precedents[k] are the cells k reads (single references and every cell of
its ranges); dependents[k] is the reverse. The algorithms here are iterative: sheets can hold chains of
thousands of formulas, deeper than Python's recursion limit.
"""


class Graph:
    def __init__(self):
        self.precedents = {}
        self.dependents = {}

    def set_precedents(self, key, keys):
        for p in self.precedents.pop(key, ()):
            deps = self.dependents.get(p)
            if deps is not None:
                deps.discard(key)
                if not deps:
                    del self.dependents[p]
        keys = set(keys)
        if keys:
            self.precedents[key] = keys
            for p in keys:
                self.dependents.setdefault(p, set()).add(key)

    def clear(self):
        self.precedents.clear()
        self.dependents.clear()

    def affected(self, starts):
        """The start cells and every cell that depends on them, directly or not."""
        seen = set(starts)
        stack = list(starts)
        while stack:
            k = stack.pop()
            for d in self.dependents.get(k, ()):
                if d not in seen:
                    seen.add(d)
                    stack.append(d)
        return seen

    def plan(self, keys):
        """Recalculation plan for a set of cells closed under dependents: a list of (cells, cyclic) groups in an
        order where every cell comes after the cells it reads. A cyclic group is a set of cells that read each
        other in a loop (a cell reading itself counts); a non-cyclic group is a single cell."""
        keys = set(keys)
        index, low, on_stack, stack, groups = {}, {}, set(), [], []
        counter = 0
        for root in sorted(keys):
            if root in index:
                continue
            work = [(root, iter(sorted(self.dependents.get(root, set()) & keys)))]
            index[root] = low[root] = counter
            counter += 1
            stack.append(root)
            on_stack.add(root)
            while work:
                node, it = work[-1]
                nxt = next(it, None)
                if nxt is not None:
                    if nxt not in index:
                        index[nxt] = low[nxt] = counter
                        counter += 1
                        stack.append(nxt)
                        on_stack.add(nxt)
                        work.append((nxt, iter(sorted(self.dependents.get(nxt, set()) & keys))))
                    elif nxt in on_stack:
                        low[node] = min(low[node], index[nxt])
                    continue
                work.pop()
                if work:
                    parent = work[-1][0]
                    low[parent] = min(low[parent], low[node])
                if low[node] == index[node]:
                    group = set()
                    while True:
                        k = stack.pop()
                        on_stack.discard(k)
                        group.add(k)
                        if k == node:
                            break
                    cyclic = len(group) > 1 or node in self.precedents.get(node, ())
                    groups.append((group, cyclic))
        groups.reverse()   # Tarjan finds the groups that are read last first
        return groups
