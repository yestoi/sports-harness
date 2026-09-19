"""§1.5: portfolio-level print-volume conservation, the layer *above* the shared simulator.

`fills.simulate_fills` conserves volume per order track: each hypothetical order claims from its
own ledger, so two counterfactual orders on the same key both see the whole print. That is how
one recorded 10-contract trade came to be credited to 45 orders for 346.60 contracts. This
module adds the missing layer and **modifies nothing below it**: `PortfolioLedger` is keyed
`(run_id, arm, variant)` -- the economic portfolio identity -- and, for each recorded print
`(ticker, taker_side, trade_id)`, holds how many contracts that portfolio has already taken.

Ruling I13(b): nothing here re-implements the simulator's own depth, priority or
trade/delta reconciliation. The ledger **caps what the simulator has already decided a track may
take** and holds no market state of its own; `tests/test_exp_liquidity.py` asserts that by
reading this file's text.

What the caller owns:

* **Allocation order.** Inside one instant the caller allocates by `(placed_at, the arm's own
  order id)` (§1.5). Two orders asking in a different order would divide the same print
  differently, so the order is part of the contract rather than an accident of dict iteration.
* **The decision to truncate or drop.** `allocate` returns the grant; the runner truncates or
  drops the `SimFill` accordingly.

Never replenished: a cancel, a re-placement at a new price, a retry and a resume all leave the
consumed quantity consumed. `release` therefore forgets one order's claim without returning any
contracts to the pool, and `restore` is idempotent -- a resumed run re-reads the same rows and
allocates nothing twice, because the allocation is keyed by the venue's own trade id.

Two portfolios are never summed: `as_rows` emits one row per portfolio identity, and no
attribute of this class holds a total across identities (§1.6a; `report.py` refuses such a row).
"""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal

ZERO = Decimal("0")


@dataclass(frozen=True, slots=True)
class LedgerKey:
    """One economic portfolio: a run's arm running one variant (§1.5)."""

    run_id: str
    arm: str
    variant: str


def _print_key(ticker: str, taker_side: str, trade_id: str) -> tuple[str, str, str]:
    return (ticker, taker_side, str(trade_id))


class PortfolioLedger:
    """How much of each recorded print each portfolio has already taken."""

    def __init__(self) -> None:
        #: (ticker, taker_side, trade_id) -> the print's own recorded size.
        self._available: dict[tuple[str, str, str], Decimal] = {}
        #: (LedgerKey, ticker, taker_side, trade_id) -> contracts this portfolio has taken.
        self._allocated: dict[tuple[LedgerKey, str, str, str], Decimal] = {}
        #: The same key plus an order id -> that order's own grant. Diagnostic only: it never
        #: enters `as_rows`, and releasing an entry returns nothing to `_allocated`.
        self._by_order: dict[tuple[LedgerKey, str, str, str, int], Decimal] = {}

    # --- the tape's side -------------------------------------------------------------

    def observe(self, ticker: str, taker_side: str, trade_id: str, count: Decimal) -> None:
        """One recorded print, once. A repeated `trade_id` is the same trade, not a new one."""
        key = _print_key(ticker, taker_side, trade_id)
        if key in self._available:
            return
        self._available[key] = Decimal(str(count))

    # --- the portfolio's side --------------------------------------------------------

    def allocate(self, key: LedgerKey, ticker: str, taker_side: str, trade_id: str,
                 order_id: int, wanted: Decimal) -> Decimal:
        """Grant `min(wanted, available - allocated)`, never less than nothing.

        A print this ledger has never seen grants nothing: the run allocates from the recorded
        tape, and an unrecorded trade is not evidence that contracts existed.
        """
        pkey = _print_key(ticker, taker_side, trade_id)
        available = self._available.get(pkey)
        if available is None:
            return ZERO
        akey = (key, *pkey)
        allocated = self._allocated.get(akey, ZERO)
        grant = min(Decimal(str(wanted)), available - allocated)
        if grant <= ZERO:
            return ZERO
        self._allocated[akey] = allocated + grant
        okey = (key, *pkey, int(order_id))
        self._by_order[okey] = self._by_order.get(okey, ZERO) + grant
        return grant

    def release(self, key: LedgerKey, ticker: str, taker_side: str, trade_id: str,
                order_id: int) -> None:
        """A cancel. The order's claim is forgotten; the contracts stay consumed (§1.5)."""
        self._by_order.pop((key, *_print_key(ticker, taker_side, trade_id), int(order_id)), None)

    def release_order(self, key: LedgerKey, order_id: int) -> None:
        """`release` for every print one order claimed, without naming them.

        The caller that closes an order knows the order, not the prints it happened to take
        from, so the ledger answers that question from `_by_order`. Exactly as `release`, this
        returns **no contracts to the pool**: `_allocated` is untouched, which is what makes a
        cancel, a re-placement and a retry all leave the consumed quantity consumed (§1.5).
        """
        order_id = int(order_id)
        for okey in [k for k in self._by_order if k[0] == key and k[4] == order_id]:
            del self._by_order[okey]

    def taken_by(self, key: LedgerKey, order_id: int) -> Decimal:
        """What one order has been granted so far. Diagnostic, never an input to a grant."""
        return sum((value for okey, value in self._by_order.items()
                    if okey[0] == key and okey[4] == int(order_id)), ZERO)

    # --- persistence (§1.4: part of the checkpoint) ------------------------------------

    def as_rows(self) -> list[dict]:
        """One row per portfolio identity and print: `exp_allocation`'s own columns, plus the
        two the print key needs (`ticker`, `taker_side`), which `restore` reads back and
        `storage.write_allocations` projects away."""
        rows = []
        for (key, ticker, taker_side, trade_id), allocated in self._allocated.items():
            rows.append({"run_id": key.run_id, "arm_id": key.arm, "variant_id": key.variant,
                         "source_trade_id": trade_id, "ticker": ticker,
                         "taker_side": taker_side,
                         "available": self._available[(ticker, taker_side, trade_id)],
                         "allocated": allocated})
        rows.sort(key=lambda row: (row["run_id"], row["arm_id"], row["variant_id"],
                                   row["source_trade_id"]))
        return rows

    def observed_rows(self) -> list[dict]:
        """Every print this ledger has seen, taken from or not, with its recorded size."""
        rows = [{"ticker": ticker, "taker_side": taker_side, "source_trade_id": trade_id,
                 "available": available}
                for (ticker, taker_side, trade_id), available in self._available.items()]
        rows.sort(key=lambda row: (row["ticker"], row["taker_side"], row["source_trade_id"]))
        return rows

    def as_state(self) -> dict:
        """The **whole** ledger for `exp_checkpoint.state` (§1.4).

        Both halves, because they answer different questions: `allocated` is what each
        portfolio has taken, and `observed` is every print the run has seen -- including the
        ones nothing has taken from yet. A checkpoint carrying only the first would forget a
        print observed before the boundary, and an order hit by that print after the boundary
        would be granted nothing at all (fix round 1, ruling D23/I1).
        """
        return {"observed": self.observed_rows(), "allocated": self.as_rows()}

    def restore_state(self, state) -> None:
        """The inverse of `as_state`, and idempotent for the same reason `restore` is.

        A plain list is accepted as the `allocated` half alone, which is what `as_rows()`
        returned before the whole ledger was carried.
        """
        if isinstance(state, Sequence) and not isinstance(state, (str, bytes, dict)):
            self.restore(state)
            return
        for row in state.get("observed") or []:
            self._available[_print_key(row["ticker"], row["taker_side"],
                                       row["source_trade_id"])] = Decimal(str(row["available"]))
        self.restore(state.get("allocated") or [])

    def restore(self, rows: Sequence[dict]) -> None:
        """Rebuild from `as_rows()`. Idempotent: the same rows restored twice consume once."""
        for row in rows:
            pkey = _print_key(row["ticker"], row["taker_side"], row["source_trade_id"])
            self._available[pkey] = Decimal(str(row["available"]))
            akey = (LedgerKey(run_id=row["run_id"], arm=row["arm_id"],
                              variant=row["variant_id"]), *pkey)
            self._allocated[akey] = Decimal(str(row["allocated"]))
