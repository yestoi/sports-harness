"""`harness exp …` (§1.1). Every subcommand of this milestone lands here with its implementation."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal

import typer
from sqlalchemy import text

from harness.config.settings import get_settings
from harness.execution.book import newest_ws_connect
from harness.experiments.execution_viability import EXP_DB_ROLE, EXP_LABEL
from harness.experiments.execution_viability import (bookhealth, source, storage,
                                                     veto_profile)
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
    print(f"  claim order     {veto_profile.CLAIM_ORDER_AFTER}")
    print(f"  claim order now {veto_profile.CLAIM_ORDER_BEFORE}")
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
