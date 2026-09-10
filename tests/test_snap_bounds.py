"""Fix 31: every snapshot builder's SQL carries a bound, and the bound rides an index.

The 2026-09-10 incident is the reason this file exists. With the snapshot scheduler on, the
executor's `exec.loop_ms` went from a 5,275 ms average to 54,653 ms in eleven minutes, IO wait
went from 1-5 % to 24-35 %, and eight builder sections hit the 2 s statement timeout. The NAS has
7.7 GB of RAM with 1.0-1.4 GB available against a 34 GB database, so a builder read that walks
cold pages of a large table does not merely cost its own milliseconds: it evicts the executor's
working set from a page cache that cannot hold both. Every individual query in that deploy had
passed review, and several had been measured on a laptop at tens of milliseconds.

So the rule this file enforces is structural rather than a measurement: **a statement that names
a table which grows with the season must carry a time bound or a row cap.** A reviewer can check
that by reading; a test can check it on every commit, including the commit that adds the sixth
builder two months from now.

The two lists are the whole judgement. `BOUNDED` is every table whose row count grows with the
season, and a statement naming one must be bounded. `TINY` is every table whose row count is set
by the shape of the system rather than by how long it has been running -- one row per team, per
market, per registered variant, per ISO week, per surface -- and a statement naming one of those
needs nothing. A table in neither list fails the test by name, which is the point: the next
person to read a new table here has to say which kind it is.
"""

import ast
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from sqlalchemy.dialects import postgresql

from harness.dashboard import queries as queries_mod
from harness.dashboard import scheduler as scheduler_mod
from harness.dashboard import window as window_mod
from harness.dashboard.snapshots import floor, gate, pulse, study, ticket

#: The modules whose `text()` statements run on the snapshot engine, under its 2 s timeout.
MODULES = (pulse, floor, study, gate, ticket, scheduler_mod, window_mod)

#: Grows with the season. The first thirteen are the fix-31 brief's own list; `operator_events`
#: and `venue_requests` are appended because they are append-only logs with the same property,
#: and every read of them here is already bounded.
BOUNDED_TABLES = frozenset({
    "orderbook_events", "venue_trades", "fair_values", "intents", "orders", "fills", "signals",
    "runs", "job_runs", "metric_samples", "equity_snapshots", "order_watch_samples",
    "game_score_events", "operator_events", "venue_requests",
})

#: Bounded by the shape of the system, not by its age:
#:   games, teams          -- one row per game and per team of a season, thousands at most
#:   venue_markets         -- one row per tradable market
#:   strategy_variants     -- one row per registered variant, six today
#:   gate_reports          -- at most one evaluation a day, and `_HISTORY` caps it anyway
#:   parlay_*              -- one card a week, its legs, its ledger lines
#:   check_results         -- one row per check per sweep, read at one `ts`
#:   exec_heartbeat        -- a single row, `id = 1`
#:   kill_switch           -- a single row, `id = 1`
#:   venue_status          -- one row per env, two
#:   dashboard_snapshots   -- one row per surface, plus one per ISO week
#:   report_runs           -- one final run a week and one provisional every six hours
#:   report_cells          -- keyed by `report_run_id`, the leading column of its primary key
TINY_TABLES = frozenset({
    "games", "teams", "venue_markets", "strategy_variants", "gate_reports", "check_results",
    "exec_heartbeat", "kill_switch", "venue_status", "dashboard_snapshots", "report_runs",
    "report_cells",
})

#: Not a table: SQL keywords that follow `from`/`join` in these statements.
_NOT_A_TABLE = frozenset({"lateral"})
#: The parlay tables are tiny as a family -- one card a week and everything hanging off it -- so
#: they are exempted by prefix rather than by listing five names that will become seven.
_TINY_PREFIXES = ("parlay_",)

_TABLE = re.compile(r"\b(?:from|join)\s+([a-z_][a-z0-9_]*)", re.I)
_LIMIT = re.compile(r"\blimit\b", re.I)
_INTERVAL = re.compile(r"\binterval\b", re.I)
#: `%(name)s` is what a bind parameter renders as once a SQLAlchemy Core statement is compiled
#: for PostgreSQL, which is how `queries.py`'s statements reach this file; `:name` is what a
#: `text()` literal carries. Both are the same thing -- a bound the builder computed from its
#: own `now` -- so the pattern below accepts either.
#:
#: A comparison of a time column against a bound -- a bind parameter the builder computed from
#: its own `now`, or a literal `now()`. This is what "a `now() - interval` predicate on the BRIN
#: or leading-index column" looks like once the instant is injected rather than read from the
#: transaction clock, which every builder does so a test can pin the window.
_TIME_BOUND = re.compile(
    r"\b(ts|created_at|filled_at|placed_at|started_at|generated_at|fetched_at|built_at|"
    r"evaluated_at|last_loop_at|kickoff_utc|updated_at|signal_created_at)\s*"
    r"(>=|>|<=|<|between)\s*(:\w+|%\(\w+\)s|now\s*\(\s*\))", re.I)


def _is_text_call(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
            and node.func.id == "text" and bool(node.args)
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str))


def _statements(module) -> list[tuple[str, str]]:
    """Every `text("...")` literal in `module`, as `(assigned name, sql)`.

    Read out of the source rather than off the module's attributes so a statement built inside a
    function, or one added without a module-level name, is caught the same way.
    """
    tree = ast.parse(Path(module.__file__).read_text())
    names = {id(node.value): target.id
             for node in ast.walk(tree) if isinstance(node, ast.Assign)
             for target in node.targets if isinstance(target, ast.Name)}
    return [(names.get(id(node), f"<line {node.lineno}>"), node.args[0].value)
            for node in ast.walk(tree) if _is_text_call(node)]


#: `-- ...` to end of line. Every statement here carries prose saying what its bound is and
#: which index serves it, and prose contains words like "from" and "limit": a scanner that read
#: the comments would find tables that are not read and bounds that are not there.
_SQL_COMMENT = re.compile(r"--[^\n]*")


def _code(sql: str) -> str:
    return _SQL_COMMENT.sub(" ", sql)


def _tables(sql: str) -> set[str]:
    found = {name.lower() for name in _TABLE.findall(_code(sql))} - _NOT_A_TABLE
    return {name for name in found if not name.startswith(_TINY_PREFIXES)}


def _bounded(sql: str) -> bool:
    body = _code(sql)
    return bool(_LIMIT.search(body) or _INTERVAL.search(body) or _TIME_BOUND.search(body))


class _RecordingSession:
    """Enough of a `Session` for `queries.py` to build and issue its statements against.

    `harness/dashboard/queries.py` is the one module in this file's remit that does not use
    `text()`: it builds its reads through SQLAlchemy Core, so the source scanner above cannot
    see them, and it is also the module holding the read the fix-31 brief singled out as
    unindexed. Rather than exempt it, its statements are captured here as they are issued and
    compiled to the SQL PostgreSQL would receive, then put through the same two checks.
    """

    def __init__(self) -> None:
        self.statements = []

    def execute(self, statement, *args, **kwargs):
        self.statements.append(statement)
        return self

    def scalars(self):
        return self

    def all(self):
        return []


def _orm_statements() -> list[tuple[str, str]]:
    session = _RecordingSession()
    now = datetime(2026, 9, 12, 18, 0, tzinfo=timezone.utc)
    # Both shapes of the funnel read: the capped one the Floor builder issues, and the uncapped
    # one the legacy page still issues. They are different statements and only one has a limit.
    queries_mod.recent_run_notes(session, now - queries_mod.WINDOW_6H, limit=2000)
    queries_mod.recent_run_notes(session, now - queries_mod.WINDOW_24H)
    queries_mod.signals_by_variant_from_notes(session, [])
    return [(f"<compiled {i}>", str(stmt.compile(dialect=postgresql.dialect())))
            for i, stmt in enumerate(session.statements)]


ALL_STATEMENTS = [(module.__name__.rsplit(".", 1)[-1], name, sql)
                  for module in MODULES for name, sql in _statements(module)]
ALL_STATEMENTS += [("queries", name, sql) for name, sql in _orm_statements()]


def test_the_scan_finds_every_builders_statements():
    """A guard on the guard: an extraction that silently found nothing would pass every
    assertion below while checking nothing at all."""
    by_module = {module for module, _, _ in ALL_STATEMENTS}
    assert by_module == {"pulse", "floor", "study", "gate", "ticket", "scheduler", "window",
                         "queries"}
    assert len(ALL_STATEMENTS) > 30
    # Three statements, not two: the capped funnel read and the uncapped legacy one are
    # different SQL, and the whole point of the round 1 change is that only one carries a limit.
    assert len([s for m, _, s in ALL_STATEMENTS if m == "queries"]) == 3


@pytest.mark.parametrize("module,name,sql", ALL_STATEMENTS,
                         ids=[f"{m}.{n}" for m, n, _ in ALL_STATEMENTS])
def test_every_statement_names_only_tables_this_file_has_ruled_on(module, name, sql):
    unknown = _tables(sql) - BOUNDED_TABLES - TINY_TABLES
    assert not unknown, (
        f"{module}.{name} reads {sorted(unknown)}, which is in neither list. Add it to "
        "BOUNDED_TABLES if its row count grows with the season, or to TINY_TABLES with the "
        "reason it does not.")


@pytest.mark.parametrize("module,name,sql", ALL_STATEMENTS,
                         ids=[f"{m}.{n}" for m, n, _ in ALL_STATEMENTS])
def test_every_statement_on_a_growing_table_carries_a_bound(module, name, sql):
    growing = _tables(sql) & BOUNDED_TABLES
    if not growing:
        return
    assert _bounded(sql), (
        f"{module}.{name} reads {sorted(growing)} with no `limit`, no `interval` and no "
        "comparison of a time column against a bound. On the NAS that is a scan of cold pages "
        "and it costs the executor its page cache, not just itself.")


def test_the_capped_run_notes_read_sends_its_limit_without_the_window_predicate():
    """Round 1. `runs` has no index on `started_at`, so a `where started_at >= :cutoff` beside a
    `limit` does not stop the read: PostgreSQL walks the primary key backwards discarding rows
    that fail the predicate until it has `limit` matches, and a limit above the window's true
    row count is never reached. The capped form therefore carries the limit alone and applies
    the window in Python; the uncapped legacy form carries the predicate alone."""
    capped, uncapped = [sql for module, _, sql in ALL_STATEMENTS if module == "queries"][:2]

    assert _LIMIT.search(_code(capped))
    assert "started_at >=" not in _code(capped).lower().replace("runs.", "")
    assert "runs.started_at" in capped, "the caller needs the column to apply the window itself"

    assert not _LIMIT.search(_code(uncapped))
    assert _TIME_BOUND.search(_code(uncapped))


def test_the_positions_view_is_not_read_by_any_builder():
    """Fix 31 reverses the phase 4.5 ruling that left `_EXPOSURE` on the `positions` view. The
    view has no time bound of any kind -- `o.status <> 'settled'` is a status predicate, not a
    bound -- so on a database where settlement has ever stalled it walks the season."""
    for module in MODULES + (queries_mod,):
        body = Path(module.__file__).read_text().lower()
        assert "from positions" not in body, module.__name__


def test_no_builder_sql_names_a_forbidden_table():
    """The five tape tables stay forbidden to every builder, not only to Floor (spec §0.3)."""
    for module in MODULES + (queries_mod,):
        body = Path(module.__file__).read_text().lower()
        for table in ("orderbook_events", "venue_trades", "raw_responses", "odds_snapshots",
                      "venue_quotes"):
            assert f"from {table}" not in body and f"join {table}" not in body, \
                f"{module.__name__} reads {table}"
