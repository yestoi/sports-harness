from decimal import Decimal
from harness.pricing.devig import devig, implied, power_devig, proportional_devig


def test_proportional_two_way():
    out = proportional_devig([Decimal("0.5263"), Decimal("0.5263")])
    assert out == [Decimal("0.5000"), Decimal("0.5000")]


def test_power_two_way_sums_to_one_and_favours_favourite_less_than_proportional():
    imp = [implied(Decimal("1.30")), implied(Decimal("3.80"))]  # 0.7692 + 0.2632 = 1.0324
    prop = proportional_devig(imp)
    pw = power_devig(imp)
    assert abs(sum(pw) - 1) < Decimal("0.0002")
    assert pw[0] > prop[0]  # power method takes more vig off the longshot


def test_devig_fallback_on_degenerate():
    assert devig([Decimal("1.0"), Decimal("1.0")]) == [Decimal("0.5000"), Decimal("0.5000")]
