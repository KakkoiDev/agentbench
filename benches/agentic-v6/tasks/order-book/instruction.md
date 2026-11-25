Write `book.py`: a single-instrument limit order book matching engine (Python 3 standard library only).

## API

```python
b = Book()
b.submit(order) -> list of events
b.cancel(order_id) -> list of events
b.amend(order_id, qty=None, price=None) -> list of events
b.depth(side, levels=5) -> [(price, total_qty, order_count), ...]   # side 'buy' or 'sell', best level first
b.last_price   # price of the most recent trade (string), or None
```

An order is a dict: `id` (non-empty string, never used before by an accepted order), `side` (`'buy'`/`'sell'`), `type` (`'limit'`, `'market'` or `'stop'`), `qty` (int > 0; `bool` is not an int here), `price` (limit orders only), `stop` (stop orders only), `tif` (optional), `owner` (optional string). Prices are strings matching `\d+(\.\d{1,2})?` with a value > 0 (`'100'`, `'99.5'`, `'0.01'`); in events and depth they are always written with exactly two decimals (`'100.00'`, `'99.50'`). Use exact arithmetic (no floats).

## Events (tuples, in the order they happen)

- `('accepted', id)` - first event of every valid submitted order.
- `('rejected', id, reason)` - the only event when an order is refused: reason `'invalid'` for an invalid order, `'fok'` for a fill-or-kill order that cannot be filled completely; from `cancel`/`amend`: `'unknown'` if the id is not a resting limit order (or, for cancel, a pending stop order), `'invalid'` for a bad new qty/price in `amend`. Rejected orders leave no trace (their id may be used again). For an order without a usable `id` the event carries whatever `order.get('id')` gives (`None` if absent).
- `('trade', taker_id, maker_id, price, qty)` - always at the maker's (resting order's) price.
- `('rested', id, price, qty)` - a limit order (or its remainder) is now resting on the book with qty.
- `('cancelled', id, qty, reason)` - qty is what was left; reasons: `'ioc'` (unfilled rest of an IOC limit order), `'market'` (unfilled rest of a market order or of a triggered stop order), `'stp'` (a resting order removed by self-trade prevention), `'user'` (by `cancel`).
- `('triggered', id)` - a stop order starts to execute.
- `('amended', id, price, qty)` - first event of a successful amend, with the new price and quantity.

## Validity

An order is `'invalid'` unless: it is a dict; `id` is a non-empty string not used by any earlier accepted order (even one that is finished); `side` is buy/sell; `qty` is an int > 0; `owner`, if present and not None, is a string; and by type: **limit** has a valid `price`, no `stop` key, `tif` absent/None or one of `'GTC'`, `'IOC'`, `'FOK'`; **market** has no `price` or `stop` key and `tif` absent/None, `'IOC'` or `'FOK'`; **stop** has a valid `stop` price, no `price` key, and no `tif` (absent or None). Other types are invalid.

## Matching

- Price-time priority: an incoming buy matches resting sells from the lowest price up (a limit buy only at prices <= its limit); an incoming sell matches resting buys from the highest price down (>= its limit). Within a price, the order that started resting earliest goes first. Each match trades min(remaining taker qty, maker qty); a maker that reaches 0 leaves the book.
- Self-trade prevention: if the next maker has the same owner as the taker (owners that are absent/None never match each other), the maker is removed instead - `('cancelled', maker_id, maker_qty, 'stp')` - and matching continues with the next maker.
- After matching: a limit `GTC` order (the default for limit) rests with what is left (`'rested'`); a limit `IOC` order cancels what is left (`'ioc'`); a market order (default tif IOC) cancels what is left (`'market'`). Nothing is emitted for a taker that filled completely.
- `FOK` (limit or market): before anything happens, add up the quantity of the resting opposite orders it could match at acceptable prices, not counting orders of its own owner. If that is less than its qty the result is just `[('rejected', id, 'fok')]` and nothing changes (no STP cancellations, the id stays unused). Otherwise it executes normally (and fills completely; same-owner makers it meets are still STP-cancelled).

## Stop orders

A stop order is accepted (`'accepted'`) and waits off the book (not in `depth`). A buy stop triggers when the last trade price is >= its stop price, a sell stop when the last trade price is <= its stop price. Triggering is checked at the end of every `submit` and `amend` (once the order itself has finished, including when the submitted order is the stop itself and the last trade price already satisfies it) and again after each triggered order has executed. All pending stops whose condition holds at a check become triggered; triggered stops execute one at a time, the earliest submitted first; each executes like a market order (`('triggered', id)`, then its trades and STP cancellations, then `('cancelled', id, left, 'market')` if not completely filled). Trades made by a triggered order can trigger further stops (checked after it finishes), which join the triggered ones waiting, again earliest submitted first. A stop, once triggered, executes even if the price moves back. `cancel` removes a pending (untriggered) stop with reason `'user'`.

## Cancel and amend

`cancel(id)` removes a resting limit order or a pending stop: `[('cancelled', id, remaining_qty, 'user')]`.

`amend(id, qty=None, price=None)` changes a resting limit order (None keeps the current value; `qty` is the new remaining quantity). Invalid new values -> `('rejected', id, 'invalid')`, nothing changes. If the price is unchanged and the new qty is not larger, the order keeps its place in the queue and the result is just `[('amended', id, price, qty)]`. Otherwise (larger qty or another price) the order loses its place: it leaves the book and is entered again as a new GTC limit order with the same id, owner and side: `('amended', ...)`, then any trades/STP cancellations if the new price crosses the book, then `('rested', id, price, left)` if anything is left; then stop triggers are checked.

## depth

`depth(side, levels)`: the best `levels` price levels of that side (bids highest first, asks lowest first) as `(price, total resting qty, number of resting orders)`.

## Speed

200,000 operations (mostly limit orders over 2,000 price levels, plus cancels, amends and market orders) must run in a few seconds: finding the best price must not scan every level or every order.

Make sure `python3 -m unittest` passes. The grading tests check exact event lists in many scenarios and compare everything (events, depth, last price) with a simple model of these rules over thousands of random operations.
