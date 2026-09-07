from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from harness.db.models import FairValue, KillSwitch, MarketGapSnapshot, Signal, StrategyVariant

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def test_phase2_tables_and_uniques(db_session):
    names = set(db_session.execute(text("select tablename from pg_tables where schemaname='public'")).scalars())
    assert {"fair_values", "market_gap_snapshots", "strategy_variants", "signals", "kill_switch", "config_history"} <= names
    db_session.add(StrategyVariant(variant_id="abc123abc123", name="v", tier="primary", config_json={"a": 1}, registered_at=NOW, active=True))
    fv = FairValue(run_id=1, game_id=1, market_type="moneyline", outcome_team_id=19, fair_p=Decimal("0.5500"), fair_source="direct",
                   n_groups=2, disagreement=Decimal("0.0100"), created_at=NOW)
    db_session.add(fv)
    db_session.flush()
    dup = FairValue(run_id=1, game_id=1, market_type="moneyline", outcome_team_id=19, fair_p=Decimal("0.5600"), fair_source="direct",
                    n_groups=2, created_at=NOW)
    db_session.add(dup)
    with pytest.raises(IntegrityError):
        db_session.flush()
    db_session.rollback()
    db_session.add(KillSwitch(id=1, active=False, reason="", set_at=NOW))
    db_session.flush()
