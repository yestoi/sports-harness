"""`harness exp …` (§1.1). Every subcommand of this milestone lands here with its implementation."""
from __future__ import annotations

from datetime import datetime, timedelta

import typer
from sqlalchemy import text

from harness.config.settings import get_settings
from harness.execution.book import newest_ws_connect
from harness.execution.store import first_gap_ts
from harness.experiments.execution_viability import EXP_DB_ROLE, EXP_LABEL
from harness.experiments.execution_viability import bookhealth, source, storage
from harness.logging_setup import configure_logging

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
#: and ts < :end` inside the week's partition - the `(ticker, ts)` shape §1.3(a) names.
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

#: The interval's public prints, on `ix_trades_ticker_ts`: §1.7's fourth fixture turns on them.
_PRINTS_IN = text(
    "select count(*) from venue_trades where ticker = :t and ts >= :start and ts < :end")


def _observe_intervals(session, ticker: str, since: datetime, until: datetime,
                       interval_s: int) -> list[bookhealth.HealthInput]:
    """One `HealthInput` per interval, every read bounded by that interval.

    The anchor is read once, before the loop, and carried forward: it can only change where a
    snapshot row landed, which the interval's own aggregate already counts.
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
        bounds = {"t": ticker, "start": start, "end": end}
        tape = session.execute(_INTERVAL_TAPE, bounds).one()
        prints = session.execute(_PRINTS_IN, bounds).scalar() or 0
        # A gap is per subscription (`store.first_gap_ts`), so a REST anchor has none to ask about.
        gap_ts = first_gap_ts(session, sid, anchor_id, at=end) if sid else None
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
