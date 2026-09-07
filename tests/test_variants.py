from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from harness.db.models import ConfigHistory, StrategyVariant
from harness.strategy.variants import (
    OPTIONAL_KEYS,
    REQUIRED_KEYS,
    active_variants,
    load_variants,
    register_variants,
    variant_from_config,
    variant_id_for,
    with_defaults,
)

NOW = datetime(2026, 9, 7, 18, 0, tzinfo=timezone.utc)
FIXTURES = Path(__file__).parent / "fixtures" / "variants"
FIXTURES_INVALID = Path(__file__).parent / "fixtures" / "variants_invalid"
SHIPPED = Path(__file__).parent.parent / "harness" / "variants"


def _tiny_config() -> dict:
    return yaml.safe_load((FIXTURES / "tiny.yaml").read_text())


def _write(dir_: Path, name: str, config: dict) -> None:
    dir_.mkdir(parents=True, exist_ok=True)
    (dir_ / f"{name}.yaml").write_text(yaml.safe_dump(config, sort_keys=False))


def test_variant_id_is_stable_across_key_order():
    a = _tiny_config()
    b = {k: a[k] for k in sorted(a, reverse=True)}
    assert list(a) != list(b)
    assert variant_id_for(a) == variant_id_for(b)
    assert len(variant_id_for(a)) == 12
    int(variant_id_for(a), 16)


def test_variant_id_changes_with_the_name():
    a = _tiny_config()
    b = dict(a, name="renamed")
    assert variant_id_for(a) != variant_id_for(b)


def test_load_variants_reads_the_fixture_dir():
    variants = load_variants(FIXTURES)
    assert [v.name for v in variants] == ["tiny"]
    v = variants[0]
    assert v.tier == "primary"
    assert set(v.config) == set(REQUIRED_KEYS) | set(OPTIONAL_KEYS)
    # the id is of the YAML as written, never of the defaulted config the strategy reads
    assert v.variant_id == variant_id_for(_tiny_config())


def test_load_variants_rejects_an_unknown_key():
    with pytest.raises(ValueError, match="unknown"):
        load_variants(FIXTURES_INVALID)


def test_load_variants_rejects_a_missing_key(tmp_path):
    config = _tiny_config()
    del config["stale_s"]
    _write(tmp_path, "broken", config)
    with pytest.raises(ValueError, match="missing"):
        load_variants(tmp_path)


def test_load_variants_rejects_two_primaries(tmp_path):
    _write(tmp_path, "a", dict(_tiny_config(), name="a", tier="primary"))
    _write(tmp_path, "b", dict(_tiny_config(), name="b", tier="primary"))
    with pytest.raises(ValueError, match="primary"):
        load_variants(tmp_path)


def test_load_variants_rejects_seven_secondaries(tmp_path):
    _write(tmp_path, "p", dict(_tiny_config(), name="p", tier="primary"))
    for i in range(7):
        _write(tmp_path, f"s{i}", dict(_tiny_config(), name=f"s{i}", tier="secondary"))
    with pytest.raises(ValueError, match="secondar"):
        load_variants(tmp_path)


def test_load_variants_rejects_an_unknown_tier(tmp_path):
    _write(tmp_path, "a", dict(_tiny_config(), name="a", tier="tertiary"))
    with pytest.raises(ValueError, match="tier"):
        load_variants(tmp_path)


def test_load_variants_rejects_a_duplicate_name(tmp_path):
    _write(tmp_path, "a", dict(_tiny_config(), name="dup", tier="primary"))
    _write(tmp_path, "b", dict(_tiny_config(), name="dup", tier="secondary"))
    with pytest.raises(ValueError, match="duplicate"):
        load_variants(tmp_path)


def test_shipped_variants_are_one_primary_and_six_secondaries():
    variants = load_variants(SHIPPED)
    assert len(variants) == 7
    assert [v.name for v in variants if v.tier == "primary"] == ["sharp_direct"]
    assert sorted(v.name for v in variants if v.tier == "secondary") == [
        "constrained", "nfl_only", "no_velocity", "sharp_plus_derived",
        "sharp_two_sided", "wide_band",
    ]
    assert len({v.variant_id for v in variants}) == 7
    primary = next(v for v in variants if v.tier == "primary")
    assert primary.config["sources_allowed"] == ["direct"]
    assert primary.config["apply_caps"] is False

    two_sided = next(v for v in variants if v.name == "sharp_two_sided")
    assert two_sided.config["sides"] == ["yes", "no"]
    # only the three registry keys separate it from the primary it copies
    assert {k: v for k, v in two_sided.config.items() if primary.config.get(k) != v} == {
        "name": "sharp_two_sided", "tier": "secondary", "sides": ["yes", "no"],
    }


#: The six ids in the phase 2 pre-registration record. `sides` is applied after hashing
#: precisely so these stay put; amendment 3 adds `sharp_two_sided` as a seventh id.
RECORDED_IDS = {
    "constrained": "ff363c8ac08d",
    "nfl_only": "e549e693e117",
    "no_velocity": "64ba3ef09642",
    "sharp_direct": "f259ca109084",
    "sharp_plus_derived": "49af716f8708",
    "wide_band": "c2bc45377328",
}


def test_six_committed_yamls_hash_to_the_recorded_ids():
    on_disk = {name: variant_id_for(yaml.safe_load((SHIPPED / f"{name}.yaml").read_text()))
               for name in RECORDED_IDS}
    assert on_disk == RECORDED_IDS
    loaded = {v.name: v.variant_id for v in load_variants(SHIPPED) if v.name in RECORDED_IDS}
    assert loaded == RECORDED_IDS


def test_with_defaults_adds_sides_without_changing_the_id():
    config = _tiny_config()
    before = variant_id_for(config)
    v = variant_from_config(config)

    assert v.variant_id == before
    assert v.config["sides"] == ["yes"]
    assert config == _tiny_config()  # with_defaults never mutates its input
    # the defaults really are a fresh copy per call, not one shared mutable list
    v.config["sides"].append("no")
    assert OPTIONAL_KEYS["sides"] == ["yes"]
    assert with_defaults(_tiny_config())["sides"] == ["yes"]
    # and hashing *after* the defaults would have moved every recorded id
    assert variant_id_for(with_defaults(_tiny_config())) != before


def test_a_sides_key_changes_the_id():
    config = dict(_tiny_config(), sides=["yes", "no"])
    assert variant_id_for(config) != variant_id_for(_tiny_config())
    v = variant_from_config(config)
    assert v.variant_id == variant_id_for(config)
    assert v.config["sides"] == ["yes", "no"]


@pytest.mark.parametrize("sides", [[], "yes", ["yes", "maybe"], ["over"], None, ["yes", 1]])
def test_validate_rejects_bad_sides(tmp_path, sides):
    _write(tmp_path, "a", dict(_tiny_config(), sides=sides))
    with pytest.raises(ValueError, match="sides"):
        load_variants(tmp_path)


def test_register_variants_inserts_then_reports_unchanged(db_session):
    variants = load_variants(FIXTURES)
    first = register_variants(db_session, variants, NOW)
    assert (first.added, first.unchanged, first.deactivated) == (1, 0, 0)

    second = register_variants(db_session, variants, NOW)
    assert (second.added, second.unchanged, second.deactivated) == (0, 1, 0)

    rows = db_session.query(StrategyVariant).all()
    assert len(rows) == 1
    assert rows[0].active is True
    assert rows[0].variant_id == variants[0].variant_id
    assert rows[0].config_json == variants[0].config

    history = db_session.query(ConfigHistory).all()
    assert [h.config_hash for h in history] == [variants[0].variant_id]
    assert history[0].first_seen == NOW


def test_register_variants_deactivates_the_old_row_when_the_config_changes(db_session, tmp_path):
    config = _tiny_config()
    _write(tmp_path, "tiny", config)
    old = load_variants(tmp_path)
    register_variants(db_session, old, NOW)

    _write(tmp_path, "tiny", dict(config, edge_floor=0.03))
    new = load_variants(tmp_path)
    assert new[0].variant_id != old[0].variant_id

    result = register_variants(db_session, new, NOW)
    assert (result.added, result.unchanged, result.deactivated) == (1, 0, 1)

    by_id = {r.variant_id: r for r in db_session.query(StrategyVariant).all()}
    assert len(by_id) == 2
    assert by_id[old[0].variant_id].active is False
    assert by_id[new[0].variant_id].active is True
    assert by_id[new[0].variant_id].name == "tiny"

    assert {h.config_hash for h in db_session.query(ConfigHistory).all()} == {
        old[0].variant_id, new[0].variant_id,
    }


def test_active_variants_returns_only_the_live_configs(db_session, tmp_path):
    config = _tiny_config()
    _write(tmp_path, "tiny", config)
    register_variants(db_session, load_variants(tmp_path), NOW)
    _write(tmp_path, "tiny", dict(config, edge_floor=0.03))
    new = load_variants(tmp_path)
    register_variants(db_session, new, NOW)

    live = active_variants(db_session)
    assert len(live) == 1
    assert live[0].variant_id == new[0].variant_id
    assert live[0].name == "tiny"
    assert live[0].config == new[0].config


def test_reverting_to_an_earlier_config_reactivates_that_row(db_session, tmp_path):
    config = _tiny_config()
    _write(tmp_path, "tiny", config)
    original = load_variants(tmp_path)
    register_variants(db_session, original, NOW)
    _write(tmp_path, "tiny", dict(config, edge_floor=0.03))
    register_variants(db_session, load_variants(tmp_path), NOW)

    _write(tmp_path, "tiny", config)
    result = register_variants(db_session, load_variants(tmp_path), NOW)
    assert (result.added, result.unchanged, result.deactivated) == (1, 0, 1)

    live = active_variants(db_session)
    assert [v.variant_id for v in live] == [original[0].variant_id]
    assert db_session.query(StrategyVariant).count() == 2


# --- review round 1 ---------------------------------------------------------

def test_register_variants_deactivates_a_name_that_is_no_longer_supplied(db_session, tmp_path):
    _write(tmp_path, "keep", dict(_tiny_config(), name="keep", tier="primary"))
    _write(tmp_path, "drop", dict(_tiny_config(), name="drop", tier="secondary"))
    register_variants(db_session, load_variants(tmp_path), NOW)
    assert len(active_variants(db_session)) == 2

    (tmp_path / "drop.yaml").unlink()
    result = register_variants(db_session, load_variants(tmp_path), NOW)
    assert (result.added, result.unchanged, result.deactivated) == (0, 1, 1)

    live = active_variants(db_session)
    assert [v.name for v in live] == ["keep"]
    dropped = db_session.query(StrategyVariant).filter_by(name="drop").one()
    assert dropped.active is False


def test_a_dropped_variant_is_deactivated_only_once(db_session, tmp_path):
    _write(tmp_path, "keep", dict(_tiny_config(), name="keep", tier="primary"))
    _write(tmp_path, "drop", dict(_tiny_config(), name="drop", tier="secondary"))
    register_variants(db_session, load_variants(tmp_path), NOW)
    (tmp_path / "drop.yaml").unlink()
    register_variants(db_session, load_variants(tmp_path), NOW)
    again = register_variants(db_session, load_variants(tmp_path), NOW)
    assert again.deactivated == 0


def test_a_name_at_the_column_limit_retires_without_a_collision(db_session, tmp_path):
    long_name = "n" * 64
    config = dict(_tiny_config(), name=long_name)
    _write(tmp_path, "long", config)
    old = load_variants(tmp_path)
    register_variants(db_session, old, NOW)

    _write(tmp_path, "long", dict(config, edge_floor=0.03))
    new = load_variants(tmp_path)
    register_variants(db_session, new, NOW)

    rows = {r.variant_id: r for r in db_session.query(StrategyVariant).all()}
    assert rows[new[0].variant_id].name == long_name
    retired = rows[old[0].variant_id].name
    assert len(retired) == 64
    assert retired == f"{'n' * 51}#{old[0].variant_id}"
    assert retired != long_name


def test_load_variants_rejects_an_over_long_name(tmp_path):
    _write(tmp_path, "a", dict(_tiny_config(), name="n" * 65))
    with pytest.raises(ValueError, match="64"):
        load_variants(tmp_path)


def test_load_variants_rejects_a_hash_in_the_name(tmp_path):
    _write(tmp_path, "a", dict(_tiny_config(), name="retired#deadbeef"))
    with pytest.raises(ValueError, match="#"):
        load_variants(tmp_path)


# --- review round 2 ---------------------------------------------------------

def _replay_variant(name: str = "replay_wide"):
    from harness.strategy.variants import variant_from_config

    return variant_from_config(dict(_tiny_config(), name=name, tier="replay", price_band=[0.10, 0.90]))


def _register_live_set(db_session, tmp_path):
    _write(tmp_path, "primary", dict(_tiny_config(), name="primary", tier="primary"))
    _write(tmp_path, "secondary", dict(_tiny_config(), name="secondary", tier="secondary"))
    register_variants(db_session, load_variants(tmp_path), NOW)


def test_registering_a_replay_variant_without_pruning_leaves_the_live_set_alone(db_session, tmp_path):
    _register_live_set(db_session, tmp_path)
    result = register_variants(db_session, [_replay_variant()], NOW, prune=False)
    assert (result.added, result.deactivated) == (1, 0)
    # the replay-tier row is registered (active in the table) but never joins the live set
    # that active_variants returns to the pipeline.
    assert sorted(v.name for v in active_variants(db_session)) == ["primary", "secondary"]
    replay_row = db_session.query(StrategyVariant).filter_by(name="replay_wide").one()
    assert replay_row.active is True
    assert replay_row.tier == "replay"


def test_pruning_never_touches_a_replay_tier_row(db_session, tmp_path):
    _register_live_set(db_session, tmp_path)
    register_variants(db_session, [_replay_variant()], NOW, prune=False)

    (tmp_path / "secondary.yaml").unlink()
    result = register_variants(db_session, load_variants(tmp_path), NOW)
    assert result.deactivated == 1

    live = {v.name for v in active_variants(db_session)}
    assert live == {"primary"}
    assert db_session.query(StrategyVariant).filter_by(name="secondary").one().active is False
    assert db_session.query(StrategyVariant).filter_by(name="replay_wide").one().active is True


def test_pruning_is_on_by_default(db_session, tmp_path):
    _register_live_set(db_session, tmp_path)
    (tmp_path / "secondary.yaml").unlink()
    register_variants(db_session, load_variants(tmp_path), NOW)
    assert [v.name for v in active_variants(db_session)] == ["primary"]
