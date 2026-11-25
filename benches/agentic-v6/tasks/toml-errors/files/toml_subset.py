"""A parser for TOML 1.0 without dates and times, with exact error positions."""


class TOMLError(Exception):
    def __init__(self, kind, line, col):
        super().__init__(f'{kind} error at line {line}, column {col}')
        self.kind, self.line, self.col = kind, line, col


def loads(text):
    raise NotImplementedError
