"""A multi-version transactional key-value store."""


class Conflict(Exception):
    def __init__(self, key):
        super().__init__(f'conflict on {key!r}')
        self.key = key


class TxnClosed(Exception):
    pass


class InvalidSavepoint(Exception):
    pass


class Store:
    def begin(self, serializable=False):
        raise NotImplementedError

    def get(self, key):
        raise NotImplementedError

    def gc(self):
        raise NotImplementedError

    def version_count(self):
        raise NotImplementedError
