"""A regular expression engine with Python re's matching rules and no backtracking blow-up."""


class RegexError(Exception):
    pass


def compile(pattern):
    raise NotImplementedError


def search(pattern, text):
    raise NotImplementedError


def fullmatch(pattern, text):
    raise NotImplementedError


def find_all(pattern, text):
    raise NotImplementedError
