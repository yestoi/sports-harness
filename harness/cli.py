import contextlib
import importlib.resources
import logging
import signal
import time
from datetime import datetime, timezone
from pathlib import Path

import typer
import uvicorn

from harness.config.settings import Settings, get_settings
from harness.db.engine import (
    BATCH_STATEMENT_TIMEOUT_MS,
    EXEC_STATEMENT_TIMEOUT_MS,
    make_engine,
    make_session_factory,
)
from harness.db.schema import create_schema
from harness.logging_setup import configure_logging

app = typer.Typer(no_args_is_help=True)
variants_app = typer.Typer(no_args_is_help=True)
app.add_typer(variants_app, name="variants")
log = logging.getLogger("harness")

#: How stale the executor heartbeat may be before `exec-health` reports the container unhealthy.
#: Eight loops at the 15 s period: long enough that one slow step is not an outage.
EXEC_HEALTH_MAX_AGE_S = 120


def _variants_source(s: Settings):
    """A context manager yielding a filesystem `Path` to load variant YAMLs from.

    `s.variants_dir` wins when set (an operator override); otherwise the packaged
    `harness/variants` directory is materialized (works even from a zipped install).
    """
    if s.variants_dir is not None:
        return contextlib.nullcontext(s.variants_dir)
    return importlib.resources.as_file(importlib.resources.files("harness.variants"))


def _print_variants(rows) -> None:
    print(f"{'name':<24}{'tier':<12}{'variant_id':<14}{'active'}")
    for v in rows:
        print(f"{v.name:<24}{v.tier:<12}{v.variant_id:<14}{True}")


@app.command("init-db")
def init_db() -> None:
    configure_logging()
    s = get_settings()
    # create_schema's DDL can legitimately outrun the 30 s default, and query_canceled is in its
    # retry set, so a slow statement would be cancelled twice instead of finishing.
    create_schema(make_engine(s.database_url, BATCH_STATEMENT_TIMEOUT_MS))
    log.info("schema created")


@app.command("partition-bulk-tables")
def partition_bulk_tables_cmd() -> None:
    """One-off (F19): turn the live orderbook_events and venue_trades into weekly partitions."""
    configure_logging()
    s = get_settings()
    from harness.db.partition import partition_bulk_tables

    # Same reason as init-db, more so: validating the legacy CHECK and attaching the partition
    # both scan the tape, which is far past the 30 s default statement timeout.
    engine = make_engine(s.database_url, BATCH_STATEMENT_TIMEOUT_MS)
    migrated = partition_bulk_tables(engine, datetime.now(timezone.utc))
    log.info("partitioned: %s", ", ".join(migrated) if migrated else "nothing (already partitioned)")


@app.command("tick-once")
def tick_once(force: bool = typer.Option(False, "--force", help="Fetch every source now, ignoring cadence")) -> None:
    configure_logging()
    from harness.scheduler import build_recorder

    run = build_recorder(get_settings()).maybe_tick(force=force)
    log.info("run %s status=%s n=%s credits=%s", run.id, run.status, run.n_requests, run.credits_used)


@app.command("run")
def run() -> None:
    configure_logging()
    from harness.scheduler import build_recorder, build_scheduler, build_settler

    s = get_settings()
    sched = build_scheduler(build_recorder(s), s.heartbeat_s, settler=build_settler(s),
                            settle_period_s=s.settle_period_s)
    sched.start()
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("scheduler started heartbeat=%ss settle=%ss", s.heartbeat_s, s.settle_period_s)
    while not stop["flag"]:
        time.sleep(1)
    sched.shutdown(wait=True)
    log.info("scheduler stopped")


@app.command("exec")
def exec_cmd() -> None:
    """Run the paper executor on its own interval. Places no live order and loads no secret."""
    configure_logging()
    from harness.scheduler import build_exec_scheduler, build_executor

    s = get_settings()
    sched = build_exec_scheduler(build_executor(s), s.exec_period_s)
    sched.start()
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("executor started period=%ss variants=%s", s.exec_period_s, s.exec_variants)
    while not stop["flag"]:
        time.sleep(1)
    sched.shutdown(wait=True)
    log.info("executor stopped")


@app.command("exec-once")
def exec_once() -> None:
    """One executor step, for a manual check. The advisory lock keeps it off the service's toes."""
    configure_logging()
    from harness.scheduler import build_executor

    print(build_executor(get_settings()).step())


@app.command("exec-health")
def exec_health() -> None:
    """Exit 1 when the executor's heartbeat is missing or older than EXEC_HEALTH_MAX_AGE_S.

    Its own engine and its own connection: the compose healthcheck runs it in a fresh process,
    and a health probe that shares a pool with a stalled loop would inherit the stall.
    """
    configure_logging()
    from harness.execution.store import read_heartbeat

    s = get_settings()
    with make_session_factory(make_engine(s.database_url, EXEC_STATEMENT_TIMEOUT_MS))() as session:
        row = read_heartbeat(session)
    if row is None or row.last_loop_at is None:
        log.error("no executor heartbeat")
        raise typer.Exit(1)
    age = float(row.age_s)
    if age > EXEC_HEALTH_MAX_AGE_S:
        log.error("executor heartbeat is %.0fs old (max %ss)", age, EXEC_HEALTH_MAX_AGE_S)
        raise typer.Exit(1)
    log.info("executor healthy: %.0fs since loop %s", age, row.loops)


@app.command("settle")
def settle_cmd() -> None:
    """One settlement job now: settle final games, then fetch the venue's own results.

    Safe beside the scheduled job -- every write is keyed and goes in `on conflict do nothing`,
    so the two passes cannot double-post a fill or overwrite a result.
    """
    configure_logging()
    from harness.scheduler import build_settler

    row = build_settler(get_settings()).run()
    stages = " ".join(f"{s['name']}={s['counts'] or s['error']}" for s in row.notes["stages"])
    print(f"job_run={row.id} status={row.status} budget_exhausted={row.budget_exhausted} "
          f"stale_unsettled={row.notes['stale_unsettled']} {stages}")
    if row.status == "error":
        raise typer.Exit(1)


@app.command("serve")
def serve(port: int = 8080, host: str = "0.0.0.0") -> None:
    configure_logging()
    from harness.dashboard.app import create_dashboard

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    uvicorn.run(create_dashboard(factory, s), host=host, port=port, log_config=None)


@app.command("seed-teams")
def seed_teams() -> None:
    configure_logging()
    import importlib.resources
    import json
    import urllib.request

    from harness.matching.teams import load_manual_aliases, seed_teams_from_espn

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    base = s.espn_base_url.rstrip("/")
    urls = [("nfl", f"{base}/nfl/teams?limit=100"), ("ncaaf", f"{base}/college-football/teams?limit=1000&groups=80")]
    with factory() as session:
        for sport, url in urls:
            body = json.load(urllib.request.urlopen(url, timeout=20))
            log.info("seeded %s teams from %s", seed_teams_from_espn(session, sport, body), url)
        # Commit the ESPN-seeded teams before touching the manual-alias YAML, so a packaging or
        # parsing problem with that file cannot roll back teams that were already seeded.
        session.commit()
        aliases_ref = importlib.resources.files("harness.matching").joinpath("aliases_manual.yaml")
        if not aliases_ref.is_file():
            log.error("manual aliases file not found at %s; teams were seeded but no manual aliases were loaded",
                      aliases_ref)
            return
        with importlib.resources.as_file(aliases_ref) as aliases_path:
            n = load_manual_aliases(session, aliases_path)
        session.commit()
        log.info("manual aliases loaded: %d", n)


@variants_app.command("register")
def variants_register() -> None:
    configure_logging()
    from harness.strategy.variants import active_variants, load_variants, register_variants

    s = get_settings()
    with _variants_source(s) as directory:
        variants = load_variants(directory)
        factory = make_session_factory(make_engine(s.database_url))
        with factory() as session:
            result = register_variants(session, variants, datetime.now(timezone.utc), prune=True)
            rows = active_variants(session)
    print(f"added={result.added} unchanged={result.unchanged} deactivated={result.deactivated}")
    _print_variants(rows)


@variants_app.command("list")
def variants_list() -> None:
    configure_logging()
    from harness.strategy.variants import active_variants

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    with factory() as session:
        rows = active_variants(session)
    _print_variants(rows)


@app.command("price-once")
def price_once(run_id: int = typer.Option(None, "--run-id")) -> None:
    """Price and signal one run. With `--run-id`, `now` is pinned to that run's own
    `finished_at` -- not wall-clock time -- so a backfill against an old run sees exactly the
    books it fetched and nothing that postdates it. Without `--run-id`, the latest run is
    picked (as today) and `now` is wall-clock time, since that run is effectively live.
    """
    configure_logging()
    from harness.db.models import Run
    from harness.strategy.pipeline import price_and_signal, pricing_clock_for_run

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    with factory() as session:
        if run_id is None:
            rid = session.query(Run.id).order_by(Run.id.desc()).limit(1).scalar()
            if rid is None:
                log.error("no runs found")
                raise typer.Exit(1)
            now = datetime.now(timezone.utc)
        else:
            run = session.get(Run, run_id)
            if run is None:
                log.error("run %s not found", run_id)
                raise typer.Exit(1)
            rid = run_id
            now = pricing_clock_for_run(run, s.tick_budget_s)
        result = price_and_signal(session, rid, now, s, s.price_budget_s)
    print(f"run_id={rid} {result}")


@app.command("replay")
def replay_cmd(
    from_run: int = typer.Option(..., "--from-run"),
    to_run: int = typer.Option(..., "--to-run"),
    variant: str = typer.Option(..., "--variant"),
    file: Path = typer.Option(None, "--file"),
) -> None:
    configure_logging()
    from harness.replay import replay

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    with factory() as session:
        try:
            counts = replay(session, from_run, to_run, variant, variant_file=file)
        except ValueError as exc:
            log.error("%s", exc)
            raise typer.Exit(1) from exc

    total = counts.signals_candidate + counts.signals_rejected
    rate = counts.signals_candidate / total if total else 0.0
    print(
        f"runs={counts.runs} candidate={counts.signals_candidate} rejected={counts.signals_rejected} "
        f"inserted={counts.inserted} candidate_rate={rate:.4f}"
    )


@app.command("reprocess")
def reprocess_cmd(from_raw_id: int = 0, family: list[str] = typer.Option(None), truncate: bool = False) -> None:
    configure_logging()
    from harness.normalize.runner import reprocess

    s = get_settings()
    with make_session_factory(make_engine(s.database_url))() as session:
        log.info("reprocess done %s", reprocess(session, from_raw_id, list(family) if family else None, truncate))


@app.command("ws-record")
def ws_record() -> None:
    configure_logging()
    s = get_settings()
    if not s.has_kalshi_credentials():
        log.info("kalshi credentials absent; ws recorder disabled")
        return
    from harness.feeds.http import HttpClient
    from harness.recorder.ws_sink import WsSink
    from harness.venues.kalshi.ws import WsRecorder

    factory = make_session_factory(make_engine(s.database_url))
    WsRecorder(s, factory, WsSink(factory), http=HttpClient(s.http_timeout_s)).run_forever()


@app.command("match-report")
def match_report(sport: str = "all") -> None:
    configure_logging()
    from sqlalchemy import func, text

    from harness.db.models import Game, VenueMarket
    from harness.matching.teams import AMBIGUOUS_TEAM_ID

    s = get_settings()
    with make_session_factory(make_engine(s.database_url))() as session:
        sports = ["nfl", "ncaaf"] if sport == "all" else [sport]
        for sp in sports:
            prefix = "KXNFL" if sp == "nfl" else "KXNCAAF"
            q = session.query(VenueMarket.match_status, func.count()).filter(VenueMarket.series_ticker.like(f"{prefix}%")).group_by(VenueMarket.match_status)
            counts = dict(q.all())
            total = sum(counts.values()) or 1
            print(f"== {sp}: venue markets {total}")
            for st in ("matched", "fuzzy", "manual", "unmatched"):
                print(f"  {st:10s} {counts.get(st, 0):6d}  {100 * counts.get(st, 0) / total:5.1f}%")
            reasons = session.execute(text(
                "select match_reason, count(*), min(ticker) from venue_markets where series_ticker like :p and match_status='unmatched' "
                "group by 1 order by 2 desc limit 30"), {"p": f"{prefix}%"}).all()
            for reason, n, ex in reasons:
                print(f"  {n:6d}  {reason!s:60.60s}  e.g. {ex}")
            unlinked = session.query(Game).filter(Game.sport == sp, Game.espn_event_id.is_(None)).count()
            print(f"  games without ESPN link: {unlinked}")
        recent_notes = session.execute(text(
            "select notes from runs where started_at >= now() - interval '7 days' and notes is not null "
            "order by started_at desc limit 500")).scalars().all()
        unresolved: set[str] = set()
        for notes in recent_notes:
            for name in (notes or {}).get("unresolved_teams", []):
                unresolved.add(name)
        print(f"unresolved Odds API names (7d): {sorted(unresolved) if unresolved else 'none'}")
        amb = session.execute(text(
            "select sport || '/' || source || '/' || raw_name from team_aliases where team_id = :s order by 1"),
            {"s": AMBIGUOUS_TEAM_ID}).scalars().all()
        print(f"ambiguous aliases: {len(amb)} (first 10: {amb[:10]})")


if __name__ == "__main__":
    app()
