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


def test_the_policy_carries_a_version_and_the_five_release_one_families():
    """Expected: `policy_version` is a string and `props.families` is exactly the five families
    of addendum §3.1 (D3), in the order the file lists them.

    Every card records `policy_version` (addendum §2.1), so a config that lost the key would
    write cards no later reader could attribute to a policy.
    """
    config = load_config()
    assert config.policy_version == "2026.09-1"
    assert config.props.families == ("pass_yds", "rush_yds", "rec_yds", "receptions",
                                     "anytime_td")
    assert config.props.books == ("draftkings",)


def test_the_same_game_shape_and_the_prop_budget_are_the_addendum_s_numbers():
    """Expected: anchor plus 2 to 5 legs, at most two prop legs on a smart card, 16 events per
    sport, 16 calls a tick, a 24 h prop window.

    Computed independently of the code: addendum §2.1 states `same_game: {min_legs: 3,
    max_legs: 6}` as the whole card including the anchor, so `anchor plus 2 to 5` is the same
    statement; §3.2's arithmetic (16 x 9 credits x 4 ticks an hour) rests on the other three.
    """
    props = load_config().props
    assert (props.same_game_min_legs, props.same_game_max_legs) == (3, 6)
    assert props.same_game_distinct is True
    assert props.max_prop_legs_smart == 2
    assert (props.prop_events_max, props.prop_calls_per_tick, props.prop_window_hours) == (
        16, 16, 24)
    assert props.disqualifiers == ("player_unmatched", "market_unsupported", "stale_price")


def test_market_defs_is_a_mapping_keyed_by_family_and_may_be_empty():
    """Expected: `market_defs` is a dict whose every key is one of `families`.

    Empty is legal (Task 18a records the rules): a family without a recorded rule is
    `market_unsupported` at build time, which is the D19 behaviour. A key outside `families`
    is not legal -- it would be a rule nothing can ever apply.
    """
    config = load_config()
    assert isinstance(config.props.market_defs, dict)
    assert set(config.props.market_defs) <= set(config.props.families)


def test_the_stakes_and_the_anchors_are_untouched():
    """F03: this phase changes no stake, no budget and no anchor."""
    config = load_config()
    assert (config.weekly_budget, config.smart_stake, config.lottery_stake) == (
        Decimal("50"), Decimal("25"), Decimal("5"))
    assert config.anchors == ("LSU", "NO")
    assert config.leg_max_age_minutes == 30
