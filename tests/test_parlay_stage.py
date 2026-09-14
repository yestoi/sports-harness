"""The scheduled `parlay_build` settle stage: due times, the six slots, refusals, one
replacement per decline, the stage's own 60 s budget and the rationale timeout it passes
through (addendum §2.4).

**Every test in this file gets one buildable `ncaaf` pool by default** (an LSU anchor plus six
more +EV legs, each on its own game -- enough for both the `smart` shape's 3-4 legs and the
`lottery` shape's 6-8): the autouse `_pool` fixture below, so the brief's own test bodies can
call `run_parlay_build` without naming a fixture, and
`test_an_empty_slot_records_its_reason_code` strips the anchor's price back out.
"""
import itertools
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from harness.db.models import JobState, OddsSnapshot, ParlayCard, ParlayLedger, ParlayLeg
from harness.parlay.build import build_card as _real_build_card
from harness.settlement.job import Budget
from harness.settlement.parlay_build import (_REASON_INDEX, RATIONALE_TIMEOUT_S,
                                              read_slot_state, run_parlay_build)
from tests.conftest import _make_dk_price, _make_game, _make_leg, _make_team

#: Friday 2026-09-12 18:05 CT (America/Chicago, CDT = UTC-5): the two `ncaaf` build times
#: (Friday 18:00 CT) are due, the two `nfl` ones (Saturday 18:00 CT) are not. Chicago ISO week
#: of this instant is (2026, 37) -- the `parlay_build:2026-37:...` keys below.
NOW = datetime(2026, 9, 12, 23, 5, tzinfo=timezone.utc)


def _budget(seconds: float) -> Budget:
    """A fake, monotonically increasing clock (half a second per check): `_budget(60)` never
    exhausts across one pass of the six slots (at most ~13 checks, ~6.5 fake seconds spent),
    and `_budget(1)` exhausts within the first slot or two, deterministically -- a real
    `time.monotonic` would depend on how fast the sandbox happens to run this test."""
    counter = itertools.count()
    return Budget(seconds, lambda: next(counter) * 0.5)


def _leg(session, *, team_abbr: str, opp_abbr: str, edge: Decimal, kickoff: datetime,
        price: Decimal, market_type: str = "moneyline", fair_p: Decimal = Decimal("0.55"),
        created_at: datetime | None = None, fetched_at: datetime | None = None):
    """One priced +EV leg on its own game, timed off this file's own `NOW` -- `tests.conftest`'s
    own `_pool_leg` stamps `created_at`/`fetched_at` off *its* `PARLAY_NOW` (2026-09-18), which
    is after this file's `NOW` and would fail `fetched_at <= :now` here."""
    created_at = created_at or (NOW - timedelta(hours=1))
    fetched_at = fetched_at or (NOW - timedelta(minutes=5))
    team = _make_team(session, team_abbr)
    opp = _make_team(session, opp_abbr)
    game = _make_game(session, team.id, opp.id, kickoff)
    _make_leg(session, game_id=game.id, market_type=market_type, side_team_id=team.id,
             side=None, threshold=None, fair_p=fair_p, edge=edge, created_at=created_at)
    _make_dk_price(session, game_id=game.id, market_type=market_type, team_id=team.id,
                   side=None, price=price, fetched_at=fetched_at)
    return game


@pytest.fixture(autouse=True)
def _pool(db_session):
    kickoff = NOW + timedelta(days=1)
    _leg(db_session, team_abbr="LSU", opp_abbr="OPP0", edge=Decimal("0.06"), kickoff=kickoff,
        price=Decimal("1.80"))
    for i in range(1, 7):
        _leg(db_session, team_abbr=f"OPP{i}", opp_abbr=f"OTH{i}", edge=Decimal("0.03"),
            kickoff=kickoff, price=Decimal("1.90"))
    return None


def _remove_every_anchor_price(session) -> None:
    """Strips the pool back to signals with no matching DraftKings row at all: every build,
    of every shape, ends the same way `build_card` always ends one -- `NoAnchorPriced` (D14)."""
    session.query(OddsSnapshot).delete()
    session.flush()


def _card(session, *, status: str, built_at: datetime, sport: str = "ncaaf", kind: str = "smart",
         correlated: bool = False, declined_reason: str | None = None,
         parent_card_id: int | None = None, year: int = 2026, week: int = 37) -> ParlayCard:
    card = ParlayCard(year=year, week=week, sport=sport, kind=kind, built_at=built_at,
                      stake=Decimal("25.00"), status=status, correlated=correlated,
                      declined_reason=declined_reason, parent_card_id=parent_card_id)
    session.add(card)
    session.flush()
    return card


def _declined_card(session, *, sport: str, shape: str, year: int = 2026,
                   week: int = 37) -> ParlayCard:
    kind = "lottery" if shape.startswith("lottery") else "smart"
    return _card(session, status="void", built_at=NOW - timedelta(hours=2), sport=sport,
                kind=kind, correlated=(shape == "lottery_same_game"),
                declined_reason="declined", year=year, week=week)


def _decline(session, card: ParlayCard) -> None:
    card.status = "void"
    card.declined_reason = "declined"
    session.flush()


def _built_card(session) -> ParlayCard:
    return _card(session, status="proposed", built_at=NOW)


def _stake(session, *, year: int, week: int, amount: Decimal) -> None:
    session.add(ParlayLedger(ts=NOW, card_id=999999, kind="stake", amount=amount, year=year,
                             week=week))
    session.flush()


def _raises(exc: Exception):
    def _fn(*_args, **_kwargs):
        raise exc
    return _fn


def _job_state(session) -> dict:
    rows = session.query(JobState).filter(JobState.key.like("parlay_build:%")).all()
    return {row.key: read_slot_state(session, row.key) for row in rows}


def _add_legs(session, card: ParlayCard, *, game_ids: list[int]) -> None:
    """Enough of a `ParlayLeg` row to make `_card_shape` (and only `_card_shape`) work: one row
    per `game_id` given, on the card. Never a real priced leg -- these tests only need the
    shape a real build would have produced."""
    for seq, game_id in enumerate(game_ids, start=1):
        session.add(ParlayLeg(card_id=card.id, seq=seq, game_id=game_id, market_type="ml",
                              dk_american=-110, dk_decimal=Decimal("1.9100"),
                              plain_text=f"leg {seq}", status="pending"))
    session.flush()


def test_the_stage_builds_one_card_per_slot_after_its_build_time(db_session, env_settings):
    """Expected: on Friday evening the two college slots build and the four NFL slots do not.

    Computed independently: `BUILD_TIMES` is `ncaaf` Friday 18:00 CT and `nfl` Saturday 18:00
    CT, and NOW is 18:05 CT Friday, so exactly the college slots are due.
    """
    result = run_parlay_build(db_session, NOW, _budget(60))
    built = {k: v for k, v in _job_state(db_session).items() if "built" in v}
    assert {k.rsplit(":", 2)[-2] for k in built} == {"ncaaf"}
    assert result.counts["built"] == len(built)


def test_a_second_run_in_the_same_hour_builds_nothing(db_session, env_settings):
    run_parlay_build(db_session, NOW, _budget(60))
    before = db_session.query(ParlayCard).count()
    run_parlay_build(db_session, NOW + timedelta(minutes=5), _budget(60))
    assert db_session.query(ParlayCard).count() == before


def test_an_empty_slot_records_its_reason_code(db_session, env_settings):
    _remove_every_anchor_price(db_session)
    run_parlay_build(db_session, NOW, _budget(60))
    state = _job_state(db_session)["parlay_build:2026-37:ncaaf:smart"]
    assert state["reason"] == "no_anchor_priced" and state["at"] == NOW.isoformat()


def test_a_declined_card_is_replaced_once_and_the_slot_reads_replacement_pending(db_session,
                                                                                 env_settings):
    card = _declined_card(db_session, sport="ncaaf", shape="smart")
    run_parlay_build(db_session, NOW, _budget(60))
    child = db_session.query(ParlayCard).filter_by(parent_card_id=card.id).one()
    assert child.status == "proposed"
    _decline(db_session, child)
    run_parlay_build(db_session, NOW + timedelta(hours=1), _budget(60))
    assert db_session.query(ParlayCard).filter_by(parent_card_id=child.id).count() == 0
    assert _job_state(db_session)["parlay_build:2026-37:ncaaf:smart"]["reason"] == "not_built_yet"


def test_a_slot_whose_week_is_at_cap_records_week_at_cap(db_session, env_settings):
    _stake(db_session, year=2026, week=37, amount=Decimal("50"))
    run_parlay_build(db_session, NOW, _budget(60))
    assert _job_state(db_session)["parlay_build:2026-37:ncaaf:smart"]["reason"] == "week_at_cap"


def test_a_build_failure_is_caught_logged_and_recorded_as_builder_failed(db_session, env_settings,
                                                                         monkeypatch):
    monkeypatch.setattr("harness.settlement.parlay_build.build_card",
                        _raises(RuntimeError("boom")))
    result = run_parlay_build(db_session, NOW, _budget(60))
    assert _job_state(db_session)["parlay_build:2026-37:ncaaf:smart"]["reason"] == "builder_failed"
    assert result.error is None          # a stage failure is not a job failure


def test_the_stage_stops_starting_builds_after_sixty_seconds_and_records_elapsed(db_session,
                                                                                  env_settings):
    result = run_parlay_build(db_session, NOW, _budget(1))
    assert result.budget_exhausted is True
    assert result.counts["built"] <= 1


def test_expiry_runs_first_and_voids_a_stale_proposed_card(db_session, env_settings):
    old = _card(db_session, status="proposed", built_at=NOW - timedelta(days=8))
    run_parlay_build(db_session, NOW, _budget(60))
    assert db_session.get(ParlayCard, old.id).status == "void"
    assert db_session.get(ParlayCard, old.id).declined_reason == "expired"


def test_the_stage_is_registered_after_parlay_grade():
    """`load_stages()`'s live `STAGES` registry can already hold `parlay_build` from this very
    file's own top-level import (several test files import a stage module directly at
    collection time to reach its pure functions -- `test_benchmarks.py::
    test_stages_registered_in_order` documents and works around the identical problem for
    `parlay_grade`), so this rebuilds the registry from a clean slate in `STAGE_MODULES`' own
    order -- exactly what a fresh process's first `load_stages()` call sees -- and restores
    whatever it found before returning, leaving no trace for a test that runs after it."""
    import importlib

    from harness.settlement import job as job_module
    from harness.settlement.job import STAGE_MODULES

    assert (STAGE_MODULES.index("harness.settlement.parlay_build")
            > STAGE_MODULES.index("harness.settlement.parlay_grade"))

    saved_stages = list(job_module.STAGES)
    try:
        job_module.STAGES.clear()
        for module_name in STAGE_MODULES:
            importlib.reload(importlib.import_module(module_name))
        names = [name for name, _ in job_module.STAGES]
    finally:
        job_module.STAGES.clear()
        job_module.STAGES.extend(saved_stages)

    assert names.index("parlay_build") > names.index("parlay_grade")


def test_the_stage_bounds_the_rationale_call(db_session, env_settings, monkeypatch):
    """B-I9: the one model call a build makes is bounded at RATIONALE_TIMEOUT_S, so a slow
    provider costs the settle run half a minute rather than the hour."""
    seen = {}
    monkeypatch.setattr("harness.settlement.parlay_build.build_card",
                        lambda *a, **kw: seen.update(kw) or _built_card(db_session))
    run_parlay_build(db_session, NOW, _budget(60))
    assert seen["timeout_s"] == RATIONALE_TIMEOUT_S


def test_a_correlated_cross_game_lottery_card_does_not_satisfy_the_same_game_slot(db_session,
                                                                                  env_settings):
    """Review round 1, Critical 1 (reviewer's probe): a plain cross-game `lottery` card is
    routinely `correlated = True` (`build_card` only dedupes games for `smart`), so the slot
    decision must never consult that flag -- only the card's own legs say which shape it is."""
    cross_game = _card(db_session, status="proposed", built_at=NOW - timedelta(hours=1),
                       kind="lottery", correlated=True)
    _add_legs(db_session, cross_game, game_ids=[9101, 9102, 9103])
    run_parlay_build(db_session, NOW, _budget(60))
    state = _job_state(db_session)
    assert state["parlay_build:2026-37:ncaaf:lottery"] == {"built": cross_game.id}
    assert state["parlay_build:2026-37:ncaaf:lottery_same_game"].get("built") is None


def test_a_same_game_card_satisfies_only_the_same_game_slot(db_session, env_settings):
    same_game = _card(db_session, status="proposed", built_at=NOW - timedelta(hours=1),
                      kind="lottery", correlated=True)
    _add_legs(db_session, same_game, game_ids=[9201, 9201, 9201])
    run_parlay_build(db_session, NOW, _budget(60))
    state = _job_state(db_session)
    assert state["parlay_build:2026-37:ncaaf:lottery_same_game"] == {"built": same_game.id}
    # The `lottery` slot does not adopt the same-game card either: with the autouse pool still
    # present it legitimately builds its *own*, separate, cross-game card.
    assert state["parlay_build:2026-37:ncaaf:lottery"].get("built") != same_game.id


def test_a_settled_card_keeps_its_slot_built_for_the_week(db_session, env_settings):
    """Review round 1, Important 1: a graded card must not re-open its own slot."""
    card = _card(db_session, status="cashed", built_at=NOW - timedelta(hours=6))
    run_parlay_build(db_session, NOW, _budget(60))
    assert _job_state(db_session)["parlay_build:2026-37:ncaaf:smart"] == {"built": card.id}
    assert db_session.query(ParlayCard).filter_by(sport="ncaaf", kind="smart").count() == 1


def test_a_busted_card_also_keeps_its_slot_built_for_the_week(db_session, env_settings):
    card = _card(db_session, status="busted", built_at=NOW - timedelta(hours=6))
    run_parlay_build(db_session, NOW, _budget(60))
    assert _job_state(db_session)["parlay_build:2026-37:ncaaf:smart"] == {"built": card.id}


def test_a_raise_during_one_shapes_build_leaves_no_half_built_card(db_session, env_settings,
                                                                   monkeypatch):
    """Review round 1, Important 2: `build_card` flushes the card (and its legs) before
    `write_rationale` can raise; the savepoint around the call means a raise there leaves
    nothing behind."""
    def _half_built_then_raise(session, settings, sport, week, kind, now, *args, **kwargs):
        session.add(ParlayCard(year=2026, week=37, sport=sport, kind=kind, built_at=now,
                               stake=Decimal("25.00"), status="proposed", correlated=False))
        session.flush()
        raise RuntimeError("boom after insert")

    monkeypatch.setattr("harness.settlement.parlay_build.build_card", _half_built_then_raise)
    run_parlay_build(db_session, NOW, _budget(60))
    assert db_session.query(ParlayCard).count() == 0


def test_a_raise_in_one_shape_never_costs_the_expiry_or_an_earlier_shapes_card(db_session,
                                                                               env_settings,
                                                                               monkeypatch):
    """Review round 1, Important 2: a raise (or a DB error) building one shape must roll back
    only that shape's savepoint, never this run's expiry write or a card an earlier slot already
    built."""
    old = _card(db_session, status="proposed", built_at=NOW - timedelta(days=8))
    calls = {"n": 0}

    def _raise_once_then_real(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("boom")
        return _real_build_card(*args, **kwargs)

    monkeypatch.setattr("harness.settlement.parlay_build.build_card", _raise_once_then_real)
    result = run_parlay_build(db_session, NOW, _budget(60))
    assert db_session.get(ParlayCard, old.id).status == "void"     # expiry unaffected
    assert result.counts["built"] >= 1                             # a later shape still built


def test_the_reason_encoding_is_pinned_not_positional(db_session, env_settings):
    """Review round 1, Important 3: the mapping is a literal, not a derivation from
    `harness.parlay.build.REASON_CODES`'s position, so reordering that tuple cannot remap an
    already-written row's meaning."""
    assert _REASON_INDEX == {
        "no_anchor_priced": -1, "anchor_bye": -2, "no_props_fresh": -3,
        "player_unmatched": -4, "market_unsupported": -5, "side_unsupported": -6,
        "stale_price": -7, "week_at_cap": -8, "gamelog_budget_spent": -9,
        "not_built_yet": -10, "builder_failed": -11, "replacement_pending": -12,
    }


def test_a_replacement_build_records_replacement_pending_until_the_child_exists(db_session,
                                                                                env_settings,
                                                                                monkeypatch):
    """§2.4, implemented as the brief specifies (review round 1 ruling): the slot reads
    `replacement_pending` from the moment the decline is noticed until the replacement actually
    exists, which this spy observes mid-build."""
    card = _declined_card(db_session, sport="ncaaf", shape="smart")
    seen = {}

    def _spy(*args, **kwargs):
        # `NOW` is also on/after `nfl`'s own due hour (America/Chicago), so `build_card` is
        # called for every due slot, not only the one this test cares about; only the
        # `ncaaf`/`smart` call is this test's replacement build.
        sport = args[2] if len(args) > 2 else kwargs.get("sport")
        kind = args[4] if len(args) > 4 else kwargs.get("kind")
        if sport == "ncaaf" and kind == "smart":
            seen["mid_build"] = _job_state(db_session)["parlay_build:2026-37:ncaaf:smart"]
        return _real_build_card(*args, **kwargs)

    monkeypatch.setattr("harness.settlement.parlay_build.build_card", _spy)
    run_parlay_build(db_session, NOW, _budget(60))
    assert seen["mid_build"] == {"reason": "replacement_pending", "at": NOW.isoformat()}
    child = db_session.query(ParlayCard).filter_by(parent_card_id=card.id).one()
    assert _job_state(db_session)["parlay_build:2026-37:ncaaf:smart"] == {"built": child.id}


def test_a_budget_break_counts_the_slots_it_never_looked_at(db_session, env_settings):
    """Review round 1, Minor 6."""
    result = run_parlay_build(db_session, NOW, _budget(1))
    assert result.budget_exhausted is True
    assert result.counts["skipped"] >= 1
