"""Frozen strategy configurations: load from YAML, hash, register in the database.

A variant is a whole YAML config hashed to a 12-hex `variant_id` (spec §6.7). The hash
covers every key including `name` and `tier`, so any edit -- a rename included -- produces
a new id and the signals recorded under the old id keep meaning exactly what they meant.
"""

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import ConfigHistory, StrategyVariant

REQUIRED_KEYS: frozenset[str] = frozenset({
    "name",
    "tier",
    "sports",
    "sources_allowed",
    "edge_floor",
    "edge_ceiling",
    "disagreement_mult",
    "as_seed",
    "price_band",
    "min_ttk_min",
    "max_spread_c",
    "min_volume_24h",
    "velocity_max_pts",
    "stale_s",
    "kelly_fraction",
    "bankroll",
    "per_bet_cap",
    "per_game_cap",
    "daily_cap",
    "max_open",
    "floor_stake",
    "apply_caps",
})

TIERS = ("primary", "secondary", "replay")
MAX_PRIMARY = 1
MAX_SECONDARY = 5

#: `strategy_variants.name` is String(64) and unique, so a retired row is renamed to
#: `<name truncated>#<variant_id>`; a live name may never contain the separator.
NAME_MAX = 64
RETIRED_SEPARATOR = "#"
VARIANT_ID_LEN = 12
NAME_STEM_MAX = NAME_MAX - len(RETIRED_SEPARATOR) - VARIANT_ID_LEN


@dataclass(frozen=True)
class Variant:
    name: str
    tier: str
    config: dict
    variant_id: str


@dataclass(frozen=True)
class RegisterResult:
    added: int = 0
    unchanged: int = 0
    deactivated: int = 0


def variant_id_for(config: dict) -> str:
    canonical = json.dumps(config, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()[:12]


def _validate(config: object, source: str) -> dict:
    if not isinstance(config, dict):
        raise ValueError(f"{source}: variant config must be a mapping, got {type(config).__name__}")
    keys = set(config)
    missing = REQUIRED_KEYS - keys
    if missing:
        raise ValueError(f"{source}: missing variant keys {sorted(missing)}")
    unknown = keys - REQUIRED_KEYS
    if unknown:
        raise ValueError(f"{source}: unknown variant keys {sorted(unknown)}")
    if config["tier"] not in TIERS:
        raise ValueError(f"{source}: tier must be one of {TIERS}, got {config['tier']!r}")
    name = config["name"]
    if not isinstance(name, str) or not name:
        raise ValueError(f"{source}: name must be a non-empty string")
    if len(name) > NAME_MAX:
        raise ValueError(f"{source}: name must be at most {NAME_MAX} characters, got {len(name)}")
    if RETIRED_SEPARATOR in name:
        raise ValueError(f"{source}: name must not contain {RETIRED_SEPARATOR!r}; it marks a retired row")
    return config


def variant_from_config(config: dict, source: str = "<config>") -> Variant:
    _validate(config, source)
    return Variant(
        name=config["name"],
        tier=config["tier"],
        config=config,
        variant_id=variant_id_for(config),
    )


def load_variants(dir: Path) -> list[Variant]:
    """Load and validate every `*.yaml` in `dir`, sorted by filename."""
    variants: list[Variant] = []
    seen: dict[str, str] = {}
    for path in sorted(Path(dir).glob("*.yaml")):
        variant = variant_from_config(yaml.safe_load(path.read_text()), path.name)
        if variant.name in seen:
            raise ValueError(f"{path.name}: duplicate variant name {variant.name!r} (also in {seen[variant.name]})")
        seen[variant.name] = path.name
        variants.append(variant)

    primaries = [v.name for v in variants if v.tier == "primary"]
    if len(primaries) > MAX_PRIMARY:
        raise ValueError(f"at most {MAX_PRIMARY} primary variant, got {sorted(primaries)}")
    secondaries = [v.name for v in variants if v.tier == "secondary"]
    if len(secondaries) > MAX_SECONDARY:
        raise ValueError(f"at most {MAX_SECONDARY} secondary variants, got {sorted(secondaries)}")
    return variants


def _retired_name(name: str, variant_id: str) -> str:
    """`strategy_variants.name` is unique, so a superseded row has to give the name up.

    The stem is truncated rather than the whole string, so the `variant_id` -- the part that
    makes the retired name unique -- always survives.
    """
    return f"{name[:NAME_STEM_MAX]}{RETIRED_SEPARATOR}{variant_id}"


def register_variants(session: Session, variants: list[Variant], now: datetime) -> RegisterResult:
    """Make `variants` the active registry rows, recording every config ever seen.

    A name whose config changed keeps its old row (renamed and `active=False`) so historic
    signals still resolve; the new config gets a fresh row. An active row whose name is no
    longer supplied is deactivated too, so deleting a YAML takes the variant out of the
    live set instead of leaving the pipeline scoring it forever.
    """
    added = unchanged = deactivated = 0

    for variant in variants:
        by_id = session.get(StrategyVariant, variant.variant_id)
        if by_id is not None and by_id.name == variant.name and by_id.active:
            unchanged += 1
        else:
            holder = session.execute(
                select(StrategyVariant).where(StrategyVariant.name == variant.name)
            ).scalar_one_or_none()
            if holder is not None and holder.variant_id != variant.variant_id:
                holder.name = _retired_name(holder.name, holder.variant_id)
                holder.active = False
                deactivated += 1
                session.flush()
            if by_id is not None:
                by_id.name = variant.name
                by_id.tier = variant.tier
                by_id.config_json = variant.config
                by_id.registered_at = now
                by_id.active = True
            else:
                session.add(StrategyVariant(
                    variant_id=variant.variant_id,
                    name=variant.name,
                    tier=variant.tier,
                    config_json=variant.config,
                    registered_at=now,
                    active=True,
                ))
            added += 1
            session.flush()

        session.execute(
            insert(ConfigHistory)
            .values(config_hash=variant.variant_id, config_json=variant.config, first_seen=now)
            .on_conflict_do_nothing(index_elements=["config_hash"])
        )

    supplied = {v.name for v in variants}
    dropped = session.execute(
        select(StrategyVariant).where(StrategyVariant.active.is_(True))
    ).scalars().all()
    for row in dropped:
        if row.name not in supplied:
            row.active = False
            deactivated += 1

    session.commit()
    return RegisterResult(added=added, unchanged=unchanged, deactivated=deactivated)


def active_variants(session: Session) -> list[Variant]:
    rows = session.execute(
        select(StrategyVariant).where(StrategyVariant.active.is_(True)).order_by(StrategyVariant.name)
    ).scalars().all()
    return [
        Variant(name=r.name, tier=r.tier, config=r.config_json, variant_id=r.variant_id)
        for r in rows
    ]
