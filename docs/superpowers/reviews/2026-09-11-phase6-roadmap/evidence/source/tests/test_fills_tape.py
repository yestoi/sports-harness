"""F1's premise, checked against a real 60-second slice of the recorded tape.

The queue model reads a print as "the resting side's level shrank by this much". That is a
claim about how Kalshi reports a trade, and it is only true if a print on the taker side
pairs with a negative delta *on the other side, at the other side's price*: a taker-YES
trade at a YES price of 0.03 is a NO bid at 0.97 being lifted, and the delta the venue
publishes is `no -156.06 @ 0.97`, not `yes ... @ 0.03`.

This file never skips. It reads a committed fixture, no database, and if the premise ever
fails on a fresh export that is a ruling on the fill rules, not a reason to relax the test.
"""

import json
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path

from harness.execution.book import opp
from harness.execution.fills import TapeDelta, TapePrint, price_on_side

FIXTURE = Path(__file__).parent / "fixtures" / "tape_sample_lou_miss_2026-09-07T03.json"
WITHIN = timedelta(seconds=1)


def _tape() -> tuple[dict, list[TapePrint], list[TapeDelta]]:
    body = json.loads(FIXTURE.read_text())
    prints = [
        TapePrint(trade_id=r["trade_id"], ts=datetime.fromisoformat(r["ts"]),
                  yes_price=r["yes_price"], count=r["count"],
                  # The canonical taker side is `taker_outcome_side or taker_side`, never
                  # defaulted (F5); this slice carries only the already-canonical column.
                  taker_side=r.get("taker_outcome_side") or r.get("taker_side"),
                  source=r["source"])
        for r in body["prints"]
    ]
    deltas = [
        TapeDelta(event_id=r["id"], ts=datetime.fromisoformat(r["ts"]), side=r["side"],
                  price=r["price"], delta=r["delta"], sid=r["sid"], seq=r["seq"])
        for r in body["deltas"]
    ]
    return body, prints, deltas


def _matching_delta(p: TapePrint, deltas: list[TapeDelta], price: Decimal, side: str):
    for d in deltas:
        if (d.side == side and d.price == price and d.delta < 0
                and -d.delta >= p.count and abs(d.ts - p.ts) <= WITHIN):
            return d
    return None


def test_fixture_shape_is_the_recorded_slice():
    body, prints, deltas = _tape()
    assert body["ticker"] == "KXNCAAFGAME-26SEP06LOUMISS-LOU"
    assert (len(prints), len(deltas)) == (196, 616)
    assert body["counts"]["taker_sides"] == {"yes": 65, "no": 131}
    assert all(p.taker_side in ("yes", "no") for p in prints)
    # Fractional counts are real on this venue (F40), which is why every quantity is Decimal.
    assert any(p.count % 1 != 0 for p in prints)


def test_recorded_prints_have_a_matching_delta_within_1s():
    _, prints, deltas = _tape()
    matched, misses = 0, []
    for p in prints:
        resting = opp(p.taker_side)
        if _matching_delta(p, deltas, price_on_side(p, resting), resting) is not None:
            matched += 1
        else:
            misses.append((p.trade_id, p.taker_side, str(p.yes_price), str(p.count)))
    assert matched == len(prints) == 196, f"{matched} of {len(prints)} matched; misses {misses[:5]}"


def test_the_worked_example_pairs_across_the_book():
    _, prints, deltas = _tape()
    p = next(x for x in prints if x.taker_side == "yes" and x.yes_price == Decimal("0.0300")
             and x.count == Decimal("156.06"))
    d = _matching_delta(p, deltas, price_on_side(p, "no"), "no")
    assert (d.side, d.price, d.delta) == ("no", Decimal("0.9700"), Decimal("-156.06"))
    # The literal reading -- look for the delta on the taker's own side -- finds nothing.
    assert _matching_delta(p, deltas, price_on_side(p, "yes"), "yes") is None


def test_the_taker_side_reading_matches_nothing_on_this_tape():
    """The contrast case: a delta on the *taker's* side, which is what "same side, same price
    as the print" would mean. It matches 0 of 196, which is why the rule reads across."""
    _, prints, deltas = _tape()
    matched = sum(
        _matching_delta(p, deltas, price_on_side(p, p.taker_side), p.taker_side) is not None
        for p in prints
    )
    assert matched == 0
