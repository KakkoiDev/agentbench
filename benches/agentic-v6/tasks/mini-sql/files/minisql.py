"""A small SQL SELECT engine over in-memory tables."""


class SQLError(Exception):
    pass


def execute(sql, tables):
    raise NotImplementedError
