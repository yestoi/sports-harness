"""§1.5: one 10-contract print cannot fill 45 counterfactual orders for 346.60 contracts."""
import json
from decimal import Decimal
from pathlib import Path

from harness.experiments.execution_viability.liquidity import LedgerKey, PortfolioLedger

FIXTURE = json.loads((Path("tests/fixtures/exp_print_10_contracts.json")).read_text())
KEY = LedgerKey(run_id="r1", arm="A", variant="sharp_two_sided")


def test_one_portfolio_never_receives_more_than_the_print():
    ledger = PortfolioLedger()
    print_ = FIXTURE["print"]
    ledger.observe(print_["ticker"], print_["taker_side"], print_["trade_id"],
                   Decimal(str(print_["count"])))
    granted = [ledger.allocate(KEY, print_["ticker"], print_["taker_side"], print_["trade_id"],
                               order["id"], Decimal("10"))
               for order in sorted(FIXTURE["orders"], key=lambda o: (o["placed_at"], o["id"]))]
    assert sum(granted) == Decimal("10")            # not 346.60
    assert granted[0] == Decimal("10") and granted[1] == Decimal("0")
    assert len([g for g in granted if g > 0]) == 1


def _seeded():
    """A ledger that has seen the fixture's one 10-contract print. Returns (ledger, args)."""
    ledger = PortfolioLedger()
    pr = FIXTURE["print"]
    ledger.observe(pr["ticker"], pr["taker_side"], pr["trade_id"], Decimal(str(pr["count"])))
    return ledger, (pr["ticker"], pr["taker_side"], pr["trade_id"])


def test_a_cancel_and_re_entry_never_replenishes():
    ledger, key = _seeded()
    assert ledger.allocate(KEY, *key, 1, Decimal("10")) == Decimal("10")
    ledger.release(KEY, *key, 1)          # the order is cancelled and placed again at a new price
    assert ledger.allocate(KEY, *key, 1, Decimal("10")) == Decimal("0")
    assert ledger.allocate(KEY, *key, 2, Decimal("10")) == Decimal("0")


def test_a_partial_fill_consumes_only_what_it_took():
    ledger, key = _seeded()
    assert ledger.allocate(KEY, *key, 1, Decimal("4")) == Decimal("4")
    assert ledger.allocate(KEY, *key, 2, Decimal("10")) == Decimal("6")
    assert ledger.allocate(KEY, *key, 3, Decimal("10")) == Decimal("0")


def test_resume_restores_the_ledger_and_does_not_double_allocate():
    ledger, key = _seeded()
    assert ledger.allocate(KEY, *key, 1, Decimal("10")) == Decimal("10")
    rows = ledger.as_rows()
    restored = PortfolioLedger()
    restored.restore(rows)
    assert restored.allocate(KEY, *key, 2, Decimal("10")) == Decimal("0")
    assert restored.as_rows() == rows       # a refused allocation writes nothing


def test_a_checkpoint_carries_a_print_nothing_has_taken_yet():
    """Ruling D23/I1: the whole ledger is the resume point, not only what was allocated.

    `as_rows()` is empty until something is taken, so a checkpoint carrying only that half
    would forget a print observed before the boundary; the order it hits after the boundary
    would then be granted nothing at all, and the run would under-fill for a reason no row
    records.
    """
    ledger, key = _seeded()
    assert ledger.as_rows() == []
    restored = PortfolioLedger()
    restored.restore_state(ledger.as_state())
    assert restored.allocate(KEY, *key, 1, Decimal("10")) == Decimal("10")
    assert restored.allocate(KEY, *key, 2, Decimal("10")) == Decimal("0")


def test_a_checkpoint_of_a_partly_taken_print_resumes_where_it_stopped():
    ledger, key = _seeded()
    assert ledger.allocate(KEY, *key, 1, Decimal("4")) == Decimal("4")
    restored = PortfolioLedger()
    restored.restore_state(ledger.as_state())
    assert restored.allocate(KEY, *key, 2, Decimal("10")) == Decimal("6")
    assert restored.as_rows() == [dict(row, allocated=Decimal("10"))
                                  for row in ledger.as_rows()]


def test_releasing_a_whole_order_forgets_it_without_replenishing():
    """What `adapter._close` calls: the closing side knows the order, not the prints it took
    from. The pool is unchanged, so the next order still sees only the remainder (§1.5)."""
    ledger, key = _seeded()
    assert ledger.allocate(KEY, *key, 1, Decimal("6")) == Decimal("6")
    assert ledger.taken_by(KEY, 1) == Decimal("6")
    ledger.release_order(KEY, 1)
    assert ledger.taken_by(KEY, 1) == Decimal("0")
    assert ledger.allocate(KEY, *key, 2, Decimal("10")) == Decimal("4")


def test_two_portfolios_are_independent_and_are_never_summed():
    other = LedgerKey(run_id="r1", arm="B", variant="sharp_two_sided")
    ledger, key = _seeded()
    assert ledger.allocate(KEY, *key, 1, Decimal("10")) == Decimal("10")
    assert ledger.allocate(other, *key, 1, Decimal("10")) == Decimal("10")
    rows = ledger.as_rows()
    assert len(rows) == 2                                  # one per portfolio identity
    assert {r["arm_id"] for r in rows} == {"A", "B"}   # exp_allocation names it arm_id
    assert not hasattr(ledger, "total")                    # §1.6(a): no summed row exists


def test_the_ledger_only_caps_what_the_simulator_already_decided():
    # I13b: PortfolioLedger holds no queue, no depth and no reconciliation window of its own.
    import harness.experiments.execution_viability.liquidity as lq

    text_ = open(lq.__file__).read()
    for name in ("RECON_HORIZON", "queue_remaining", "cursor_event_id", "buckets"):
        assert name not in text_
