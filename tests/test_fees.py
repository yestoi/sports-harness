from decimal import Decimal
from harness.pricing.fees import (
    KALSHI_FOOTBALL, ceil_to_cent, ceil_to_centicent, cost, fee_for_order, fee_model_for, fee_per_contract,
)


def test_ceil_to_cent():
    assert ceil_to_cent(Decimal("0.4374")) == Decimal("0.44") and ceil_to_cent(Decimal("0.44")) == Decimal("0.44")


def test_ceil_to_centicent():
    assert ceil_to_centicent(Decimal("0.004375")) == Decimal("0.0044")
    assert ceil_to_centicent(Decimal("0.0044")) == Decimal("0.0044")


def test_taker_and_maker_fee_at_50c_for_100_contracts():
    assert fee_for_order(KALSHI_FOOTBALL, "taker", Decimal("0.50"), 100) == Decimal("1.75")
    assert fee_for_order(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 100) == Decimal("0.4375")
    assert fee_per_contract(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 100) == Decimal("0.0044")


def test_tiny_order_rounds_to_centicent():
    assert fee_for_order(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 1) == Decimal("0.0044")
    assert fee_per_contract(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 1) == Decimal("0.0044")


def test_fee_per_contract_is_centicent():
    for n in (12, 100):
        assert fee_per_contract(KALSHI_FOOTBALL, "maker", Decimal("0.55"), n) == Decimal("0.0043")
    assert fee_per_contract(KALSHI_FOOTBALL, "maker", Decimal("0.55"), 1) == Decimal("0.0044")
    assert fee_for_order(KALSHI_FOOTBALL, "maker", Decimal("0.50"), 1) == Decimal("0.0044")


def test_fee_model_for():
    assert fee_model_for("quadratic", 1).maker_rate == 0
    assert fee_model_for("quadratic_with_maker_fees", 1) == KALSHI_FOOTBALL
    assert fee_model_for(None, None) == KALSHI_FOOTBALL


def test_cost():
    assert cost(KALSHI_FOOTBALL, "taker", Decimal("0.35"), 20) == Decimal("0.35") + fee_per_contract(KALSHI_FOOTBALL, "taker", Decimal("0.35"), 20)
