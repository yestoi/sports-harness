"""The config file's values, which are the user's and are not the model's to tune."""
from decimal import Decimal

from harness.parlay.config import ParlayConfig, load_config


def test_the_budget_values_are_the_users():
    """R:213-218, all user decisions: $50 a week, $25 smart, $5 lottery, at most three lottery
    cards. The $10 left over stays unspent (D15)."""
    config = load_config()
    assert config.weekly_budget == Decimal("50")
    assert config.smart_stake == Decimal("25")
    assert config.lottery_stake == Decimal("5")
    assert config.lottery_cards_max == 3
    assert (config.smart_stake + config.lottery_stake * config.lottery_cards_max
            <= config.weekly_budget)


def test_the_anchors_are_lsu_and_the_saints():
    assert load_config().anchors == ("LSU", "NO")


def test_the_leg_shape_and_freshness_rules():
    config = load_config()
    assert config.smart_legs == (3, 4)
    assert config.lottery_legs == (6, 8)
    assert config.leg_max_age_minutes == 30
    assert config.pool_window_hours == 6
    assert config.expiry_days == 7


def test_the_yaml_ships_inside_the_package():
    import tomllib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    data = tomllib.loads((root / "pyproject.toml").read_text())
    assert "parlay/*.yaml" in data["tool"]["setuptools"]["package-data"]["harness"]
