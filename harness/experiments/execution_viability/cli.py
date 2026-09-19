"""`harness exp …` (§1.1). Every subcommand of this milestone lands here with its implementation."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import hashlib

import typer
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, OperationalError

from harness.config.settings import get_settings
from harness.execution.book import newest_ws_connect
from harness.experiments.execution_viability import (EXP_DB_ROLE, EXP_LABEL,
                                                     IsolationError, exp_label)
from harness.experiments.execution_viability import (adapter, arms, baseline, bookhealth,
                                                     capture, decision, episodes, forecast,
                                                     liquidity, outcomes, report, source,
                                                     storage, veto_profile)
from harness.logging_setup import configure_logging
from harness.research import pacing

exp_app = typer.Typer(no_args_is_help=True,
                      help="Phase 6D.1's isolated execution-viability experiment (exploratory)")

#: §3 row 2's privilege read-back, run against the server rather than inferred from the code.
_PRIVILEGE_READBACK = text(
    "select t, has_table_privilege(:role, t, 'INSERT') as may from unnest(array["
    "'orders','fills','intents','signals','ledger','source_state','research_spend',"
    "'veto_decisions']) t")   # eight catalogue lookups, no table read


@exp_app.command("isolation-check")
def isolation_check() -> None:
    """Print the privilege read-back of §3 row 2. Reads nothing else and writes nothing."""
    configure_logging()
    # §0.6 (ruling D50): the label first, like every other `harness exp` command. This command
    # takes no argument to validate, so "after argument validation" is immediately; it used to
    # print the label last, which left the first line of the one command an operator runs
    # before the grant saying nothing about whose numbers these are.
    print(EXP_LABEL)
    s = get_settings()
    with source.reader(s) as session:
        role = session.execute(text("select current_user")).scalar()
        rows = session.execute(_PRIVILEGE_READBACK, {"role": EXP_DB_ROLE}).all()
        exp_insert = session.execute(text(
            "select has_table_privilege(:role, 'exp_run', 'INSERT')"), {"role": EXP_DB_ROLE}
        ).scalar()
    print(f"role={role} exp_tables={len(storage.load_exp_metadata().tables)}")
    for name, may in rows:
        print(f"  {name:<16} insert={may}")
    print(f"  exp_run          insert={exp_insert}")


#: One aggregate per interval over `orderbook_events`, bounded by `ticker = :t and ts >= :start
#: and ts < :end` inside the week's partition, riding `ix_obe_ticker_ts (ticker, ts)`.
_INTERVAL_TAPE = text(
    "select count(*) filter (where kind = 'delta') as deltas, "
    "count(*) filter (where kind = 'snapshot') as snapshots, "
    "max(seq) filter (where kind = 'delta') as seq_max, "
    "max(ts) as last_ts "
    "from orderbook_events where ticker = :t and ts >= :start and ts < :end")

#: The WebSocket anchor in force at an instant: one row on the same access path.
_ANCHOR_AT = text(
    "select id, sid, seq from orderbook_events "
    "where ticker = :t and kind = 'snapshot' and ts <= :instant "
    "order by ts desc, id desc limit 1")

#: Issued only for an interval whose own aggregate already counted a snapshot row in it.
_NEWEST_SNAPSHOT_IN = text(
    "select id, sid, seq from orderbook_events "
    "where ticker = :t and kind = 'snapshot' and ts >= :start and ts < :end "
    "order by ts desc, id desc limit 1")

#: The first gap **inside this interval** on the subscription the interval is judged on (review
#: fix D5). `store.first_gap_ts` answers a different question - the first gap since the *anchor* -
#: which is the same instant for every interval after the one it happened in, so a second gap on
#: an un-reanchored subscription would never be returned at all and a quiet stretch during a real
#: recorder outage would read as `inactive_confirmed`. Bounded by the interval, pruned to the
#: week's partition and served by the partial index `ix_obe_gap (sid, id) where kind = 'gap'`.
#:
#: There is deliberately no `ticker` predicate: **every** gap row the recorder writes carries
#: `ticker = ''` (`harness/recorder/ws_sink.py` writes both the seq-gap row and the sink-exception
#: row that way, and `store.first_gap_ts`'s own docstring says so), because a gap is per
#: subscription and dirties every ticker on it (§0.12). A `ticker = :t` bound would match no gap
#: row in production at all.
_FIRST_GAP_IN = text(
    "select min(ts) from orderbook_events "
    "where kind = 'gap' and sid = :sid and ts >= :start and ts < :end")

#: The interval's public prints, on `ix_trades_ticker_ts`: §1.7's fourth fixture turns on them.
_PRINTS_IN = text(
    "select count(*) from venue_trades where ticker = :t and ts >= :start and ts < :end")


def _observe_intervals(session, ticker: str, since: datetime, until: datetime,
                       interval_s: int) -> list[bookhealth.HealthInput]:
    """One `HealthInput` per interval, every read bounded by that interval.

    The anchor is read once, before the loop, and carried forward: it can only change where a
    snapshot row landed, which the interval's own aggregate already counts. The gap question is
    asked of the interval itself rather than of the anchor, so the second gap on one subscription
    is as visible as the first (review fix D5).
    """
    anchor = session.execute(_ANCHOR_AT, {"t": ticker, "instant": since}).first()
    # No WebSocket snapshot at or before `since` means there is no sequence to continue: the
    # executor would be on a REST ladder (sid 0, seq 0), which `classify` reads as unknown
    # continuity rather than as a quiet market.
    source_, anchor_id, sid, seq = (("ws", anchor.id, anchor.sid, anchor.seq) if anchor
                                    else ("rest", 0, 0, 0))
    step = timedelta(seconds=interval_s)
    out: list[bookhealth.HealthInput] = []
    start = since
    while start < until:
        end = min(start + step, until)
        bounds = {"t": ticker, "sid": sid, "start": start, "end": end}
        tape = session.execute(_INTERVAL_TAPE, bounds).one()
        prints = session.execute(_PRINTS_IN, bounds).scalar() or 0
        # A gap is per subscription, so a REST anchor (sid 0) has no subscription to ask about.
        gap_ts = session.execute(_FIRST_GAP_IN, bounds).scalar() if sid else None
        reanchored = bool(tape.snapshots)
        # A re-anchor restarts the sequence, so no advance across that boundary is comparable;
        # the interval is unresolved on continuity grounds and carries no invented advance.
        seq_after = seq if (reanchored or tape.seq_max is None) else int(tape.seq_max)
        out.append(bookhealth.HealthInput(
            ticker=ticker, interval_start=start, interval_end=end, anchor_source=source_,
            anchor_id=anchor_id, sid=sid, seq_before=seq, seq_after=seq_after,
            events_in_interval=int(tape.deltas), reconnect_at=newest_ws_connect(session, at=end),
            first_gap_ts=gap_ts, last_event_ts=tape.last_ts, reanchored=reanchored,
            prints_in_interval=int(prints)))
        if reanchored:
            fresh = session.execute(_NEWEST_SNAPSHOT_IN, bounds).one()
            source_, anchor_id, sid, seq = "ws", fresh.id, fresh.sid, fresh.seq
        else:
            seq = seq_after
        start = end
    return out


@exp_app.command("book-health")
def book_health(ticker: str = typer.Option(..., "--ticker"),
                since: datetime = typer.Option(..., "--since", formats=["%Y-%m-%dT%H:%M:%S%z"]),
                until: datetime = typer.Option(..., "--until", formats=["%Y-%m-%dT%H:%M:%S%z"]),
                interval_s: int = typer.Option(600, "--interval-s"),
                run_id: str = typer.Option(None, "--run-id"),
                persist: bool = typer.Option(False, "--persist/--no-persist")) -> None:
    """Classify one ticker's intervals (§1.7). Reads through the read-only reader; writes only
    with --persist, and only into exp_book_health."""
    configure_logging()
    if until <= since:
        raise typer.BadParameter("--until must be after --since")
    if interval_s <= 0:
        raise typer.BadParameter("--interval-s must be a positive number of seconds")
    if persist and not run_id:
        raise typer.BadParameter("--persist needs --run-id: every exp_book_health row carries it")
    # §0.6: every `harness exp` command says whose numbers these are before it prints any.
    print(EXP_LABEL)
    s = get_settings()
    # The sample frame is re-derivable from the command's own arguments (§1.7c).
    seed = int(since.timestamp())
    with source.reader(s) as session:
        observations = _observe_intervals(session, ticker, since, until, interval_s)
        verified = {obs.interval_start
                    for obs in bookhealth.sample_intervals(observations, seed=seed)}
        # M5: `HealthRow` is `frozen=True`, so the verified snapshot is folded into the
        # evidence **before** the row is constructed rather than written into the dict a frozen
        # row already holds. The sample frame is resolved first for the same reason.
        rows = []
        for obs in observations:
            row = bookhealth.classify(obs)
            if obs.interval_start in verified:
                row = replace(row, evidence={
                    **row.evidence,
                    "snapshot_at_interval_end": bookhealth.verify_snapshot(
                        session, ticker, row.interval_end)})
            rows.append(row)
    counts = bookhealth.summarize(rows)
    print(f"ticker={ticker} intervals={len(rows)} verified={len(verified)} "
          f"interval_s={interval_s} seed={seed}")
    for name in bookhealth.CLASSIFICATIONS:
        print(f"  {name:<20} {counts[name]}")
    for caveat in bookhealth.CAVEATS:
        print(f"  caveat: {caveat}")
    if persist:
        writer = storage.ExperimentWriter.open(s, run_id=run_id)
        try:
            written = bookhealth.persist(writer, rows)
            writer.commit()
            print(f"persisted={written} table=exp_book_health run={run_id}")
        finally:
            writer.close()


@exp_app.command("veto-profile")
def veto_profile_cmd(
        name: str = typer.Option(..., "--name",
                                 help=f"one of {', '.join(pacing.PROFILE_NAMES)}"),
        preflight: bool = typer.Option(False, "--preflight/--no-preflight"),
        since: datetime = typer.Option(None, "--since", formats=["%Y-%m-%dT%H:%M:%S%z"]),
        until: datetime = typer.Option(None, "--until", formats=["%Y-%m-%dT%H:%M:%S%z"]),
        cost_per_pair: float = typer.Option(
            0.085, "--cost-per-pair",
            help="dollars per paired call; the review's measured $0.085 by default"),
) -> None:
    """Print §1.8's pacing profile, its hash and §0.14c's question; with --preflight, replay the
    stored arrivals of a window against it.

    **Activates nothing.** No `Settings` value is written, no `veto_decisions` row is written and
    no model is called: the profile stays dormant until the user's dated decision (§0.14c), which
    is also §0.8's amendment instant. Without --preflight the command reads no database at all.
    """
    configure_logging()
    # §0.6: every `harness exp` command says whose numbers these are before it prints any.
    print(EXP_LABEL)
    try:
        profile = pacing.load_profile(name)
    except KeyError as unknown:
        raise typer.BadParameter(str(unknown)) from None
    hours = max((w.hours_before_kickoff for w in profile.windows), default=0)
    reserved = max((w.reserved_fraction for w in profile.windows), default=0)
    print(f"profile={profile.name} hash={profile.profile_hash()}")
    print(f"  json            {veto_profile.profile_json(profile)}")
    print(f"  reserved        {reserved} of each day for signals inside {hours} h of kickoff")
    for window in profile.windows:
        print(f"    window        {window.sport:<8} {window.hours_before_kickoff} h "
              f"{window.reserved_fraction}")
    for day in sorted(profile.weekday_allocation):
        print(f"    weekly        {day:<8} {profile.weekday_allocation[day]}")
    print(f"  release         {profile.release_hour_ct}:00 America/Chicago")
    print(f"  claim order (today, in force)                    "
          f"{veto_profile.CLAIM_ORDER_BEFORE}")
    print(f"  claim order (under this profile, not in force)   "
          f"{veto_profile.CLAIM_ORDER_AFTER}")
    print("  status          dormant; activation is the user's dated decision (§0.14c) and this "
          "command writes no setting")
    print(f"question (§0.14c, unanswered): {veto_profile.AMENDMENT_QUESTION}")
    if not preflight:
        return
    if since is None or until is None or until <= since:
        raise typer.BadParameter("--preflight needs --since and --until, with --until after it")
    now = datetime.now(timezone.utc)
    s = get_settings()
    with source.reader(s) as session:
        report = veto_profile.preflight(
            session, profile, since=since, until=until, now=now,
            cost_per_pair=Decimal(str(cost_per_pair)), daily_cap=s.veto_daily_usd_cap,
            weekly_cap=s.veto_weekly_usd_cap)
    print(veto_profile.render_preflight(report))
    print(veto_profile.amendment_record(profile, prepared_at=now))


def _tickers(raw: str) -> list[str]:
    out = [t.strip() for t in raw.split(",") if t.strip()]
    if not out:
        raise typer.BadParameter("--tickers takes a comma-separated list of venue tickers")
    return out


def _clock(session, *, since: datetime, until: datetime, variant_ids: list[str]):
    """The resolved clock and the number printed beside it (§1.3d, C1)."""
    instants = capture.resolve_instants(session, warmup_start=since, observation_end=until,
                                        variant_ids=variant_ids)
    samples, p50_ms = capture.loop_sample_stats(session, warmup_start=since,
                                                observation_end=until)
    span_s = (until - since).total_seconds()
    estimate = (capture.live_loop_estimate(samples, span_s, p50_ms)
                if samples and p50_ms > 0 else 0)
    return instants, samples, estimate


@exp_app.command("capture")
def capture_cmd(run_id: str = typer.Option(..., "--run-id"),
                since: datetime = typer.Option(..., "--since",
                                               formats=["%Y-%m-%dT%H:%M:%S%z"]),
                until: datetime = typer.Option(..., "--until",
                                               formats=["%Y-%m-%dT%H:%M:%S%z"]),
                tickers: str = typer.Option(..., "--tickers"),
                variants: str = typer.Option(..., "--variants")) -> None:
    """Write one slice of the tape to the hashed NDJSON tree (§1.3a) and print each file's
    sha256. Reads through the read-only reader, so it fails closed without the grant."""
    configure_logging()
    if until <= since:
        raise typer.BadParameter("--until must be after --since")
    ticker_list = _tickers(tickers)
    variant_ids = [v.strip() for v in variants.split(",") if v.strip()]
    if not variant_ids:
        raise typer.BadParameter("--variants takes a comma-separated list of variant ids")
    # §0.6: every `harness exp` command says whose numbers these are before it prints any.
    print(EXP_LABEL)
    s = get_settings()
    with source.reader(s) as session:
        # The clock is resolved first: its count and the estimate beside it are what the run's
        # `loop_spacing_unreconstructable` row records, so no capture persists a zeroed one.
        instants, samples, estimate = _clock(session, since=since, until=until,
                                             variant_ids=variant_ids)
        result = capture.capture_slice(
            s, session, run_id=run_id, warmup_start=since, observation_end=until,
            tickers=ticker_list, variant_ids=variant_ids, instants=len(instants),
            live_estimate=estimate)
    print(f"run={run_id} tickers={len(ticker_list)} dir={storage.run_dir(s, run_id)}")
    # C1: the resolved instant count is printed beside the live loop estimate, always, so the
    # thinned opportunity clock cannot be read as the live one.
    print(f"  retained action instants   {len(instants)}")
    print(f"  live loop estimate         {estimate} (from {samples} exec.loop_ms samples)")
    for stream in capture.CAPTURE_STREAMS:
        print(f"  {stream:<14} sha256={result.hashes[stream]}")
    # §1.2: the manifest entry carries each stream's statement and bound parameters beside its
    # digest, which is what `capture_hash_mismatch` is evaluated against later.
    print(f"  manifest entry  streams={len(result.capture_hashes)} "
          f"canonical_json_bytes={len(result.manifest_json)}")
    for limitation in result.limitations:
        print(f"  limitation      {limitation.kind}")


@exp_app.command("baseline-check")
def baseline_check(run_id: str = typer.Option(..., "--run-id"),
                   since: datetime = typer.Option(..., "--since",
                                                  formats=["%Y-%m-%dT%H:%M:%S%z"]),
                   until: datetime = typer.Option(..., "--until",
                                                  formats=["%Y-%m-%dT%H:%M:%S%z"]),
                   variants: str = typer.Option(..., "--variants")) -> None:
    """Compare arm A against the recorded slice at the retained action instants (§1.3f).

    Writes nothing: it steps the shared functions through the read-only reader and prints the
    comparison. Missing input history reads `incomplete/unverifiable`, never as a pass.
    """
    configure_logging()
    if until <= since:
        raise typer.BadParameter("--until must be after --since")
    variant_ids = [v.strip() for v in variants.split(",") if v.strip()]
    if not variant_ids:
        raise typer.BadParameter("--variants takes a comma-separated list of variant ids")
    # §0.6: the label first, after the arguments are validated and before any number.
    print(EXP_LABEL)
    s = get_settings()
    with source.reader(s) as session:
        instants, _samples, estimate = _clock(session, since=since, until=until,
                                              variant_ids=variant_ids)
        # Function scope, like `storage.load_exp_metadata`: this package imports no executor
        # module at import time, and `tests/test_exp_isolation.py` asserts that shape.
        from harness.execution.plan import ExecSettings
        from harness.execution.store import variant_configs

        runner = adapter.ArmRunner(run_id=run_id, arm_id="A", policy=None,
                                   variant_cfg=variant_configs(session, variant_ids),
                                   exec_settings=ExecSettings.from_settings(s), walkers={},
                                   tz=s.tz_local)
        steps = runner.run(session, list(instants))
        produced = [action for result in steps for action in result.actions]
        produced_fills = [fill for result in steps for fill in result.fills]
        recorded = baseline.recorded_actions(session, warmup_start=since, observation_end=until,
                                             variant_ids=variant_ids)
        tape_fills = baseline.recorded_fills(session, warmup_start=since, observation_end=until,
                                             variant_ids=variant_ids)
    # §1.3(f) compares the actions **and** the watched fills: a lifecycle that places and
    # cancels at the right instants but fills differently is not a reproduction of it.
    mismatches = (baseline.compare_actions(recorded, produced, run_id=run_id, arm_id="A")
                  + baseline.compare_fills(tape_fills, produced_fills, run_id=run_id,
                                           arm_id="A"))
    spacing = capture.spacing_limitation(run_id, warmup_start=since, observation_end=until,
                                         instants=len(instants), live_estimate=estimate,
                                         now=datetime.now(timezone.utc))
    print(baseline.render_baseline(mismatches, instants=len(instants), live_estimate=estimate,
                                   limitations=[spacing]))


#: The sport the window's orders belong to, on the same access path the instants query uses
#: (`ix_orders_key_placed`): `interval_for(sport, ...)` answers for **one** sport, so arm B's
#: allowance may not be built for a window that resolves to two of them or to none.
_RUN_SPORTS = text(
    "select distinct sport from orders where variant_id = any(:variant_ids) "
    "and placed_at >= :start and placed_at <= :end and replay = false "
    "and sport is not null")


def _window_sport(session, *, variant_ids: list[str], since: datetime, until: datetime) -> str:
    """The one sport arm B's cadence is read against, or a refusal (§1.6a)."""
    rows = session.execute(_RUN_SPORTS, {"variant_ids": list(variant_ids), "start": since,
                                         "end": until}).scalars().all()
    sports = sorted({row for row in rows if row})
    if len(sports) != 1:
        named = ", ".join(sports) if sports else "no sport at all"
        raise typer.BadParameter(
            f"arm B's allowance is derived per sport and this window resolves to {named}: "
            "`interval_for(sport, now, kickoffs, tz)` answers for one sport, so a window that "
            "crosses two -- or reconstructs none -- is refused rather than decided under a "
            "guess (§1.6a)")
    return sports[0]


def _cadence_allowance(session, spec, s, *, variant_ids: list[str], since: datetime,
                       until: datetime, window_start: datetime):
    """§1.6(a)/M2: arm B's allowance, resolved for **this** run before the runner is built.

    An arm whose policy declares no allowance (§1.6's arm A) gets `None` and is untouched. An
    arm that declares one gets the run's own as-of kickoff reconstruction (ruling I3), this
    deployment's tick budget and the capture window the walk-back may not leave -- so a run
    labelled `B` can never quietly decide under arm A's rule, and a run that could not build
    the allowance is refused here rather than stepped.
    """
    if spec.policy.cadence_allowance is None:
        return None
    sport = _window_sport(session, variant_ids=variant_ids, since=since, until=until)
    return arms.cadence_allowance_for(
        sport,
        lambda at: capture.kickoffs_asof(session, at=at, sport=sport,
                                         variant_ids=list(variant_ids), since=window_start),
        s.tz_local, tick_budget_s=s.tick_budget_s, exec_period_s=s.exec_period_s,
        window_start=window_start)


#: The run's frozen manifest and its hash, on `exp_run`'s own primary key `run_id` (§4.1): one
#: row, one index lookup, no scan. The resume point itself is read by `storage.resume`, which
#: is the only reader of `exp_checkpoint` (§1.2's refusal happens there and nowhere else).
_EXP_RUN_MANIFEST = text("select manifest, manifest_hash from exp_run where run_id = :r")


def _check_resume(stored_hash: str, manifest) -> None:
    """§1.2's refusal at the **run** level, before the arm's checkpoint is read at all."""
    from harness.experiments.execution_viability.manifest import check_resume

    check_resume(stored_hash, manifest)


#: G2/1.9(a): the arm's own orders for this run, closed and open, in the shape
#: `outcomes.record_outcomes` reads them. `exp_order` keeps no fill instant of its own (2: the
#: lifecycle columns are `placed_at`, `expiry`, `cancelled_at`), so the entry instant is the
#: **first** fill of the order -- the price the markouts are measured from is the order's own
#: `prob`, which is what T4's schedule defines the outcome against. An order that never filled
#: carries a null and `record_outcomes` skips it rather than recording a missing outcome for it.
#: `ix_exp_order_run_arm (run_id, arm_id, placed_at)` bounds the scan to this run's arm.
#: M-b: the entry instants are a **grouped** left join, not a correlated subquery. The
#: correlated form re-walked the arm's whole fill range once per order -- `ix_exp_fill_trade`
#: leads on `(run_id, arm_id, source_trade_id)`, so `exp_order_id` is not an access path -- and
#: a three-day run would do that ~41,000 times under the source session's 25 s
#: `statement_timeout`. The aggregate below reads the arm's fills once, bounded by the same
#: `run_id`/`arm_id` the outer query is, and produces the identical result set: `min` over the
#: same rows, `null` for an order with no fill.
_ARM_ORDERS = text(
    "select o.id, o.venue_market_id, o.side, o.prob, f.filled_at "
    "from exp_order o "
    "left join (select exp_order_id, min(filled_at) as filled_at from exp_fill "
    "           where run_id = :r and arm_id = :a group by exp_order_id) f "
    "  on f.exp_order_id = o.id "
    "where o.run_id = :r and o.arm_id = :a order by o.id")

#: What this run's arm has already recorded, **and whether it was censored**. `exp_outcome` has
#: no unique key to upsert on (2: its only constraint is the surrogate primary key) and this
#: task adds no DDL, so idempotence across a resumed run is a read of the pairs already
#: present: a `(exp_order_id, horizon)` that is there is not written again, and every write
#: still goes through T1's writer alone. `censored` is read with them for M21: a censored row
#: is the one kind whose value can still change, when its horizon finally arrives.
_RECORDED_OUTCOMES = text(
    "select exp_order_id, horizon, censored from exp_outcome "
    "where run_id = :r and arm_id = :a")


def _record_run_outcomes(session, writer, *, run_id: str, arm_id: str, orders: list[dict],
                         until: datetime, batch_rows: int) -> list[dict]:
    """1.9(a)'s rows for this run's orders, idempotently and through the writer alone.

    Orders are grouped by the horizons still missing for them -- at most one group per subset of
    `outcomes.HORIZONS` -- and each group is written in slices no wider than the writer's own
    `exp_batch_rows` bound (4.3), so one `record_outcomes` call can never exceed it. The rows
    returned are the ones **this** invocation recorded, which is what the summary line counts.

    **A censored row is re-observed when its horizon arrives** (M21, D44's residual). Skipping
    every pair that exists made a censored row permanent: the 30-minute markout of an order
    filled ten minutes before the window's end was recorded "not yet" and never revisited, even
    on the resumed run whose `until` was hours later. Such a pair is recomputed and **updated
    in place** on its natural key -- §4.7's role has UPDATE and no DELETE -- and only when
    `outcomes.matured_horizons` says the instant has actually arrived, so a horizon that is
    still in the future is left alone and a `close` horizon the tape kept no close for is never
    touched at all. The extra read is one `venue_markets` lookup per order that carries a
    censored row, and none on a run's first invocation, where nothing is recorded yet.
    """
    recorded: dict[tuple, bool] = {
        (row.exp_order_id, row.horizon): bool(row.censored) for row in
        session.execute(_RECORDED_OUTCOMES, {"r": run_id, "a": arm_id})}
    groups: dict[tuple, list[dict]] = {}
    regroups: dict[tuple, list[dict]] = {}
    for order in orders:
        missing = tuple(h for h in outcomes.HORIZONS if (order["id"], h) not in recorded)
        if missing:
            groups.setdefault(missing, []).append(order)
        censored = tuple(h for h in outcomes.HORIZONS
                         if recorded.get((order["id"], h)) is True)
        if censored:
            matured = outcomes.matured_horizons(session, order, now=until, horizons=censored)
            if matured:
                key = tuple(h for h in censored if h in matured)
                regroups.setdefault(key, []).append(order)
    rows: list[dict] = []
    for horizons, group in groups.items():
        size = max(1, batch_rows // len(horizons))
        for start in range(0, len(group), size):
            rows += outcomes.record_outcomes(session, writer, run_id=run_id, arm_id=arm_id,
                                             orders=group[start:start + size], now=until,
                                             horizons=horizons)
    for horizons, group in regroups.items():
        rows += outcomes.rerecord_outcomes(session, writer, run_id=run_id, arm_id=arm_id,
                                           orders=group, now=until, horizons=horizons)
    return rows


@exp_app.command("run")
def run_cmd(run_id: str = typer.Option(..., "--run-id"),
            arm: str = typer.Option("A", "--arm"),
            since: datetime = typer.Option(..., "--since", formats=["%Y-%m-%dT%H:%M:%S%z"]),
            until: datetime = typer.Option(..., "--until", formats=["%Y-%m-%dT%H:%M:%S%z"]),
            variants: str = typer.Option(..., "--variants"),
            chunk_hours: float = typer.Option(1.0, "--chunk-hours")) -> None:
    """Step one arm over the retained decision instants of a window, writing its own orders,
    fills, print allocations and resume point (§1.4).

    Resumes where the arm's `exp_checkpoint` row left off, and refuses outright if the run's
    manifest has changed since that checkpoint (§1.2). Reads through §1.1(b)'s read-only
    reader and writes only `exp_*` rows through §1.1(c)'s writer, so it fails closed without
    the §4.7 grant.
    """
    configure_logging()
    if until <= since:
        raise typer.BadParameter("--until must be after --since")
    if chunk_hours <= 0:
        raise typer.BadParameter("--chunk-hours must be a positive number of hours")
    variant_ids = [v.strip() for v in variants.split(",") if v.strip()]
    if not variant_ids:
        raise typer.BadParameter("--variants takes a comma-separated list of variant ids")
    # §1.6: the arm is one of the experiment's own, and it is stepped under **its** policy.
    # An unknown arm id was previously accepted and silently stepped under the baseline, which
    # would have written `exp_order` rows labelled with an arm nobody defined.
    spec = arms.ARMS.get(arm)
    if spec is None:
        raise typer.BadParameter(
            f"--arm takes one of §1.6's arms ({', '.join(sorted(arms.ARMS))}); {arm!r} is not "
            "one of them. Arm C is T7's and is deliberately absent until its preflight exists")
    # §0.6: the label first, after the arguments are validated and before any number.
    print(EXP_LABEL)
    s = get_settings()
    from harness.db.migrate import HEAD_REVISION
    from harness.execution.plan import ExecSettings
    from harness.execution.store import variant_configs

    resumed = False
    with source.reader(s) as session:
        instants, _samples, estimate = _clock(session, since=since, until=until,
                                              variant_ids=variant_ids)
        row = session.execute(_EXP_RUN_MANIFEST, {"r": run_id}).first()
        if row is None:
            raise typer.BadParameter(
                f"no frozen exp_run row for {run_id}: `harness exp capture` and the run's "
                f"manifest come first (§1.2)")
        exec_settings = ExecSettings.from_settings(s)
        variant_cfg = variant_configs(session, variant_ids)
        # §1.2: the manifest is rebuilt for **this** code and these settings and compared with
        # the stored hash. A changed code sha, schema head, variant configuration or execution
        # setting raises `ManifestMismatch` out of here: the run is refused, not continued
        # under a different definition, and the checkpoint is left byte-identical.
        manifest = storage.rebuild_manifest(
            row.manifest, run_id=run_id, code_sha=s.build_sha, schema_version=HEAD_REVISION,
            variant_cfg=variant_cfg, exec_settings=exec_settings)
        manifest_hash = manifest.freeze()
        _check_resume(row.manifest_hash, manifest)
        state = storage.resume(session, run_id=run_id, arm_id=arm, manifest=manifest)
        resumed = state is not None
        # The walk-back may not leave the captured window, so the manifest's own warmup start
        # is its floor -- not this invocation's `--since`, which a resumed chunk may move.
        window_start = manifest.warmup_start or since
        allowance = _cadence_allowance(session, spec, s, variant_ids=variant_ids, since=since,
                                       until=until, window_start=window_start)
        runner = adapter.ArmRunner(run_id=run_id, arm_id=arm, policy=spec.policy,
                                   variant_cfg=variant_cfg,
                                   exec_settings=exec_settings, walkers={},
                                   tz=s.tz_local, ledger=liquidity.PortfolioLedger(),
                                   cadence_allowance=allowance)
        if state is not None:
            adapter.restore(runner, state)
        writer = storage.ExperimentWriter.open(s, run_id=run_id)
        try:
            # §4.3: the guard is asked before every chunk, not once at the start. A run that
            # finds the executor's loop running long stops at the chunk boundary it has just
            # checkpointed and resumes on the next invocation.
            result = adapter.run_window(
                session, runner, instants, writer=writer, manifest_hash=manifest_hash,
                since=since, until=until, chunk=timedelta(hours=chunk_hours),
                mismatch_max=s.exp_mismatch_max,
                busy=lambda: capture._yield_if_executor_busy(session, s))
            # G2 (1.9(a), ruling C2): the run's own outcomes, on the **common** schedule and
            # inside the writer's lifetime. `now=until` is the window's end, never the wall
            # clock, so a horizon later than the window is censored rather than measured
            # against a market the run never observed. The orders are read from `exp_order`
            # (closed and open) and every outcome from the capture's own tables inside
            # `record_outcomes`, so the lookahead guard is respected.
            arm_orders = [dict(row) for row in
                          session.execute(_ARM_ORDERS, {"r": run_id, "a": arm}).mappings()]
            recorded = _record_run_outcomes(session, writer, run_id=run_id, arm_id=arm,
                                            orders=arm_orders, until=until,
                                            batch_rows=s.exp_batch_rows)
            writer.commit()
        finally:
            writer.close()
    chunks = result.chunks
    print(f"run={run_id} arm={arm} resumed={resumed} chunks={len(chunks)}")
    print(f"  retained action instants   {len(instants)}")
    print(f"  live loop estimate         {estimate}")
    print(f"  stepped                    {result.stepped}")
    # M-a: the **run's** orders, counted from `exp_order` itself. `chunks[-1].orders` is the
    # last chunk's write -- closed orders leave `runner.orders` at every chunk boundary by
    # design (D23/I3) -- so a twelve-chunk window printed the handful the last chunk still had
    # open. `arm_orders` is this run and arm's whole order set, which is what the line claims.
    print(f"  orders                     {len(arm_orders)}")
    print(f"  fills                      {result.fills}")
    print(f"  open at the end            {chunks[-1].open_orders if chunks else 0}")
    if result.stopped:
        print(f"  stopped                    {result.stopped}")
    # 1.9(a)'s three statuses, kept apart in the line as they are kept apart in the table: a
    # censored horizon has not arrived, a missing one was unobservable and named why.
    censored = sum(1 for row in recorded if row["status"] == "censored")
    missing = sum(1 for row in recorded if row["status"] == "missing")
    print(f"  outcomes recorded          {len(recorded)} "
          f"(censored {censored}, missing {missing})")


#: §1.9(d)'s per-arm, per-portfolio rows, on `ix_exp_order_run_arm (run_id, arm_id, placed_at)`:
#: one aggregate per portfolio identity `(arm, variant)` and never a sum across two of them.
_REPORT_ARMS = text(
    "select arm_id, variant_id as portfolio, count(*) as orders, "
    "count(*) filter (where filled_contracts > 0) as fills, "
    "count(distinct venue_market_id) as markets, "
    "count(*) filter (where filled_contracts > 0 and filled_contracts < contracts) "
    "as partial_fills, "
    "coalesce(sum(filled_contracts), 0) as contracts, "
    "count(*) filter (where cancel_reason = 'exec_capacity') as capacity_exclusions "
    "from exp_order where run_id = :r group by 1, 2 order by 1, 2")

#: The common outcome schedule's own rows (§1.9a): the order-weighted 30-minute markout is the
#: primary number, its source age travels with it, and matured/censored/missing stay apart.
_REPORT_OUTCOMES = text(
    "select x.arm_id, o.variant_id as portfolio, "
    "avg(x.value) filter (where x.horizon = '1800') as markout_1800, "
    "avg(x.source_age_s) filter (where x.horizon = '1800') as source_age_s, "
    "count(*) filter (where x.censored) as censored, "
    "count(*) filter (where x.missing_reason is not null) as missing "
    "from exp_outcome x join exp_order o on o.id = x.exp_order_id and o.run_id = x.run_id "
    "where x.run_id = :r group by 1, 2")

#: §1.9(c)'s two sensitivities and the clustered interval's own inputs: **per order**, because
#: a game-weighted or market-side-weighted number cannot be recovered from an average that has
#: already been taken. One row per matured 30-minute outcome, with the game it belongs to
#: (the cluster) and the market side it was placed on.
_REPORT_OUTCOME_VALUES = text(
    "select x.arm_id, o.variant_id as portfolio, o.venue_market_id, o.side, x.value, "
    "vm.game_id from exp_outcome x "
    "join exp_order o on o.id = x.exp_order_id and o.run_id = x.run_id "
    "left join venue_markets vm on vm.id = o.venue_market_id "
    "where x.run_id = :r and x.horizon = '1800' and x.value is not null")

#: §1.9(c)'s denominators: the run's filled contracts (what a game's share is a share *of*)
#: and the contracts its arms actually asked for (what the ledger's allocation is measured
#: against). Both are one aggregate on `ix_exp_order_run_arm`.
_REPORT_CONTRACTS = text(
    "select coalesce(sum(filled_contracts), 0) as filled, "
    "coalesce(sum(contracts), 0) as requested from exp_order where run_id = :r")

#: §1.7's classifications, counted for the run: the three book-health counts the ops read-back
#: records beside the instant count.
_REPORT_HEALTH = text("select classification, count(*) as n from exp_book_health "
                      "where run_id = :r group by 1 order by 1")

#: C1's pair, read back from the run's own limitation row rather than recomputed here.
_REPORT_SPACING = text("select scope from exp_limitation where run_id = :r "
                       "and kind = 'loop_spacing_unreconstructable' "
                       "order by created_at desc limit 1")

#: §1.9(c)'s concentration: the largest single game's share of the run's filled contracts, and
#: what the portfolio ledger allocated of the print volume it actually observed (§1.5).
_REPORT_GAME_SHARE = text(
    "select vm.game_id, sum(f.contracts) as contracts from exp_fill f "
    "join exp_order o on o.id = f.exp_order_id and o.run_id = f.run_id "
    "left join venue_markets vm on vm.id = o.venue_market_id "
    "where f.run_id = :r group by 1 order by 2 desc limit 1")
_REPORT_ALLOCATION = text(
    "select coalesce(sum(allocated), 0) as allocated, coalesce(sum(available), 0) as available "
    "from exp_allocation where run_id = :r")


#: The `capture_hash_mismatch` rows this run already carries, by stream (M19). One row per
#: stream is enough: a tampered file is a property of the run, not of how often it is reported,
#: and `exp_limitation` has no unique key to resolve a second write against.
_REPORT_CAPTURE_LIMITATIONS = text(
    "select scope from exp_limitation where run_id = :r and kind = 'capture_hash_mismatch'")

#: How much of a capture file is hashed at a time. The tree is NDJSON in the hundreds of MB, so
#: it is read in blocks rather than into memory.
_HASH_BLOCK = 1 << 20


def _file_sha256(path: Path) -> str | None:
    """The file's sha256, or None where there is no file to hash."""
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(_HASH_BLOCK), b""):
            digest.update(block)
    return digest.hexdigest()


def _capture_hash_rows(s, *, run_id: str, manifest: dict, recorded: set[str],
                       now: datetime) -> tuple[list, int]:
    """§2's file-tree invariant, evaluated (M19, D41): re-hash the run's capture files against
    the manifest's own `capture_hashes` and return the limitation rows for the streams that no
    longer match, with the number of streams checked.

    The manifest records `{stream: {"sha256": ..., "sql": ..., "params": ...}}` -- the digest
    **and** the statement that produced it -- so a mismatch is attributable to a readable query
    rather than to a bare number (`capture.capture_manifest_entries`). A stream whose file is
    absent is a mismatch too, and says so: a missing file is not a passing hash.

    Nothing is written here; the caller opens the writer only if there is a row to write, so an
    intact tree leaves `exp report` the read-only command it has always been.
    """
    entries = (manifest or {}).get("capture_hashes") or {}
    # `storage.run_dir` would *create* the tree; a report reads it. The path is the same one
    # `capture_slice` wrote to, and a run whose directory is gone reports every stream missing
    # rather than making an empty directory that then looks like a capture.
    directory = Path(s.exp_dir) / run_id
    rows = []
    for stream in sorted(entries):
        entry = entries[stream]
        expected = entry.get("sha256") if isinstance(entry, dict) else entry
        path = directory / f"{stream}.ndjson"
        actual = _file_sha256(path)
        if actual is not None and actual == expected:
            continue
        if stream in recorded:
            continue
        rows.append(capture.Limitation(
            run_id=run_id, kind="capture_hash_mismatch",
            scope={"stream": stream, "path": str(path)},
            detail=(f"{stream}: the manifest froze sha256 {expected}; the file now hashes to "
                    f"{actual if actual is not None else 'nothing - it is absent'}. The rows "
                    f"read from this stream are not the rows the run was frozen over (§2)."),
            created_at=now))
    return rows, len(entries)


def _verify_capture_tree(s, session, *, run_id: str, manifest: dict) -> None:
    """Re-hash the run's capture tree, print the result and persist any mismatch (M19).

    The limitation rows go through `ExperimentWriter` like every other `exp_*` write, and the
    writer is opened **only** when there is one to write: a report on an intact tree needs no
    §4.7 write capability, and one on a tampered tree fails closed without it rather than
    printing a clean line.
    """
    recorded = {(row.scope or {}).get("stream")
                for row in session.execute(_REPORT_CAPTURE_LIMITATIONS, {"r": run_id})}
    rows, streams = _capture_hash_rows(s, run_id=run_id, manifest=manifest, recorded=recorded,
                                       now=datetime.now(timezone.utc))
    print(f"capture files re-hashed: {streams} streams, {len(rows)} mismatch"
          f"{'' if len(rows) == 1 else 'es'} (§2)")
    for row in rows:
        print(f"  capture_hash_mismatch   {row.scope['stream']}")
    if not rows:
        return
    writer = storage.ExperimentWriter.open(s, run_id=run_id)
    try:
        storage.write_limitations(writer, rows)
        writer.commit()
    finally:
        writer.close()


def _game_of(row):
    """The cluster a markout belongs to: its game, or the market itself when the tape kept no
    game for it (§1.9c's clustering is by game, and an unknown game is not every other one)."""
    return row.game_id if row.game_id is not None else ("market", row.venue_market_id)


def _weighted(rows, key) -> str | None:
    """A sensitivity: the mean of the per-group means (§1.9c).

    Not the order-weighted mean -- that is the primary number, computed by the outcome query
    itself. Grouping first is the whole point: one game with forty fills and one with two then
    count once each.
    """
    if not rows:
        return None
    groups: dict = {}
    for row in rows:
        groups.setdefault(key(row), []).append(float(row.value))
    means = [sum(values) / len(values) for values in groups.values()]
    return f"{sum(means) / len(means):.6f}"


@exp_app.command("report")
def report_cmd(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Render one run's arm results under §1.9's reporting contract.

    Before any arm number the run's **capture files are re-hashed** against the manifest's own
    `capture_hashes` (M19): a stream that no longer hashes to what was frozen, or whose file is
    gone, is printed and written as a `capture_hash_mismatch` limitation row through the
    writer. §2 states that invariant and nothing evaluated it until now.

    Every row this command prints is **exploratory**: it is an arm's counterfactual behaviour,
    not a registered variant's performance, so the registered table is printed empty rather
    than filled with numbers from a different measurement (§0.10, §1.9e). The episode rule,
    the resolved instant count beside the live loop estimate and the concentration lines come
    before any outcome, and the markouts are read from `exp_outcome` -- the one common
    schedule -- rather than recomputed here (ruling C2).
    """
    configure_logging()
    # §0.6: the label first, after the arguments are validated and before any number.
    print(EXP_LABEL)
    s = get_settings()
    with source.reader(s) as session:
        run = session.execute(_EXP_RUN_MANIFEST, {"r": run_id}).first()
        if run is None:
            raise typer.BadParameter(
                f"no exp_run row for {run_id}: a report is rendered for a frozen run (§1.2)")
        arms_rows = session.execute(_REPORT_ARMS, {"r": run_id}).all()
        outcome_rows = {(row.arm_id, row.portfolio): row
                        for row in session.execute(_REPORT_OUTCOMES, {"r": run_id}).all()}
        per_order = session.execute(_REPORT_OUTCOME_VALUES, {"r": run_id}).all()
        health = session.execute(_REPORT_HEALTH, {"r": run_id}).all()
        scope = session.execute(_REPORT_SPACING, {"r": run_id}).scalar() or {}
        share = session.execute(_REPORT_GAME_SHARE, {"r": run_id}).first()
        totals = session.execute(_REPORT_CONTRACTS, {"r": run_id}).first()
        allocation = session.execute(_REPORT_ALLOCATION, {"r": run_id}).first()
        # §2's file-tree invariant, before any arm number: the rows below were read from a
        # capture whose files must still hash to what the manifest froze (M19, D41).
        _verify_capture_tree(s, session, run_id=run_id, manifest=run.manifest)
    buckets: dict[tuple, list] = {}
    for row in per_order:
        buckets.setdefault((row.arm_id, row.portfolio), []).append(row)
    rows = []
    for row in arms_rows:
        outcome = outcome_rows.get((row.arm_id, row.portfolio))
        bucket = buckets.get((row.arm_id, row.portfolio), [])
        rows.append({
            "arm_id": row.arm_id, "portfolio": row.portfolio, "orders": row.orders,
            "fills": row.fills, "markets": row.markets, "partial_fills": row.partial_fills,
            "contracts": row.contracts, "capacity_exclusions": row.capacity_exclusions,
            "markout_1800": None if outcome is None else outcome.markout_1800,
            "markout_1800_game": _weighted(bucket, _game_of),
            "markout_1800_market_side": _weighted(bucket, lambda r: (r.venue_market_id, r.side)),
            # `cluster_ci`'s own inputs, per order and unaggregated: the value and the game it
            # belongs to. A market with no game clusters on itself rather than pooling every
            # unknown game into one cluster, which would understate the interval.
            "values": [float(r.value) for r in bucket],
            "clusters": [_game_of(r) for r in bucket],
            "source_age_s": None if outcome is None else outcome.source_age_s,
            "censored": None if outcome is None else outcome.censored,
            "missing": None if outcome is None else outcome.missing,
            "registered": False})
    filled = totals.filled if totals is not None else 0
    requested = totals.requested if totals is not None else 0
    concentration = {
        "maximum contribution by game": (
            f"game {share.game_id}: {share.contracts} of {filled} filled contracts"
            if share is not None else "no filled contracts in this run"),
        "allocated against requested": (
            f"{allocation.allocated} of {requested} contracts requested "
            f"({allocation.available} observed available)"
            if allocation is not None else "no allocation rows")}
    # §1.9(c): when the common outcome schedule has written nothing for this run, the two
    # sensitivities and the interval are *absent*, and the report says so in one line rather
    # than printing three dashes a reader has to interpret.
    notes = []
    if not per_order:
        notes.append(
            f"sensitivities: not supplied (no exp_outcome rows for run {run_id}); the "
            "game-weighted and market-side-weighted markouts and the game-clustered interval "
            "are computed from exp_outcome joined to exp_order, and nothing has recorded this "
            "run's outcomes yet (§1.9a).")
    cadence = ((run.manifest or {}).get("opportunity_definition") or {}).get("cadence_in_force")
    print(report.render(
        [], rows, run_id=run_id, manifest_hash=run.manifest_hash,
        concentration=concentration, instants=scope.get("instants"),
        live_loop_estimate=scope.get("live_loop_estimate"),
        cadence_in_force=cadence, notes=notes))
    # §1.7's counts, printed beside the run's own instants: the ops read-back records these
    # three numbers the first time this command is run by hand.
    print(f"book health classifications ({len(health)}):")
    for row in health:
        print(f"  {row.classification:<24} {row.n}")
    # §1.9(a): the one schedule every arm's numbers were computed on, named in the output so a
    # reader never has to infer which horizons a blank cell belongs to. `outcomes` here is the
    # **module** -- the local that used to shadow it inside this function is `outcome_rows`.
    print(f"common outcome schedule: {', '.join(outcomes.HORIZONS)} "
          f"(missing reasons: {', '.join(outcomes.MISSING_REASONS)})")
    # The episode vocabulary §1.9(b)'s rule is written in; its parameters are printed above the
    # tables, where the rule itself is.
    print(f"episode sighting kinds: {episodes.CANDIDATE}, {episodes.CANCEL}, "
          f"{episodes.RE_ENTRY} (floor {episodes.GAP_FLOOR_S} s)")
    # §1.6(c): the distinctions each arm decided under, with the hash the manifest froze them
    # at, so a note cannot be dropped between the run and its report.
    for arm_id in sorted({row["arm_id"] for row in rows}):
        spec = arms.ARMS.get(arm_id)
        if spec is not None:
            print(f"  arm {arm_id} spec={spec.spec_hash()[:12]} "
                  f"distinctions={len(spec.notes)} source={spec.observation_source}")


#: §4.6's activation checklist, printed in full by `exp observe` before it does anything.
#: Steps 1-5 are the loop's and print their evidence here; **step 0 is the user's** grant and
#: secret and is a prerequisite of 1-5; steps 6 and 7 are §0.14c's and §0.14a's dated
#: decisions, are not prerequisites of 1-5 and never gate them.
ACTIVATION_CHECKLIST: tuple[tuple[str, str, str], ...] = (
    ("0", "the user",
     "the harness_exp grant and secrets/exp_db_password exist (§4.7); until they do every "
     "run fails closed with IsolationError at session open"),
    ("1", "loop",
     "frozen manifest: exp_run.status = 'frozen', hash recorded, cohort ids and seed printed; "
     "the cohort id space is games.odds_api_event_id, the provider event id"),
    ("2", "loop",
     "recorded measurement boundary: the activation instant in UTC and CT, journaled and "
     "written to exp_run before the first observation"),
    ("3", "loop",
     "resource preflight: free space above 25 %, exp_capture_max_gb / exp_raw_body_max_gb "
     "headroom, exec.loop_ms p95 and recorder.tick_ms recorded as the before-half"),
    ("4", "loop",
     "budget and coverage checks: credits remaining against exp_observer_credit_cap and the "
     "credits_watch_fraction guard, the cohort's eligible-market enumeration complete"),
    ("5", "loop",
     "ordinary release verification: the deploy carrying the observer passes §3 and the "
     "standing verify rows, **and app-research's own loop is running** - "
     "ResearchWorker.run_once returns before every pass unless research_worker_enabled is "
     "true and the Anthropic key file is present, so arm C collects nothing without both"),
    ("6", "the user", "the veto profile's activation, if any, is §0.14c's dated decision"),
    ("7", "the user", "any production holding-policy adoption is §0.14a's dated decision"),
)

#: §1.6(h): the refusal is a **reduction**, never a purchase. The line is printed beside every
#: unavailable reason so no reader can take the refusal as a request for a bigger tier.
_NO_PURCHASE = ("scope is reduced or arm C is reported unavailable with its reason; no tier is "
                "bought and no cap is raised (§1.6h)")


def _step_zero(session) -> str | None:
    """§4.6 step 0's privilege read-back, or the reason arm C cannot start (C2, §3 row 2).

    `has_table_privilege` raises when the role itself does not exist, which is step 0 not being
    done rather than a fault of this command, so it is reported the same way as a failed
    read-back (fix round 1, Important 9).
    """
    try:
        rows = session.execute(_PRIVILEGE_READBACK, {"role": EXP_DB_ROLE}).all()
    except DBAPIError:
        return (f"step 0's privilege read-back could not run: the {EXP_DB_ROLE!r} role does "
                f"not exist; §4.7's CREATE ROLE / GRANT is the user's one-off step")
    writable = sorted(name for name, may in rows if may)
    if writable:
        return (f"step 0's privilege read-back fails: {EXP_DB_ROLE} may INSERT into "
                f"{', '.join(writable)}; the grant of §4.7 revokes it")
    may_write_exp = session.execute(text(
        "select has_table_privilege(:role, 'exp_observation', 'INSERT')"),
        {"role": EXP_DB_ROLE}).scalar()
    if not may_write_exp:
        return (f"step 0's privilege read-back fails: {EXP_DB_ROLE} may not INSERT into "
                f"exp_observation; §4.7's GRANT runs after the migration")
    return None


def _unavailable(reason: str) -> None:
    """§1.6(h)'s refusal, printed the one way, then exit 1."""
    print(f"arm C unavailable: {reason}")
    print(f"  {_NO_PURCHASE}")
    raise typer.Exit(code=1)


@exp_app.command("observe")
def observe_cmd(run_id: str = typer.Option(..., "--run-id"),
                once: bool = typer.Option(False, "--once/--no-once")) -> None:
    """Arm C's observation, by hand (§1.6e). Prints §4.6's activation checklist, then the
    preflights that can refuse it.

    **Activates nothing.** The ordinary home of this work is `app-research`'s worker loop
    (§4.2), which runs it only when `exp_observer_enabled` is set, §4.7's secret is in place and
    a frozen `exp_run` row's observation window is open. Without `--once` this command reads and
    prints only. With `--once` it makes exactly one interval's reads -- one `fetch_featured` per
    sport in the cohort, six credits -- under the same checks the pass runs, and writes only
    `exp_observation` rows through §1.1(c)'s writer.

    It **refuses to start** when step 0's grant or secret is missing, when its privilege
    read-back fails, or when step 4's budget and coverage preflight does not fit, printing
    `arm C unavailable: <reason>` and §1.6(h)'s no-purchase line (§1.6h).
    """
    configure_logging()
    # §0.6: the label first, after the arguments are validated and before any number.
    print(EXP_LABEL)
    # Imported here, not at module scope: `observer.py` calls `register_pass` at import, and
    # `harness/cli.py` imports this module in **every** `harness` process. A package-level
    # import would put the observer in `PASSES` before `load_passes()` imports veto and
    # annotate, inverting the sweep order `PASS_MODULES` documents (fix round 1, Important 4).
    from harness.experiments.execution_viability import observer

    s = get_settings()
    print(f"activation checklist (§4.6), run={run_id}:")
    for step, owner, text_ in ACTIVATION_CHECKLIST:
        print(f"  ({step}) {owner:<9} {text_}")
    # The live values of the three booleans that gate collection. Never a secret's content:
    # `has_anthropic_key()` and `has_exp_db_password()` are `is_file()`-and-non-empty tests.
    print(f"  gates            research_worker_enabled={s.research_worker_enabled} "
          f"anthropic_key_present={s.has_anthropic_key()} "
          f"exp_observer_enabled={s.exp_observer_enabled} "
          f"exp_db_password_present={s.has_exp_db_password()}")
    now = datetime.now(timezone.utc)
    try:
        with source.reader(s) as session:
            refusal = _step_zero(session)
            if refusal is None:
                run = observer.begin_run(session, s, run_id=run_id, now=now)
                if run is None:
                    refusal = f"no frozen exp_run row for {run_id} (§4.6 step 1)"
                elif not run.games:
                    refusal = (
                        "the frozen manifest resolves to no eligible cohort market "
                        "(§4.6 step 4's coverage check): the cohort id space is "
                        "games.odds_api_event_id, the provider event id, and every listed id "
                        "must name a game with a confidently matched direct-fair market")
                else:
                    ok, reason = observer.budget_ok(s, credits_used=run.credits_used,
                                                    remaining=run.remaining)
                    if not ok:
                        refusal = (f"step 4's budget check refuses on {reason}: used="
                                   f"{run.credits_used} cap={s.exp_observer_credit_cap} "
                                   f"remaining={run.remaining}")
                    elif run.window_end is not None and now > run.window_end:
                        refusal = (f"the frozen observation window closed at "
                                   f"{run.window_end.isoformat()} (§1.6g)")
            if refusal is not None:
                _unavailable(refusal)
            print(f"  (1) cohort         {len(run.games)} games, seed={run.seed}")
            for game in run.games:
                print(f"      game         {game.sport:<6} {game.canonical_game_id} "
                      f"kickoff={game.kickoff_utc.isoformat()} markets={len(game.markets)}")
            window = run.window_end.isoformat() if run.window_end else "none"
            print(f"      window end     {window}")
            print(f"  (4) budget         used={run.credits_used} "
                  f"cap={s.exp_observer_credit_cap} remaining={run.remaining} "
                  f"per_interval={2 * observer.CREDITS_PER_CALL}")
            print(f"      interval       {s.exp_observe_interval_s} s")
            print(f"      raw bodies     {run.body_bytes} bytes stored, ceiling "
                  f"{s.exp_raw_body_max_gb} GiB")
            if not once:
                print("nothing observed: --once makes one interval's reads; the prospective "
                      "cohort runs in app-research's worker loop (§4.2)")
                return
            writer = storage.ExperimentWriter.open(s, run_id=run_id)
            try:
                spent = observer.observe_once(session, now, s, observer.odds_client(s),
                                              run=run, writer=writer)
                writer.commit()
            finally:
                writer.close()
                # Nothing calls `worker.close_passes()` in a one-shot process, so this command
                # owns the client's teardown (fix round 1, Minor 7).
                observer.close_client()
    except IsolationError as refused:
        # §4.6 step 0's own headline failures -- no secret, no role -- raise out of
        # `source.reader` before `_step_zero` can run, and §1.6(h) requires a refusal here,
        # not a traceback (fix round 1, Important 9).
        _unavailable(f"step 0 is not done: {refused}")
    except OperationalError:
        # Deliberately not the driver's message: it can carry the connection string.
        _unavailable(
            "step 0 is not done: the harness_exp role cannot connect to the harness database "
            "(§4.7's CREATE ROLE / GRANT has not been run, or the password in "
            "secrets/exp_db_password does not match the role's)")
    print(f"observed at {now.isoformat()} credits={spent} used={run.credits_used} "
          f"remaining={run.remaining}")
    if run.dormant:
        print(f"  dormant        {run.dormant}; every scheduled-but-unmade read is labelled "
              f"and the run never retries (§1.6i)")

#: §1.3(f)'s proof, counted: how many differences between the recorded slice and an arm's
#: reproduction the run recorded, and how many of them name a cause. One aggregate on
#: `ix_exp_mismatch_run (run_id, explained)`.
_DECIDE_MISMATCHES = text(
    "select count(*) as n, count(*) filter (where explained) as explained "
    "from exp_mismatch where run_id = :r")

#: The run's own named limitations (§1.3): what the record cannot answer, by kind. On
#: `ix_exp_limitation_run (run_id, kind)`.
_DECIDE_LIMITATIONS = text("select kind, count(*) as n from exp_limitation where run_id = :r "
                           "group by 1 order by 1")

#: §1.9(a)'s coverage: matured, censored and missing outcomes for the run, as **counts**. No
#: value is averaged across two arms here - a pooled markout would answer nobody's question.
#: This count is **run-wide**; the forecast's maturity figure is one identity's, and each line
#: names its own scope so the two cannot be read as one number (review Minor 5).
_DECIDE_OUTCOME_COVERAGE = text(
    "select count(*) as rows_, "
    "count(*) filter (where value is not null and not censored) as matured, "
    "count(*) filter (where censored) as censored, "
    "count(*) filter (where missing_reason is not null) as missing "
    "from exp_outcome where run_id = :r")

#: The 30-minute markout's sign, **per portfolio identity** `(arm, variant)` and never pooled
#: across two of them (§1.5): the forecast's economics come from one identity's own rows.
_DECIDE_SIGN = text(
    "select o.arm_id, o.variant_id as portfolio, count(x.value) as matured, "
    "avg(x.value) as mean_1800 from exp_outcome x "
    "join exp_order o on o.id = x.exp_order_id and o.run_id = x.run_id "
    "where x.run_id = :r and x.horizon = '1800' and x.value is not null group by 1, 2")

#: Arm C's rows (ruling D27): `exp_observation` with `source = 'exp_observer'`, by status.
#: `credits` is non-zero on exactly one row per call, so `sum(credits)` is the spend and the
#: count of non-zero rows is the calls made (T7).
_DECIDE_OBSERVER = text(
    "select status, count(*) as n, coalesce(sum(credits), 0) as credits, "
    "count(*) filter (where credits > 0) as calls from exp_observation "
    "where run_id = :r and source = :src group by 1 order by 1")

#: The registered variant the forecast projects, resolved by **name**: `orders.variant_id` is
#: the 12-hex config hash, and `sharp_two_sided` is the name that hash is registered under.
_DECIDE_VARIANT_ID = text("select variant_id from strategy_variants where name = :name")

#: §1.10's first quantity - **observed watched fills**, live facts. A filled order is counted
#: once however many fill rows it took (`count(distinct o.id)` against `count(f.id)`), replayed
#: rows are excluded, and only `queue_model` fills count: a snapshot-cross fill is not a rested
#: maker fill and this forecast is about resting.
_DECIDE_OBSERVED_FILLS = text(
    "select count(distinct o.id) as filled_orders, count(f.id) as fill_rows, "
    "count(distinct vm.game_id) as distinct_games "
    "from fills f join orders o on o.id = f.order_id "
    "left join venue_markets vm on vm.id = o.venue_market_id "
    "where o.variant_id = :v and f.fill_method = 'queue_model' and not f.replay")

#: The window those fills accrued over, and the orders placed in it: the denominator of a rate.
#:
#: `and not replay` is not optional (fix round 1, Critical 1). `harness/replay.py` writes replay
#: orders into this same table on a 15 s grid over historical tape, and the fill half of this
#: pair already excludes them: without it, one replay of an old window stretches
#: `max(placed_at) - min(placed_at)`, divides every projected rate by the stretch and inflates
#: the time-to-target - in the one artifact the user reads. §1.10's first quantity is *live
#: watched facts*, and both halves now mean the same thing by "observed".
_DECIDE_OBSERVED_ORDERS = text(
    "select count(*) as orders, min(placed_at) as first_order, max(placed_at) as last_order "
    "from orders where variant_id = :v and not replay")

#: §1.10's clean-book eligibility, **measured** (fix round 1, Critical 2): the live watched
#: filled orders none of whose fills lies in an interval this run classified
#: `data_loss_confirmed` or `unresolved` for that ticker. `inactive_confirmed` is a quiet market,
#: not a faulted book, so it does not exclude a fill. Bounded by the variant and by the run's own
#: health rows; asked only when the run classified something, because "no classification" is
#: *unmeasured* and the fill count is not a substitute for a measurement.
#:
#: The predicate is **per order**, not per fill row (fix round 2, ruling D39). Filtering fill rows
#: and then counting `distinct order_id` lets an order with several partial fills escape through
#: whichever one happened to land outside the faulted interval, which would report an order that
#: was partly filled against a book this run could not vouch for as eligible. The population is
#: the same one `_DECIDE_OBSERVED_FILLS` counts - an order with at least one live `queue_model`
#: fill - so the eligible count is always a subset of the filled-order count above it.
_DECIDE_CLEAN_BOOK = text(
    "select count(*) as clean from orders o "
    "where o.variant_id = :v "
    "and exists (select 1 from fills f where f.order_id = o.id "
    "and f.fill_method = 'queue_model' and not f.replay) "
    "and not exists (select 1 from fills f join exp_book_health h "
    "on h.run_id = :r and h.ticker = o.ticker "
    "and h.classification in ('data_loss_confirmed', 'unresolved') "
    "and f.filled_at >= h.interval_start and f.filled_at < h.interval_end "
    "where f.order_id = o.id and f.fill_method = 'queue_model' and not f.replay)")


def _decide_observed(session, variant_id: str | None, *, run_id: str, mature_outcomes: int,
                     markout_sign: str | None,
                     health_intervals: int) -> tuple[forecast.Observed, list[str]]:
    """§1.10's observed base, read from the live tables, with what it could not read named.

    An unregistered variant is not an empty one: that branch carries `window_read=False`, so the
    forecast says the window is unread instead of printing a fabricated one-day window. Clean-book
    eligibility is measured against the run's own `exp_book_health` intervals, and stays `None`
    when the run classified nothing.
    """
    if variant_id is None:
        return (forecast.Observed(days=1.0, window_read=False, mature_outcomes=mature_outcomes,
                                  markout_sign=markout_sign),
                [f"the registered variant {forecast.PROJECTED_VARIANT} is not in "
                 "strategy_variants in this database, so no observed watched fill could be "
                 "read: the observed base above is empty because it is unread, not because it "
                 "is a measured zero"])
    fills = session.execute(_DECIDE_OBSERVED_FILLS, {"v": variant_id}).one()
    placed = session.execute(_DECIDE_OBSERVED_ORDERS, {"v": variant_id}).one()
    span = ((placed.last_order - placed.first_order).total_seconds() / 86400.0
            if placed.first_order and placed.last_order else 0.0)
    notes: list[str] = []
    if span <= 0:
        notes.append("the observed window is shorter than a day (or holds a single live order), "
                     "so the rates above are per day over one day, not a measured daily rate")
    clean, source = None, ""
    if health_intervals:
        clean = int(session.execute(
            _DECIDE_CLEAN_BOOK, {"v": variant_id, "r": run_id}).scalar() or 0)
        source = (f"this run's {health_intervals} exp_book_health intervals (an order is "
                  "eligible only when **none** of its fills fell inside a data_loss_confirmed "
                  "or unresolved interval for its ticker; an inactive_confirmed interval is a "
                  "quiet market and excludes nothing)")
    else:
        notes.append("clean-book eligibility is unmeasured: this run has no exp_book_health "
                     "row, and the filled-order count is not a substitute for a classification")
    return (forecast.Observed(
        orders=int(placed.orders or 0), fills=int(fills.filled_orders or 0),
        fill_rows=int(fills.fill_rows or 0), distinct_games=int(fills.distinct_games or 0),
        days=max(span, 1.0), clean_book_eligible=clean, clean_book_source=source,
        mature_outcomes=mature_outcomes, markout_sign=markout_sign), notes)


@exp_app.command("decide")
def decide_cmd(run_id: str = typer.Option(..., "--run-id"),
               veto_profile_name: str | None = typer.Option(
                   None, "--veto-profile",
                   help=f"one of {', '.join(pacing.PROFILE_NAMES)}; the dormant profile this "
                        "report preflights and cites. Turns nothing on, and is required for a "
                        "complete veto-pacing section."),
               since: datetime = typer.Option(None, "--since",
                                              formats=["%Y-%m-%dT%H:%M:%S%z"]),
               until: datetime = typer.Option(None, "--until",
                                              formats=["%Y-%m-%dT%H:%M:%S%z"]),
               cost_per_pair: float = typer.Option(
                   0.085, "--cost-per-pair",
                   help="dollars per paired call in the preflight; the review's measured $0.085"),
               arm_b_better: bool | None = typer.Option(
                   None, "--arm-b-better/--arm-b-not-better",
                   help="the operator's A-against-B comparison. Omitted is 'unknown', which on "
                        "its own can only produce 'insufficient evidence'; nothing here infers "
                        "it from fill counts.")) -> None:
    """Render §1.11's decision report for one frozen run (§1.10, §1.11, I11, D16).

    **Recommends; decides nothing.** No setting is written, no policy is adopted and no
    boundary instant is recorded: production adoption is §0.14a's dated decision and the pacing
    profile's activation is §0.14c's, and both questions are printed here unanswered.

    Every number comes from the record: the run's mismatches and limitations, its arms' order and
    fill counts through T4's own `report.arm_table` (so every exploratory row carries its
    `exp_label`), the common outcome schedule's coverage, the book-health classifications, arm
    C's own `exp_observation` rows, the veto profile's preflight against stored arrivals, and -
    for §1.10's observed base - the live watched, non-replayed `queue_model` fills of the
    registered `sharp_two_sided` variant. An empty `exp_outcome` is reported as an
    **unavailable** outcome, never as a zero markout (§1.9a).

    `--veto-profile`, `--since` and `--until` are required for a complete report: §1.11 counts
    the preflight against stored arrivals under unchanged caps as completion evidence, so
    without it the veto-pacing section is empty and `decision.render` refuses (I11, D16).

    The rendered text is the report 6D.1's evidence is judged on; it is placed under
    `docs/superpowers/autopilot/reports/` from this command's output, never hand-written.
    """
    configure_logging()
    if veto_profile_name is not None and veto_profile_name not in pacing.PROFILE_NAMES:
        raise typer.BadParameter(
            f"--veto-profile must name one of {', '.join(pacing.PROFILE_NAMES)}")
    if (since is None) != (until is None):
        raise typer.BadParameter("--since and --until are given together: the preflight window "
                                 "is a window")
    if since is not None and until <= since:
        raise typer.BadParameter("--until must be after --since")
    # §0.6: the label first, after the arguments are validated and before any number.
    print(EXP_LABEL)
    # Imported here rather than at module scope for the reason `exp observe` documents:
    # `observer.py` registers a pass at import and `harness/cli.py` imports this module in
    # every `harness` process (fix round 1, Important 4).
    from harness.experiments.execution_viability import observer

    now = datetime.now(timezone.utc)
    s = get_settings()
    with source.reader(s) as session:
        run = session.execute(_EXP_RUN_MANIFEST, {"r": run_id}).first()
        if run is None:
            raise typer.BadParameter(
                f"no exp_run row for {run_id}: a decision report is rendered for a frozen run "
                "(§1.2), never for a run that was never recorded")
        mismatches = session.execute(_DECIDE_MISMATCHES, {"r": run_id}).one()
        limitations = session.execute(_DECIDE_LIMITATIONS, {"r": run_id}).all()
        arm_rows = session.execute(_REPORT_ARMS, {"r": run_id}).all()
        coverage = session.execute(_DECIDE_OUTCOME_COVERAGE, {"r": run_id}).one()
        signs = session.execute(_DECIDE_SIGN, {"r": run_id}).all()
        health = session.execute(_REPORT_HEALTH, {"r": run_id}).all()
        observer_rows = session.execute(
            _DECIDE_OBSERVER, {"r": run_id, "src": observer.OBSERVER_SOURCE}).all()
        variant_id = session.execute(
            _DECIDE_VARIANT_ID, {"name": forecast.PROJECTED_VARIANT}).scalar()
        # §1.10's economics: one portfolio identity's own sign, never a pooled average (§1.5).
        # Arm A is the baseline the continuation scenario projects, so its rows are the ones
        # the observed base carries.
        baseline_arm = next((row for row in signs
                             if row.arm_id == "A" and row.portfolio == variant_id), None)
        markout_sign = None
        if baseline_arm is not None and baseline_arm.matured:
            mean = float(baseline_arm.mean_1800)
            markout_sign = "positive" if mean > 0 else "negative" if mean < 0 else "flat"
        observed, observed_notes = _decide_observed(
            session, variant_id, run_id=run_id,
            mature_outcomes=int(baseline_arm.matured) if baseline_arm is not None else 0,
            markout_sign=markout_sign,
            health_intervals=sum(int(row.n) for row in health))
        # §1.8/§1.11's completion evidence: the profile replayed against the arrivals that
        # actually happened, under the caps that are in force. Read-only, like everything here.
        preflight = None
        if veto_profile_name is not None and since is not None:
            preflight = veto_profile.preflight(
                session, pacing.load_profile(veto_profile_name), since=since, until=until,
                now=now, cost_per_pair=Decimal(str(cost_per_pair)),
                daily_cap=s.veto_daily_usd_cap, weekly_cap=s.veto_weekly_usd_cap)

    # §1.3(f): the proof is the mismatch count **and** its resolution, never one of them.
    resolved = int(mismatches.n or 0) == int(mismatches.explained or 0)
    baseline_lines = [
        f"mismatches: {mismatches.n} recorded, {mismatches.explained} explained with a named "
        f"cause ({'all resolved' if resolved else 'unresolved differences remain'}); a "
        "mismatch this run could not explain bounds every arm number below (§1.3f).",
        f"clock mode: {(run.manifest or {}).get('clock_mode', '(not in the manifest)')}; "
        f"manifest hash {run.manifest_hash[:12]}, which every resume was refused against "
        "(§1.2).",
        "isolation: every row of this run was written by §1.1(c)'s writer under the "
        f"{EXP_DB_ROLE} role, whose INSERT privileges `harness exp isolation-check` reads back "
        "from the server (§3 row 2).",
    ]
    baseline_lines += ([f"limitation: {row.kind} x{row.n}" for row in limitations]
                       or ["limitations: no exp_limitation row for this run."])

    # §1.9(d)'s rows through T4's own renderer (fix round 1, Important 1 / Minor 10): one line
    # per portfolio identity, never a sum (§1.5), and `exp_label` on every exploratory row. Every
    # row here is exploratory - an arm's counterfactual behaviour is not a registered variant's
    # performance - so the registered table is not printed at all (§0.10, §1.9e).
    if arm_rows:
        arm_results = report.arm_table(
            [{"arm_id": row.arm_id, "portfolio": row.portfolio, "orders": row.orders,
              "fills": row.fills, "markets": row.markets, "partial_fills": row.partial_fills,
              "contracts": row.contracts, "capacity_exclusions": row.capacity_exclusions,
              "registered": False} for row in arm_rows],
            run_id=run_id, manifest_hash=run.manifest_hash, exploratory=True)
    else:
        arm_results = "no exp_order row for this run: neither arm produced a comparable result."
    arm_results += (
        "\n\nNo registered variant's recorded performance is in that table: every row is "
        "exploratory and carries its exp_label, in the same shape `harness exp report --run-id "
        "<run>` prints under §1.9's two-table contract (§0.10, §1.9e).")
    arm_ids = {row.arm_id for row in arm_rows}

    # Arm C (ruling D27): its rows are `exp_observation` rows with `source = 'exp_observer'`,
    # not an `arm_id`, so its result is its own counters or the reason it never ran (§1.6h, §9).
    if observer_rows:
        spend = sum(int(row.credits) for row in observer_rows)
        calls = sum(int(row.calls) for row in observer_rows)
        by_status = ", ".join(f"{row.status} x{row.n}" for row in observer_rows)
        arm_c = (f"measured: {calls} calls for {spend} credits at "
                 f"{observer.CREDITS_PER_CALL} credits a call, on the "
                 f"{s.exp_observe_interval_s} s interval; rows by status: {by_status}. The "
                 "stored observation body is a canonical re-serialisation, not the wire bytes, "
                 "so body_sha256 identifies the parsed content rather than the response (D33).")
    else:
        arm_c = ("unavailable: this run has no exp_observation row with source "
                 f"'{observer.OBSERVER_SOURCE}', so arm C collected nothing. `harness exp "
                 "observe --run-id <run>` prints §4.6's checklist and the reason it refuses; no "
                 "tier is bought and no cap is raised (§1.6h), and no faster-history "
                 "observation is invented in its place.")

    health_lines = [f"{row.classification}: {row.n}" for row in health] or [
        "no exp_book_health row for this run: `harness exp book-health --ticker <t> --persist` "
        "classifies the intervals and names the cause of each (§1.7)."]
    matured = int(coverage.matured or 0)
    health_lines.append(
        f"fresh-outcome coverage, run-wide (every arm and portfolio identity of this run): "
        f"{coverage.rows_} outcome rows, {matured} matured, {coverage.censored} censored, "
        f"{coverage.missing} missing over the common schedule "
        f"{', '.join(outcomes.HORIZONS)}. §5's maturity line counts only the projected "
        "portfolio identity's baseline arm, so the two figures can differ without either "
        "being wrong."
        + ("" if coverage.rows_ else " An empty exp_outcome is an unavailable outcome, never a "
                                     "zero markout: nothing has recorded this run's outcomes "
                                     "(§1.9a)."))
    health_lines += [f"caveat: {caveat}" for caveat in bookhealth.CAVEATS]

    # Arm B's counterfactual fills for the **same** portfolio identity: an exploratory estimate,
    # labelled, and never added to the observed count (§1.10).
    b_row = next((row for row in arm_rows
                  if row.arm_id == "B" and row.portfolio == variant_id), None)
    exploratory = forecast.Exploratory(
        fills=int(b_row.fills) if b_row is not None else 0,
        days=observed.days, label=exp_label(run_id, "B", run.manifest_hash), arm_c=arm_c)
    forecast_text = "\n".join(
        [forecast.render_forecast(forecast.project(
            observed, exploratory, shared_slots=s.exec_max_open_orders))]
        + [f"  note: {note}" for note in observed_notes])

    # §1.11's veto-pacing evidence. Without the profile **and** its preflight there is no
    # evidence here, only a sentence, so the section is left empty and `decision.render`
    # refuses (fix round 1, Important 2).
    veto_lines: list[str] = []
    if preflight is not None:
        profile = pacing.load_profile(veto_profile_name)
        veto_lines.append(f"profile {profile.name}, hash {profile.profile_hash()}; §0.8's "
                          f"amendment record is prepared as veto-pacing-"
                          f"{profile.profile_hash()[:12]}, with its boundary fields empty.")
        veto_lines.append(f"preflight header: {veto_profile.PREFLIGHT_HEADER}.")
        veto_lines.append(
            f"preflight window {preflight.since.isoformat()} .. "
            f"{preflight.until.isoformat()}: {preflight.arrivals} stored arrivals in "
            f"{preflight.buckets} buckets, {preflight.near_kickoff_buckets} of them inside a "
            f"window"
            + ("  (row cap reached)" if preflight.truncated else "") + ".")
        veto_lines.append(
            f"preflight funding: {preflight.funded} buckets funded under the profile "
            f"({preflight.funded_near_kickoff} near-kickoff) against {preflight.funded_today} "
            f"funded under today's order ({preflight.funded_near_kickoff_today} near-kickoff); "
            f"{preflight.uncovered} not covered; week total {preflight.week_total} at "
            f"{preflight.cost_per_pair} a pair.")
        veto_lines.append(
            f"caps unchanged: {preflight.daily_cap} USD a day and {preflight.weekly_cap} USD an "
            "ISO week, enforced by the same atomic reservation; a profile changes only when a "
            "cap binds.")
        veto_lines += [f"preflight caveat: {caveat}" for caveat in preflight.caveats]
        veto_lines.append(f"claim order in force today: {veto_profile.CLAIM_ORDER_BEFORE}")
        veto_lines.append("claim order under the profile, not in force: "
                          f"{veto_profile.CLAIM_ORDER_AFTER}")
        veto_lines.append(
            f"live setting: veto_pacing_profile={s.veto_pacing_profile!r}; the reservation "
            "check is a no-op while it is None, and this command writes no setting.")

    # Fix round 1, Important 4: `better` is the operator's, or it is unknown. It is never
    # inferred from fill counts - one extra counterfactual fill is not "arm B is better", and a
    # headline the user's §0.14a decision rests on may not turn on an inference nobody made.
    recommendation, why = decision.recommend(
        comparable_arms=len(arm_ids),
        accrual_identified=observed.distinct_games >= forecast.IDENTIFIED_MIN_GAMES,
        mature_outcomes=matured, markout_sign=markout_sign, baseline_resolved=resolved,
        arm_b_better=arm_b_better)
    evidence = decision.Evidence(
        baseline_proof="\n".join(f"- {line}" for line in baseline_lines),
        arm_results=arm_results,
        book_health="\n".join(f"- {line}" for line in health_lines),
        veto_pacing="\n".join(f"- {line}" for line in veto_lines),
        forecast=forecast_text, recommendation=recommendation, arm_c=arm_c, reason=why,
        run_id=run_id, manifest_hash=run.manifest_hash)
    try:
        print(decision.render(evidence, now=now))
    except decision.MissingEvidence as refused:
        # §1.11's own refusal, reported as a refusal rather than a traceback: the report is not
        # rendered at all while a required section is empty.
        print(f"decision report refused: {refused}")
        if not veto_lines:
            print("  the veto-pacing section is complete only with --veto-profile, --since and "
                  "--until: §1.11 counts the preflight against stored arrivals under unchanged "
                  "caps as completion evidence, and this command will not assert it without "
                  "reading it (I11, D16)")
        raise typer.Exit(code=1) from None
