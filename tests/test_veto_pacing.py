"""§1.8: the pacing profile, dormant by default, and what it does when it is not.

The dormancy cases come first because they are the ones that must never break: with
`Settings.veto_pacing_profile` unset - the default - `reserve_spend`'s arithmetic and the claim
order are today's, and this file asserts both directly rather than by inspection.
"""
import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session
from typer.testing import CliRunner

from harness.experiments.execution_viability import EXP_LABEL
from harness.experiments.execution_viability import veto_profile
from harness.experiments.execution_viability import cli as exp_cli
from harness.experiments.execution_viability.cli import exp_app
from harness.experiments.execution_viability.manifest import canonical_json
from harness.research import pacing, veto
from harness.research.features import VALIDITY_WINDOWS, invalidated
from harness.research.spend import BudgetRefused, reserve_spend, worst_case_usd
from harness.weeks import chicago_day
from tests.veto_fixtures import enqueue, seed_game, seed_signal

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)          # a Wednesday, 07:00 CT
SATURDAY = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)
MODEL = "claude-sonnet-5"                                        # a real key of spend.PRICES
#: `research_spend.usd` and `usd_reserved` are `Numeric(10, 4)` while `worst_case_usd` quantizes
#: to six places, so a value read back out of the database is the projection to four.
_STORED = Decimal("0.0001")

runner = CliRunner()


# --- dormancy -------------------------------------------------------------------------------

def test_todays_claim_order_is_unchanged_while_the_profile_is_none(env_settings):
    assert env_settings.veto_pacing_profile is None
    assert pacing.load_profile(None) is None
    sql = str(veto.claim_statement(None))
    assert "order by q.bucket_start, q.game_id nulls last, q.market_type" in " ".join(sql.split())
    assert "kickoff" not in sql


def test_the_reserved_floor_is_zero_while_the_profile_is_none():
    assert pacing.reserved_floor(None, NOW, Decimal("25"), near_kickoff=False) == Decimal("0")
    assert pacing.weekly_floor(None, NOW, Decimal("150")) == Decimal("0")
    assert pacing.near_kickoff(None, NOW, NOW + timedelta(hours=1)) is False


def test_an_unknown_profile_name_refuses_rather_than_disabling_pacing_silently():
    with pytest.raises(KeyError, match="unknown veto pacing profile"):
        pacing.load_profile("no_such_profile")


# --- the profile's arithmetic ----------------------------------------------------------------

def test_a_profile_reserves_half_the_day_for_near_kickoff_work():
    profile = pacing.PacingProfile(
        name="near_kickoff_50", windows=(pacing.Window("nfl", 6, Decimal("0.50")),
                                         pacing.Window("ncaaf", 6, Decimal("0.50"))),
        weekday_allocation={"saturday": Decimal("0.25"), "sunday": Decimal("0.25")},
        release_hour_ct=21, kickoff_first=True)
    # A far-from-kickoff caller may spend only the unreserved half: $25 x 0.50 = $12.50 held back.
    assert pacing.reserved_floor(profile, NOW, Decimal("25"),
                                 near_kickoff=False) == Decimal("12.50")
    # A near-kickoff caller may spend into the reserve: nothing is held back from it.
    assert pacing.reserved_floor(profile, NOW, Decimal("25"), near_kickoff=True) == Decimal("0")
    # $12.50 / $0.085 a pair = 147 pairs a day, so at least 140 near-kickoff opportunities a week
    # are covered (§1.8's expected result, computed here from the two numbers).
    assert int(Decimal("12.50") / Decimal("0.085")) >= 140


def test_the_unspent_reserve_is_released_after_twenty_one_hundred_chicago():
    profile = pacing.PacingProfile("p", (pacing.Window("nfl", 6, Decimal("0.50")),), {}, 21, True)
    before = datetime(2026, 9, 16, 1, 0, tzinfo=timezone.utc)    # 20:00 CT on the 15th
    after = datetime(2026, 9, 16, 3, 0, tzinfo=timezone.utc)     # 22:00 CT on the 15th
    assert pacing.released(profile, before) is False
    assert pacing.released(profile, after) is True
    # And the release is what the floor means: nothing is held back after the release hour.
    assert pacing.reserved_floor(profile, before, Decimal("25"),
                                 near_kickoff=False) == Decimal("12.50")
    assert pacing.reserved_floor(profile, after, Decimal("25"),
                                 near_kickoff=False) == Decimal("0")


def test_a_busy_saturday_cannot_consume_an_explicitly_reserved_sunday_allocation():
    profile = pacing.PacingProfile(
        "slate", (pacing.Window("nfl", 6, Decimal("0.50")),),
        {"saturday": Decimal("0.25"), "sunday": Decimal("0.25")}, 21, True)
    weekly = Decimal("150")
    assert pacing.weekly_floor(profile, SATURDAY, weekly) == Decimal("37.50")   # Sunday's 25 %
    # On Sunday itself nothing later in the week is reserved, so the whole remainder is spendable.
    assert pacing.weekly_floor(profile, SATURDAY + timedelta(days=1), weekly) == Decimal("0")


def test_near_kickoff_is_the_window_before_kickoff_and_not_after_it():
    profile = pacing.load_profile("near_kickoff_50")
    assert pacing.near_kickoff(profile, NOW, NOW + timedelta(hours=5)) is True
    assert pacing.near_kickoff(profile, NOW, NOW + timedelta(hours=7)) is False
    assert pacing.near_kickoff(profile, NOW, NOW - timedelta(minutes=1)) is False
    assert pacing.near_kickoff(profile, NOW, None) is False
    # A sport with no window of its own is never near-kickoff; the two §1.8 sports are.
    assert pacing.near_kickoff(profile, NOW, NOW + timedelta(hours=1), sport="nba") is False
    assert pacing.near_kickoff(profile, NOW, NOW + timedelta(hours=1), sport="nfl") is True


def test_the_profile_hash_is_over_the_canonical_serialisation():
    profile = pacing.load_profile("near_kickoff_50")
    # The leaf cannot import the experiment package (§0.4), so the two serialisations are pinned
    # to each other here instead: the hash in the amendment record is the canonical one.
    assert canonical_json(json.loads(profile.as_json())) == profile.as_json()
    assert len(profile.profile_hash()) == 64
    assert profile.profile_hash() == pacing.load_profile("near_kickoff_50").profile_hash()


# --- the reservation ---------------------------------------------------------------------------

def _settled(session, day, usd):
    """One settled `research_spend` row, so the day's total is exactly `usd`."""
    session.execute(text(
        "insert into research_spend (day, kind, model, calls, input_tokens, output_tokens,"
        " cache_read_tokens, cache_write_tokens, searches, usd_reserved, usd)"
        " values (:day, 'veto', :model, 1, 0, 0, 0, 0, 0, 0, :usd)"),
        {"day": day, "model": MODEL, "usd": usd})
    session.commit()


def test_reserve_spend_refuses_a_far_from_kickoff_call_into_the_reserve(db_session, env_settings):
    object.__setattr__(env_settings, "veto_pacing_profile", "near_kickoff_50")
    object.__setattr__(env_settings, "veto_daily_usd_cap", Decimal("25"))
    projection = worst_case_usd(MODEL, 0)
    # Spend the day to one cent under the cap. The unreserved half -- $25 - $12.50 -- is long
    # gone, so a far-from-kickoff call refuses even though the cap itself still has room.
    _settled(db_session, chicago_day(NOW), Decimal("25") - projection - Decimal("0.01"))
    with pytest.raises(BudgetRefused) as refused:
        reserve_spend(db_session, NOW, env_settings, "veto", [MODEL], searches=0,
                      near_kickoff=False)
    assert refused.value.cap == "daily_reserved"       # ruling I2: the attribute is `cap`
    assert refused.value.limit == Decimal("12.50")     # the daily cap minus the reserved floor
    db_session.rollback()
    assert db_session.execute(
        text("select coalesce(sum(usd_reserved), 0) from research_spend")).scalar() == Decimal("0")


def test_a_near_kickoff_call_may_spend_into_the_same_reserve(db_session, env_settings):
    object.__setattr__(env_settings, "veto_pacing_profile", "near_kickoff_50")
    object.__setattr__(env_settings, "veto_daily_usd_cap", Decimal("25"))
    projection = worst_case_usd(MODEL, 0)
    _settled(db_session, chicago_day(NOW), Decimal("25") - projection - Decimal("0.01"))
    reservation = reserve_spend(db_session, NOW, env_settings, "veto", [MODEL], searches=0,
                                near_kickoff=True)      # identical setup, no raise
    db_session.commit()
    assert reservation.per_model[MODEL] == projection
    assert db_session.execute(
        text("select coalesce(sum(usd_reserved), 0) from research_spend")
    ).scalar() == projection.quantize(_STORED)


def test_two_sessions_racing_the_reservation_cannot_overspend(db_session, env_settings):
    # The existing advisory lock is what makes this true; the pacing check sits inside it, so the
    # second session reads the first's reservation and refuses. Room for exactly one call.
    object.__setattr__(env_settings, "veto_pacing_profile", "near_kickoff_50")
    object.__setattr__(env_settings, "veto_daily_usd_cap", Decimal("25"))
    projection = worst_case_usd(MODEL, 0)
    _settled(db_session, chicago_day(NOW),
             Decimal("25") - (projection * 2) + Decimal("0.01"))
    first = reserve_spend(db_session, NOW, env_settings, "veto", [MODEL], searches=0,
                          near_kickoff=True)
    db_session.commit()                                # the lock lives to the transaction's end
    other = Session(bind=db_session.get_bind())
    try:
        with pytest.raises(BudgetRefused) as refused:
            reserve_spend(other, NOW, env_settings, "veto", [MODEL], searches=0,
                          near_kickoff=True)
        assert refused.value.cap == "daily"
    finally:
        other.rollback()
        other.close()
    assert first.per_model[MODEL] == projection


def test_reserve_spend_is_untouched_by_a_released_profile_after_the_release_hour(db_session,
                                                                                env_settings):
    """21:00 CT: the day's unspent reserve is back in the general pool, so a far-from-kickoff
    call spends against the unchanged $25 exactly as it does today."""
    object.__setattr__(env_settings, "veto_pacing_profile", "near_kickoff_50")
    object.__setattr__(env_settings, "veto_daily_usd_cap", Decimal("25"))
    evening = datetime(2026, 9, 17, 3, 0, tzinfo=timezone.utc)   # 22:00 CT on the 16th
    projection = worst_case_usd(MODEL, 0)
    _settled(db_session, chicago_day(evening), Decimal("25") - projection - Decimal("0.01"))
    reservation = reserve_spend(db_session, evening, env_settings, "veto", [MODEL], searches=0,
                                near_kickoff=False)
    assert reservation.per_model[MODEL] == projection


# --- the claim order ---------------------------------------------------------------------------

def _two_buckets(session):
    """An old bucket (kicked off two hours ago) enqueued first, and a fresh one 90 minutes out."""
    old_game, old_market = seed_game(session, kickoff=NOW - timedelta(hours=2), status="in")
    fresh_game, fresh_market = seed_game(session, kickoff=NOW + timedelta(minutes=90))
    old_signal = seed_signal(session, market=old_market, created_at=NOW - timedelta(hours=3))
    fresh_signal = seed_signal(session, market=fresh_market,
                               created_at=NOW - timedelta(minutes=20))
    enqueue(session, signal=old_signal, game=old_game, bucket_start=NOW - timedelta(hours=3),
            enqueued_at=NOW - timedelta(hours=3))
    enqueue(session, signal=fresh_signal, game=fresh_game,
            bucket_start=NOW - timedelta(minutes=20), enqueued_at=NOW - timedelta(minutes=20))
    session.commit()
    return old_game.id, fresh_game.id


def test_the_stale_backlog_cannot_monopolise_a_new_kickoff_window(db_session):
    old_game_id, fresh_game_id = _two_buckets(db_session)
    profile = pacing.load_profile("near_kickoff_50")
    claimed = veto.claim_bucket(db_session, NOW, profile=profile)
    assert [q.game_id for q in claimed] == [fresh_game_id]
    assert old_game_id not in [q.game_id for q in claimed]
    # The join is what carries the near-kickoff decision to the reservation.
    assert claimed[0].kickoff_utc == NOW + timedelta(minutes=90)
    assert pacing.near_kickoff(profile, NOW, claimed[0].kickoff_utc) is True


def test_the_same_fixture_claims_the_old_bucket_first_while_the_profile_is_none(db_session):
    old_game_id, fresh_game_id = _two_buckets(db_session)
    claimed = veto.claim_bucket(db_session, NOW)        # today's call site, unchanged
    assert [q.game_id for q in claimed] == [old_game_id]
    assert fresh_game_id not in [q.game_id for q in claimed]


def test_new_material_information_forces_a_call_inside_the_reservation(env_settings):
    # §1.8(e): a same-key resting order is repeated context, not a reason to suppress news. The
    # existing invalidator is what decides, and pacing gets no veto over it: a fair move at or
    # above `FAIR_MOVE_INVALIDATOR` still names a reason under the profile, and the near-kickoff
    # exemption is what funds the call it forces.
    object.__setattr__(env_settings, "veto_pacing_profile", "near_kickoff_50")
    trigger = {"espn_status": "pre", "weather_fetched_at": None, "fair_p": Decimal("0.50")}
    assert invalidated(trigger, dict(trigger)) is None               # repeated context: no call
    assert invalidated(trigger, {**trigger, "fair_p": Decimal("0.53")}) == "fair_move"
    profile = pacing.load_profile(env_settings.veto_pacing_profile)
    assert pacing.reserved_floor(profile, NOW, Decimal("25"), near_kickoff=True) == Decimal("0")


def test_every_skipped_evaluation_keeps_its_label_and_names_the_new_reason_code():
    # §3 row 4: `daily_reserved` appears only after the amendment instant; the decision label
    # itself stays `veto_skipped_budget`, so H9's decided population is unchanged by the code.
    refused = BudgetRefused("daily_reserved", Decimal("24.99"), Decimal("0.05"), Decimal("12.50"))
    assert refused.cap == "daily_reserved"
    assert veto.DECISIONS == ("proceed", "reduce", "veto", "veto_skipped_budget", "veto_error")
    source = Path(veto.__file__).read_text()
    # veto.py:409 already writes `reason_code=refused.cap`; this task changes neither (ruling I2).
    assert "reason_code=refused.cap" in source
    # And the column that carries it is wide enough for the one new code, which is the only way
    # `daily_reserved` can reach the database at all.
    from harness.db.models import VetoDecision

    assert len("daily_reserved") <= VetoDecision.__table__.c.reason_code.type.length


# --- the preflight, the amendment record and the command ---------------------------------------

def test_the_preflight_replays_stored_arrivals_and_invents_no_answer(db_session):
    """§1.8(d): every number is an opportunity count taken from `veto_queue`, and the profile's
    coverage is compared with today's on the same stored arrivals."""
    _two_buckets(db_session)
    profile = pacing.load_profile("near_kickoff_50")
    report = veto_profile.preflight(
        db_session, profile, since=NOW - timedelta(hours=6), until=NOW + timedelta(hours=1),
        now=NOW, cost_per_pair=Decimal("0.085"), daily_cap=Decimal("25"),
        weekly_cap=Decimal("150"))
    assert report.arrivals == 2 and report.buckets == 2
    # Both arrivals were inside 6 h of their own kickoff **at the instant they arrived** - the
    # older one was enqueued an hour before its game started - and the preflight judges each
    # bucket as of its own `bucket_start`, never as of a later reading instant. As of `now` the
    # old kickoff has passed, which is the different question the live claim order asks.
    assert report.near_kickoff_buckets == 2
    assert pacing.near_kickoff(profile, NOW, NOW - timedelta(hours=2)) is False
    assert report.funded == 2 and report.funded_today == 2
    assert report.funded + report.uncovered == report.buckets
    assert report.day_totals and max(report.day_totals.values()) <= Decimal("25")
    assert report.week_total <= Decimal("150")
    assert "opportunities, not outcomes" in veto_profile.PREFLIGHT_HEADER
    # No decision, no call and no model answer is created by a preflight.
    assert db_session.execute(text("select count(*) from veto_decisions")).scalar() == 0


def _paced_day(session):
    """Two far-from-kickoff arrivals in the morning and two near-kickoff arrivals in the evening,
    all on one America/Chicago day, all on games kicking off that evening."""
    kickoff = datetime(2026, 9, 17, 0, 0, tzinfo=timezone.utc)      # 19:00 CT on the 16th
    starts = [(NOW, kickoff),                                       # 07:00 CT, 12 h out: far
              (NOW + timedelta(hours=1), kickoff),                  # 08:00 CT, 11 h out: far
              (NOW + timedelta(hours=9), kickoff),                  # 16:00 CT, 3 h out: near
              (NOW + timedelta(hours=9, minutes=30),
               kickoff + timedelta(minutes=30))]                    # 16:30 CT, 3 h out: near
    for bucket_start, kick in starts:
        game, market = seed_game(session, kickoff=kick)
        signal = seed_signal(session, market=market, created_at=bucket_start)
        enqueue(session, signal=signal, game=game, bucket_start=bucket_start,
                enqueued_at=bucket_start)
    session.commit()
    return starts


def test_the_preflight_spends_the_day_in_arrival_order_not_in_kickoff_order(db_session):
    """Review fix, Important 1: money spent in the morning is gone by the evening.

    $10 a day at $4 a pair is two pairs, and the profile holds half the day ($5) for near-kickoff
    work. Chronologically: the 07:00 arrival takes $4 of the $5 general share, the 08:00 arrival
    is refused (it would cross the $5 line), the 16:00 near-kickoff arrival takes the reserve, and
    the 16:30 one is refused by the day's cap. One far-from-kickoff call and one near-kickoff call
    are funded. Sorting the whole day by kickoff proximity first - what this walk used to do -
    would fund **both** evening buckets and neither morning one, reporting twice the near-kickoff
    coverage the day can actually pay for.
    """
    _paced_day(db_session)
    profile = pacing.load_profile("near_kickoff_50")
    report = veto_profile.preflight(
        db_session, profile, since=NOW - timedelta(hours=1), until=NOW + timedelta(hours=11),
        now=NOW, cost_per_pair=Decimal("4"), daily_cap=Decimal("10"),
        weekly_cap=Decimal("150"))
    assert report.buckets == 4 and report.near_kickoff_buckets == 2
    assert report.funded == 2
    assert report.funded_near_kickoff == 1        # not 2: the morning spent half the day first
    # funded == 2 with one near-kickoff call means the other funded call was a far-from-kickoff
    # one, which is the arrival-order property this case exists to prove.
    assert report.uncovered == 2
    assert report.day_totals == {chicago_day(NOW): Decimal("8")}
    # Today's order funds the two morning arrivals and no near-kickoff work at all: there is no
    # reserve to hold anything back, which is the gap §1.8 exists to close.
    assert report.funded_today == 2 and report.funded_near_kickoff_today == 0
    assert "arrival order" in veto_profile.PREFLIGHT_HEADER


def test_the_built_profile_and_the_registry_entry_are_the_same_object_by_hash():
    """§0.8's guarantee is that the hash names exactly what activation turns on, so the builder
    and the registry must not be able to drift apart."""
    built = veto_profile.build_profile("near_kickoff_50", near_kickoff_fraction=Decimal("0.50"))
    registered = pacing.load_profile("near_kickoff_50")
    assert built.profile_hash() == registered.profile_hash()
    assert built.as_json() == registered.as_json()


def test_the_amendment_record_leaves_the_boundary_instant_for_the_user():
    profile = pacing.load_profile("near_kickoff_50")
    record = veto_profile.amendment_record(profile, prepared_at=NOW)
    assert profile.profile_hash() in record
    assert "written at activation by the user's dated decision" in record
    assert "q.bucket_start, q.game_id nulls last, q.market_type" in record   # the before order
    assert "$25" in record and "$150" in record                              # caps unchanged
    assert "veto_skipped_budget" in record
    # §0.14c's question is rendered, never answered.
    assert "Do you activate it, and from what date?" in veto_profile.AMENDMENT_QUESTION


def test_the_command_prints_the_profile_and_the_question_and_activates_nothing():
    result = runner.invoke(exp_app, ["veto-profile", "--name", "near_kickoff_50"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == EXP_LABEL          # §0.6, every `harness exp` command
    profile = pacing.load_profile("near_kickoff_50")
    assert profile.profile_hash()[:12] in result.output
    assert "Do you activate it, and from what date?" in result.output
    # The command activates nothing, and the guard is the source rather than a `Settings` object
    # the command never sees: no module behind it assigns the setting at all.
    for module in (veto_profile, exp_cli):
        body = Path(module.__file__).read_text()
        assert "veto_pacing_profile =" not in body          # no assignment
        assert "setattr(" not in body                       # and no indirect one


# --- 1.8(e): the cache's validity window in the sweep ------------------------------------------

def _expired_cache_bucket(session, *, sport="nfl"):
    """Two signals of one bucket, twenty minutes apart, with identical features.

    Twenty minutes is past `VALIDITY_WINDOWS["espn_status"]` (900 s) and inside the other two,
    and nothing the three invalidator keys read has moved between them: no fair values at all
    (so `fair_p` is the signal's own and equal), one score event before both, no weather. The
    only thing that separates the second signal from the trigger's cached context is its age.
    """
    bucket = NOW - timedelta(minutes=30)
    game, market = seed_game(session, kickoff=NOW + timedelta(hours=3), sport=sport,
                             score_status="scheduled", score_ts=bucket - timedelta(hours=1))
    signals = [seed_signal(session, market=market, created_at=bucket),
               seed_signal(session, market=market, created_at=bucket + timedelta(minutes=20))]
    for signal in signals:
        enqueue(session, signal=signal, game=game, bucket_start=bucket,
                enqueued_at=signal.created_at)
    session.commit()
    return [signal.id for signal in signals]


def _decisions(session):
    return session.execute(text(
        "select signal_id, decision, from_cache, reason_code, feature_delta, call_id "
        "from veto_decisions order by signal_id")).all()


def test_an_expired_cache_forces_a_call_and_records_the_window_it_left(db_session, env_settings):
    """1.8(e) **under a loaded profile** (D47): new material information bypasses the cached
    context, and so does a context that has simply run out: the second signal gets its own
    call, not the trigger's answer.

    The profile is named because the expiry path ships dormant with the rest of §1.8; the
    dormancy case below asserts the other half.
    """
    from tests.test_veto_worker import FakeClient

    object.__setattr__(env_settings, "veto_pacing_profile", "near_kickoff_50")
    ids = _expired_cache_bucket(db_session)
    client = FakeClient()
    counts = veto.veto_pass(db_session, NOW, env_settings, client=client)
    assert counts["calls"] == 2 and counts["decided"] == 2
    rows = _decisions(db_session)
    assert [row.signal_id for row in rows] == ids
    assert [row.from_cache for row in rows] == [False, False]
    assert rows[1].reason_code == "espn_status"          # the key's own label, not a new one
    assert rows[1].feature_delta == {"expired": "espn_status", "window_s": 900}
    assert int(VALIDITY_WINDOWS["espn_status"].total_seconds()) == 900
    assert rows[0].call_id != rows[1].call_id            # two paired calls, two call ids
    assert len(client.calls) == 4                        # primary and shadow, twice


def test_the_same_sweep_under_an_exhausted_cap_writes_the_caps_reason_code(db_session,
                                                                           env_settings):
    """The reservation is unchanged: an expiry-forced call goes through `reserve_spend` like
    any other and is refused with the existing cap code when the day is gone (brief rule 4).

    The day is settled to exactly one paired call short of the $25 cap, so the trigger's call
    fits and the expiry-forced one does not: what the second signal gets is `veto_skipped_budget`
    with `daily`, never a call and never the window's key.
    """
    from harness.research.client import PRIMARY_MODEL, SHADOW_MODEL
    from tests.test_veto_worker import FakeClient

    object.__setattr__(env_settings, "veto_pacing_profile", "near_kickoff_50")
    ids = _expired_cache_bucket(db_session)
    pair = sum((worst_case_usd(model, env_settings.veto_max_searches)
                for model in (PRIMARY_MODEL, SHADOW_MODEL)), Decimal("0"))
    _settled(db_session, chicago_day(NOW), env_settings.veto_daily_usd_cap - pair)
    client = FakeClient()
    counts = veto.veto_pass(db_session, NOW, env_settings, client=client)
    assert (counts["calls"], counts["skipped_budget"]) == (1, 1)
    rows = _decisions(db_session)
    assert [row.signal_id for row in rows] == ids
    assert rows[1].decision == "veto_skipped_budget"
    assert rows[1].reason_code == "daily"                # the cap's code, not "espn_status"
    assert rows[1].feature_delta == {} and rows[1].call_id is None
    assert len(client.calls) == 2                        # one pair, not two


def test_the_expiry_path_is_dormant_while_no_profile_is_named(db_session, env_settings):
    """D47: with `veto_pacing_profile` at its default `None` the elapsed-window half of 1.8(e)
    is not on the live path at all.

    The same fixture as the case above -- a cache twenty minutes old, past
    `VALIDITY_WINDOWS["espn_status"]`, with every invalidator key identical -- and the second
    signal still inherits the trigger's decision from cache: `from_cache = True`, no second
    call, no `{"expired": ...}` delta, and `reserve_spend` asked exactly once. The decided
    population therefore does not move at this release; §1.8 activates as one thing.
    """
    from harness.research import spend as spend_mod
    from tests.test_veto_worker import FakeClient

    assert env_settings.veto_pacing_profile is None
    reservations = []
    real_reserve = spend_mod.reserve_spend

    def counting(session, now, settings, kind, models, searches=None, **kwargs):
        reservations.append(kind)
        return real_reserve(session, now, settings, kind, models, searches, **kwargs)

    veto.reserve_spend = counting
    try:
        ids = _expired_cache_bucket(db_session)
        client = FakeClient()
        counts = veto.veto_pass(db_session, NOW, env_settings, client=client)
    finally:
        veto.reserve_spend = real_reserve
    assert counts["calls"] == 1 and counts["decided"] == 2
    rows = _decisions(db_session)
    assert [row.signal_id for row in rows] == ids
    assert [row.from_cache for row in rows] == [False, True]
    # The cached row carries its ordinary feature delta (the clock moved twenty minutes), and
    # nothing about an expiry: no `{"expired": ...}` key can be written on this path.
    assert "expired" not in rows[1].feature_delta and "window_s" not in rows[1].feature_delta
    assert rows[1].reason_code is None
    assert rows[1].call_id == rows[0].call_id     # the trigger's own answer, inherited
    assert len(client.calls) == 2                 # one pair, not two
    assert reservations == ["veto"]               # one reservation, for the trigger


def test_the_pacing_reservation_is_asked_about_the_items_own_sport(db_session, env_settings):
    """M7: `near_kickoff` is asked for the queue item's sport, not for every window at once.

    Both sports are seeded because `near_kickoff_50` declares a window for each: the call site
    has to name `nfl` for an nfl item and `ncaaf` for an ncaaf one. Under this profile both
    windows are 6 h, so the boolean is the same either way -- the assertion is on what the
    reservation was *asked*, which is what stops a profile with two different windows from
    judging a game against the other sport's.
    """
    from tests.test_veto_worker import FakeClient

    object.__setattr__(env_settings, "veto_pacing_profile", "near_kickoff_50")
    asked = []
    real_near = pacing.near_kickoff

    def spy(profile, now, kickoff, *, sport=None):
        asked.append(sport)
        return real_near(profile, now, kickoff, sport=sport)

    for sport in ("nfl", "ncaaf"):
        db_session.rollback()
        db_session.execute(text("delete from veto_decisions"))
        db_session.execute(text("delete from veto_queue"))
        db_session.commit()
        _expired_cache_bucket(db_session, sport=sport)
        veto.pacing.near_kickoff = spy
        try:
            counts = veto.veto_pass(db_session, NOW, env_settings, client=FakeClient())
        finally:
            veto.pacing.near_kickoff = real_near
        assert counts["calls"] >= 1
    assert asked == ["nfl", "nfl", "ncaaf", "ncaaf"]   # one per paired call, both sports
