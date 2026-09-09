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
migrate_app = typer.Typer(no_args_is_help=True, help="Alembic: additive schema history")
app.add_typer(migrate_app, name="migrate")
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


@migrate_app.command("upgrade")
def migrate_upgrade() -> None:
    """Run every migration the database has not applied yet, up to the baseline."""
    configure_logging()
    from harness.db.migrate import upgrade_head

    upgrade_head(get_settings().database_url)
    log.info("migrate: upgraded")


@migrate_app.command("stamp")
def migrate_stamp() -> None:
    """Record head as applied without executing it: the pre-Alembic database's one-time bridge."""
    configure_logging()
    from harness.db.migrate import stamp_head

    stamp_head(get_settings().database_url)
    log.info("migrate: stamped")


@migrate_app.command("current")
def migrate_current() -> None:
    """Print the revision the database records, or `none` when it has never been stamped."""
    from harness.db.migrate import current_revision

    print(current_revision(get_settings().database_url) or "none")


@migrate_app.command("ensure")
def migrate_ensure() -> None:
    """The guarded one-time stamp `make deploy-nas` runs before init-db.

    Prints the branch it took: `stamped` (a populated pre-Alembic database), `upgraded` (an
    empty one) or `current` (upgrade from the stored revision).
    """
    configure_logging()
    from harness.db.migrate import ensure

    print(ensure(get_settings().database_url))


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

    recorder = build_recorder(get_settings())
    try:
        run = recorder.maybe_tick(force=force)
    finally:
        # One-shot path: close the limits reader's httpx clients rather than leaking them until
        # the process exits (Task 6b fix round 1, Minor). A no-op when no reader was built.
        recorder.close()
    log.info("run %s status=%s n=%s credits=%s", run.id, run.status, run.n_requests, run.credits_used)


@app.command("run")
def run() -> None:
    configure_logging()
    from harness.ops.backup import delete_verified_plaintexts, encrypt_pending
    from harness.scheduler import build_recorder, build_scheduler, build_settler

    s = get_settings()
    backup_factory = make_session_factory(make_engine(s.database_url))

    def _backup_encrypt() -> None:
        now = datetime.now(timezone.utc)
        with backup_factory() as session:
            encrypted = encrypt_pending(session, s.backup_dir, s.backup_recipient_file, s.build_sha, now)
            deleted = delete_verified_plaintexts(session, s.backup_dir, s.build_sha, now)
        log.info("backup_encrypt rows=%s deleted_plaintexts=%s", len(encrypted), len(deleted))

    sched = build_scheduler(build_recorder(s), s.heartbeat_s, settler=build_settler(s),
                            settle_period_s=s.settle_period_s, backup_encrypt=_backup_encrypt,
                            backup_period_s=s.backup_encrypt_period_s)
    sched.start()
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("scheduler started heartbeat=%ss settle=%ss backup_encrypt=%ss", s.heartbeat_s,
             s.settle_period_s, s.backup_encrypt_period_s)
    while not stop["flag"]:
        time.sleep(1)
    sched.shutdown(wait=True)
    log.info("scheduler stopped")


@app.command("backup-keygen")
def backup_keygen_cmd(
    identity: Path = typer.Option(Path("secrets/backup_age_key"), "--identity",
                                  help="Where to write the private identity (0600)"),
    recipient: Path = typer.Option(Path("deploy/backup_age.pub"), "--recipient",
                                   help="Where to write the public recipient (committed source, pushed to the NAS)"),
) -> None:
    """Mac only: generates the backup age keypair. Refuses when `backup_dir` exists, which
    means this is running on the NAS -- the private key is never written there.

    `--identity`/`--recipient` default to the repo-relative paths the Makefile expects
    (`secrets/backup_age_key`, `deploy/backup_age.pub`) -- not `Settings.backup_recipient_file`,
    which is the NAS container's runtime bind-mount path, not a place to ever write from here.

    Prints the copy-out instruction with the actual paths written (never the private key
    itself); the controller adds the Carried-fixes nag the first time this succeeds and clears
    it once the user confirms.
    """
    configure_logging()
    from harness.ops.backup import keygen

    s = get_settings()
    if s.backup_dir.exists():
        log.error("backup_dir %s exists; backup-keygen runs on the Mac only", s.backup_dir)
        raise typer.Exit(1)
    keygen(identity, recipient)
    print(f"Wrote {identity} (0600) and {recipient}.")
    print(f"COPY {identity} SOMEWHERE SAFE NOW. A backup no one can decrypt is not a backup.")
    print(f"The private key is never pushed to the NAS; only {recipient} is.")


@app.command("backup-encrypt")
def backup_encrypt_cmd() -> None:
    """One encrypt pass now, for a manual check beside the scheduled `app-run` job."""
    configure_logging()
    from harness.ops.backup import encrypt_pending

    s = get_settings()
    with make_session_factory(make_engine(s.database_url))() as session:
        rows = encrypt_pending(session, s.backup_dir, s.backup_recipient_file, s.build_sha,
                               datetime.now(timezone.utc))
    print(f"rows={len(rows)}")


@app.command("backup-decrypt")
def backup_decrypt_cmd(
    age_file: Path = typer.Argument(..., help="Ciphertext to decrypt"),
    out: Path = typer.Option(..., "--out", help="Where to write the decrypted plaintext"),
) -> None:
    """Mac-side, with `secrets/backup_age_key`. Prints the sha256 of the decrypted plaintext."""
    configure_logging()
    from harness.ops.agefmt import AgeError
    from harness.ops.backup import decrypt_file

    s = get_settings()
    try:
        digest = decrypt_file(age_file, out, s.backup_identity_file)
    except (AgeError, ValueError) as exc:
        # decrypt() raises a bare ValueError for an unparseable identity, beside AgeError for
        # every other way a decrypt can fail.
        log.error("backup-decrypt failed: %s", exc)
        raise typer.Exit(1) from exc
    print(digest)


@app.command("backup-drill-record")
def backup_drill_record_cmd(
    build_sha: str = typer.Option(..., "--build-sha"),
    decrypt_ok: bool = typer.Option(..., "--decrypt-ok/--no-decrypt-ok"),
    plaintext_sha256: str = typer.Option(..., "--plaintext-sha256"),
    rows_match: bool = typer.Option(None, "--rows-match/--no-rows-match"),
) -> None:
    """Records a Mac-side decrypt drill's result. The Mac half of §4.4, run over the tunnel."""
    configure_logging()
    from harness.ops.backup import record_drill

    s = get_settings()
    with make_session_factory(make_engine(s.database_url))() as session:
        row = record_drill(session, build_sha, decrypt_ok, plaintext_sha256, rows_match,
                           datetime.now(timezone.utc), {})
    print(f"backup_run={row.id} status={row.status}")


@app.command("backup-precheck")
def backup_precheck_cmd() -> None:
    """The query half of §5's deploy order: exit 0 when the newest nightly `backup_runs` row is
    `ok` and younger than `backup_nightly_max_age_h`, else 1. Never runs docker itself -- the
    deploy recipe (Task 14) decides what to run on a non-zero exit.
    """
    configure_logging()
    from harness.ops.backup import newest_nightly_ok

    s = get_settings()
    with make_session_factory(make_engine(s.database_url))() as session:
        row = newest_nightly_ok(session, datetime.now(timezone.utc), s.backup_nightly_max_age_h)
    if row is None:
        print(f"no nightly backup_runs row ok within {s.backup_nightly_max_age_h}h")
        raise typer.Exit(1)
    print(f"ok backup_run={row.id} finished_at={row.finished_at}")


@app.command("kalshi-smoke")
def kalshi_smoke_cmd(
    env: str = typer.Option("demo", "--env", help="Only 'demo' is accepted."),
) -> None:
    """One full authenticated round trip against Kalshi's demo exchange (addendum 1.5).

    A controller command, never a service: no compose service mounts the demo key pair, so the
    credentials reach a container only for the length of one `docker compose run --rm` that
    supplies the two mounts itself. See docs/runbooks/phase0-deploy.md.

    Exits 0 when every step passed, and also when the demo account is unfunded (pre-loaded
    decision 1: an unfunded demo cannot rest an order, and that is not a deploy failure).
    Demo prices are not evidence and reach no table.
    """
    configure_logging()
    from harness.venues.kalshi.authed import (
        KalshiApiError, LiveGuardRefused, PriceOffGrid,
    )
    from harness.venues.kalshi.http import VenueTransportError
    from harness.venues.kalshi.smoke import format_steps, run_smoke

    if env != "demo":
        # The production writer is refused by `make_writer` anyway; refusing here keeps the
        # command's contract single-valued rather than resting on a guard two layers down.
        print(f"kalshi-smoke runs against demo only, not {env!r}")
        raise typer.Exit(2)
    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    try:
        result = run_smoke(s, factory, datetime.now(timezone.utc))
    except LiveGuardRefused as exc:
        print(f"refused: {exc.missing} is missing")
        raise typer.Exit(1) from None
    except (KalshiApiError, VenueTransportError, PriceOffGrid) as exc:
        # `run_smoke` turns every in-sequence failure into a named step, so these can only
        # arrive from the writer build itself. They are still named outcomes, not tracebacks:
        # the class name only, never the message, which may quote a response body.
        print(f"failed before the sequence started: {type(exc).__name__}")
        raise typer.Exit(1) from None
    print(format_steps(result))
    raise typer.Exit(result.exit_code())


@app.command("venue-enable")
def venue_enable_cmd(
    venue: str = typer.Argument(..., help="The venue to re-enable, e.g. kalshi."),
    env: str = typer.Option("prod", "--env", help="prod or demo."),
) -> None:
    """Clear an `unavailable` or `frozen` `venue_status` row: the manual re-enable after an
    outage mark. Never automatic -- an account refused twice needs a human to find out why
    before it sends again -- and the controller journals every use."""
    configure_logging()
    from harness.execution.venue import enable_venue, read_status

    s = get_settings()
    with make_session_factory(make_engine(s.database_url))() as session:
        before = read_status(session, venue, env)
        changed = enable_venue(session, venue, env, datetime.now(timezone.utc))
        session.commit()
        after = read_status(session, venue, env)
    if changed:
        print(f"{venue}/{env}: {before} -> {after}")
    else:
        print(f"{venue}/{env}: no change (was {before})")


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


@app.command("benchmarks")
def benchmarks_cmd(game_id: int = typer.Option(..., "--game-id")) -> None:
    """Compute benchmarks for one game now, by hand -- for an operator who does not want to
    wait for the game's kickoff + 5 min to fall inside the next scheduled settlement pass.

    Runs the same insert path the `benchmarks`/`result_benchmarks` stages do, scoped to
    `game_id`; the insert is through the same unique key, so a re-run is exactly as idempotent
    as the scheduled job's.
    """
    configure_logging()
    from harness.db.models import Game
    from harness.settlement.benchmarks import (
        compute_benchmarks_for_game,
        insert_result_benchmarks_for_game,
    )

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    now = datetime.now(timezone.utc)
    with factory() as session:
        if session.get(Game, game_id) is None:
            log.error("game %s not found", game_id)
            raise typer.Exit(1)
        n_benchmarks = compute_benchmarks_for_game(session, game_id, now)
        session.commit()
        n_result = insert_result_benchmarks_for_game(session, game_id, now)
        session.commit()
    print(f"game_id={game_id} benchmarks={n_benchmarks} result_benchmarks={n_result}")


@app.command("report")
def report_cmd(
    week: int = typer.Option(..., "--week", help="ISO week number (R7)"),
    year: int = typer.Option(2026, "--year"),
    out: str = typer.Option("-", "--out", help="Markdown destination; '-' is stdout"),
    selected_out: Path = typer.Option(None, "--selected-out",
                                      help="Write the selection artefact (week-38 freeze)"),
    confirm: Path = typer.Option(None, "--confirm",
                                help="Restrict tables 2 and 4 to a selection artefact"),
) -> None:
    """The weekly report (§7.2): ten tables over one ISO week's non-replay rows.

    Read-only. `--selected-out` writes the cells and contrasts this report selects, which is
    what the controller commits as `docs/reports/2026-w38-selected.json` on Monday
    2026-09-21; `--confirm` reads such a file back and evaluates only that set, which is what
    the week-3 confirmation report does.
    """
    configure_logging()
    from harness import telemetry
    from harness.report.tables import weekly_tables
    from harness.report.weekly import (
        build_meta,
        persist_report,
        read_selected,
        render_markdown,
        restrict_to_selection,
        selection_document,
        write_selected,
    )

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url, BATCH_STATEMENT_TIMEOUT_MS))
    with factory() as session:
        try:
            tables = weekly_tables(session, year, week, s)
        except ValueError as exc:  # an ISO week that does not exist in that year
            log.error("%s", exc)
            raise typer.Exit(1) from exc
        meta = build_meta(session, s, year, week, confirmation=confirm is not None)
        # The selection is computed from the unrestricted tables: a confirmation run reports on
        # a frozen set, it never selects a new one.
        if selected_out is not None:
            write_selected(selected_out, selection_document(tables, meta))
            log.info("selection written to %s", selected_out)
        if confirm is not None:
            tables = restrict_to_selection(tables, read_selected(confirm))
        document = render_markdown(tables, meta)
        # Task 12b: report_runs/report_cells and the report_written event, in the same
        # transaction as the markdown write -- `provisional = false`, since a scheduled
        # `harness report` is the week's authoritative rendering (the hourly `report_wtd`
        # settlement stage writes the provisional ones).
        #
        # Fix round 1, I5: a `--confirm` run is deliberately restricted to a frozen cell set
        # (the week-3 confirmation), never a rendering of the week's real data -- persisting it
        # as `provisional = false` would make it indistinguishable from, and shadow, the actual
        # weekly report for that week. So it persists nothing at all; the confirmation's only
        # output is the markdown file/stdout, exactly as before this task.
        if confirm is None:
            now = datetime.now(timezone.utc)
            report_run_id = persist_report(session, tables, meta, year, week,
                                           provisional=False, markdown=document)
            telemetry.event(
                session, "report_written", f"weekly report {year}-W{week:02d} written",
                ref={"year": year, "week": week, "report_run_id": report_run_id}, ts=now)
            session.commit()
    if out == "-":
        print(document, end="")
    else:
        path = Path(out)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(document)
        log.info("report written to %s", path)


@app.command("gate")
def gate_cmd() -> None:
    """Evaluate the go-live gate (§9.5, F13) and store one row per exec variant.

    Prints every variant's criteria table, marks the one row the phase gate is judged on
    (`Settings.gate_variant`, falling back to the active primary) and exits 0 whether or not
    the gate passed: the exit code reports whether the evaluation ran, not the verdict. It
    cannot report a pass in phase 3 -- `legal_decision` and `live_trading_env` are False by
    construction -- and every run stores a new report; no stored row is ever rewritten.
    """
    configure_logging()
    from harness import telemetry
    from harness.report.gate import (
        evaluate_all,
        exec_variant_ids,
        registered_variants,
        render_gate,
    )

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url, BATCH_STATEMENT_TIMEOUT_MS))
    now = datetime.now(timezone.utc)
    with factory() as session:
        variant_ids = exec_variant_ids(session, s)
        if not variant_ids:
            log.error("no active registered variant to evaluate; run `harness variants register`")
            raise typer.Exit(1)
        rows = registered_variants(session)
        results = evaluate_all(session, now, variant_ids, s.gate_variant)
        telemetry.event(session, "gate_evaluated", f"gate evaluated for {len(results)} variant(s)",
                        ref={"criteria_hash": results[0].criteria_hash if results else None}, ts=now)
        session.commit()
        document = render_gate(results, {r.variant_id: r.name for r in rows},
                               {r.variant_id: r.tier for r in rows})
    print(document)
    log.info("gate evaluated at %s for %d variant(s)", now.isoformat(), len(results))


#: `operator_events.kind` values `harness note` may write (design spec §3.2).
NOTE_KINDS = ("amendment", "alias_pass", "verify_pass", "verify_fail", "drill", "note")


@app.command("note")
def note_cmd(
    text: str = typer.Argument(..., help="Free text; sanitized and truncated to 200 chars (F50)"),
    kind: str = typer.Option(..., "--kind", help=f"one of: {', '.join(NOTE_KINDS)}"),
) -> None:
    """One `operator_events` row for the operator or the autopilot (design spec §3.2): a
    pre-registration amendment, an alias pass, a verify pass or fail, a drill, or a plain note.
    Prints the new row's id."""
    configure_logging()
    from harness import telemetry

    if kind not in NOTE_KINDS:
        log.error("unknown --kind %r; must be one of: %s", kind, ", ".join(NOTE_KINDS))
        raise typer.Exit(1)
    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    with factory() as session:
        event_id = telemetry.event(session, kind, text, ts=datetime.now(timezone.utc))
        session.commit()
    print(event_id)


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
    execute: bool = typer.Option(False, "--execute",
                                help="Also re-run the paper executor on a 15 s grid over the range"),
) -> None:
    """Re-score a run range under one variant; with `--execute`, re-run the executor over it.

    Places no order anywhere: `--execute` steps a *replay* executor, whose every row is tagged
    `replay = true` and which never touches a live row, the ledger, the heartbeat or telemetry.
    """
    configure_logging()
    from harness.replay import ReplayStepError, replay

    s = get_settings()
    # The executor's own statement timeout: `--execute` runs the same loop the service does,
    # and a tape scan that outlives a step is a stall there for the same reason it is here.
    engine = (make_engine(s.database_url, EXEC_STATEMENT_TIMEOUT_MS) if execute
              else make_engine(s.database_url))
    factory = make_session_factory(engine)
    with factory() as session:
        try:
            counts = replay(session, from_run, to_run, variant, variant_file=file,
                            execute=execute, settings=s)
        # A replay whose grid did not run cleanly has no counts worth printing: exiting 0 with
        # `orders=0` would read as a total replay-versus-live divergence rather than a failure.
        except (ValueError, ReplayStepError) as exc:
            log.error("%s", exc)
            raise typer.Exit(1) from exc

    total = counts.signals_candidate + counts.signals_rejected
    rate = counts.signals_candidate / total if total else 0.0
    tail = f" orders={counts.orders} fills={counts.fills}" if execute else ""
    print(
        f"runs={counts.runs} candidate={counts.signals_candidate} rejected={counts.signals_rejected} "
        f"inserted={counts.inserted} candidate_rate={rate:.4f}{tail}"
    )


@app.command("export-fixture")
def export_fixture_cmd(
    from_run: int = typer.Option(None, "--from-run"),
    to_run: int = typer.Option(None, "--to-run"),
    kind: str = typer.Option("day", "--kind", help="day | ws-tape"),
    ticker: str = typer.Option(None, "--ticker"),
    from_ts: str = typer.Option(None, "--from", help="ISO-8601 instant, UTC if no offset"),
    to_ts: str = typer.Option(None, "--to", help="ISO-8601 instant, UTC if no offset"),
    out: str = typer.Option(..., "--out", help="File to write, or '-' for stdout"),
) -> None:
    """Dump a slice of the record as JSON, so a day can be replayed away from the NAS.

    `--kind day --from-run A --to-run B` dumps the `raw_responses` of the range plus every
    `orderbook_events` and `venue_trades` row inside the runs' own window -- the three tables a
    replay reads and the only ones that cannot be rebuilt from anything else. `--kind ws-tape
    --ticker T --from TS --to TS` dumps one ticker's anchoring snapshot, deltas and prints, the
    shape `tests/fixtures/tape_sample_*.json` already carries.

    Reads only. Nothing here writes a row, and no credential is opened: the bodies come off the
    database exactly as the recorder taped them.
    """
    configure_logging()
    from harness.fixtures import export_day, export_ws_tape, write_export

    s = get_settings()
    with make_session_factory(make_engine(s.database_url, BATCH_STATEMENT_TIMEOUT_MS))() as session:
        try:
            if kind == "day":
                if from_run is None or to_run is None:
                    raise ValueError("--kind day needs --from-run and --to-run")
                doc = export_day(session, from_run, to_run, s.tick_budget_s)
            elif kind == "ws-tape":
                if not ticker or not from_ts or not to_ts:
                    raise ValueError("--kind ws-tape needs --ticker, --from and --to")
                doc = export_ws_tape(session, ticker, _utc(from_ts), _utc(to_ts))
            else:
                raise ValueError(f"unknown --kind {kind!r} (day | ws-tape)")
        except ValueError as exc:
            log.error("%s", exc)
            raise typer.Exit(1) from exc
    write_export(doc, out)


def _utc(value: str) -> datetime:
    """One ISO-8601 instant off the command line. The record is all UTC (F2), so a bare
    timestamp is read as UTC rather than as whatever the operator's box is set to.

    Parsed here rather than by Typer, whose `datetime` option accepts three fixed formats and
    rejects the offset-bearing string every timestamp in this harness is printed with.
    """
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValueError(f"{value!r} is not an ISO-8601 instant") from exc
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


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
