"""The `exp` command group exists, is registered once, and its isolation-check prints the C2 pair."""
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest
import typer
from sqlalchemy import text
from typer.testing import CliRunner

from harness.cli import app
from harness.db.models import Order
from harness.execution.plan import ExecSettings
from harness.experiments.execution_viability import adapter, arms, cli, storage
from harness.experiments.execution_viability.cli import exp_app

runner = CliRunner()


def test_the_exp_group_is_registered_on_the_root_app():
    names = [g.name for g in app.registered_groups]
    assert names.count("exp") == 1
    assert "variants" in names and "migrate" in names        # the existing groups are untouched


def test_the_group_lists_its_commands_without_a_database():
    result = runner.invoke(exp_app, ["--help"])
    assert result.exit_code == 0
    assert "isolation-check" in result.stdout


def test_policy_compare_help_points_at_the_stateful_runner():
    result = runner.invoke(app, ["policy-compare", "--help"])
    assert result.exit_code == 0
    # Typer renders the help at the terminal width, so the pointer is asserted against the
    # whitespace-normalised text rather than against rich's line breaks.
    rendered = " ".join(result.stdout.split())
    assert "admission" in rendered and "harness exp run" in rendered


def test_the_report_command_is_registered_and_takes_a_run_id():
    # Renamed in fix round 1 (Minor 7): this case asserts registration and the option, and the
    # refusal it used to promise is exercised against the command body in
    # `tests/test_exp_report.py::test_the_report_command_refuses_a_run_that_was_never_frozen`.
    result = runner.invoke(exp_app, ["--help"])
    assert "report" in result.stdout
    help_text = runner.invoke(exp_app, ["report", "--help"])
    assert help_text.exit_code == 0
    rendered = " ".join(help_text.stdout.split())
    assert "--run-id" in rendered


WINDOW_START = datetime(2026, 9, 16, 11, 55, tzinfo=timezone.utc)
FAIR_TS = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)     # 07:00 CT, outside any window


def test_run_refuses_an_arm_that_is_not_one_of_the_experiments():
    # §1.6 (fix round 1, Important 3): an unknown arm used to be accepted and stepped under
    # the baseline, so a run labelled with it would have written `exp_order` rows nobody could
    # attribute. The refusal is argument validation: it happens before any database work.
    result = runner.invoke(exp_app, ["run", "--run-id", "0198e2b0-0000-7000-8000-000000000001",
                                     "--arm", "Z", "--since", "2026-09-16T11:00:00+0000",
                                     "--until", "2026-09-16T13:00:00+0000",
                                     "--variants", "v1"])
    assert result.exit_code == 2                    # click's usage error, not a traceback
    # The message itself, from the callback: the runner sends a usage error to stderr, which
    # this click version keeps out of `result.stdout`.
    with pytest.raises(typer.BadParameter) as raised:
        cli.run_cmd(run_id="0198e2b0-0000-7000-8000-000000000001", arm="Z",
                    since=datetime(2026, 9, 16, 11, tzinfo=timezone.utc),
                    until=datetime(2026, 9, 16, 13, tzinfo=timezone.utc), variants="v1",
                    chunk_hours=1.0)
    assert "--arm" in str(raised.value) and "A, B" in str(raised.value)


def _seed_order(session, *, sport="nfl", variant_id="v1"):
    session.add(Order(intent_id=uuid.uuid4(), variant_id=variant_id, venue="kalshi",
                      client_order_id=f"cli-{uuid.uuid4().hex[:8]}", ticker="KXNFLGAME-X",
                      venue_market_id=1, side="yes", prob=Decimal("0.4800"),
                      contracts=Decimal("10"), status="open", placed_at=FAIR_TS, sport=sport))
    session.flush()


def test_arm_b_is_built_with_a_bound_allowance_and_arm_a_with_none(db_session, env_settings):
    # §1.6(a)/M2: the command binds B's allowance before the runner is used, so a run labelled
    # B can never decide under A's rule. A's runner carries `None` and its branch stays dead.
    _seed_order(db_session)
    kwargs = dict(variant_ids=["v1"], since=WINDOW_START, until=FAIR_TS + timedelta(hours=1),
                  window_start=WINDOW_START)
    allowance = cli._cadence_allowance(db_session, arms.ARMS["B"], env_settings, **kwargs)
    assert allowance is not None
    assert cli._cadence_allowance(db_session, arms.ARMS["A"], env_settings, **kwargs) is None

    exec_settings = ExecSettings.from_settings(env_settings)
    b = adapter.ArmRunner(run_id="0198e2b0-0000-7000-8000-000000000001", arm_id="B",
                          policy=arms.ARMS["B"].policy, variant_cfg={},
                          exec_settings=exec_settings, cadence_allowance=allowance)
    a = adapter.ArmRunner(run_id="0198e2b0-0000-7000-8000-000000000001", arm_id="A",
                          policy=arms.ARMS["A"].policy, variant_cfg={},
                          exec_settings=exec_settings, cadence_allowance=None)
    assert b.policy.cadence_allowance is allowance
    assert b.policy.cadence_allowance is not arms._unbound      # the placeholder was replaced
    assert a.policy.cadence_allowance is None
    # And the bound callable answers against the real reconstruction: an unanchored overnight
    # row (07:00 CT, no kickoff in the tape) takes I4's closed form.
    assert allowance(SimpleNamespace(fair_ts=FAIR_TS)) == arms.OVERNIGHT_UNANCHORED_S


def test_a_window_that_reconstructs_no_sport_is_refused_rather_than_guessed(db_session,
                                                                            env_settings):
    with pytest.raises(typer.BadParameter):
        cli._cadence_allowance(db_session, arms.ARMS["B"], env_settings, variant_ids=["nope"],
                               since=WINDOW_START, until=FAIR_TS + timedelta(hours=1),
                               window_start=WINDOW_START)


# --- T7: `exp observe` (fix round 1, Important 8) ----------------------------------------------


def test_observe_is_registered_and_documents_its_two_options():
    assert "observe" in runner.invoke(exp_app, ["--help"]).stdout
    result = runner.invoke(exp_app, ["observe", "--help"])
    assert result.exit_code == 0
    rendered = " ".join(result.stdout.split())
    assert "--run-id" in rendered and "--once" in rendered


def test_observe_refuses_a_run_that_was_never_frozen(db_session, env_settings, monkeypatch):
    """§1.6(h): arm C is reported unavailable with its reason; no tier is bought, no cap raised.

    `_step_zero` is replaced with its pass answer so this case reaches step 4's coverage check:
    the test role's privilege read-back is a different refusal, covered by its own message.
    """
    from contextlib import contextmanager

    @contextmanager
    def reader(_s):
        yield db_session

    monkeypatch.setattr(cli.source, "reader", reader)
    monkeypatch.setattr(cli, "get_settings", lambda: env_settings)
    monkeypatch.setattr(cli, "_step_zero", lambda session: None)
    result = runner.invoke(exp_app, ["observe", "--run-id",
                                     "0198e2b0-0000-7000-8000-00000000dead"])
    lines = result.stdout.splitlines()
    assert result.exit_code == 1
    # §0.6: the label first, before any other line the command prints.
    assert lines[0].strip() == cli.EXP_LABEL
    unavailable = [ln for ln in lines if ln.startswith("arm C unavailable:")]
    assert len(unavailable) == 1 and "no frozen exp_run row" in unavailable[0]
    assert any("no tier is bought and no cap is raised" in ln for ln in lines)
    # The activation checklist and the live gate values are printed, and no secret is.
    assert any("activation checklist" in ln for ln in lines)
    assert any("research_worker_enabled=" in ln and "anthropic_key_present=" in ln
               for ln in lines)
    assert "test-key" not in result.stdout


# --- G2 (1.9(a), ruling C2): `exp run` records the run's outcomes ------------------------------

OUT_RUN = "0198e2b0-0000-7000-8000-00000000c0de"
OUT_HASH = "c" * 64
FILLED = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)
UNTIL = datetime(2026, 9, 16, 14, 0, tzinfo=timezone.utc)


class _KeptOpenWriter(storage.ExperimentWriter):
    """T1's writer on the test's own session.

    `ExperimentWriter.open` connects as 1.1(b)'s least-privileged role, which no test holds, so
    the session is injected here exactly as `report_cmd`'s cases inject the reader's. `close()`
    is the one method that may not run: it rolls that same session back.
    """

    def close(self) -> None:
        pass


def _seed_filled_order(session, *, arm="A"):
    """One frozen run, one filled order of its arm, and the tape the markout is read from."""
    from harness.db.models import ExpFill, ExpOrder, ExpRun, VenueMarket, VenueQuote

    session.add(ExpRun(run_id=OUT_RUN, created_at=FILLED, manifest_hash=OUT_HASH, manifest={},
                       code_sha="a" * 40, clock_mode="retained_action_instants",
                       status="frozen"))
    market = VenueMarket(venue="kalshi", ticker="KXNFLGAME-OUT", event_ticker="EV",
                         series_ticker="KXNFLGAME", market_type="moneyline",
                         match_status="matched", first_seen_raw_id=1, last_seen_at=FILLED,
                         match_confidence=Decimal("1.00"), match_reason="seed")
    session.add(market)
    session.flush()
    order = ExpOrder(run_id=OUT_RUN, arm_id=arm, arm_order_id=-1, variant_id="v1",
                     venue_market_id=market.id, ticker="KXNFLGAME-OUT", side="yes",
                     prob=Decimal("0.4800"), contracts=Decimal("10"),
                     filled_contracts=Decimal("10"), placed_at=FILLED - timedelta(minutes=5),
                     status="filled")
    session.add(order)
    session.flush()
    session.add(ExpFill(run_id=OUT_RUN, arm_id=arm, exp_order_id=order.id, filled_at=FILLED,
                        contracts=Decimal("10"), prob=Decimal("0.4800"), fill_method="queue"))
    # 0.4800 at the fill and 0.5100 thirty minutes later: a +0.03 markout in YES space. The
    # market keeps no close, so the `close` horizon is censored rather than missing.
    for at, mid in ((FILLED, Decimal("0.4800")),
                    (FILLED + timedelta(seconds=1800), Decimal("0.5100"))):
        session.add(VenueQuote(raw_id=int(at.timestamp()), run_id=int(at.timestamp()),
                               venue_market_id=market.id, yes_bid=mid - Decimal("0.0100"),
                               yes_ask=mid + Decimal("0.0100"),
                               no_bid=Decimal("1") - (mid + Decimal("0.0100")),
                               no_ask=Decimal("1") - (mid - Decimal("0.0100")), fetched_at=at))
    session.flush()
    session.commit()
    return order.id


def _run_cmd_seams(monkeypatch, db_session, env_settings):
    """Everything `run_cmd` needs that is not this task's subject: the 1.1(b) reader and
    writer, the manifest rebuild and its resume check, and the tape walk itself. What runs for
    real is the command's own outcome recording and the line it prints."""
    from contextlib import contextmanager

    @contextmanager
    def reader(_s):
        yield db_session

    monkeypatch.setattr(cli.source, "reader", reader)
    monkeypatch.setattr(cli, "get_settings", lambda: env_settings)
    monkeypatch.setattr(cli.storage.ExperimentWriter, "open",
                        lambda s, *, run_id, engine=None: _KeptOpenWriter(
                            db_session, run_id=run_id, batch_rows=s.exp_batch_rows))
    monkeypatch.setattr(cli.storage, "rebuild_manifest",
                        lambda *a, **k: SimpleNamespace(freeze=lambda: OUT_HASH,
                                                        warmup_start=None))
    monkeypatch.setattr(cli, "_check_resume", lambda *a, **k: None)
    monkeypatch.setattr(cli.storage, "resume", lambda *a, **k: None)
    monkeypatch.setattr(cli.adapter, "run_window",
                        lambda *a, **k: SimpleNamespace(chunks=(), stepped=0, fills=0,
                                                        stopped=None))


def _run(run_id=OUT_RUN, arm="A"):
    cli.run_cmd(run_id=run_id, arm=arm, since=FILLED - timedelta(hours=1), until=UNTIL,
                variants="v1", chunk_hours=1.0)


def _outcome_rows(session):
    from sqlalchemy import text

    return session.execute(text(
        "select exp_order_id, horizon, value, censored, missing_reason, observed_at "
        "from exp_outcome where run_id = :r and arm_id = 'A' order by horizon"),
        {"r": OUT_RUN}).all()


def test_run_records_one_outcome_row_per_order_and_horizon(db_session, env_settings,
                                                            monkeypatch, capsys):
    """(a) 1.9(a)/C2: nothing called `record_outcomes` on a real run, so `exp_outcome` was
    empty and every sensitivity was absent. The horizons are the common schedule's, the
    observation instant is the window's end, and the values are the tape's."""
    order_id = _seed_filled_order(db_session)
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    _run()
    rows = _outcome_rows(db_session)
    assert [row.horizon for row in rows] == ["1800", "close", "t0"]
    assert {row.exp_order_id for row in rows} == {order_id}
    matured = [row for row in rows if row.horizon == "1800"][0]
    assert matured.value == Decimal("0.030000")        # 0.5100 - 0.4800, hand-computed
    assert matured.censored is False and matured.missing_reason is None
    # `now=until`, never the wall clock: a horizon past the window's end is censored (1.9a).
    assert [row.observed_at for row in rows] == [UNTIL] * 3
    assert [row.horizon for row in rows if row.censored] == ["close"]


def test_a_second_invocation_of_the_same_run_writes_no_duplicate_outcomes(db_session,
                                                                          env_settings,
                                                                          monkeypatch):
    """(b) `exp run` resumes; a `(exp_order_id, horizon)` this run has already recorded is
    never written twice."""
    _seed_filled_order(db_session)
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    _run()
    first = [(row.exp_order_id, row.horizon, row.value) for row in _outcome_rows(db_session)]
    _run()
    assert [(row.exp_order_id, row.horizon, row.value)
            for row in _outcome_rows(db_session)] == first
    assert len(first) == 3


def test_the_summary_line_names_the_counts(db_session, env_settings, monkeypatch, capsys):
    """(c) One line after the existing summary, and 0.6's label still before every number."""
    _seed_filled_order(db_session)
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    _run()
    out = capsys.readouterr().out
    assert out.splitlines()[0] == cli.EXP_LABEL
    line = [ln for ln in out.splitlines() if "outcomes recorded" in ln]
    assert line == ["  outcomes recorded          3 (censored 1, missing 0)"]
    # Nothing else in the summary moved -- except that the orders line is now the run's own
    # count (M-a): this fixture has exactly one `exp_order` row and no chunk at all, where the
    # line used to read `chunks[-1].orders` and print 0 for a run that placed an order.
    assert "  orders                     1" in out and "  fills                      0" in out


def test_the_report_of_that_run_no_longer_says_the_sensitivities_are_absent(db_session,
                                                                            env_settings,
                                                                            monkeypatch,
                                                                            capsys):
    """(d) The end of G2: `exp report` reads the rows `exp run` has now written, so the three
    markout columns carry numbers instead of one line saying nothing recorded them."""
    _seed_filled_order(db_session)
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    _run()
    capsys.readouterr()
    cli.report_cmd(run_id=OUT_RUN)
    out = capsys.readouterr().out
    assert "sensitivities: not supplied" not in out
    header = [ln for ln in out.splitlines() if "markout" in ln][0]
    assert header.index("order-weighted") < header.index("game-weighted")
    assert "market-side-weighted" in header
    assert "0.030000" in out                     # the two sensitivities, on the one filled order


# --- M-a: the summary's "orders" line is the run's, not the last chunk's -----------------------

def test_the_orders_line_counts_the_runs_orders_not_the_last_chunks(db_session, env_settings,
                                                                    monkeypatch, capsys):
    """M-a: closed orders leave `runner.orders` at every chunk boundary (D23/I3), so
    `chunks[-1].orders` is one chunk's write and not the window's.

    Two chunks here, each reporting one order, over a run whose arm has two `exp_order` rows:
    the line must say 2 -- the run's own order count, which is what a reader takes it for --
    and not the 1 the last chunk wrote.
    """
    first = _seed_filled_order(db_session)
    second = _second_order(db_session)
    assert first != second
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    chunks = (SimpleNamespace(orders=1, open_orders=1, instants=1, fills=0, stopped=None),
              SimpleNamespace(orders=1, open_orders=0, instants=1, fills=0, stopped=None))
    monkeypatch.setattr(cli.adapter, "run_window",
                        lambda *a, **k: SimpleNamespace(chunks=chunks, stepped=2, fills=0,
                                                        stopped=None))
    _run()
    out = capsys.readouterr().out
    assert db_session.execute(text(
        "select count(*) from exp_order where run_id = :r and arm_id = 'A'"),
        {"r": OUT_RUN}).scalar() == 2
    assert "  orders                     2" in out
    assert "chunks=2" in out
    # The open-order line is still the last chunk's, which is what "at the end" means.
    assert "  open at the end            0" in out


def _second_order(session, *, arm="A"):
    """A second order of the same run and arm, unfilled: it belongs to the run's count and has
    no outcome (`record_outcomes` skips an order that never filled)."""
    from harness.db.models import ExpOrder, VenueMarket

    market = VenueMarket(venue="kalshi", ticker="KXNFLGAME-OUT2", event_ticker="EV",
                         series_ticker="KXNFLGAME", market_type="moneyline",
                         match_status="matched", first_seen_raw_id=2, last_seen_at=FILLED,
                         match_confidence=Decimal("1.00"), match_reason="seed")
    session.add(market)
    session.flush()
    order = ExpOrder(run_id=OUT_RUN, arm_id=arm, arm_order_id=-2, variant_id="v1",
                     venue_market_id=market.id, ticker="KXNFLGAME-OUT2", side="yes",
                     prob=Decimal("0.4800"), contracts=Decimal("10"),
                     filled_contracts=Decimal("0"), placed_at=FILLED - timedelta(minutes=5),
                     status="open")
    session.add(order)
    session.flush()
    session.commit()
    return order.id


# --- M21: a censored horizon is re-observed once it matures -----------------------------------

def test_a_censored_horizon_is_re_recorded_when_it_matures(db_session, env_settings,
                                                           monkeypatch):
    """M21 (D44's residual): G2's skip-not-update left a censored row censored for ever.

    The first invocation ends one minute after the fill, so the 30-minute horizon has not
    arrived and is recorded `censored` with no value. The second ends two hours later, when the
    tape does carry the mid: the same row -- same `(run, arm, order, horizon)`, no duplicate --
    now carries the hand-computed +0.03 markout, `censored = false` and the later
    `observed_at`. The `close` horizon has no close stamp on this market, so it stays censored
    and is never touched.
    """
    order_id = _seed_filled_order(db_session)
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    early = FILLED + timedelta(minutes=1)
    cli.run_cmd(run_id=OUT_RUN, arm="A", since=FILLED - timedelta(hours=1), until=early,
                variants="v1", chunk_hours=1.0)
    rows = {row.horizon: row for row in _outcome_rows(db_session)}
    assert rows["1800"].censored is True and rows["1800"].value is None
    assert rows["1800"].observed_at == early
    # The window now reaches past the horizon: the same row is re-observed, not duplicated.
    _run()
    after = _outcome_rows(db_session)
    assert len(after) == 3                                   # still one row per horizon
    rows = {row.horizon: row for row in after}
    assert rows["1800"].censored is False
    assert rows["1800"].value == Decimal("0.030000")         # 0.5100 - 0.4800, hand-computed
    assert rows["1800"].observed_at == UNTIL                 # when it was actually observed
    assert rows["t0"].value == Decimal("0.000000")           # matured at the first invocation
    assert rows["close"].censored is True                    # no close stamp: never matures
    assert {row.exp_order_id for row in after} == {order_id}


def test_a_censored_horizon_that_has_not_matured_is_left_alone(db_session, env_settings,
                                                               monkeypatch):
    """The other half of M21: re-observation is not re-writing. A second invocation whose
    window still ends before the horizon leaves the censored row byte for byte as it was."""
    _seed_filled_order(db_session)
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    early = FILLED + timedelta(minutes=1)
    for _ in range(2):
        cli.run_cmd(run_id=OUT_RUN, arm="A", since=FILLED - timedelta(hours=1), until=early,
                    variants="v1", chunk_hours=1.0)
    rows = _outcome_rows(db_session)
    assert len(rows) == 3
    censored = {row.horizon: row for row in rows if row.censored}
    assert set(censored) == {"1800", "close"}
    assert [row.observed_at for row in rows] == [early] * 3


# --- M19: `exp report` re-hashes the capture tree ----------------------------------------------

def _captured_run(session, tmp_path, env_settings):
    """A frozen run whose manifest names one capture stream, with the file on disk."""
    from harness.db.models import ExpRun

    object.__setattr__(env_settings, "exp_dir", tmp_path / "exp")
    directory = tmp_path / "exp" / OUT_RUN
    directory.mkdir(parents=True)
    body = b'{"id": 1}\n'
    (directory / "orders.ndjson").write_bytes(body)
    import hashlib

    digest = hashlib.sha256(body).hexdigest()
    session.add(ExpRun(run_id=OUT_RUN, created_at=FILLED, manifest_hash=OUT_HASH,
                       manifest={"capture_hashes": {"orders": {
                           "sha256": digest,
                           "sql": "select id from orders where ...",
                           "params": {"start": "2026-09-16T12:00:00+00:00"}}}},
                       code_sha="a" * 40, clock_mode="retained_action_instants",
                       status="frozen"))
    session.commit()
    return directory


def test_report_re_hashes_the_capture_files_and_says_they_match(db_session, env_settings,
                                                                monkeypatch, capsys, tmp_path):
    """M19: §2's file-tree invariant, evaluated. An intact tree writes nothing and opens no
    writer -- `exp report` stays the read-only command it has always been."""
    _captured_run(db_session, tmp_path, env_settings)
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    monkeypatch.setattr(cli.storage.ExperimentWriter, "open",
                        lambda *a, **k: pytest.fail("an intact tree opens no writer"))
    cli.report_cmd(run_id=OUT_RUN)
    out = capsys.readouterr().out
    assert out.splitlines()[0] == cli.EXP_LABEL          # §0.6: the label first, still
    assert "capture files re-hashed: 1 streams, 0 mismatches" in out
    assert _limitations(db_session) == []


def test_report_writes_capture_hash_mismatch_for_a_tampered_file(db_session, env_settings,
                                                                 monkeypatch, capsys, tmp_path):
    """M19: one tampered file, one `capture_hash_mismatch` row through the writer.

    The row names the stream and the path, and the detail carries both digests, so a reader of
    `exp_limitation` can tell which stream stopped being the one the run was frozen over.
    """
    directory = _captured_run(db_session, tmp_path, env_settings)
    (directory / "orders.ndjson").write_bytes(b'{"id": 2}\n')      # one byte of the tape moved
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    cli.report_cmd(run_id=OUT_RUN)
    out = capsys.readouterr().out
    assert out.splitlines()[0] == cli.EXP_LABEL
    assert "capture files re-hashed: 1 streams, 1 mismatch" in out
    assert "  capture_hash_mismatch   orders" in out
    rows = _limitations(db_session)
    assert [row.kind for row in rows] == ["capture_hash_mismatch"]
    assert rows[0].scope["stream"] == "orders"
    assert rows[0].scope["path"].endswith(f"{OUT_RUN}/orders.ndjson")
    assert "the manifest froze sha256" in rows[0].detail
    # The invariant is a property of the run, not of how often it is reported: a second report
    # prints it again and writes no second row.
    cli.report_cmd(run_id=OUT_RUN)
    assert len(_limitations(db_session)) == 1


def test_report_reports_an_absent_capture_file_as_a_mismatch(db_session, env_settings,
                                                             monkeypatch, capsys, tmp_path):
    """A missing file is not a passing hash (M19)."""
    directory = _captured_run(db_session, tmp_path, env_settings)
    (directory / "orders.ndjson").unlink()
    _run_cmd_seams(monkeypatch, db_session, env_settings)
    cli.report_cmd(run_id=OUT_RUN)
    out = capsys.readouterr().out
    assert "capture files re-hashed: 1 streams, 1 mismatch" in out
    assert "it is absent" in _limitations(db_session)[0].detail


def _limitations(session):
    return session.execute(text(
        "select kind, scope, detail from exp_limitation where run_id = :r order by created_at"),
        {"r": OUT_RUN}).all()


# --- M3: `exp isolation-check`'s printed read-back ----------------------------------------------

def test_isolation_check_prints_one_read_back_line_per_privilege_it_names(db_session,
                                                                          env_settings,
                                                                          monkeypatch, capsys):
    """M3 (task-1 review Minor 3): the §3 row 2 read-back had no automated test at all.

    The role probed is this test database's own -- `harness_exp` does not exist here, and
    `has_table_privilege` raises on a role that does not -- so what is asserted is the shape of
    the read-back: `EXP_LABEL` as the **first** line (§0.6, ruling D50), then the eight
    production tables §3 row 2 names, each on its own line and each with the server's own
    answer, then the `exp_run` line.
    """
    from contextlib import contextmanager

    role = db_session.execute(text("select current_user")).scalar()
    monkeypatch.setattr(cli, "EXP_DB_ROLE", role)
    monkeypatch.setattr(cli, "get_settings", lambda: env_settings)

    @contextmanager
    def reader(_s):
        yield db_session

    monkeypatch.setattr(cli.source, "reader", reader)
    cli.isolation_check()
    lines = capsys.readouterr().out.splitlines()
    # §0.6 / D50: the label is the first line, before any number, as in the other eight commands.
    assert lines[0] == cli.EXP_LABEL
    assert lines[1].startswith(f"role={role} exp_tables=")
    names = [line.split()[0] for line in lines[2:]]
    assert names == ["orders", "fills", "intents", "signals", "ledger", "source_state",
                     "research_spend", "veto_decisions", "exp_run"]
    # The test role owns the schema, so the server answers True here; the point of the read-back
    # is that the answer comes from `has_table_privilege` rather than from the code's belief.
    assert all(line.endswith("insert=True") for line in lines[2:])
    assert lines[-1].startswith("  exp_run")

