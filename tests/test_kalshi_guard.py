"""Task 8: the live guard. `make_writer(settings, env, session=None)` is the only constructor
of a write-capable `KalshiWriter` (Task 7 refuses direct construction outside it). `env=demo`
needs both demo secret files (`is_file()`, never `exists()` -- C3) and a resolved host ending in
`demo.kalshi.co`. `env=prod` needs all four of `LIVE_TRADING=1`, `Settings.mode == "live"`, a
stored passing `gate_reports` row for the gate variant, and `secrets/legal_decision` to exist;
none exists today, so every one of the 15 proper subsets of those four conditions still refuses,
each naming the first missing one in `LIVE_CONDITIONS` order.
"""
import itertools
import os
from datetime import datetime, timezone
from decimal import Decimal

import pytest
from sqlalchemy import text as sa_text
from sqlalchemy.orm import sessionmaker

from harness.config.settings import Settings
from harness.db.engine import make_engine
from harness.db.models import GateReport, StrategyVariant
from harness.venues.kalshi.authed import KalshiWriter, LiveGuardRefused, make_writer

from tests.test_kalshi_authed import FakeTransport

FOUR = ["live_trading", "mode", "gate", "legal"]

#: A fixed 12-char id for the one variant these tests ever register. Every table involved is
#: truncated between tests by the `db_session` fixture, so reusing the same id across
#: parametrized cases is safe.
_GATE_VARIANT_ID = "aaaaaaaaaaaa"

DATABASE_URL = "postgresql+psycopg://u:p@h:5432/db"


def _condition_text(name: str) -> str:
    return {
        "live_trading": "LIVE_TRADING",
        "mode": "mode=live",
        "gate": "gate",
        "legal": "secrets/legal_decision",
    }[name]


def _register_passing_gate_report(gate_variant_name: str) -> None:
    """Commits a registered variant and a passing `gate_reports` row for it through a throwaway
    connection to `DATABASE_URL_TEST`. `_settings` below has no `session` parameter -- its only
    callers pass `tmp_path` and flags -- so this is the only way the "gate" flag can be made
    true, and the commit is what makes it visible to the caller's own `db_session` (a separate
    connection) under Postgres's default read-committed isolation."""
    url = os.environ["DATABASE_URL_TEST"]
    engine = make_engine(url)
    try:
        with sessionmaker(bind=engine)() as session:
            session.execute(sa_text(
                "insert into strategy_variants (variant_id, name, tier, config_json, "
                "registered_at, active) values (:vid, :name, 'primary', '{}'::jsonb, now(), true) "
                "on conflict (variant_id) do nothing"),
                {"vid": _GATE_VARIANT_ID, "name": gate_variant_name})
            session.add(GateReport(
                evaluated_at=datetime.now(timezone.utc), variant_id=_GATE_VARIANT_ID,
                gate_variant=True, criteria_json={}, criteria_hash="test-hash", passed=True))
            session.commit()
    finally:
        engine.dispose()


def _settings(tmp_path, **flags):
    """A Settings whose four live conditions are individually satisfiable."""
    if flags.get("gate"):
        _register_passing_gate_report("sharp_direct")
    legal_file = tmp_path / "legal_decision"
    if flags.get("legal"):
        legal_file.write_text("present")
    base = Settings(database_url=DATABASE_URL)
    return base.model_copy(update={
        "live_trading": 1 if flags.get("live_trading") else 0,
        "mode": "live" if flags.get("mode") else "paper",
        "legal_decision_file": legal_file,
        "gate_variant": "sharp_direct",
    })


def _demo_key_files(tmp_path):
    return tmp_path / "kalshi_demo_key_id", tmp_path / "kalshi_demo_private_key.pem"


def _settings_pointing_demo_files_at(tmp_path) -> Settings:
    key_file, pem_file = _demo_key_files(tmp_path)
    return Settings(database_url=DATABASE_URL, kalshi_demo_key_id_file=key_file,
                    kalshi_demo_private_key_file=pem_file)


def _settings_with_demo_files(tmp_path) -> Settings:
    key_file, pem_file = _demo_key_files(tmp_path)
    key_file.write_text("demo-key-id")
    pem_file.write_text("demo-private-key-pem")
    return _settings_pointing_demo_files_at(tmp_path)


def _register_variant(db_session, variant_id: str, name: str) -> None:
    """Registers a `strategy_variants` row directly on the caller's own `db_session`, so it is
    visible to `make_writer(..., session=db_session)` in the same test without a second
    connection (unlike `_register_passing_gate_report`, which has no session to use)."""
    db_session.add(StrategyVariant(variant_id=variant_id, name=name, tier="primary",
                                   config_json={}, registered_at=datetime.now(timezone.utc),
                                   active=True))
    db_session.flush()


# --- prod: the four-condition guard ---------------------------------------------------------

@pytest.mark.parametrize("present", [c for n in range(4)
                                     for c in itertools.combinations(FOUR, n)])
def test_make_writer_prod_refuses_every_subset(tmp_path, db_session, present):
    # 15 subsets: every combination short of all four.
    s = _settings(tmp_path, **{name: (name in present) for name in FOUR})
    with pytest.raises(LiveGuardRefused) as exc:
        make_writer(s, "prod", session=db_session)
    assert exc.value.env == "prod"
    first_missing = next(name for name in FOUR if name not in present)
    assert _condition_text(first_missing) in exc.value.missing


def test_make_writer_prod_refuses_even_with_all_four_because_none_exists(db_session, env_settings):
    # The real deployed settings: LIVE_TRADING=0, HARNESS_MODE=paper, no passing gate row,
    # no secrets/legal_decision.
    with pytest.raises(LiveGuardRefused):
        make_writer(env_settings, "prod", session=db_session)


def test_harness_mode_live_alone_does_not_enable_writes(tmp_path, db_session):
    s = _settings(tmp_path, mode=True)      # only mode=live
    with pytest.raises(LiveGuardRefused) as exc:
        make_writer(s, "prod", session=db_session)
    assert "LIVE_TRADING" in exc.value.missing


def test_a_passing_gate_row_for_another_variant_does_not_count(tmp_path, db_session):
    # fix round 1, I1: "sharp_direct" (Settings.gate_variant) is registered and evaluated -- its
    # own row does not pass -- while a *different* variant's row does pass. The gate_reports
    # query is scoped to the resolved variant_id, so the other variant's passing row must not be
    # read as satisfying condition 3. (Previously this test never registered "sharp_direct" at
    # all, so the name lookup returned None and the gate_reports query was never reached.)
    _register_variant(db_session, "bbbbbbbbbbbb", "sharp_direct")
    now = datetime.now(timezone.utc)
    db_session.add(GateReport(evaluated_at=now, variant_id="bbbbbbbbbbbb", gate_variant=True,
                              criteria_json={}, criteria_hash="own", passed=False))
    db_session.add(GateReport(evaluated_at=now, variant_id="other", gate_variant=False,
                              criteria_json={}, criteria_hash="other", passed=True))
    db_session.flush()
    s = _settings(tmp_path, live_trading=True, mode=True, legal=True)
    with pytest.raises(LiveGuardRefused) as exc:
        make_writer(s, "prod", session=db_session)
    assert "gate" in exc.value.missing


def test_a_passing_gate_row_for_the_gate_variant_advances_past_the_gate_condition(tmp_path, db_session):
    # The positive counterpart to the test above: once the gate variant's own row is the one
    # that passes, condition 3 is satisfied and the guard moves on to the next condition
    # (secrets/legal_decision, deliberately left absent here).
    _register_variant(db_session, "bbbbbbbbbbbb", "sharp_direct")
    db_session.add(GateReport(evaluated_at=datetime.now(timezone.utc), variant_id="bbbbbbbbbbbb",
                              gate_variant=True, criteria_json={}, criteria_hash="own",
                              passed=True))
    db_session.flush()
    s = _settings(tmp_path, live_trading=True, mode=True)   # legal deliberately absent
    with pytest.raises(LiveGuardRefused) as exc:
        make_writer(s, "prod", session=db_session)
    assert "secrets/legal_decision" in exc.value.missing


def test_an_older_passing_row_does_not_count_once_a_newer_evaluation_fails(tmp_path, db_session):
    # Ruling: condition 3 is judged on the *newest* evaluation only. An evaluation from a week
    # ago passing is not enough once a later evaluation exists and did not mark this variant a
    # pass -- "the gate has passed" is not the rule; "the gate passes now" is.
    _register_variant(db_session, "bbbbbbbbbbbb", "sharp_direct")
    older = datetime(2026, 9, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 8, tzinfo=timezone.utc)
    db_session.add(GateReport(evaluated_at=older, variant_id="bbbbbbbbbbbb", gate_variant=True,
                              criteria_json={}, criteria_hash="old", passed=True))
    db_session.add(GateReport(evaluated_at=newer, variant_id="bbbbbbbbbbbb", gate_variant=True,
                              criteria_json={}, criteria_hash="new", passed=False))
    db_session.flush()
    s = _settings(tmp_path, live_trading=True, mode=True, legal=True)
    with pytest.raises(LiveGuardRefused) as exc:
        make_writer(s, "prod", session=db_session)
    assert "gate" in exc.value.missing


def test_the_newest_evaluation_passing_advances_even_after_an_older_failure(tmp_path, db_session):
    # The mirror image: an older failing evaluation does not haunt a variant that the newest
    # evaluation marks passing.
    _register_variant(db_session, "bbbbbbbbbbbb", "sharp_direct")
    older = datetime(2026, 9, 1, tzinfo=timezone.utc)
    newer = datetime(2026, 9, 8, tzinfo=timezone.utc)
    db_session.add(GateReport(evaluated_at=older, variant_id="bbbbbbbbbbbb", gate_variant=True,
                              criteria_json={}, criteria_hash="old", passed=False))
    db_session.add(GateReport(evaluated_at=newer, variant_id="bbbbbbbbbbbb", gate_variant=True,
                              criteria_json={}, criteria_hash="new", passed=True))
    db_session.flush()
    s = _settings(tmp_path, live_trading=True, mode=True)   # legal deliberately absent
    with pytest.raises(LiveGuardRefused) as exc:
        make_writer(s, "prod", session=db_session)
    assert "secrets/legal_decision" in exc.value.missing


def test_make_writer_prod_refuses_on_missing_key_files_even_with_all_four_conditions_met(
        tmp_path, db_session):
    # Fix round 1, M6: past all four named conditions, a missing production key file must still
    # be a clean LiveGuardRefused, not a bare FileNotFoundError leaking out of kalshi_key_id()/
    # kalshi_private_key_pem(). Unreachable in the deployed posture like the rest of this branch
    # -- this only pins the failure mode for the day all four conditions are true.
    _register_variant(db_session, "bbbbbbbbbbbb", "sharp_direct")
    db_session.add(GateReport(evaluated_at=datetime.now(timezone.utc), variant_id="bbbbbbbbbbbb",
                              gate_variant=True, criteria_json={}, criteria_hash="own",
                              passed=True))
    db_session.flush()
    s = _settings(tmp_path, live_trading=True, mode=True, legal=True)
    assert not s.has_kalshi_credentials()      # the default /run/secrets/... paths don't exist
    with pytest.raises(LiveGuardRefused) as exc:
        make_writer(s, "prod", session=db_session)
    assert "kalshi production key files" in exc.value.missing


# --- demo: secret files and the host assertion ----------------------------------------------

def test_make_writer_demo_refuses_without_the_demo_secret_files(tmp_path, env_settings):
    with pytest.raises(LiveGuardRefused) as exc:
        make_writer(env_settings, "demo")
    assert "kalshi_demo" in exc.value.missing


def test_make_writer_demo_refuses_when_the_secret_path_is_a_directory(tmp_path):
    # C3: Compose materialises a missing bind source as an empty directory. exists() would be
    # True; is_file() is what the guard tests.
    (tmp_path / "kalshi_demo_key_id").mkdir()
    (tmp_path / "kalshi_demo_private_key.pem").mkdir()
    s = _settings_pointing_demo_files_at(tmp_path)
    assert s.kalshi_demo_key_id_file.exists() is True
    assert s.has_kalshi_demo_credentials() is False
    with pytest.raises(LiveGuardRefused):
        make_writer(s, "demo")


def test_make_writer_demo_refuses_a_non_demo_host(tmp_path):
    s = _settings_with_demo_files(tmp_path)
    s = s.model_copy(update={"kalshi_demo_base_url":
                             "https://api.elections.kalshi.com/trade-api/v2"})
    with pytest.raises(LiveGuardRefused) as exc:
        make_writer(s, "demo")
    assert "demo.kalshi.co" in exc.value.missing


def test_make_writer_demo_builds_a_writes_enabled_writer_when_the_files_exist(tmp_path):
    s = _settings_with_demo_files(tmp_path)
    writer = make_writer(s, "demo")
    try:
        assert writer._transport._writes_enabled is True
        assert writer._transport._env == "demo"
    finally:
        # fix round 1, M8: make_writer opens three real httpx.Client objects for a working demo
        # transport (the HttpClient's own client, plus the transport's read and write clients);
        # closing them keeps this test pristine under -W error.
        writer._transport.close()
        writer._transport._http.close()


def test_make_writer_rejects_an_unknown_env(env_settings):
    with pytest.raises(ValueError):
        make_writer(env_settings, "staging")


def test_direct_construction_outside_the_factory_raises():
    with pytest.raises(RuntimeError, match="make_writer"):
        KalshiWriter(FakeTransport(), None, per_bet_cap_dollars=Decimal("1"),
                     contract_cap=Decimal("1"), kill_switch_active=lambda: False,
                     writes_allowed=True)
