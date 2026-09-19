"""`harness exp …` (§1.1). Every subcommand of this milestone lands here with its implementation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import typer
from sqlalchemy import text

from harness.config.settings import get_settings
from harness.execution.book import newest_ws_connect
from harness.experiments.execution_viability import EXP_DB_ROLE, EXP_LABEL
from harness.experiments.execution_viability import (adapter, arms, baseline, bookhealth,
                                                     capture, episodes, liquidity, outcomes,
                                                     report, source, storage, veto_profile)
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
    print(EXP_LABEL)


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
        rows = [bookhealth.classify(obs) for obs in observations]
        verified = {obs.interval_start
                    for obs in bookhealth.sample_intervals(observations, seed=seed)}
        for row in rows:
            if row.interval_start in verified:
                row.evidence["snapshot_at_interval_end"] = bookhealth.verify_snapshot(
                    session, ticker, row.interval_end)
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


#: The run's frozen manifest and its hash, on `exp_run`'s own primary key `run_id` (§4.1): one
#: row, one index lookup, no scan. The resume point itself is read by `storage.resume`, which
#: is the only reader of `exp_checkpoint` (§1.2's refusal happens there and nowhere else).
_EXP_RUN_MANIFEST = text("select manifest, manifest_hash from exp_run where run_id = :r")


def _check_resume(stored_hash: str, manifest) -> None:
    """§1.2's refusal at the **run** level, before the arm's checkpoint is read at all."""
    from harness.experiments.execution_viability.manifest import check_resume

    check_resume(stored_hash, manifest)


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
        runner = adapter.ArmRunner(run_id=run_id, arm_id=arm, policy=None,
                                   variant_cfg=variant_cfg,
                                   exec_settings=exec_settings, walkers={},
                                   tz=s.tz_local, ledger=liquidity.PortfolioLedger())
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
        finally:
            writer.close()
    chunks = result.chunks
    print(f"run={run_id} arm={arm} resumed={resumed} chunks={len(chunks)}")
    print(f"  retained action instants   {len(instants)}")
    print(f"  live loop estimate         {estimate}")
    print(f"  stepped                    {result.stepped}")
    print(f"  orders                     {chunks[-1].orders if chunks else 0}")
    print(f"  fills                      {result.fills}")
    print(f"  open at the end            {chunks[-1].open_orders if chunks else 0}")
    if result.stopped:
        print(f"  stopped                    {result.stopped}")


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


@exp_app.command("report")
def report_cmd(run_id: str = typer.Option(..., "--run-id")) -> None:
    """Render one run's arm results under §1.9's reporting contract.

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
        outcomes = {(row.arm_id, row.portfolio): row
                    for row in session.execute(_REPORT_OUTCOMES, {"r": run_id}).all()}
        health = session.execute(_REPORT_HEALTH, {"r": run_id}).all()
        scope = session.execute(_REPORT_SPACING, {"r": run_id}).scalar() or {}
        share = session.execute(_REPORT_GAME_SHARE, {"r": run_id}).first()
        allocation = session.execute(_REPORT_ALLOCATION, {"r": run_id}).first()
    rows = []
    for row in arms_rows:
        outcome = outcomes.get((row.arm_id, row.portfolio))
        rows.append({
            "arm_id": row.arm_id, "portfolio": row.portfolio, "orders": row.orders,
            "fills": row.fills, "markets": row.markets, "partial_fills": row.partial_fills,
            "contracts": row.contracts, "capacity_exclusions": row.capacity_exclusions,
            "markout_1800": None if outcome is None else outcome.markout_1800,
            "source_age_s": None if outcome is None else outcome.source_age_s,
            "censored": None if outcome is None else outcome.censored,
            "missing": None if outcome is None else outcome.missing,
            "registered": False})
    concentration = {
        "maximum contribution by game": (
            f"game {share.game_id}: {share.contracts} contracts" if share is not None
            else "no filled contracts in this run"),
        "allocated against observed print volume": (
            f"{allocation.allocated} of {allocation.available} contracts"
            if allocation is not None else "no allocation rows")}
    print(report.render(
        [], rows, run_id=run_id, manifest_hash=run.manifest_hash,
        concentration=concentration, instants=scope.get("instants"),
        live_loop_estimate=scope.get("live_loop_estimate")))
    # §1.7's counts, printed beside the run's own instants: the ops read-back records these
    # three numbers the first time this command is run by hand.
    print(f"book health classifications ({len(health)}):")
    for row in health:
        print(f"  {row.classification:<24} {row.n}")
    # §1.9(a): the one schedule every arm's numbers were computed on, named in the output so a
    # reader never has to infer which horizons a blank cell belongs to.
    print(f"common outcome schedule: {', '.join(outcomes.HORIZONS)} "
          f"(missing reasons: {', '.join(outcomes.MISSING_REASONS)})")
    cadence = ((run.manifest or {}).get("opportunity_definition") or {}).get("cadence_in_force")
    if cadence:
        print(f"episode gap rule at the run's cadence ({cadence} s): "
              f"{episodes.gap_rule_s(int(cadence))} s")
    # §1.6(c): the distinctions each arm decided under, with the hash the manifest froze them
    # at, so a note cannot be dropped between the run and its report.
    for arm_id in sorted({row["arm_id"] for row in rows}):
        spec = arms.ARMS.get(arm_id)
        if spec is not None:
            print(f"  arm {arm_id} spec={spec.spec_hash()[:12]} "
                  f"distinctions={len(spec.notes)} source={spec.observation_source}")
