from datetime import datetime, timedelta, timezone
from decimal import Decimal
from harness.pricing.consensus import BookFair, consensus

T = datetime(2026, 9, 9, 22, 0, tzinfo=timezone.utc)


def test_consensus_requires_pinnacle():
    assert consensus([BookFair("betonlineag", Decimal("0.55"), T)]) is None


def test_consensus_weights_and_disagreement():
    c = consensus([BookFair("pinnacle", Decimal("0.60"), T), BookFair("betonlineag", Decimal("0.56"), T), BookFair("lowvig", Decimal("0.54"), T)])
    assert c.n_groups == 2
    assert c.group_fairs["bol"] == Decimal("0.5500")
    assert Decimal("0.57") < c.fair_p < Decimal("0.59")  # log-odds weighted toward pinnacle
    assert c.disagreement == Decimal("0.0250")  # population std of {0.60, 0.55}
    assert c.newest_ts == T


def test_consensus_pinnacle_only():
    c = consensus([BookFair("pinnacle", Decimal("0.60"), T)])
    assert c.fair_p == Decimal("0.6000") and c.disagreement == Decimal("0.0000") and c.n_groups == 1


def test_newest_ts_ignores_non_sharp_books():
    later = T + timedelta(minutes=30)
    c = consensus([BookFair("pinnacle", Decimal("0.60"), T), BookFair("draftkings", Decimal("0.58"), later)])
    assert c.newest_ts == T
