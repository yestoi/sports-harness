import os
from datetime import datetime, timedelta
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from sqlalchemy import select, text
from typer.testing import CliRunner

from harness.cli import app
from harness.config.settings import get_settings
from harness.db.models import (
    Intent,
    OrderEvent,
    OddsSnapshot,
    Order,
    Run,
    Signal,
    StrategyVariant,
    VenueQuote,
)
from harness.replay import (
    PopulationError,
    RegisteredVariantError,
    _resolve_variant,
    replay,
)
from harness.strategy.pipeline import price_and_signal
from harness.strategy.variants import Variant, register_variants, variant_from_config
from tests.test_pipeline import NOW, _seed
from tests.test_replay_execute import GRID_S, _finish

runner = CliRunner()

FIXTURES = Path(__file__).parent / "fixtures" / "variants"


def _sharp_direct_config() -> dict:
    return yaml.safe_load((FIXTURES / "tiny.yaml").read_text()) | {"name": "sharp_direct"}


def _register_sharp_direct(db_session):
    variant = variant_from_config(_sharp_direct_config(), "sharp_direct")
    register_variants(db_session, [variant], NOW, prune=False)
    return variant


def _probe_config() -> dict:
    return yaml.safe_load((FIXTURES / "tiny.yaml").read_text()) | {"name": "probe"}


def test_replay_matches_live_signals_and_second_call_inserts_nothing(env_settings, db_session):
    game, run, markets = _seed(db_session)
    _register_sharp_direct(db_session)

    live_result = price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)
    live = live_result["signals"]["sharp_direct"]
    assert live["candidate"] + live["rejected"] == 9

    counts = replay(db_session, env_settings, run.id, run.id, variant_name="sharp_direct")
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
    counts2 = replay(db_session, env_settings, run.id, run.id, variant_name="sharp_direct")
    assert counts2.runs == 1
    assert counts2.signals_candidate == live["candidate"]
    assert counts2.signals_rejected == live["rejected"]
    assert counts2.inserted == 0


def test_replay_file_variant_registers_replay_tier_without_pruning_live_set(env_settings, db_session, tmp_path):
    game, run, markets = _seed(db_session)
    _register_sharp_direct(db_session)
    price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)

    baseline = replay(db_session, env_settings, run.id, run.id, variant_name="sharp_direct")

    wide_config = _sharp_direct_config() | {"name": "wide", "price_band": [0.10, 0.90]}
    variant_file = tmp_path / "wide.yaml"
    variant_file.write_text(yaml.safe_dump(wide_config))

    wide_counts = replay(db_session, env_settings, run.id, run.id, variant_name="wide",
                         variant_file=variant_file)

    assert wide_counts.runs == 1
    assert wide_counts.signals_candidate >= baseline.signals_candidate

    sharp_row = db_session.get(StrategyVariant, variant_from_config(_sharp_direct_config()).variant_id)
    assert sharp_row is not None
    assert sharp_row.active is True

    wide_row = db_session.query(StrategyVariant).filter_by(name="wide").one()
    assert wide_row.tier == "replay"
    assert wide_row.active is True


def test_replay_file_name_mismatch_raises_and_registers_nothing(env_settings, db_session, tmp_path):
    game, run, markets = _seed(db_session)
    _register_sharp_direct(db_session)
    price_and_signal(db_session, run.id, NOW, env_settings, budget_s=20)

    wide_config = _sharp_direct_config() | {"name": "wide", "price_band": [0.10, 0.90]}
    variant_file = tmp_path / "wide.yaml"
    variant_file.write_text(yaml.safe_dump(wide_config))

    with pytest.raises(ValueError, match="does not match"):
        replay(db_session, env_settings, run.id, run.id, variant_name="not_registered",
               variant_file=variant_file)

    assert db_session.query(StrategyVariant).filter_by(name="wide").one_or_none() is None
    assert db_session.query(Signal).filter_by(run_id=run.id, replay=True).count() == 0


def test_replay_range_with_no_snapshots_returns_zero_runs(env_settings, db_session):
    game, run, markets = _seed(db_session)
    _register_sharp_direct(db_session)

    counts = replay(db_session, env_settings, run.id + 1000, run.id + 2000,
                    variant_name="sharp_direct")

    assert counts.runs == 0
    assert counts.signals_candidate == 0
    assert counts.signals_rejected == 0
    assert counts.inserted == 0


def test_replay_unknown_variant_name_raises(env_settings, db_session):
    with pytest.raises(ValueError, match="no variant"):
        replay(db_session, env_settings, 1, 1, variant_name="does_not_exist")


def test_replay_file_refuses_a_registered_live_variant(db_session, tmp_path):
    # A registered primary row, exactly the shape the Amendment 3 incident hit.
    register_variants(db_session, [Variant(name="sharp_direct", tier="primary",
                                           config={"name": "sharp_direct"},
                                           variant_id="abc123abc123")], NOW, prune=False)
    f = tmp_path / "sharp_direct.yaml"
    f.write_text("name: sharp_direct\n")
    with pytest.raises(RegisteredVariantError) as exc:
        _resolve_variant(db_session, "sharp_direct", f, NOW)
    assert "without --file" in str(exc.value)
    # and nothing was renamed or deactivated
    row = db_session.execute(select(StrategyVariant).where(
        StrategyVariant.name == "sharp_direct")).scalar_one()
    assert row.active is True


def test_replay_file_still_allows_an_unregistered_name(db_session, tmp_path):
    f = tmp_path / "probe.yaml"
    f.write_text(yaml.safe_dump(_probe_config()))
    variant = _resolve_variant(db_session, "probe", f, NOW)
    assert variant.tier == "replay"


def test_replay_file_still_allows_a_registered_replay_tier_row(db_session, tmp_path):
    register_variants(db_session, [Variant(name="probe", tier="replay",
                                           config={"name": "probe"},
                                           variant_id="def456def456")], NOW, prune=False)
    f = tmp_path / "probe.yaml"
    f.write_text(yaml.safe_dump(_probe_config()))
    assert _resolve_variant(db_session, "probe", f, NOW).tier == "replay"


def test_replay_without_file_resolves_the_registered_row(db_session):
    register_variants(db_session, [Variant(name="sharp_direct", tier="primary",
                                           config={"name": "sharp_direct"},
                                           variant_id="abc123abc123")], NOW, prune=False)
    assert _resolve_variant(db_session, "sharp_direct", None, NOW).variant_id == "abc123abc123"


# --- C6: the range's own population, under one shared capacity counter -----------------
#
# Everything below is spec 1.6: a replay of a past range has to contend for the same
# `max_open_orders` slots the live loop contended for, which means one executor over the whole
# executed set rather than one per variant, and the set has to come from the range's own record
# rather than from today's `Settings.exec_variants`.


def _pop_variant(name: str) -> Variant:
    """`tiny` under another name: same rules, so the three variants differ only in identity.

    Deliberate. The capacity test is about the shared counter, not about the strategy: three
    identical rule sets produce the same edges on the same markets, so which of them wins a slot
    is decided by the ordering rule alone (`-edge`, then `signal_created_at`, then `signal_id`)
    and the test never has to model a second strategy to explain a starved variant.
    """
    return variant_from_config(_sharp_direct_config() | {"name": name}, name)


def _repriced_run(db_session, source_run_id: int, at: datetime, offset: int) -> Run:
    """Another priced tick `at`: the seeded run's odds and quotes again under a new run id.

    Copying rather than re-fetching keeps every run's inputs identical, so the only thing that
    varies across the range is which run a signal belongs to -- which is exactly what
    `resolve_population` reads. `uq_odds_snapshot_row` and `uq_venue_quote_row` are keyed on
    `raw_id` rather than on the run, so each copy takes its own offset: a later tick read its
    own HTTP responses, and pretending otherwise would collide.
    """
    run = Run(started_at=at, finished_at=at, status="ok")
    db_session.add(run)
    db_session.flush()
    for row in (db_session.query(OddsSnapshot).filter_by(run_id=source_run_id)
                .order_by(OddsSnapshot.id).all()):
        db_session.add(OddsSnapshot(
            raw_id=row.raw_id + offset, run_id=run.id, book=row.book, game_id=row.game_id,
            market_type=row.market_type, outcome_team_id=row.outcome_team_id,
            outcome_side=row.outcome_side, point=row.point, price_decimal=row.price_decimal,
            book_last_update=row.book_last_update, fetched_at=at))
    for row in (db_session.query(VenueQuote).filter_by(run_id=source_run_id)
                .order_by(VenueQuote.id).all()):
        db_session.add(VenueQuote(
            raw_id=row.raw_id + offset, run_id=run.id, venue_market_id=row.venue_market_id,
            yes_bid=row.yes_bid, yes_ask=row.yes_ask, no_bid=row.no_bid, no_ask=row.no_ask,
            yes_bid_size=row.yes_bid_size, yes_ask_size=row.yes_ask_size, volume=row.volume,
            volume_24h=row.volume_24h, open_interest=row.open_interest, fetched_at=at))
    db_session.flush()
    return run


def _live_order(db_session, signal, placed_at, i):
    """One non-replay `orders` row reachable from `signal` through an `intents` row.

    `resolve_population` reads the `orders -> intents -> signals` chain because that is the only
    path from a placed order to the run it was placed in (`config_history` carries no run), so
    the fixture has to build the whole chain rather than an orders row on its own. The row is
    `expired`: the range is in the past, nothing in these tests reads the live partition as
    working orders, and a past order left `open` would be a claim the fixture has no reason to
    make.
    """
    intent = Intent(
        signal_id=signal.id, variant_id=signal.variant_id, venue="kalshi",
        venue_market_id=signal.venue_market_id, ticker=f"KXNFL-P-{signal.venue_market_id}",
        side=signal.side, target_prob=Decimal("0.4000"), target_contracts=Decimal("10.00"),
        edge=signal.edge, fair_p=signal.fair_p, signal_created_at=signal.created_at,
        created_at=signal.created_at, replay=False)
    db_session.add(intent)
    db_session.flush()
    db_session.add(Order(
        intent_id=intent.id, variant_id=signal.variant_id, venue="kalshi", mode="paper",
        client_order_id=f"live-{signal.run_id}-{signal.variant_id}-{i}",
        ticker=intent.ticker, venue_market_id=signal.venue_market_id, side=signal.side,
        prob=Decimal("0.4000"), contracts=Decimal("10.00"), status="expired",
        placed_at=placed_at, expiry=placed_at + timedelta(minutes=5), replay=False))
    db_session.flush()


@pytest.fixture
def seeded_range(env_settings, db_session):
    """Three priced runs, three executed variants, and `max_open_orders = 2`.

    The range is a *record*, not a scenario: each of the three runs carries its own gap
    snapshots (so `replay()` has something to score) and a non-replay order per variant reached
    through `intents -> signals` (so `resolve_population` can read the executed set off the
    orders that were actually placed). Every variant places in every run, so the baseline range
    has exactly one population and no refusal.

    `exec_max_open_orders` is 2 against three variants that each want more than that, which is
    what makes the shared counter visible: the live loop had two slots for the whole executed
    set, and a per-variant replay would hand each variant its own two.

    No tape is seeded. A market with no book is not dirty -- it is R10's `no_book` placement
    path -- so the executor still places, and this task is about which intents get a slot, not
    about how they fill.
    """
    game, run_a, markets = _seed(db_session)
    _finish(db_session, run_a, NOW)
    variants = [_pop_variant(name) for name in ("pop_a", "pop_b", "pop_c")]
    register_variants(db_session, variants, NOW, prune=True)
    db_session.commit()

    runs = [run_a]
    for step in (1, 2):
        runs.append(_repriced_run(db_session, run_a.id, NOW + timedelta(seconds=GRID_S * step),
                                  offset=1_000_000 * step))
    db_session.commit()
    for i, run in enumerate(runs):
        price_and_signal(db_session, run.id, NOW + timedelta(seconds=GRID_S * i), env_settings,
                         budget_s=20)
    db_session.commit()

    by_id = {v.variant_id: v for v in variants}
    for i, run in enumerate(runs):
        placed_at = NOW + timedelta(seconds=GRID_S * i)
        for variant_id in sorted(by_id):
            signal = (db_session.query(Signal)
                      .filter_by(run_id=run.id, variant_id=variant_id, replay=False)
                      .order_by(Signal.id).first())
            assert signal is not None, (run.id, variant_id)
            _live_order(db_session, signal, placed_at, i)
    db_session.commit()

    def register_change(at_run: int, keep: list[str]) -> None:
        """Make the runs from `at_run` on place for only `keep`, as a changed set looks.

        Deleting the other variants' orders for those runs is the simplest way to write "the
        executed set changed here" into a *fixture*; it is not, and must not be read as, an edit
        to the real paper history, where no row is ever rewritten (spec 6.7).
        """
        stale = [row.id for row in db_session.query(Order).filter_by(replay=False).all()
                 if row.variant_id not in keep
                 and int(row.client_order_id.split("-")[1]) >= at_run]
        db_session.query(Order).filter(Order.id.in_(stale)).delete(synchronize_session=False)
        db_session.commit()

    return SimpleNamespace(
        first=runs[0].id, last=runs[-1].id,
        variants=sorted(by_id), names=[by_id[v].name for v in sorted(by_id)],
        settings=env_settings.model_copy(update={"exec_max_open_orders": 2}),
        register_change=register_change)


def _end_of(order) -> datetime | None:
    return order.cancelled_at or order.expiry


def _max_concurrent_open(session) -> int:
    """The most replay orders resting at any one instant.

    Counted over the placement instants, because that is where the count can only rise: an
    order that ends releases a slot, and no slot is ever taken except at a placement. This is
    the quantity `max_open_orders` bounds, and it is what a shared counter makes true of the
    whole population rather than of one variant at a time.
    """
    rows = session.query(Order).filter_by(replay=True).all()
    return max((sum(1 for row in rows
                    if row.placed_at <= instant
                    and (_end_of(row) is None or instant < _end_of(row)))
                for instant in {row.placed_at for row in rows}), default=0)


def _orders_for(session, variant_id: str) -> int:
    return session.query(Order).filter_by(replay=True, variant_id=variant_id).count()


def _replay_order_count(session) -> int:
    return session.query(Order).filter_by(replay=True).count()


def _clear_replay_rows(session) -> None:
    """Empty the replay partition between two replays of the same range.

    `store.working_orders` scopes open orders by `replay` and not by variant -- correctly, since
    that is the shared counter this task is about -- so a single-variant replay run after a
    population replay would find the population replay's own orders resting in its slots. The
    two replays are alternative treatments of one range, never a sequence, so the second starts
    from the record the first started from.
    """
    session.execute(text("delete from fills where order_id in "
                         "(select id from orders where replay = true)"))
    session.execute(text("delete from order_events where replay = true"))
    session.execute(text("delete from orders where replay = true"))
    session.execute(text("delete from intents where replay = true"))
    session.execute(text("delete from signals where replay = true"))
    session.commit()
    session.expire_all()


def _cli(db_session, monkeypatch, args) -> object:
    """`harness replay ...` against the test database, with the fixture's rows committed."""
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    db_session.commit()
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    try:
        return runner.invoke(app, args)
    finally:
        get_settings.cache_clear()


@pytest.fixture
def capacity_skips(monkeypatch):
    """Every `exec_capacity` skip the planner *decided*, as planner actions rather than rows.

    Counted here and not off `order_events` because the two quantities differ by construction:
    `uq_skip_once` keys on `(intent_id, kind, reason)`, so an intent skipped for capacity at
    six consecutive instants leaves one row. Spec 1.6 asks for the occurrences, and the only
    place they exist is `plan_actions`'s return value -- `ExecStats.skipped` is incremented
    only when the row was actually written (`harness/execution/loop.py`), so it is already the
    row count, and it carries no reason. Wrapping the loop module's own reference is the same
    instrument `test_replay_advances_its_book_instead_of_rebuilding_it` uses for `load_book_at`
    and changes no production code.
    """
    from harness.execution import loop as loop_mod
    from harness.execution.plan import EXEC_CAPACITY, Skip

    real = loop_mod.plan_actions
    seen: list = []

    def counting(*args, **kwargs):
        actions = real(*args, **kwargs)
        seen.extend(a for a in actions
                    if isinstance(a, Skip) and a.reason == EXEC_CAPACITY)
        return actions

    monkeypatch.setattr(loop_mod, "plan_actions", counting)
    return seen


def _intent_keys(session) -> set:
    """Every `(variant, market, side)` key the replay's intents carry.

    The planner's own placement key (`plan._key`), so this is the set it ranks and the set the
    two capacity slots are taken from.
    """
    return {(row.variant_id, row.venue_market_id, row.side)
            for row in session.query(Intent).filter_by(replay=True).all()}


def _capacity_skip_rows(session) -> int:
    """Distinct replay intents carrying an `exec_capacity` skip row."""
    return (session.query(OrderEvent)
            .filter_by(replay=True, kind="skipped", reason="exec_capacity").count())


def test_a_range_population_replay_shares_one_capacity_counter(db_session, seeded_range,
                                                               capacity_skips):
    """Expected: at most two orders open at any instant, over the whole population.

    Derived from the planner's own rule rather than from the code: `plan_actions` handles open
    orders first and then intents by edge, and `max_open_orders` is 2 here, so the two
    highest-edge intents of the whole executed set fill both slots and every other intent of
    that instant is skipped for capacity -- whichever variant it belongs to. A single-variant
    executor would give each of the three variants its own pair of slots, which is three times
    the capacity the live loop had.
    """
    counts = replay(db_session, seeded_range.settings, seeded_range.first, seeded_range.last,
                    population="range", execute=True)
    db_session.commit()
    db_session.expire_all()

    assert counts.mode == "range_population"
    assert sorted(counts.population) == sorted(seeded_range.variants)
    assert counts.orders > 0, "a replay that placed nothing tests no capacity rule"
    assert _max_concurrent_open(db_session) <= 2
    assert _replay_order_count(db_session) <= 2

    # The skip *occurrences*, derived from the ordering rule rather than from the code: at every
    # one of the grid's instants the planner ranks the whole population's placeable keys and can
    # place only while `still_open < 2`, so every key that is neither holding one of the two
    # resting orders nor winning a slot is skipped for capacity -- at that instant and at every
    # instant after it, since the two orders rest to their expiry. That is
    # `grid_steps * (keys - 2)` decisions, and it is the quantity a single-variant executor
    # could not produce: with the counter to itself each variant would place instead of skip.
    keys = _intent_keys(db_session)
    assert len(keys) == 12 and counts.orders == 2

    # The capacity rule binds while the newest signal on a key is still a candidate: the
    # `latest_decision == REJECTED` test precedes the capacity test (D14, T5), so once a later
    # run's signal rejects a key, that key stops reaching the capacity rule at all. Here every
    # key's signals after the first run are rejected -- asserted, not assumed -- so the binding
    # instants are the grid instants from the first run's clock up to the second run's.
    later = {row.decision for row in db_session.query(Signal)
             .filter(Signal.replay.is_(True), Signal.run_id > seeded_range.first,
                     Signal.venue_market_id.in_({key[1] for key in keys})).all()}
    assert later == {"rejected"}
    binding = GRID_S // seeded_range.settings.exec_period_s
    assert binding == 3

    # 12 keys compete for 2 slots at the first instant: 2 place and 10 are skipped for
    # capacity. At each of the next two instants the two resting orders hold their own keys, so
    # the same 10 compete for no slot at all and are skipped again. 3 x 10 = 30 planner
    # actions, and not one of them would exist under a per-variant executor, where each
    # variant's 4 keys would meet 2 slots of its own.
    assert len(capacity_skips) == binding * (len(keys) - counts.orders) == 30

    # The rows are a different quantity, asserted separately so the two are never confused:
    # `uq_skip_once` keys on `(intent_id, kind, reason)`, so the 30 occurrences above are 10
    # rows -- one per intent the counter turned away, whatever number of instants it turned it
    # away at. That gap is why spec 1.6 asks for the planner actions and the rows apart.
    assert counts.capacity_skips == _capacity_skip_rows(db_session)
    assert counts.capacity_skips == len(keys) - counts.orders == 10


def test_a_single_variant_replay_of_the_same_range_produces_more_orders(db_session,
                                                                        seeded_range):
    """Expected: strictly more orders for a starved variant than the shared replay gave it.

    Derived independently: three variants want more than the two slots the population has, so
    at least one of them ends the shared replay with nothing -- it lost every slot to an intent
    of another variant. Replayed alone, with the counter to itself, it is never skipped for
    capacity another variant consumed, and the intents it lost become orders. That inequality is
    the whole point of C6: a single-variant replay is not a baseline for a live loop that shared
    one counter.
    """
    shared = replay(db_session, seeded_range.settings, seeded_range.first, seeded_range.last,
                    population="range", execute=True)
    db_session.commit()
    db_session.expire_all()
    shared_by_variant = {v: _orders_for(db_session, v) for v in seeded_range.variants}
    assert shared.orders == sum(shared_by_variant.values())
    starved = [v for v, n in shared_by_variant.items() if n == 0]
    assert starved, f"the shared counter starved nobody: {shared_by_variant}"

    _clear_replay_rows(db_session)
    name = seeded_range.names[seeded_range.variants.index(starved[0])]
    alone = replay(db_session, seeded_range.settings, seeded_range.first, seeded_range.last,
                   variant_name=name, execute=True)
    db_session.commit()
    db_session.expire_all()

    assert alone.mode == "single_variant"
    assert alone.population == (starved[0],)
    assert alone.orders > shared_by_variant[starved[0]]


def test_a_range_spanning_a_change_of_the_executed_set_is_refused(monkeypatch, db_session,
                                                                  seeded_range):
    """Expected: `PopulationError`, nothing written, and the CLI exits 3.

    Derived independently: the executed set changed during the paper run (pre-registration
    Amendments 2, 3 and 4), and a range that spans such a change has no single population to be
    equivalent to. Reporting a number for it would be reporting the average of two different
    experiments. The refusal is total -- no partial output -- because a partially written replay
    is worse than none: its rows look like every other replay row.
    """
    seeded_range.register_change(seeded_range.first + 1, [seeded_range.variants[0]])

    with pytest.raises(PopulationError, match="change of the executed set"):
        replay(db_session, seeded_range.settings, seeded_range.first, seeded_range.last,
               population="range", execute=True)
    assert _replay_order_count(db_session) == 0
    assert db_session.query(Signal).filter_by(replay=True).count() == 0

    # `--variant` is optional after step 1, so this reaches `replay()` and exits on the refusal
    # rather than on a missing option (Typer's own exit 2).
    result = _cli(db_session, monkeypatch,
                  ["replay", "--from-run", str(seeded_range.first),
                   "--to-run", str(seeded_range.last), "--population", "range"])
    assert result.exit_code == 3, result.output
    assert _replay_order_count(db_session) == 0


def test_the_command_refuses_a_range_that_spans_the_deploy_boundary(monkeypatch, db_session,
                                                                    seeded_range):
    """Expected: exit 3 and nothing written, for a range straddling `--boundary-run-id`.

    Derived independently: the two sides of the 6B deploy are scored by different simulators, so
    a single number over both is their average rather than either -- the same objection as a
    change of the executed set, for a different reason. The boundary is a number only the
    controller knows, which is why it is an option and not a constant: a task that compiled one
    in would be deciding a measurement boundary the user has not been asked about.
    """
    result = _cli(db_session, monkeypatch,
                  ["replay", "--from-run", str(seeded_range.first),
                   "--to-run", str(seeded_range.last), "--population", "range",
                   "--boundary-run-id", str(seeded_range.first)])
    assert result.exit_code == 3, result.output
    assert _replay_order_count(db_session) == 0


def test_the_command_refuses_a_replay_with_neither_variant_nor_population(monkeypatch,
                                                                          db_session,
                                                                          seeded_range):
    """Expected: exit 1, the bad-arguments code, not the refusal code.

    Derived independently: with `--variant` now optional, a command carrying neither it nor
    `--population` names no set at all. That is an operator error the operator fixes by typing
    more, which is exit 1; exit 3 means the range itself has no single baseline and the fix is
    to split it. Keeping the two apart is what lets the controller script the difference.
    """
    result = _cli(db_session, monkeypatch,
                  ["replay", "--from-run", str(seeded_range.first),
                   "--to-run", str(seeded_range.last)])
    assert result.exit_code == 1, result.output


def test_the_counts_carry_both_step_counts_and_both_correction_sets(db_session, seeded_range):
    """Expected: the resolved population, today's set, the correction ids on each side, and both
    step counts -- with no parity verdict anywhere in the output.

    Derived independently: the grid steps exactly `exec_period_s`, while the live loop ran 27
    steps in the 19:00 CT hour where the grid would have run 240. A 2 % pass/fail across that
    difference would be a verdict about the timing policy rather than about the replay, so the
    verdict is suspended and the numbers are published in its place (D15, ruling IM-6).
    Restoring it needs 6D's instrumentation, which is why `live_steps` is still None here.
    """
    counts = replay(db_session, seeded_range.settings, seeded_range.first, seeded_range.last,
                    population="range", execute=True)

    # The three runs are 45 s apart, so the range is 90 s wide, and the grid steps exactly
    # `exec_period_s` across it: 0, 15, 30, 45, 60, 75 and 90 s. Seven instants, of which only
    # three are a run's own pricing clock -- which is the timing policy the suspended parity
    # verdict is about, stated here as a number rather than judged.
    assert counts.grid_steps == 2 * GRID_S // seeded_range.settings.exec_period_s + 1 == 7
    assert counts.live_steps is None
    assert counts.today_variants == tuple(seeded_range.settings.exec_variants)
    assert counts.population != counts.today_variants
    assert counts.corrections_replayed and counts.corrections_live
    assert set(counts.corrections_replayed) <= set(counts.corrections_live)


def test_a_truncated_population_read_is_refused_rather_than_answered(db_session, seeded_range):
    """Expected: `PopulationError` naming the cap, and nothing written.

    Derived independently: `_POPULATION_BY_RUN` ends in `limit :cap`, and a truncated read is
    indistinguishable from a complete one -- the rows that were cut are the *tail*, which is
    where a later change of the executed set would be. Answering from a truncated read would
    replay a wide range under the population of its earlier half and say nothing about it,
    which is the one failure this refusal exists to prevent; a range that hits the cap is
    therefore refused exactly like a range that spans a change. Reaching the cap exactly is
    refused too, because the query cannot tell the two apart -- the operator splits the range
    either way.
    """
    from harness.replay import resolve_population

    # Three runs, three variants: nine (run, variant) rows, so a cap of two truncates.
    with pytest.raises(PopulationError, match="cap"):
        resolve_population(db_session, seeded_range.first, seeded_range.last, None, cap=2)
    assert _replay_order_count(db_session) == 0
    assert db_session.query(Signal).filter_by(replay=True).count() == 0

    # Above the cap the same range answers, which is what makes the refusal above a statement
    # about the truncation rather than about the range.
    assert resolve_population(db_session, seeded_range.first, seeded_range.last, None,
                              cap=10) == sorted(seeded_range.variants)


def test_an_unfilled_correction_range_is_in_force_and_a_numeric_one_is_tested(monkeypatch):
    """Expected: prose covers any range; `A-B` covers only the ranges it overlaps.

    Derived from the manifest rather than from the code: C0's `affected_run_id_range` is still
    the controller's prose placeholder, and reporting "no corrections in force" from a field
    nobody has filled in would turn a missing value into a measurement claim. The numeric branch
    is the one the ranges get parsed by the day they are filled in, so it is asserted now rather
    than on the merge commit that first exercises it.
    """
    from dataclasses import replace as _dc_replace

    from harness import replay as replay_mod
    from harness.corrections import CORRECTIONS
    from harness.replay import _corrections_for, _parse_run_range

    assert _parse_run_range("all runs through the 6B deploy") is None
    assert _parse_run_range("100-200") == (100, 200)
    assert _parse_run_range("100-two hundred") is None
    assert _corrections_for(1, 2) == tuple(c.id for c in CORRECTIONS)

    numeric = _dc_replace(CORRECTIONS[0], id="CX", affected_run_id_range="100-200")
    monkeypatch.setattr(replay_mod, "CORRECTIONS", [numeric])
    assert _corrections_for(300, 400) == ()
    assert _corrections_for(50, 99) == ()
    assert _corrections_for(150, 400) == ("CX",)
    assert _corrections_for(200, 400) == ("CX",)

