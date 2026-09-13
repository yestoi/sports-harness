"""The parlay budget and prop policy, loaded from `parlay.yaml` (roadmap R:213-218; addendum
§2.1).

Every value in that file is a user decision. Nothing here has a default that could paper over a
missing key: a config file that lost `weekly_budget` or `policy_version` raises at load rather
than building a card against an invented budget or policy.
"""
import importlib.resources
from dataclasses import dataclass
from decimal import Decimal

import yaml


@dataclass(frozen=True)
class PropPolicy:
    """The prop half of the policy (addendum §2.1). `market_defs` maps a family to DraftKings'
    own settlement rule, recorded verbatim with its source and date (D19); a family absent from
    it is `market_unsupported` and the builder never writes a leg for it."""
    families: tuple[str, ...]
    books: tuple[str, ...]
    max_prop_legs_smart: int
    same_game_min_legs: int
    same_game_max_legs: int
    same_game_distinct: bool
    disqualifiers: tuple[str, ...]
    prop_events_max: int
    prop_calls_per_tick: int
    prop_window_hours: int
    market_defs: dict[str, str]


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
    policy_version: str
    props: PropPolicy


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
        policy_version=str(raw["policy_version"]),
        props=_props(raw["props"]),
    )


def _props(raw: dict) -> PropPolicy:
    """The `props` block. No `.get` with a default anywhere: a config that lost a key raises at
    load rather than building a card against an invented policy. `market_defs` is the one key
    allowed to be empty, and every key in it must name a listed family."""
    families = tuple(str(f) for f in raw["families"])
    same_game = raw["same_game"]
    defs = {str(k): str(v) for k, v in (raw["market_defs"] or {}).items()}
    unknown = sorted(set(defs) - set(families))
    if unknown:
        raise ValueError(f"market_defs names families that are not in `families`: {unknown}")
    return PropPolicy(
        families=families,
        books=tuple(str(b) for b in raw["books"]),
        max_prop_legs_smart=int(raw["max_prop_legs_smart"]),
        same_game_min_legs=int(same_game["min_legs"]),
        same_game_max_legs=int(same_game["max_legs"]),
        same_game_distinct=bool(same_game["distinct_player_or_market"]),
        disqualifiers=tuple(str(d) for d in raw["disqualifiers"]),
        prop_events_max=int(raw["prop_events_max"]),
        prop_calls_per_tick=int(raw["prop_calls_per_tick"]),
        prop_window_hours=int(raw["prop_window_hours"]),
        market_defs=defs)
