from pathlib import Path

import pytest
import yaml

from harness.db.models import Signal, StrategyVariant
from harness.replay import replay
from harness.strategy.pipeline import price_and_signal
from harness.strategy.variants import register_variants, variant_from_config
from tests.test_pipeline import NOW, _seed

FIXTURES = Path(__file__).parent / "fixtures" / "variants"


def _sharp_direct_config() -> dict:
    return yaml.safe_load((FIXTURES / "tiny.yaml").read_text()) | {"name": "sharp_direct"}


def _register_sharp_direct(db_session):
    variant = variant_from_config(_sharp_direct_config(), "sharp_direct")
    register_variants(db_session, [variant], NOW, prune=False)
    return variant


def test_replay_matches_live_signals_and_second_call_inserts_nothing(env_settings, db_session):
    game, run, markets = _seed(db_session)
    _register_sharp_direct(db_session)

    live_result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    live = live_result["signals"]["sharp_direct"]
    assert live["candidate"] + live["rejected"] == 9

    counts = replay(db_session, run.id, run.id, "sharp_direct")
    assert counts.runs == 1
    assert counts.signals_candidate == live["candidate"]
    assert counts.signals_rejected == live["rejected"]
    assert counts.inserted == live["candidate"] + live["rejected"]

    replay_signals = (
        db_session.query(Signal).filter_by(run_id=run.id, replay=True).all()
    )
    assert len(replay_signals) == live["candidate"] + live["rejected"]
    live_signals = db_session.query(Signal).filter_by(run_id=run.id, replay=False).all()
    assert len(live_signals) == live["candidate"] + live["rejected"]

    # Second call over the same range re-derives the same decisions but inserts nothing new.
    counts2 = replay(db_session, run.id, run.id, "sharp_direct")
    assert counts2.runs == 1
    assert counts2.signals_candidate == live["candidate"]
    assert counts2.signals_rejected == live["rejected"]
    assert counts2.inserted == 0


def test_replay_file_variant_registers_replay_tier_without_pruning_live_set(env_settings, db_session, tmp_path):
    game, run, markets = _seed(db_session)
    _register_sharp_direct(db_session)
    price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)

    baseline = replay(db_session, run.id, run.id, "sharp_direct")

    wide_config = _sharp_direct_config() | {"name": "wide", "price_band": [0.10, 0.90]}
    variant_file = tmp_path / "wide.yaml"
    variant_file.write_text(yaml.safe_dump(wide_config))

    wide_counts = replay(db_session, run.id, run.id, "wide", variant_file=variant_file)

    assert wide_counts.runs == 1
    assert wide_counts.signals_candidate >= baseline.signals_candidate

    sharp_row = db_session.get(StrategyVariant, variant_from_config(_sharp_direct_config()).variant_id)
    assert sharp_row is not None
    assert sharp_row.active is True

    wide_row = db_session.query(StrategyVariant).filter_by(name="wide").one()
    assert wide_row.tier == "replay"
    assert wide_row.active is True


def test_replay_range_with_no_snapshots_returns_zero_runs(env_settings, db_session):
    game, run, markets = _seed(db_session)
    _register_sharp_direct(db_session)

    counts = replay(db_session, run.id + 1000, run.id + 2000, "sharp_direct")

    assert counts.runs == 0
    assert counts.signals_candidate == 0
    assert counts.signals_rejected == 0
    assert counts.inserted == 0


def test_replay_unknown_variant_name_raises(db_session):
    with pytest.raises(ValueError, match="no variant"):
        replay(db_session, 1, 1, "does_not_exist")
