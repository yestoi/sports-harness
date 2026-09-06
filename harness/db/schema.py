from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from harness.db.models import Base


def week_bounds(now: datetime) -> tuple[datetime, datetime]:
    if now.tzinfo is None:
        raise ValueError("week_bounds requires a tz-aware datetime")
    now = now.astimezone(timezone.utc)
    start = (now - timedelta(days=now.weekday())).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=7)


def _partition_name(start: datetime) -> str:
    iso = start.isocalendar()
    return f"raw_responses_y{iso.year}w{iso.week:02d}"


def ensure_partitions(session: Session, now: datetime) -> list[str]:
    created: list[str] = []
    start, _ = week_bounds(now)
    for i in range(2):
        s = start + timedelta(days=7 * i)
        e = s + timedelta(days=7)
        name = _partition_name(s)
        exists = session.execute(text("select 1 from pg_class where relname = :n"), {"n": name}).first()
        if exists:
            continue
        session.execute(text(
            f"create table {name} partition of raw_responses "
            f"for values from ('{s.isoformat()}') to ('{e.isoformat()}')"
        ))
        created.append(name)
    session.commit()
    return created


def create_schema(engine: Engine) -> None:
    Base.metadata.create_all(engine)
    with engine.begin() as conn:
        conn.execute(text("create index if not exists ix_raw_source_fetched on raw_responses (source, fetched_at)"))
        conn.execute(text("create index if not exists ix_raw_fetched_brin on raw_responses using brin (fetched_at)"))
        conn.execute(text("create index if not exists ix_raw_run on raw_responses (run_id)"))
        conn.execute(text(
            "create unique index if not exists uq_odds_snapshot_row on odds_snapshots "
            "(raw_id, book, market_type, coalesce(outcome_team_id, -1), coalesce(outcome_side, ''), coalesce(point, 0))"))


def drop_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("drop table if exists raw_responses cascade"))
        conn.execute(text(
            "drop table if exists runs, trade_watermarks, source_state, teams, team_aliases, games, "
            "odds_snapshots, venue_markets, venue_quotes, orderbook_snapshots, venue_trades, "
            "orderbook_events, normalize_state cascade"))
