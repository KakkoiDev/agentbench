"""A dependency-aware task runner on a pool of threads."""


class CycleError(ValueError):
    def __init__(self, cycle):
        super().__init__('dependency cycle: ' + ' -> '.join(cycle))
        self.cycle = cycle


class Scheduler:
    def __init__(self, workers=1):
        raise NotImplementedError

    def add(self, name, fn, deps=(), priority=0, group=None, retries=0):
        raise NotImplementedError

    def limit(self, group, n):
        raise NotImplementedError

    def cancel(self, name):
        raise NotImplementedError

    def run(self):
        raise NotImplementedError
