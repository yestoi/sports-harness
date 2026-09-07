from datetime import datetime
from decimal import Decimal

from harness.pricing.consensus import BookFair, Consensus, consensus
from harness.pricing.devig import devig
from harness.pricing.lines import Line

SHARP_BOOKS = ("pinnacle", "betonlineag", "lowvig")


def _pair_fair(leg1: Line, leg2: Line) -> tuple[Decimal, datetime | None]:
    fair_p = devig([leg1.price, leg2.price], method="power")[0]
    updates = [u for u in (leg1.last_update, leg2.last_update) if u is not None]
    last_update = max(updates) if updates else None
    return fair_p, last_update


def direct_fair(pairs: dict[str, tuple[Line, Line]], now: datetime) -> tuple[Consensus, list[BookFair]] | None:
    fairs: list[BookFair] = []
    for book in SHARP_BOOKS:
        if book not in pairs:
            continue
        leg1, leg2 = pairs[book]
        fair_p, last_update = _pair_fair(leg1, leg2)
        fairs.append(BookFair(book, fair_p, last_update))
    c = consensus(fairs)
    if c is None:
        return None
    return c, fairs


def soft_fair(pairs: dict[str, tuple[Line, Line]], book: str = "draftkings") -> Decimal | None:
    if book not in pairs:
        return None
    leg1, leg2 = pairs[book]
    fair_p, _ = _pair_fair(leg1, leg2)
    return fair_p
