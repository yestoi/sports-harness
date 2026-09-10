"""The parlay budget, loaded from `parlay.yaml` (roadmap R:213-218).

Every value in that file is a user decision. Nothing here has a default that could paper over a
missing key: a config file that lost `weekly_budget` raises at load rather than building a card
against an invented budget.
"""
import importlib.resources
from dataclasses import dataclass
from decimal import Decimal

import yaml


@dataclass(frozen=True)
class ParlayConfig:
    weekly_budget: Decimal
    smart_stake: Decimal
    lottery_stake: Decimal
    lottery_cards_max: int
    anchors: tuple[str, ...]
    smart_legs: tuple[int, int]
    lottery_legs: tuple[int, int]
    leg_max_age_minutes: int
    pool_window_hours: int
    pool_min_edge: Decimal
    expiry_days: int


def load_config() -> ParlayConfig:
    raw = yaml.safe_load(
        importlib.resources.files("harness.parlay").joinpath("parlay.yaml").read_text())
    return ParlayConfig(
        weekly_budget=Decimal(str(raw["weekly_budget"])),
        smart_stake=Decimal(str(raw["smart_stake"])),
        lottery_stake=Decimal(str(raw["lottery_stake"])),
        lottery_cards_max=int(raw["lottery_cards_max"]),
        anchors=tuple(str(a) for a in raw["anchors"]),
        smart_legs=(int(raw["smart_legs"][0]), int(raw["smart_legs"][1])),
        lottery_legs=(int(raw["lottery_legs"][0]), int(raw["lottery_legs"][1])),
        leg_max_age_minutes=int(raw["leg_max_age_minutes"]),
        pool_window_hours=int(raw["pool_window_hours"]),
        pool_min_edge=Decimal(str(raw["pool_min_edge"])),
        expiry_days=int(raw["expiry_days"]),
    )
