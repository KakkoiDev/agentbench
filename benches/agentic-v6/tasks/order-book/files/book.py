"""A limit order book matching engine."""


class Book:
    def __init__(self):
        self.last_price = None

    def submit(self, order):
        raise NotImplementedError

    def cancel(self, order_id):
        raise NotImplementedError

    def amend(self, order_id, qty=None, price=None):
        raise NotImplementedError

    def depth(self, side, levels=5):
        raise NotImplementedError
