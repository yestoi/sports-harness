# Phase 0 Recorder Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** An append-only recorder, running in Docker on the NAS before the first NFL Week 1 kickoff (Wed 2026-09-09 19:20 CT), that stores every raw response from The Odds API, Kalshi's public API, and ESPN so all later phases can be rebuilt from it.

**Architecture:** One Python package `harness/`. A 30-second APScheduler heartbeat calls `maybe_tick()`, which uses a pure cadence planner to decide which sources are due, fetches them through a shared HTTP wrapper, and stores each response verbatim as JSONB in a weekly-partitioned `raw_responses` table with a `runs` row per tick. Every fetch is `fetch_*() -> FetchResult` (I/O) and every interpretation is a pure `parse_*()` function. A separate `harness serve` process exposes `/healthz`.

**Tech Stack:** Python 3.12, httpx, SQLAlchemy 2 (sync), psycopg 3, Pydantic Settings, APScheduler 3, FastAPI + uvicorn, pytest, Postgres 16, Docker Compose.

**Spec:** `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2), sections 4, 4.1, 4.2, 5.1, 5.2 (public Kalshi), 5.4, 5.5 (`raw_responses`, `runs`), 13, 14, 15 phase 0. Review context: `docs/superpowers/specs/2026-09-06-adversarial-review.md`.

## Global Constraints

- Python 3.12; sync code only (spec §2, §4). No asyncio.
- All internal times are tz-aware UTC; `America/Chicago` appears only in scheduling windows and rendering (spec §4.2).
- Every HTTP call has a 10 s timeout; a tick has a 100 s budget after which remaining ladder/trade fetches are skipped and counted (spec §4.2).
- `raw_responses` is append-only; nothing in later phases may read a source phase 0 does not store (spec §5.5, §15).
- Secrets come from 0600 file mounts, never the DB or logs; log redaction drops `Authorization`, signature headers, and `apiKey` query values (spec §14).
- The Odds API: `bookmakers=` parameter, never `regions=`; featured markets `h2h,spreads,totals`; alternates `alternate_spreads,alternate_totals` via the per-event endpoint; credit headers logged on `runs` (spec §5.1).
- Kalshi public endpoints need no key. Football series: `KXNFLGAME, KXNFLSPREAD, KXNFLTOTAL, KXNCAAFGAME, KXNCAAFSPREAD, KXNCAAFTOTAL` (verified 2026-09-06).
- Cadence (spec §4.1): per sport 2 min from 3 h before that sport's first kickoff of the day until its last kickoff; 5 min all day Sat/Sun; 15 min otherwise; none 01:00–08:00 CT. Burst 20 s from T−100 to T−60 min for NFL games. Full Kalshi ladders only for markets whose event date is today (CT) with a quote inside 20–80c, capped at 400 per tick.
- APScheduler jobs: `max_instances=1`, `coalesce=True`, `misfire_grace_time=60` (spec §4.2).
- Commit after every task with the trailer:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Wp1gV1T1tYrgYDP3EJALci
  ```

## Verified API facts (2026-09-06, do not re-derive)

- Kalshi base `https://api.elections.kalshi.com/trade-api/v2`. `GET /markets?series_ticker=X&status=open&limit=1000&cursor=...` returns `{"cursor": str|"" , "markets": [...]}`; market fields include `ticker, event_ticker, title, yes_sub_title, strike_type, floor_strike, yes_bid_dollars, yes_ask_dollars, no_bid_dollars, no_ask_dollars, yes_bid_size_fp, yes_ask_size_fp, volume_fp, volume_24h_fp, open_interest_fp, close_time, updated_time`. Prices are strings like `"0.2200"`. `KXNCAAFSPREAD` exceeds one page (cursor non-empty).
- `GET /markets/{ticker}/orderbook?depth=20` returns `{"orderbook_fp": {"yes_dollars": [["0.2000","501.98"], ...], "no_dollars": [...]}}` (bids only, both sides).
- `GET /markets/trades?ticker=T&limit=1000&min_ts=<unix seconds>` returns `{"cursor": ..., "trades": [{"trade_id", "ticker", "created_time", "count_fp", "yes_price_dollars", "no_price_dollars", "taker_side", "is_block_trade"}]}` newest first; `min_ts` is honored. Global (unfiltered) trades run ~1000 prints per 4 seconds exchange-wide, so never poll unfiltered.
- Event ticker embeds the date: `KXNFLGAME-26SEP21NYGLAR` → 2026-09-21.
- 30 rapid unauthenticated orderbook calls returned 200; still sleep 50 ms between Kalshi calls.
- ESPN: `https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard` and `.../football/college-football/scoreboard?groups=80&limit=400`. `events[].id`, `events[].date` (ISO, e.g. `2026-09-10T00:20Z`), `events[].name`, `events[].competitions[0].competitors[].team.abbreviation` and `.homeAway`, `events[].status.type.name`.
- The Odds API: `GET https://api.the-odds-api.com/v4/sports/{sport}/odds?apiKey&bookmakers&markets&oddsFormat=decimal&dateFormat=iso` → list of `{id, commence_time, home_team, away_team, bookmakers:[{key,last_update,markets:[{key,outcomes:[{name,price,point?}]}]}]}`. `GET /v4/sports/{sport}/events/{eventId}/odds?...&markets=alternate_spreads,alternate_totals`. Cost = markets × ceil(bookmakers/10). Headers `x-requests-remaining`, `x-requests-used`, `x-requests-last`. Sport keys `americanfootball_nfl`, `americanfootball_ncaaf`. Bookmakers: `pinnacle,betonlineag,lowvig,draftkings,fanduel,novig,kalshi`.

## File structure

```
pyproject.toml                     package metadata, deps, pytest config
.gitignore / .dockerignore
.env.example                       non-secret env; secrets are files under ./secrets/
Dockerfile
docker-compose.yml                 app-run, app-serve, postgres
secrets/.gitkeep                   odds_api_key mounted at /run/secrets/odds_api_key
harness/__init__.py
harness/config/settings.py         Settings (pydantic-settings): DATABASE_URL, secret file paths, cadence knobs
harness/logging_setup.py           JSON logging + RedactionFilter
harness/db/engine.py               make_engine(url), SessionLocal
harness/db/models.py               RawResponse, Run, TradeWatermark, SourceState
harness/db/schema.py               create_schema(engine), ensure_partitions(session, now)
harness/feeds/http.py              FetchResult, HttpClient.get(...) with timeout/retry policy
harness/feeds/odds_api.py          OddsApiClient.fetch_featured / fetch_event_alternates; parse_credit_headers
harness/feeds/espn.py              EspnClient.fetch_scoreboard; parse_kickoffs (pure)
harness/venues/kalshi/public.py    KalshiPublic.fetch_markets_all / fetch_orderbook / fetch_trades; parse_market_summaries, event_date_from_ticker (pure)
harness/recorder/store.py          store_raw, start_run, finish_run
harness/recorder/cadence.py        plan_tick (pure): what is due, intervals, ladder/trade selection
harness/recorder/tick.py           Recorder.maybe_tick(now): orchestrates fetch+store with budget
harness/scheduler.py               build_scheduler(recorder)
harness/health.py                  FastAPI app: GET /healthz
harness/cli.py                     harness init-db | tick-once | run | serve
tests/conftest.py                  fixtures: settings, db session (skips without DATABASE_URL_TEST), sample payloads
tests/fixtures/*.json              real-shaped sample responses (small)
tests/test_settings.py, test_logging.py, test_schema.py, test_http.py, test_odds_api.py,
tests/test_espn.py, test_kalshi_public.py, test_store.py, test_cadence.py, test_tick.py, test_health.py
```

---

### Task 1: Project scaffold, settings, and test bootstrap

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `.dockerignore`, `.env.example`, `secrets/.gitkeep`, `harness/__init__.py`, `harness/config/__init__.py`, `harness/config/settings.py`, `tests/__init__.py`, `tests/conftest.py`, `tests/test_settings.py`

**Interfaces:**
- Produces: `harness.config.settings.Settings` with fields `database_url: str`, `database_url_test: str | None`, `odds_api_key_file: Path`, `odds_api_bookmakers: str`, `kalshi_base_url: str`, `espn_base_url: str`, `tick_budget_s: int = 100`, `http_timeout_s: float = 10.0`, `ladder_cap_per_tick: int = 400`, `kalshi_sleep_s: float = 0.05`, `tz_local: str = "America/Chicago"`; method `odds_api_key() -> str` reading the secret file, stripped. `get_settings() -> Settings` cached.

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[build-system]
requires = ["setuptools>=69"]
build-backend = "setuptools.build_meta"

[project]
name = "harness"
version = "0.0.1"
requires-python = ">=3.12"
dependencies = [
  "httpx>=0.27",
  "sqlalchemy>=2.0",
  "psycopg[binary]>=3.1",
  "pydantic>=2.7",
  "pydantic-settings>=2.3",
  "apscheduler>=3.10,<4",
  "fastapi>=0.111",
  "uvicorn>=0.30",
  "python-json-logger>=2.0",
  "typer>=0.12",
]

[project.optional-dependencies]
dev = ["pytest>=8", "pytest-httpx>=0.30", "freezegun>=1.5", "respx>=0.21"]

[project.scripts]
harness = "harness.cli:app"

[tool.setuptools.packages.find]
include = ["harness*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"
```

- [ ] **Step 2: Create `.gitignore`, `.dockerignore`, `.env.example`, `secrets/.gitkeep`**

`.gitignore`:
```
__pycache__/
*.pyc
.venv/
.env
secrets/*
!secrets/.gitkeep
pgdata/
.pytest_cache/
*.egg-info/
```

`.dockerignore`:
```
.venv
.git
pgdata
secrets
tests
docs
```

`.env.example`:
```
DATABASE_URL=postgresql+psycopg://harness:harness@postgres:5432/harness
ODDS_API_KEY_FILE=/run/secrets/odds_api_key
ODDS_API_BOOKMAKERS=pinnacle,betonlineag,lowvig,draftkings,fanduel,novig,kalshi
TZ=UTC
```

`secrets/.gitkeep`: empty file.

- [ ] **Step 3: Write the failing settings test**

`tests/test_settings.py`:
```python
from pathlib import Path

from harness.config.settings import Settings


def test_settings_reads_key_from_file(tmp_path: Path, monkeypatch):
    key_file = tmp_path / "odds_api_key"
    key_file.write_text("abc123\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("ODDS_API_KEY_FILE", str(key_file))
    s = Settings()
    assert s.odds_api_key() == "abc123"
    assert s.tick_budget_s == 100
    assert s.odds_api_bookmakers.startswith("pinnacle")
    assert s.kalshi_base_url == "https://api.elections.kalshi.com/trade-api/v2"
```

- [ ] **Step 4: Run it to verify it fails**

Run: `python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]' && pytest tests/test_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: harness.config.settings`

- [ ] **Step 5: Implement settings**

`harness/__init__.py` and `harness/config/__init__.py`: empty.

`harness/config/settings.py`:
```python
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str
    database_url_test: str | None = None
    odds_api_key_file: Path = Path("/run/secrets/odds_api_key")
    odds_api_base_url: str = "https://api.the-odds-api.com/v4"
    odds_api_bookmakers: str = "pinnacle,betonlineag,lowvig,draftkings,fanduel,novig,kalshi"
    kalshi_base_url: str = "https://api.elections.kalshi.com/trade-api/v2"
    espn_base_url: str = "https://site.api.espn.com/apis/site/v2/sports/football"
    tick_budget_s: int = 100
    http_timeout_s: float = 10.0
    ladder_cap_per_tick: int = 400
    kalshi_sleep_s: float = 0.05
    heartbeat_s: int = 30
    tz_local: str = "America/Chicago"

    def odds_api_key(self) -> str:
        return self.odds_api_key_file.read_text().strip()


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

`tests/__init__.py`: empty. `tests/conftest.py`:
```python
import os

import pytest


@pytest.fixture
def env_settings(monkeypatch, tmp_path):
    key_file = tmp_path / "odds_api_key"
    key_file.write_text("test-key")
    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg://u:p@h:5432/db")
    monkeypatch.setenv("ODDS_API_KEY_FILE", str(key_file))
    from harness.config.settings import Settings

    return Settings()


@pytest.fixture
def db_session():
    url = os.environ.get("DATABASE_URL_TEST")
    if not url:
        pytest.skip("DATABASE_URL_TEST not set")
    from sqlalchemy.orm import sessionmaker

    from harness.db.engine import make_engine
    from harness.db.schema import create_schema, drop_schema

    engine = make_engine(url)
    drop_schema(engine)
    create_schema(engine)
    Session = sessionmaker(bind=engine)
    with Session() as s:
        yield s
        s.rollback()
    engine.dispose()
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `pytest tests/test_settings.py -v`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore .dockerignore .env.example secrets/.gitkeep harness tests
git commit -m "feat: project scaffold and settings"
```

---

### Task 2: JSON logging with secret redaction

**Files:**
- Create: `harness/logging_setup.py`, `tests/test_logging.py`

**Interfaces:**
- Produces: `configure_logging(level: str = "INFO") -> None`; `redact(text: str) -> str` (pure) that replaces the value of `apiKey=`/`api_key=` query params, `Authorization` header values, and any header whose name contains `SIGNATURE` or `ACCESS-KEY` with `[REDACTED]`.

- [ ] **Step 1: Write the failing test**

`tests/test_logging.py`:
```python
import json
import logging

from harness.logging_setup import configure_logging, redact


def test_redact_api_key_and_auth():
    s = "GET https://x/v4/odds?apiKey=SECRET123&markets=h2h Authorization: Bearer tok KALSHI-ACCESS-SIGNATURE: sig=="
    out = redact(s)
    assert "SECRET123" not in out
    assert "tok" not in out.split("Authorization: ")[1][:10]
    assert "sig==" not in out
    assert "markets=h2h" in out


def test_json_logging_redacts(capsys):
    configure_logging("INFO")
    logging.getLogger("t").info("calling url apiKey=ABC")
    line = capsys.readouterr().err.strip().splitlines()[-1]
    rec = json.loads(line)
    assert rec["message"] == "calling url apiKey=[REDACTED]"
    assert "levelname" in rec
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_logging.py -v`
Expected: FAIL with `ModuleNotFoundError: harness.logging_setup`

- [ ] **Step 3: Implement**

`harness/logging_setup.py`:
```python
import logging
import re
import sys

from pythonjsonlogger import jsonlogger

_PATTERNS = [
    (re.compile(r"(apiKey|api_key)=([^&\s]+)", re.I), r"\1=[REDACTED]"),
    (re.compile(r"(Authorization:\s*)(\S+\s+)?(\S+)", re.I), r"\1[REDACTED]"),
    (re.compile(r"([A-Z\-]*(?:SIGNATURE|ACCESS-KEY)[A-Z\-]*:\s*)(\S+)", re.I), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    for pat, rep in _PATTERNS:
        text = pat.sub(rep, text)
    return text


class RedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = redact(str(record.getMessage()))
        record.args = ()
        return True


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.handlers.clear()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(jsonlogger.JsonFormatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)
    root.setLevel(level)
    logging.getLogger("httpx").setLevel("WARNING")
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `pytest tests/test_logging.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/logging_setup.py tests/test_logging.py
git commit -m "feat: json logging with secret redaction"
```

---

### Task 3: Database models, weekly-partitioned raw table, and schema creation

**Files:**
- Create: `harness/db/__init__.py`, `harness/db/engine.py`, `harness/db/models.py`, `harness/db/schema.py`, `tests/test_schema.py`

**Interfaces:**
- Produces:
  - `make_engine(url: str) -> Engine`
  - ORM models: `Run(id, started_at, finished_at, status, error, n_requests, credits_used, odds_remaining, budget_exhausted, notes)`, `RawResponse(id, run_id, source, endpoint, params, fetched_at, http_status, body)`, `TradeWatermark(ticker PK, last_ts, last_volume_fp)`, `SourceState(key PK, last_fetched_at)`.
  - `create_schema(engine)`, `drop_schema(engine)`, `ensure_partitions(session, now: datetime) -> list[str]` creating `raw_responses_yYYYYwWW` partitions for the current and next ISO week if missing.
  - `week_bounds(now) -> (start_utc, end_utc)` pure.

- [ ] **Step 1: Write the failing tests**

`tests/test_schema.py`:
```python
from datetime import datetime, timezone

from sqlalchemy import text

from harness.db.schema import ensure_partitions, week_bounds


def test_week_bounds_monday_to_monday_utc():
    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)  # Wednesday
    start, end = week_bounds(now)
    assert start == datetime(2026, 9, 7, tzinfo=timezone.utc)
    assert end == datetime(2026, 9, 14, tzinfo=timezone.utc)


def test_ensure_partitions_creates_two_weeks(db_session):
    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
    created = ensure_partitions(db_session, now)
    assert created == ["raw_responses_y2026w37", "raw_responses_y2026w38"]
    again = ensure_partitions(db_session, now)
    assert again == []
    names = db_session.execute(
        text("select inhrelid::regclass::text from pg_inherits where inhparent = 'raw_responses'::regclass")
    ).scalars().all()
    assert set(names) >= {"raw_responses_y2026w37", "raw_responses_y2026w38"}


def test_raw_insert_roundtrip(db_session):
    from harness.db.models import RawResponse, Run

    now = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
    ensure_partitions(db_session, now)
    run = Run(started_at=now, status="running")
    db_session.add(run)
    db_session.flush()
    row = RawResponse(run_id=run.id, source="kalshi", endpoint="/markets", params={"series_ticker": "KXNFLGAME"},
                      fetched_at=now, http_status=200, body={"markets": []})
    db_session.add(row)
    db_session.flush()
    got = db_session.get(RawResponse, (row.id, row.fetched_at))
    assert got.body == {"markets": []}
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_schema.py -v`
Expected: `test_week_bounds...` FAIL with import error; DB tests SKIP unless `DATABASE_URL_TEST` set. To run DB tests locally: `docker run -d --name harness-pg-test -e POSTGRES_USER=harness -e POSTGRES_PASSWORD=harness -e POSTGRES_DB=harness_test -p 5433:5432 postgres:16` then `export DATABASE_URL_TEST=postgresql+psycopg://harness:harness@localhost:5433/harness_test`.

- [ ] **Step 3: Implement engine and models**

`harness/db/__init__.py`: empty.

`harness/db/engine.py`:
```python
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import sessionmaker


def make_engine(url: str) -> Engine:
    return create_engine(url, pool_pre_ping=True, future=True)


def make_session_factory(engine: Engine) -> sessionmaker:
    return sessionmaker(bind=engine, expire_on_commit=False)
```

`harness/db/models.py`:
```python
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Run(Base):
    __tablename__ = "runs"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # running|ok|error|skipped
    error: Mapped[str | None] = mapped_column(Text)
    n_requests: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    credits_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    odds_remaining: Mapped[int | None] = mapped_column(Integer)
    budget_exhausted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    notes: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)


class RawResponse(Base):
    """Partitioned by range on fetched_at (weekly). Composite PK required by Postgres partitioning."""
    __tablename__ = "raw_responses"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), primary_key=True)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(32), nullable=False)  # odds_api|kalshi|espn
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    params: Mapped[dict] = mapped_column(JSONB, default=dict, nullable=False)
    http_status: Mapped[int] = mapped_column(Integer, nullable=False)
    body: Mapped[dict | list | None] = mapped_column(JSONB)
    __table_args__ = {"postgresql_partition_by": "RANGE (fetched_at)"}


class TradeWatermark(Base):
    __tablename__ = "trade_watermarks"
    ticker: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_volume_fp: Mapped[float] = mapped_column(Numeric(18, 2), nullable=False)


class SourceState(Base):
    __tablename__ = "source_state"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # e.g. odds_featured:americanfootball_nfl
    last_fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
```

- [ ] **Step 4: Implement schema helpers**

`harness/db/schema.py`:
```python
from datetime import datetime, timedelta, timezone

from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from harness.db.models import Base


def week_bounds(now: datetime) -> tuple[datetime, datetime]:
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


def drop_schema(engine: Engine) -> None:
    with engine.begin() as conn:
        conn.execute(text("drop table if exists raw_responses cascade"))
        conn.execute(text("drop table if exists runs, trade_watermarks, source_state cascade"))
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_schema.py -v` (with `DATABASE_URL_TEST` exported)
Expected: 3 PASS

- [ ] **Step 6: Commit**

```bash
git add harness/db tests/test_schema.py tests/conftest.py
git commit -m "feat: db models, weekly partitions, schema creation"
```

---

### Task 4: HTTP wrapper with timeout and read-retry policy

**Files:**
- Create: `harness/feeds/__init__.py`, `harness/feeds/http.py`, `tests/test_http.py`

**Interfaces:**
- Produces: `FetchResult(status: int, headers: dict[str, str], body: dict | list | None, fetched_at: datetime, url: str, elapsed_s: float)`; `HttpClient(timeout_s: float, sleep=time.sleep)` with `get(url, params: dict | None = None, redact_params: tuple[str, ...] = ("apiKey",)) -> FetchResult`. Policy: one retry on connection error, timeout, or 5xx after 1 s; on 429 sleep `Retry-After` (default 2 s) once and retry; 4xx other than 429 returns without retry. Non-JSON bodies yield `body=None`. Never raises for HTTP status; raises `FetchError` only after retries are exhausted on transport errors.

- [ ] **Step 1: Write the failing tests**

`tests/test_http.py`:
```python
import httpx
import pytest
import respx

from harness.feeds.http import FetchError, HttpClient


@respx.mock
def test_get_returns_json_and_headers():
    respx.get("https://x/a").mock(return_value=httpx.Response(200, json={"ok": 1}, headers={"x-requests-last": "3"}))
    c = HttpClient(timeout_s=1, sleep=lambda s: None)
    r = c.get("https://x/a", params={"apiKey": "SECRET", "q": "1"})
    assert r.status == 200 and r.body == {"ok": 1}
    assert r.headers["x-requests-last"] == "3"
    assert "SECRET" not in r.url and "q=1" in r.url


@respx.mock
def test_retries_once_on_5xx_then_succeeds():
    route = respx.get("https://x/b").mock(side_effect=[httpx.Response(502), httpx.Response(200, json=[])])
    c = HttpClient(timeout_s=1, sleep=lambda s: None)
    r = c.get("https://x/b")
    assert r.status == 200 and route.call_count == 2


@respx.mock
def test_429_sleeps_retry_after_then_retries():
    slept = []
    route = respx.get("https://x/c").mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "5"}), httpx.Response(200, json={})])
    c = HttpClient(timeout_s=1, sleep=slept.append)
    r = c.get("https://x/c")
    assert r.status == 200 and slept == [5.0] and route.call_count == 2


@respx.mock
def test_transport_error_twice_raises():
    respx.get("https://x/d").mock(side_effect=httpx.ConnectError("boom"))
    c = HttpClient(timeout_s=1, sleep=lambda s: None)
    with pytest.raises(FetchError):
        c.get("https://x/d")


@respx.mock
def test_non_json_body_is_none():
    respx.get("https://x/e").mock(return_value=httpx.Response(200, text="<html>"))
    r = HttpClient(timeout_s=1, sleep=lambda s: None).get("https://x/e")
    assert r.status == 200 and r.body is None
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_http.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Implement**

`harness/feeds/__init__.py`: empty.

`harness/feeds/http.py`:
```python
import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

import httpx

log = logging.getLogger(__name__)


class FetchError(Exception):
    pass


@dataclass(frozen=True)
class FetchResult:
    status: int
    headers: dict[str, str]
    body: dict | list | None
    fetched_at: datetime
    url: str
    elapsed_s: float


class HttpClient:
    def __init__(self, timeout_s: float, sleep: Callable[[float], None] = time.sleep):
        self._client = httpx.Client(timeout=timeout_s, headers={"User-Agent": "harness-recorder/0.1"})
        self._sleep = sleep

    def _redacted_url(self, resp: httpx.Response, redact_params: tuple[str, ...]) -> str:
        u = resp.request.url
        params = dict(u.params)
        for k in redact_params:
            if k in params:
                params[k] = "[REDACTED]"
        return str(u.copy_with(params=params))

    def get(self, url: str, params: dict | None = None, redact_params: tuple[str, ...] = ("apiKey",)) -> FetchResult:
        attempts = 0
        while True:
            attempts += 1
            t0 = time.monotonic()
            try:
                resp = self._client.get(url, params=params)
            except (httpx.TransportError, httpx.TimeoutException) as e:
                if attempts >= 2:
                    raise FetchError(f"{url}: {e!r}") from e
                self._sleep(1.0)
                continue
            elapsed = time.monotonic() - t0
            if resp.status_code == 429 and attempts < 2:
                ra = resp.headers.get("Retry-After")
                self._sleep(float(ra) if ra and ra.replace(".", "", 1).isdigit() else 2.0)
                continue
            if 500 <= resp.status_code < 600 and attempts < 2:
                self._sleep(1.0)
                continue
            try:
                body = resp.json()
            except ValueError:
                body = None
            return FetchResult(
                status=resp.status_code,
                headers={k.lower(): v for k, v in resp.headers.items()},
                body=body,
                fetched_at=datetime.now(timezone.utc),
                url=self._redacted_url(resp, redact_params),
                elapsed_s=elapsed,
            )

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_http.py -v`
Expected: 5 PASS

- [ ] **Step 5: Commit**

```bash
git add harness/feeds tests/test_http.py
git commit -m "feat: http client with timeout and bounded retry"
```

---

### Task 5: The Odds API client

**Files:**
- Create: `harness/feeds/odds_api.py`, `tests/test_odds_api.py`, `tests/fixtures/odds_featured_nfl.json`

**Interfaces:**
- Consumes: `HttpClient.get`, `Settings`.
- Produces: `OddsApiClient(http: HttpClient, base_url: str, api_key: str, bookmakers: str)` with `fetch_featured(sport: str) -> FetchResult` (markets `h2h,spreads,totals`) and `fetch_event_alternates(sport: str, event_id: str) -> FetchResult` (markets `alternate_spreads,alternate_totals`). Pure: `parse_credit_headers(headers) -> Credits(last: int, used: int, remaining: int)` (zeros when absent); `parse_event_ids_and_times(body) -> list[tuple[str, datetime]]` returning `(event_id, commence_time_utc)`.

- [ ] **Step 1: Create the fixture**

`tests/fixtures/odds_featured_nfl.json`:
```json
[
  {
    "id": "e1f2a3",
    "sport_key": "americanfootball_nfl",
    "commence_time": "2026-09-10T00:20:00Z",
    "home_team": "Seattle Seahawks",
    "away_team": "New England Patriots",
    "bookmakers": [
      {
        "key": "pinnacle",
        "last_update": "2026-09-06T16:00:00Z",
        "markets": [
          {"key": "h2h", "outcomes": [{"name": "Seattle Seahawks", "price": 1.68}, {"name": "New England Patriots", "price": 2.30}]},
          {"key": "spreads", "outcomes": [{"name": "Seattle Seahawks", "price": 1.92, "point": -3.5}, {"name": "New England Patriots", "price": 1.98, "point": 3.5}]},
          {"key": "totals", "outcomes": [{"name": "Over", "price": 1.91, "point": 44.5}, {"name": "Under", "price": 1.99, "point": 44.5}]}
        ]
      }
    ]
  }
]
```

- [ ] **Step 2: Write the failing tests**

`tests/test_odds_api.py`:
```python
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient, parse_credit_headers, parse_event_ids_and_times

FIX = json.loads((Path(__file__).parent / "fixtures" / "odds_featured_nfl.json").read_text())


@respx.mock
def test_fetch_featured_builds_request():
    route = respx.get("https://o/v4/sports/americanfootball_nfl/odds").mock(
        return_value=httpx.Response(200, json=FIX, headers={"x-requests-last": "3", "x-requests-used": "10", "x-requests-remaining": "4990"}))
    c = OddsApiClient(HttpClient(1, sleep=lambda s: None), "https://o/v4", "KEY", "pinnacle,lowvig")
    r = c.fetch_featured("americanfootball_nfl")
    q = dict(route.calls[0].request.url.params)
    assert q == {"apiKey": "KEY", "bookmakers": "pinnacle,lowvig", "markets": "h2h,spreads,totals",
                 "oddsFormat": "decimal", "dateFormat": "iso"}
    assert r.body == FIX and "KEY" not in r.url


@respx.mock
def test_fetch_event_alternates_builds_request():
    route = respx.get("https://o/v4/sports/americanfootball_ncaaf/events/abc/odds").mock(
        return_value=httpx.Response(200, json={"id": "abc", "bookmakers": []}))
    c = OddsApiClient(HttpClient(1, sleep=lambda s: None), "https://o/v4", "KEY", "pinnacle")
    c.fetch_event_alternates("americanfootball_ncaaf", "abc")
    assert dict(route.calls[0].request.url.params)["markets"] == "alternate_spreads,alternate_totals"


def test_parse_credit_headers():
    c = parse_credit_headers({"x-requests-last": "3", "x-requests-used": "10", "x-requests-remaining": "4990"})
    assert (c.last, c.used, c.remaining) == (3, 10, 4990)
    z = parse_credit_headers({})
    assert (z.last, z.used, z.remaining) == (0, 0, 0)


def test_parse_event_ids_and_times():
    out = parse_event_ids_and_times(FIX)
    assert out == [("e1f2a3", datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc))]
    assert parse_event_ids_and_times(None) == []
```

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/test_odds_api.py -v`
Expected: FAIL with import error

- [ ] **Step 4: Implement**

`harness/feeds/odds_api.py`:
```python
from dataclasses import dataclass
from datetime import datetime, timezone

from harness.feeds.http import FetchResult, HttpClient

FEATURED_MARKETS = "h2h,spreads,totals"
ALTERNATE_MARKETS = "alternate_spreads,alternate_totals"


@dataclass(frozen=True)
class Credits:
    last: int
    used: int
    remaining: int


def parse_credit_headers(headers: dict[str, str]) -> Credits:
    def _i(k: str) -> int:
        v = headers.get(k, "0")
        try:
            return int(float(v))
        except ValueError:
            return 0
    return Credits(_i("x-requests-last"), _i("x-requests-used"), _i("x-requests-remaining"))


def parse_event_ids_and_times(body: dict | list | None) -> list[tuple[str, datetime]]:
    if not isinstance(body, list):
        return []
    out: list[tuple[str, datetime]] = []
    for ev in body:
        try:
            ts = datetime.fromisoformat(ev["commence_time"].replace("Z", "+00:00")).astimezone(timezone.utc)
            out.append((ev["id"], ts))
        except (KeyError, ValueError, AttributeError):
            continue
    return out


class OddsApiClient:
    def __init__(self, http: HttpClient, base_url: str, api_key: str, bookmakers: str):
        self._http = http
        self._base = base_url.rstrip("/")
        self._key = api_key
        self._bookmakers = bookmakers

    def _params(self, markets: str) -> dict:
        return {"apiKey": self._key, "bookmakers": self._bookmakers, "markets": markets,
                "oddsFormat": "decimal", "dateFormat": "iso"}

    def fetch_featured(self, sport: str) -> FetchResult:
        return self._http.get(f"{self._base}/sports/{sport}/odds", params=self._params(FEATURED_MARKETS))

    def fetch_event_alternates(self, sport: str, event_id: str) -> FetchResult:
        return self._http.get(f"{self._base}/sports/{sport}/events/{event_id}/odds",
                              params=self._params(ALTERNATE_MARKETS))
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_odds_api.py -v`
Expected: 4 PASS

- [ ] **Step 6: Commit**

```bash
git add harness/feeds/odds_api.py tests/test_odds_api.py tests/fixtures/odds_featured_nfl.json
git commit -m "feat: odds api client with credit header parsing"
```

---

### Task 6: ESPN scoreboard client and kickoff parser

**Files:**
- Create: `harness/feeds/espn.py`, `tests/test_espn.py`, `tests/fixtures/espn_nfl_scoreboard.json`

**Interfaces:**
- Produces: `EspnClient(http, base_url)` with `fetch_scoreboard(sport: Literal["nfl","ncaaf"]) -> FetchResult` (nfl → `/nfl/scoreboard`; ncaaf → `/college-football/scoreboard?groups=80&limit=400`). Pure: `Kickoff(sport: str, espn_event_id: str, kickoff_utc: datetime, home: str, away: str, status: str)`; `parse_kickoffs(sport, body) -> list[Kickoff]` (skips malformed events).

- [ ] **Step 1: Create the fixture**

`tests/fixtures/espn_nfl_scoreboard.json`:
```json
{
  "events": [
    {
      "id": "401872656",
      "date": "2026-09-10T00:20Z",
      "name": "New England Patriots at Seattle Seahawks",
      "status": {"type": {"name": "STATUS_SCHEDULED"}},
      "competitions": [{"competitors": [
        {"homeAway": "home", "team": {"abbreviation": "SEA", "displayName": "Seattle Seahawks"}},
        {"homeAway": "away", "team": {"abbreviation": "NE", "displayName": "New England Patriots"}}
      ]}]
    },
    {"id": "bad", "date": "not-a-date", "competitions": []}
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_espn.py`:
```python
import json
from datetime import datetime, timezone
from pathlib import Path

import httpx
import respx

from harness.feeds.espn import EspnClient, Kickoff, parse_kickoffs
from harness.feeds.http import HttpClient

FIX = json.loads((Path(__file__).parent / "fixtures" / "espn_nfl_scoreboard.json").read_text())


def test_parse_kickoffs_skips_malformed():
    out = parse_kickoffs("nfl", FIX)
    assert out == [Kickoff("nfl", "401872656", datetime(2026, 9, 10, 0, 20, tzinfo=timezone.utc),
                           "Seattle Seahawks", "New England Patriots", "STATUS_SCHEDULED")]


@respx.mock
def test_fetch_scoreboard_urls():
    r1 = respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=FIX))
    r2 = respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    c = EspnClient(HttpClient(1, sleep=lambda s: None), "https://e")
    c.fetch_scoreboard("nfl")
    c.fetch_scoreboard("ncaaf")
    assert r1.called and dict(r2.calls[0].request.url.params) == {"groups": "80", "limit": "400"}
```

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/test_espn.py -v`
Expected: FAIL with import error

- [ ] **Step 4: Implement**

`harness/feeds/espn.py`:
```python
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from harness.feeds.http import FetchResult, HttpClient

Sport = Literal["nfl", "ncaaf"]
_PATH = {"nfl": "/nfl/scoreboard", "ncaaf": "/college-football/scoreboard"}
_PARAMS = {"nfl": None, "ncaaf": {"groups": "80", "limit": "400"}}


@dataclass(frozen=True)
class Kickoff:
    sport: str
    espn_event_id: str
    kickoff_utc: datetime
    home: str
    away: str
    status: str


def parse_kickoffs(sport: str, body: dict | list | None) -> list[Kickoff]:
    if not isinstance(body, dict):
        return []
    out: list[Kickoff] = []
    for ev in body.get("events", []):
        try:
            ts = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
            comps = ev["competitions"][0]["competitors"]
            home = next(c["team"]["displayName"] for c in comps if c["homeAway"] == "home")
            away = next(c["team"]["displayName"] for c in comps if c["homeAway"] == "away")
            status = ev.get("status", {}).get("type", {}).get("name", "")
            out.append(Kickoff(sport, str(ev["id"]), ts, home, away, status))
        except (KeyError, ValueError, IndexError, StopIteration, AttributeError):
            continue
    return out


class EspnClient:
    def __init__(self, http: HttpClient, base_url: str):
        self._http = http
        self._base = base_url.rstrip("/")

    def fetch_scoreboard(self, sport: Sport) -> FetchResult:
        return self._http.get(f"{self._base}{_PATH[sport]}", params=_PARAMS[sport], redact_params=())
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_espn.py -v`
Expected: 2 PASS

- [ ] **Step 6: Commit**

```bash
git add harness/feeds/espn.py tests/test_espn.py tests/fixtures/espn_nfl_scoreboard.json
git commit -m "feat: espn scoreboard client and kickoff parser"
```

---

### Task 7: Kalshi public client (markets with pagination, orderbook, trades) and market summary parser

**Files:**
- Create: `harness/venues/__init__.py`, `harness/venues/kalshi/__init__.py`, `harness/venues/kalshi/public.py`, `tests/test_kalshi_public.py`, `tests/fixtures/kalshi_markets_page.json`

**Interfaces:**
- Produces: `FOOTBALL_SERIES = ("KXNFLGAME","KXNFLSPREAD","KXNFLTOTAL","KXNCAAFGAME","KXNCAAFSPREAD","KXNCAAFTOTAL")`; `KalshiPublic(http, base_url, sleep_s: float, sleep=time.sleep)` with `fetch_markets_all(series_ticker) -> list[FetchResult]` (follows `cursor` until empty, max 20 pages), `fetch_orderbook(ticker) -> FetchResult` (`depth=20`), `fetch_trades(ticker, min_ts: datetime) -> FetchResult` (`limit=1000`). Pure: `MarketSummary(ticker, event_ticker, series_ticker, event_date: date | None, yes_bid: Decimal | None, yes_ask: Decimal | None, volume_fp: Decimal, close_time: datetime | None)`; `parse_market_summaries(body) -> list[MarketSummary]`; `event_date_from_ticker(event_ticker) -> date | None` (parses `26SEP21` → 2026-09-21).

- [ ] **Step 1: Create the fixture**

`tests/fixtures/kalshi_markets_page.json`:
```json
{
  "cursor": "",
  "markets": [
    {"ticker": "KXNFLGAME-26SEP21NYGLAR-NYG", "event_ticker": "KXNFLGAME-26SEP21NYGLAR", "title": "New York G wins",
     "yes_sub_title": "New York G", "strike_type": "structured", "yes_bid_dollars": "0.2200", "yes_ask_dollars": "0.2300",
     "no_bid_dollars": "0.7700", "no_ask_dollars": "0.7800", "volume_fp": "1234.00", "volume_24h_fp": "50.00",
     "open_interest_fp": "900.00", "close_time": "2026-09-24T00:15:00Z", "status": "open"},
    {"ticker": "KXNFLSPREAD-26SEP14DENKC-KC7", "event_ticker": "KXNFLSPREAD-26SEP14DENKC", "title": "Kansas City wins by over 6.5 points?",
     "yes_sub_title": "Kansas City", "strike_type": "greater", "floor_strike": 6.5, "yes_bid_dollars": "0.3400", "yes_ask_dollars": "0.3500",
     "no_bid_dollars": "0.6500", "no_ask_dollars": "0.6600", "volume_fp": "0.00", "volume_24h_fp": "0.00",
     "open_interest_fp": "0.00", "close_time": "2026-09-17T00:15:00Z", "status": "open"},
    {"ticker": "WEIRD", "event_ticker": "WEIRD"}
  ]
}
```

- [ ] **Step 2: Write the failing tests**

`tests/test_kalshi_public.py`:
```python
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx
import respx

from harness.feeds.http import HttpClient
from harness.venues.kalshi.public import (FOOTBALL_SERIES, KalshiPublic, MarketSummary,
                                          event_date_from_ticker, parse_market_summaries)

FIX = json.loads((Path(__file__).parent / "fixtures" / "kalshi_markets_page.json").read_text())


def test_event_date_from_ticker():
    assert event_date_from_ticker("KXNFLGAME-26SEP21NYGLAR") == date(2026, 9, 21)
    assert event_date_from_ticker("KXNCAAFSPREAD-26SEP12BUFFFIU") == date(2026, 9, 12)
    assert event_date_from_ticker("WEIRD") is None


def test_parse_market_summaries():
    out = parse_market_summaries(FIX)
    assert len(out) == 3
    m = out[0]
    assert m == MarketSummary("KXNFLGAME-26SEP21NYGLAR-NYG", "KXNFLGAME-26SEP21NYGLAR", "KXNFLGAME", date(2026, 9, 21),
                              Decimal("0.2200"), Decimal("0.2300"), Decimal("1234.00"),
                              datetime(2026, 9, 24, 0, 15, tzinfo=timezone.utc))
    assert out[2].yes_bid is None and out[2].volume_fp == Decimal("0") and out[2].event_date is None


@respx.mock
def test_fetch_markets_all_follows_cursor():
    page1 = {"cursor": "abc", "markets": [{"ticker": "A", "event_ticker": "A"}]}
    page2 = {"cursor": "", "markets": [{"ticker": "B", "event_ticker": "B"}]}
    route = respx.get("https://k/markets").mock(side_effect=[httpx.Response(200, json=page1), httpx.Response(200, json=page2)])
    c = KalshiPublic(HttpClient(1, sleep=lambda s: None), "https://k", sleep_s=0, sleep=lambda s: None)
    pages = c.fetch_markets_all("KXNFLGAME")
    assert [p.body["markets"][0]["ticker"] for p in pages] == ["A", "B"]
    q0 = dict(route.calls[0].request.url.params)
    q1 = dict(route.calls[1].request.url.params)
    assert q0 == {"series_ticker": "KXNFLGAME", "status": "open", "limit": "1000"}
    assert q1["cursor"] == "abc"


@respx.mock
def test_fetch_orderbook_and_trades_params():
    ob = respx.get("https://k/markets/T1/orderbook").mock(return_value=httpx.Response(200, json={"orderbook_fp": {}}))
    tr = respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    c = KalshiPublic(HttpClient(1, sleep=lambda s: None), "https://k", sleep_s=0, sleep=lambda s: None)
    c.fetch_orderbook("T1")
    c.fetch_trades("T1", datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc))
    assert dict(ob.calls[0].request.url.params) == {"depth": "20"}
    assert dict(tr.calls[0].request.url.params) == {"ticker": "T1", "limit": "1000", "min_ts": "1788710400"}


def test_football_series_constant():
    assert "KXNCAAFTOTAL" in FOOTBALL_SERIES and len(FOOTBALL_SERIES) == 6
```

- [ ] **Step 3: Run to verify they fail**

Run: `pytest tests/test_kalshi_public.py -v`
Expected: FAIL with import error

- [ ] **Step 4: Implement**

`harness/venues/__init__.py`, `harness/venues/kalshi/__init__.py`: empty.

`harness/venues/kalshi/public.py`:
```python
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Callable

from harness.feeds.http import FetchResult, HttpClient

FOOTBALL_SERIES = ("KXNFLGAME", "KXNFLSPREAD", "KXNFLTOTAL", "KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL")
_MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"], 1)}
_DATE_RE = re.compile(r"^[A-Z]+-(\d{2})([A-Z]{3})(\d{2})")


@dataclass(frozen=True)
class MarketSummary:
    ticker: str
    event_ticker: str
    series_ticker: str
    event_date: date | None
    yes_bid: Decimal | None
    yes_ask: Decimal | None
    volume_fp: Decimal
    close_time: datetime | None


def event_date_from_ticker(event_ticker: str) -> date | None:
    m = _DATE_RE.match(event_ticker or "")
    if not m:
        return None
    yy, mon, dd = m.groups()
    try:
        return date(2000 + int(yy), _MONTHS[mon], int(dd))
    except (KeyError, ValueError):
        return None


def _dec(v) -> Decimal | None:
    if v is None:
        return None
    try:
        return Decimal(str(v))
    except InvalidOperation:
        return None


def _ts(v) -> datetime | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def parse_market_summaries(body: dict | list | None) -> list[MarketSummary]:
    if not isinstance(body, dict):
        return []
    out: list[MarketSummary] = []
    for m in body.get("markets", []):
        ticker = m.get("ticker")
        event_ticker = m.get("event_ticker", "")
        if not ticker:
            continue
        out.append(MarketSummary(
            ticker=ticker,
            event_ticker=event_ticker,
            series_ticker=event_ticker.split("-")[0] if event_ticker else "",
            event_date=event_date_from_ticker(event_ticker),
            yes_bid=_dec(m.get("yes_bid_dollars")),
            yes_ask=_dec(m.get("yes_ask_dollars")),
            volume_fp=_dec(m.get("volume_fp")) or Decimal("0"),
            close_time=_ts(m.get("close_time")),
        ))
    return out


class KalshiPublic:
    def __init__(self, http: HttpClient, base_url: str, sleep_s: float, sleep: Callable[[float], None] = time.sleep):
        self._http = http
        self._base = base_url.rstrip("/")
        self._sleep_s = sleep_s
        self._sleep = sleep

    def _pause(self) -> None:
        if self._sleep_s:
            self._sleep(self._sleep_s)

    def fetch_markets_all(self, series_ticker: str, max_pages: int = 20) -> list[FetchResult]:
        pages: list[FetchResult] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"series_ticker": series_ticker, "status": "open", "limit": "1000"}
            if cursor:
                params["cursor"] = cursor
            r = self._http.get(f"{self._base}/markets", params=params, redact_params=())
            pages.append(r)
            cursor = (r.body or {}).get("cursor", "") if isinstance(r.body, dict) else ""
            self._pause()
            if not cursor or r.status != 200:
                break
        return pages

    def fetch_orderbook(self, ticker: str) -> FetchResult:
        r = self._http.get(f"{self._base}/markets/{ticker}/orderbook", params={"depth": "20"}, redact_params=())
        self._pause()
        return r

    def fetch_trades(self, ticker: str, min_ts: datetime) -> FetchResult:
        r = self._http.get(f"{self._base}/markets/trades",
                           params={"ticker": ticker, "limit": "1000", "min_ts": str(int(min_ts.timestamp()))},
                           redact_params=())
        self._pause()
        return r
```

- [ ] **Step 5: Run the tests**

Run: `pytest tests/test_kalshi_public.py -v`
Expected: 5 PASS

- [ ] **Step 6: Commit**

```bash
git add harness/venues tests/test_kalshi_public.py tests/fixtures/kalshi_markets_page.json
git commit -m "feat: kalshi public client with pagination, orderbook, trades"
```

---

### Task 8: Raw store and run bookkeeping

**Files:**
- Create: `harness/recorder/__init__.py`, `harness/recorder/store.py`, `tests/test_store.py`

**Interfaces:**
- Consumes: models from Task 3, `FetchResult` from Task 4.
- Produces: `start_run(session, now) -> Run` (commits); `store_raw(session, run_id, source, endpoint, params, result: FetchResult) -> int` (adds, flushes, returns id; does not commit); `finish_run(session, run, status, error=None, **counters)` (sets `finished_at`, counters, commits); `get_source_state(session, key) -> datetime | None`; `set_source_state(session, key, ts)`; `get_watermarks(session) -> dict[str, TradeWatermark]`; `upsert_watermark(session, ticker, last_ts, last_volume_fp)`.

- [ ] **Step 1: Write the failing tests**

`tests/test_store.py`:
```python
from datetime import datetime, timezone
from decimal import Decimal

from harness.db.models import RawResponse, Run
from harness.db.schema import ensure_partitions
from harness.feeds.http import FetchResult
from harness.recorder.store import (finish_run, get_source_state, get_watermarks, set_source_state, start_run,
                                    store_raw, upsert_watermark)

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def _fr(body, status=200):
    return FetchResult(status=status, headers={}, body=body, fetched_at=NOW, url="u", elapsed_s=0.1)


def test_run_and_raw_lifecycle(db_session):
    ensure_partitions(db_session, NOW)
    run = start_run(db_session, NOW)
    assert run.id and run.status == "running"
    rid = store_raw(db_session, run.id, "kalshi", "/markets", {"series_ticker": "KXNFLGAME"}, _fr({"markets": []}))
    assert rid
    finish_run(db_session, run, "ok", n_requests=1, credits_used=0, odds_remaining=None)
    got = db_session.get(Run, run.id)
    assert got.status == "ok" and got.finished_at is not None and got.n_requests == 1
    raw = db_session.query(RawResponse).filter_by(run_id=run.id).one()
    assert raw.endpoint == "/markets" and raw.http_status == 200


def test_source_state_and_watermarks(db_session):
    assert get_source_state(db_session, "odds_featured:nfl") is None
    set_source_state(db_session, "odds_featured:nfl", NOW)
    assert get_source_state(db_session, "odds_featured:nfl") == NOW
    upsert_watermark(db_session, "T1", NOW, Decimal("10.00"))
    upsert_watermark(db_session, "T1", NOW, Decimal("12.00"))
    wm = get_watermarks(db_session)
    assert wm["T1"].last_volume_fp == Decimal("12.00")
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_store.py -v`
Expected: FAIL with import error (or SKIP without `DATABASE_URL_TEST`; set it as in Task 3)

- [ ] **Step 3: Implement**

`harness/recorder/__init__.py`: empty.

`harness/recorder/store.py`:
```python
from datetime import datetime
from decimal import Decimal

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import RawResponse, Run, SourceState, TradeWatermark
from harness.feeds.http import FetchResult


def start_run(session: Session, now: datetime) -> Run:
    run = Run(started_at=now, status="running")
    session.add(run)
    session.commit()
    return run


def store_raw(session: Session, run_id: int, source: str, endpoint: str, params: dict, result: FetchResult) -> int:
    row = RawResponse(run_id=run_id, source=source, endpoint=endpoint, params=params,
                      fetched_at=result.fetched_at, http_status=result.status, body=result.body)
    session.add(row)
    session.flush()
    return row.id


def finish_run(session: Session, run: Run, status: str, error: str | None = None, *, n_requests: int = 0,
               credits_used: int = 0, odds_remaining: int | None = None, budget_exhausted: bool = False,
               notes: dict | None = None, finished_at: datetime | None = None) -> None:
    run.status = status
    run.error = error
    run.n_requests = n_requests
    run.credits_used = credits_used
    run.odds_remaining = odds_remaining
    run.budget_exhausted = budget_exhausted
    run.notes = notes or {}
    run.finished_at = finished_at or datetime.now(tz=run.started_at.tzinfo)
    session.commit()


def get_source_state(session: Session, key: str) -> datetime | None:
    row = session.get(SourceState, key)
    return row.last_fetched_at if row else None


def set_source_state(session: Session, key: str, ts: datetime) -> None:
    stmt = insert(SourceState).values(key=key, last_fetched_at=ts)
    stmt = stmt.on_conflict_do_update(index_elements=["key"], set_={"last_fetched_at": ts})
    session.execute(stmt)


def get_watermarks(session: Session) -> dict[str, TradeWatermark]:
    return {w.ticker: w for w in session.query(TradeWatermark).all()}


def upsert_watermark(session: Session, ticker: str, last_ts: datetime, last_volume_fp: Decimal) -> None:
    stmt = insert(TradeWatermark).values(ticker=ticker, last_ts=last_ts, last_volume_fp=last_volume_fp)
    stmt = stmt.on_conflict_do_update(index_elements=["ticker"],
                                      set_={"last_ts": last_ts, "last_volume_fp": last_volume_fp})
    session.execute(stmt)
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_store.py -v`
Expected: 2 PASS

- [ ] **Step 5: Commit**

```bash
git add harness/recorder tests/test_store.py
git commit -m "feat: raw response store, run bookkeeping, watermarks"
```

---

### Task 9: Cadence planner (pure)

**Files:**
- Create: `harness/recorder/cadence.py`, `tests/test_cadence.py`

**Interfaces:**
- Consumes: `Kickoff` (Task 6), `MarketSummary` (Task 7).
- Produces:
  - `SPORTS = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf"}`.
  - `interval_for(sport: str, now: datetime, kickoffs: list[Kickoff], tz: str) -> int | None`: seconds between polls for that sport, or `None` when quiet hours (01:00–08:00 local). Rules: 20 if any NFL kickoff is between T−100 and T−60 min from now (NFL only); 120 if now is within [first_kickoff_today − 3h, last_kickoff_today] for that sport's local calendar day; 300 if local weekday is Sat or Sun; else 900.
  - `is_due(last: datetime | None, now: datetime, interval: int | None) -> bool`.
  - `alternates_due(now, events: list[tuple[str, datetime]], last_alt: dict[str, datetime]) -> list[str]`: event ids whose commence_time is within the next 36 h and (commence − now ≤ 3 h and last ≥ 120 s ago) or (last ≥ 900 s ago).
  - `select_ladders(now, markets: list[MarketSummary], kickoffs: list[Kickoff], tz, cap: int) -> list[str]`: tickers whose `event_date` equals today's local date, with `yes_bid` and `yes_ask` both present and either inside [0.20, 0.80], only when some kickoff of any sport is within the next 3 h or in progress today (kickoff ≤ now ≤ kickoff + 4 h); sorted by `volume_fp` desc; truncated to `cap`.
  - `select_trade_tickers(markets: list[MarketSummary], watermarks: dict[str, tuple[datetime, Decimal]]) -> list[tuple[str, datetime]]`: tickers whose `volume_fp` differs from the watermark volume (or have no watermark and volume > 0), paired with `min_ts = watermark.last_ts − 5 s` or `now − 24 h` when new. Pass `now` too.

- [ ] **Step 1: Write the failing tests**

`tests/test_cadence.py`:
```python
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from harness.feeds.espn import Kickoff
from harness.recorder.cadence import alternates_due, interval_for, is_due, select_ladders, select_trade_tickers
from harness.venues.kalshi.public import MarketSummary

TZ = "America/Chicago"
UTC = timezone.utc


def k(sport, ts, id_="1"):
    return Kickoff(sport, id_, ts, "H", "A", "STATUS_SCHEDULED")


def test_quiet_hours_returns_none():
    # 03:00 CT on a Wednesday = 08:00 UTC
    now = datetime(2026, 9, 9, 8, 0, tzinfo=UTC)
    assert interval_for("nfl", now, [], TZ) is None


def test_weekday_no_games_900():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)  # 10:00 CT Wed
    assert interval_for("nfl", now, [], TZ) == 900


def test_weekend_300():
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)  # Sat 10:00 CT
    assert interval_for("ncaaf", now, [], TZ) == 300


def test_game_window_120():
    kick = datetime(2026, 9, 10, 0, 20, tzinfo=UTC)  # Wed 19:20 CT
    now = kick - timedelta(hours=2)
    assert interval_for("nfl", now, [k("nfl", kick)], TZ) == 120
    assert interval_for("ncaaf", now, [k("nfl", kick)], TZ) == 900  # other sport unaffected


def test_nfl_inactives_burst_20():
    kick = datetime(2026, 9, 13, 17, 0, tzinfo=UTC)
    now = kick - timedelta(minutes=80)
    assert interval_for("nfl", now, [k("nfl", kick)], TZ) == 20
    assert interval_for("ncaaf", now, [k("nfl", kick)], TZ) == 300  # Sunday, no ncaaf burst


def test_is_due():
    now = datetime(2026, 9, 9, 15, 0, tzinfo=UTC)
    assert is_due(None, now, 120)
    assert not is_due(now - timedelta(seconds=60), now, 120)
    assert is_due(now - timedelta(seconds=121), now, 120)
    assert not is_due(None, now, None)


def test_alternates_due():
    now = datetime(2026, 9, 12, 15, 0, tzinfo=UTC)
    events = [("near", now + timedelta(hours=2)), ("far", now + timedelta(hours=20)), ("toofar", now + timedelta(hours=48))]
    last = {"near": now - timedelta(seconds=130), "far": now - timedelta(seconds=130)}
    assert alternates_due(now, events, last) == ["near"]
    assert alternates_due(now, events, {}) == ["near", "far"]


def _ms(ticker, ev_date, bid, ask, vol):
    return MarketSummary(ticker, "E", "S", ev_date, Decimal(bid) if bid else None, Decimal(ask) if ask else None,
                         Decimal(vol), None)


def test_select_ladders_filters_and_caps():
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)  # Sat 15:00 CT
    kicks = [k("ncaaf", now + timedelta(hours=1))]
    today = date(2026, 9, 12)
    ms = [
        _ms("in_band", today, "0.40", "0.42", "100"),
        _ms("edge_band", today, "0.15", "0.25", "500"),
        _ms("out_band", today, "0.05", "0.08", "900"),
        _ms("no_quote", today, None, "0.50", "900"),
        _ms("tomorrow", date(2026, 9, 13), "0.50", "0.52", "900"),
    ]
    assert select_ladders(now, ms, kicks, TZ, cap=10) == ["edge_band", "in_band"]
    assert select_ladders(now, ms, kicks, TZ, cap=1) == ["edge_band"]
    assert select_ladders(now, ms, [], TZ, cap=10) == []


def test_select_trade_tickers():
    now = datetime(2026, 9, 12, 20, 0, tzinfo=UTC)
    ms = [_ms("changed", None, None, None, "12"), _ms("same", None, None, None, "10"),
          _ms("new_quiet", None, None, None, "0"), _ms("new_active", None, None, None, "3")]
    wm = {"changed": (now - timedelta(hours=1), Decimal("10")), "same": (now - timedelta(hours=1), Decimal("10"))}
    out = select_trade_tickers(now, ms, wm)
    assert out == [("changed", now - timedelta(hours=1, seconds=5)), ("new_active", now - timedelta(hours=24))]
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_cadence.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Implement**

`harness/recorder/cadence.py`:
```python
from datetime import datetime, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from harness.feeds.espn import Kickoff
from harness.venues.kalshi.public import MarketSummary

SPORTS = {"nfl": "americanfootball_nfl", "ncaaf": "americanfootball_ncaaf"}
BAND_LO, BAND_HI = Decimal("0.20"), Decimal("0.80")


def _local(now: datetime, tz: str) -> datetime:
    return now.astimezone(ZoneInfo(tz))


def interval_for(sport: str, now: datetime, kickoffs: list[Kickoff], tz: str) -> int | None:
    loc = _local(now, tz)
    if 1 <= loc.hour < 8:
        return None
    mine = [x for x in kickoffs if x.sport == sport]
    if sport == "nfl":
        for x in mine:
            delta = x.kickoff_utc - now
            if timedelta(minutes=60) <= delta <= timedelta(minutes=100):
                return 20
    today = [x.kickoff_utc for x in mine if _local(x.kickoff_utc, tz).date() == loc.date()]
    if today and (min(today) - timedelta(hours=3)) <= now <= max(today):
        return 120
    if loc.weekday() >= 5:
        return 300
    return 900


def is_due(last: datetime | None, now: datetime, interval: int | None) -> bool:
    if interval is None:
        return False
    if last is None:
        return True
    return (now - last).total_seconds() >= interval


def alternates_due(now: datetime, events: list[tuple[str, datetime]], last_alt: dict[str, datetime]) -> list[str]:
    out: list[str] = []
    for event_id, commence in events:
        until = commence - now
        if until < timedelta(0) or until > timedelta(hours=36):
            continue
        interval = 120 if until <= timedelta(hours=3) else 900
        if is_due(last_alt.get(event_id), now, interval):
            out.append(event_id)
    return out


def _in_band(p: Decimal | None) -> bool:
    return p is not None and BAND_LO <= p <= BAND_HI


def select_ladders(now: datetime, markets: list[MarketSummary], kickoffs: list[Kickoff], tz: str, cap: int) -> list[str]:
    active = any(-timedelta(hours=4) <= (x.kickoff_utc - now) <= timedelta(hours=3) for x in kickoffs)
    if not active:
        return []
    today = _local(now, tz).date()
    picked = [m for m in markets
              if m.event_date == today and m.yes_bid is not None and m.yes_ask is not None
              and (_in_band(m.yes_bid) or _in_band(m.yes_ask))]
    picked.sort(key=lambda m: m.volume_fp, reverse=True)
    return [m.ticker for m in picked[:cap]]


def select_trade_tickers(now: datetime, markets: list[MarketSummary],
                         watermarks: dict[str, tuple[datetime, Decimal]]) -> list[tuple[str, datetime]]:
    out: list[tuple[str, datetime]] = []
    for m in markets:
        wm = watermarks.get(m.ticker)
        if wm is None:
            if m.volume_fp > 0:
                out.append((m.ticker, now - timedelta(hours=24)))
        elif m.volume_fp != wm[1]:
            out.append((m.ticker, wm[0] - timedelta(seconds=5)))
    return out
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_cadence.py -v`
Expected: 9 PASS

- [ ] **Step 5: Commit**

```bash
git add harness/recorder/cadence.py tests/test_cadence.py
git commit -m "feat: pure cadence planner for recorder"
```

---

### Task 10: Tick orchestration with budget and per-source isolation

**Files:**
- Create: `harness/recorder/tick.py`, `tests/test_tick.py`

**Interfaces:**
- Consumes: everything above.
- Produces: `Recorder(settings, session_factory, odds: OddsApiClient, espn: EspnClient, kalshi: KalshiPublic, clock: Callable[[], datetime] = utcnow, monotonic=time.monotonic)` with `maybe_tick() -> Run | None`. Behavior, in order, all inside one `runs` row:
  1. `ensure_partitions`.
  2. ESPN scoreboards for both sports if due at interval 900 (key `espn:<sport>`); parse kickoffs from the stored body (fall back to the most recent stored ESPN body from `raw_responses` when not fetched this tick, so cadence still works).
  3. For each sport, Odds API featured if `is_due` (key `odds_featured:<sport>`); then alternates for due events (key `odds_alt:<event_id>`); credits accumulated from headers.
  4. Kalshi bulk markets for all six series if due at the NFL/NCAAF interval of that series (key `kalshi_markets:<series>`); parse summaries.
  5. Trades for `select_trade_tickers` (watermark update per ticker to the newest `created_time` in the response, or `now` when empty), then ladders for `select_ladders`, each call checked against the tick budget; when the budget is exhausted stop, set `budget_exhausted=True`, and record skipped counts in `notes`.
  6. Any exception inside one source is logged, recorded in `notes["errors"]`, and does not stop other sources. Run status `ok` if no errors, `error` otherwise. If nothing was due, status `skipped` and the run row is still written (cheap heartbeat evidence).
  - Returns the `Run`.

- [ ] **Step 1: Write the failing tests**

`tests/test_tick.py`:
```python
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import respx
from sqlalchemy.orm import sessionmaker

from harness.db.models import RawResponse, Run, TradeWatermark
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.recorder.tick import Recorder
from harness.venues.kalshi.public import KalshiPublic

FIXD = Path(__file__).parent / "fixtures"
ODDS = json.loads((FIXD / "odds_featured_nfl.json").read_text())
ESPN = json.loads((FIXD / "espn_nfl_scoreboard.json").read_text())
KM = json.loads((FIXD / "kalshi_markets_page.json").read_text())
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)  # Wed 18:00 CT, 1h20m before the 19:20 kickoff


def _recorder(env_settings, db_session, now=NOW):
    http = HttpClient(1, sleep=lambda s: None)
    odds = OddsApiClient(http, "https://o/v4", "KEY", "pinnacle")
    espn = EspnClient(http, "https://e")
    kalshi = KalshiPublic(http, "https://k", sleep_s=0, sleep=lambda s: None)
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    clock = {"now": now}
    return Recorder(env_settings, factory, odds, espn, kalshi, clock=lambda: clock["now"]), clock


@respx.mock
def test_first_tick_fetches_everything_and_records(env_settings, db_session):
    respx.get("https://e/nfl/scoreboard").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get("https://e/college-football/scoreboard").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/odds").mock(
        return_value=httpx.Response(200, json=ODDS, headers={"x-requests-last": "3", "x-requests-remaining": "100"}))
    respx.get(url__regex=r"https://o/v4/sports/\w+/events/\w+/odds").mock(return_value=httpx.Response(200, json={}))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": [
        {"trade_id": "t1", "created_time": "2026-09-09T22:59:00Z"}]}))
    respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={"orderbook_fp": {}}))

    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "ok", run.notes
    rows = db_session.query(RawResponse).filter_by(run_id=run.id).all()
    sources = {(r.source, r.endpoint) for r in rows}
    assert ("espn", "/nfl/scoreboard") in sources
    assert ("odds_api", "/sports/americanfootball_nfl/odds") in sources
    assert ("odds_api", "/sports/americanfootball_nfl/events/e1f2a3/odds") in sources  # commence within 36h
    assert ("kalshi", "/markets") in sources
    assert ("kalshi", "/markets/trades") in sources  # fixture market has volume 1234 and no watermark
    assert run.credits_used == 3 * 2 + 2 * 2  # 2 featured calls at 3 + 2 alternates calls at 2 (fixture served for both sports)
    wm = db_session.get(TradeWatermark, "KXNFLGAME-26SEP21NYGLAR-NYG")
    assert wm is not None and wm.last_ts == datetime(2026, 9, 9, 22, 59, tzinfo=timezone.utc)


@respx.mock
def test_second_tick_within_interval_is_skipped(env_settings, db_session):
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": [], "trades": []}))
    rec, clock = _recorder(env_settings, db_session)
    first = rec.maybe_tick()
    clock["now"] = NOW + timedelta(seconds=30)
    second = rec.maybe_tick()
    assert first.status == "ok" and second.status == "skipped"
    assert db_session.query(RawResponse).filter_by(run_id=second.id).count() == 0


@respx.mock
def test_source_failure_is_isolated(env_settings, db_session):
    respx.get("https://e/nfl/scoreboard").mock(side_effect=httpx.ConnectError("down"))
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": []}))
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.status == "error" and "espn:nfl" in json.dumps(run.notes["errors"])
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi").count() == 6


@respx.mock
def test_budget_exhaustion_stops_ladders(env_settings, db_session):
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json=ESPN))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json=KM))
    respx.get("https://k/markets/trades").mock(return_value=httpx.Response(200, json={"trades": []}))
    ob = respx.get(url__regex=r"https://k/markets/[^/]+/orderbook").mock(return_value=httpx.Response(200, json={}))
    env_settings.tick_budget_s = 0  # everything after bulk markets is over budget
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert run.budget_exhausted is True and ob.call_count == 0
```

- [ ] **Step 2: Run to verify they fail**

Run: `pytest tests/test_tick.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Implement**

`harness/recorder/tick.py`:
```python
import logging
import time
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

from sqlalchemy import desc
from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings
from harness.db.models import RawResponse, Run
from harness.db.schema import ensure_partitions
from harness.feeds.espn import EspnClient, Kickoff, parse_kickoffs
from harness.feeds.odds_api import OddsApiClient, parse_credit_headers, parse_event_ids_and_times
from harness.recorder import store
from harness.recorder.cadence import (SPORTS, alternates_due, interval_for, is_due, select_ladders,
                                      select_trade_tickers)
from harness.venues.kalshi.public import FOOTBALL_SERIES, KalshiPublic, MarketSummary, parse_market_summaries

log = logging.getLogger(__name__)
_ESPN_PATH = {"nfl": "/nfl/scoreboard", "ncaaf": "/college-football/scoreboard"}
_SERIES_SPORT = {s: ("nfl" if "NFL" in s else "ncaaf") for s in FOOTBALL_SERIES}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class _Budget:
    def __init__(self, seconds: int, monotonic: Callable[[], float]):
        self._deadline = monotonic() + seconds
        self._mono = monotonic

    def ok(self) -> bool:
        return self._mono() < self._deadline


class Recorder:
    def __init__(self, settings: Settings, session_factory: sessionmaker, odds: OddsApiClient, espn: EspnClient,
                 kalshi: KalshiPublic, clock: Callable[[], datetime] = utcnow,
                 monotonic: Callable[[], float] = time.monotonic):
        self.s = settings
        self.session_factory = session_factory
        self.odds, self.espn, self.kalshi = odds, espn, kalshi
        self.clock, self.monotonic = clock, monotonic

    # ---- helpers -------------------------------------------------------------------
    def _latest_body(self, session: Session, source: str, endpoint: str) -> dict | list | None:
        row = (session.query(RawResponse).filter_by(source=source, endpoint=endpoint, http_status=200)
               .order_by(desc(RawResponse.fetched_at)).first())
        return row.body if row else None

    # ---- sources -------------------------------------------------------------------
    def _espn(self, session: Session, run: Run, now: datetime, ctx: dict) -> list[Kickoff]:
        kickoffs: list[Kickoff] = []
        for sport in SPORTS:
            key = f"espn:{sport}"
            body = None
            try:
                if is_due(store.get_source_state(session, key), now, 900):
                    r = self.espn.fetch_scoreboard(sport)  # type: ignore[arg-type]
                    store.store_raw(session, run.id, "espn", _ESPN_PATH[sport], {}, r)
                    ctx["n"] += 1
                    if r.status == 200:
                        store.set_source_state(session, key, now)
                        body = r.body
                    ctx["fetched"] = True
            except Exception as e:  # noqa: BLE001
                log.exception("espn failed")
                ctx["errors"].append({key: repr(e)})
            if body is None:
                body = self._latest_body(session, "espn", _ESPN_PATH[sport])
            kickoffs.extend(parse_kickoffs(sport, body))
        return kickoffs

    def _odds(self, session: Session, run: Run, now: datetime, kickoffs: list[Kickoff], ctx: dict) -> None:
        for sport, sport_key in SPORTS.items():
            key = f"odds_featured:{sport}"
            interval = interval_for(sport, now, kickoffs, self.s.tz_local)
            body = None
            try:
                if is_due(store.get_source_state(session, key), now, interval):
                    r = self.odds.fetch_featured(sport_key)
                    store.store_raw(session, run.id, "odds_api", f"/sports/{sport_key}/odds", {"markets": "featured"}, r)
                    ctx["n"] += 1
                    c = parse_credit_headers(r.headers)
                    ctx["credits"] += c.last
                    ctx["remaining"] = c.remaining
                    if r.status == 200:
                        store.set_source_state(session, key, now)
                        body = r.body
                    ctx["fetched"] = True
            except Exception as e:  # noqa: BLE001
                log.exception("odds featured failed")
                ctx["errors"].append({key: repr(e)})
            if body is None:
                body = self._latest_body(session, "odds_api", f"/sports/{sport_key}/odds")
            if interval is None:
                continue
            events = parse_event_ids_and_times(body)
            last_alt = {eid: ts for eid, _ in events
                        if (ts := store.get_source_state(session, f"odds_alt:{eid}")) is not None}
            for eid in alternates_due(now, events, last_alt):
                try:
                    r = self.odds.fetch_event_alternates(sport_key, eid)
                    store.store_raw(session, run.id, "odds_api", f"/sports/{sport_key}/events/{eid}/odds",
                                    {"markets": "alternates"}, r)
                    ctx["n"] += 1
                    c = parse_credit_headers(r.headers)
                    ctx["credits"] += c.last
                    ctx["remaining"] = c.remaining
                    if r.status == 200:
                        store.set_source_state(session, f"odds_alt:{eid}", now)
                    ctx["fetched"] = True
                except Exception as e:  # noqa: BLE001
                    log.exception("odds alternates failed")
                    ctx["errors"].append({f"odds_alt:{eid}": repr(e)})

    def _kalshi_markets(self, session: Session, run: Run, now: datetime, kickoffs: list[Kickoff], ctx: dict) -> list[MarketSummary]:
        summaries: list[MarketSummary] = []
        for series in FOOTBALL_SERIES:
            key = f"kalshi_markets:{series}"
            interval = interval_for(_SERIES_SPORT[series], now, kickoffs, self.s.tz_local)
            pages_bodies: list = []
            try:
                if is_due(store.get_source_state(session, key), now, interval):
                    for r in self.kalshi.fetch_markets_all(series):
                        store.store_raw(session, run.id, "kalshi", "/markets", {"series_ticker": series}, r)
                        ctx["n"] += 1
                        if r.status == 200:
                            pages_bodies.append(r.body)
                    if pages_bodies:
                        store.set_source_state(session, key, now)
                    ctx["fetched"] = True
            except Exception as e:  # noqa: BLE001
                log.exception("kalshi markets failed")
                ctx["errors"].append({key: repr(e)})
            for b in pages_bodies:
                summaries.extend(parse_market_summaries(b))
        return summaries

    def _kalshi_trades_and_ladders(self, session: Session, run: Run, now: datetime, kickoffs: list[Kickoff],
                                   summaries: list[MarketSummary], budget: _Budget, ctx: dict) -> None:
        wms = {t: (w.last_ts, Decimal(w.last_volume_fp)) for t, w in store.get_watermarks(session).items()}
        trades = select_trade_tickers(now, summaries, wms)
        vol = {m.ticker: m.volume_fp for m in summaries}
        for ticker, min_ts in trades:
            if not budget.ok():
                ctx["skipped_trades"] += 1
                continue
            try:
                r = self.kalshi.fetch_trades(ticker, min_ts)
                store.store_raw(session, run.id, "kalshi", "/markets/trades", {"ticker": ticker, "min_ts": min_ts.isoformat()}, r)
                ctx["n"] += 1
                if r.status == 200:
                    newest = now
                    for t in (r.body or {}).get("trades", []) if isinstance(r.body, dict) else []:
                        try:
                            ts = datetime.fromisoformat(t["created_time"].replace("Z", "+00:00"))
                            newest = max(newest if newest != now else ts, ts)
                        except (KeyError, ValueError):
                            pass
                    store.upsert_watermark(session, ticker, newest, vol.get(ticker, Decimal("0")))
                ctx["fetched"] = True
            except Exception as e:  # noqa: BLE001
                log.exception("kalshi trades failed")
                ctx["errors"].append({f"kalshi_trades:{ticker}": repr(e)})
        for ticker in select_ladders(now, summaries, kickoffs, self.s.tz_local, self.s.ladder_cap_per_tick):
            if not budget.ok():
                ctx["skipped_ladders"] += 1
                continue
            try:
                r = self.kalshi.fetch_orderbook(ticker)
                store.store_raw(session, run.id, "kalshi", f"/markets/{ticker}/orderbook", {"depth": 20}, r)
                ctx["n"] += 1
                ctx["fetched"] = True
            except Exception as e:  # noqa: BLE001
                log.exception("kalshi orderbook failed")
                ctx["errors"].append({f"kalshi_orderbook:{ticker}": repr(e)})

    # ---- entry point -----------------------------------------------------------------
    def maybe_tick(self) -> Run:
        now = self.clock()
        budget = _Budget(self.s.tick_budget_s, self.monotonic)
        ctx: dict = {"n": 0, "credits": 0, "remaining": None, "errors": [], "fetched": False,
                     "skipped_trades": 0, "skipped_ladders": 0}
        with self.session_factory() as session:
            ensure_partitions(session, now)
            run = store.start_run(session, now)
            try:
                kickoffs = self._espn(session, run, now, ctx)
                self._odds(session, run, now, kickoffs, ctx)
                summaries = self._kalshi_markets(session, run, now, kickoffs, ctx)
                if summaries:
                    self._kalshi_trades_and_ladders(session, run, now, kickoffs, summaries, budget, ctx)
                session.flush()
            except Exception as e:  # noqa: BLE001
                log.exception("tick failed")
                ctx["errors"].append({"tick": repr(e)})
            exhausted = ctx["skipped_trades"] > 0 or ctx["skipped_ladders"] > 0
            status = "error" if ctx["errors"] else ("ok" if ctx["fetched"] else "skipped")
            store.finish_run(session, run, status, error=None if not ctx["errors"] else "see notes",
                             n_requests=ctx["n"], credits_used=ctx["credits"], odds_remaining=ctx["remaining"],
                             budget_exhausted=exhausted,
                             notes={"errors": ctx["errors"], "skipped_trades": ctx["skipped_trades"],
                                    "skipped_ladders": ctx["skipped_ladders"]},
                             finished_at=self.clock())
            log.info("tick %s n=%d credits=%d errors=%d", status, ctx["n"], ctx["credits"], len(ctx["errors"]))
            return run
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_tick.py -v`
Expected: 4 PASS. If `test_first_tick...` fails on `credits_used`, check that both featured calls (nfl and ncaaf) hit the regex route and the alternates route returned a `x-requests-last` header; adjust the fixture headers, not the assertion, so that featured = 3 credits and alternates = 2 credits each (add `headers={"x-requests-last": "2"}` to the alternates mock).

- [ ] **Step 5: Run the whole suite**

Run: `pytest -v`
Expected: all PASS (DB tests require `DATABASE_URL_TEST`)

- [ ] **Step 6: Commit**

```bash
git add harness/recorder/tick.py tests/test_tick.py
git commit -m "feat: recorder tick orchestration with budget and source isolation"
```

---

### Task 11: Scheduler, CLI, and health endpoint

**Files:**
- Create: `harness/scheduler.py`, `harness/health.py`, `harness/cli.py`, `tests/test_health.py`

**Interfaces:**
- Produces:
  - `build_scheduler(recorder: Recorder, heartbeat_s: int) -> BackgroundScheduler` with one interval job `maybe_tick` (`max_instances=1, coalesce=True, misfire_grace_time=60`).
  - `build_recorder(settings) -> Recorder` wiring real clients.
  - FastAPI `app` in `harness/health.py` with `GET /healthz` → `{"status": "ok"|"stale"|"error", "last_run_at", "last_status", "seconds_since", "credits_remaining"}`; `stale` when the last run is older than 20 minutes; HTTP 503 when `stale` or `error`. `create_app(session_factory, clock) -> FastAPI` for tests.
  - Typer CLI `harness` with commands `init-db`, `tick-once`, `run`, `serve --port 8080`.

- [ ] **Step 1: Write the failing test**

`tests/test_health.py`:
```python
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from harness.health import create_app
from harness.recorder.store import finish_run, start_run

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def test_healthz_reports_last_run(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    run = start_run(db_session, NOW - timedelta(minutes=2))
    finish_run(db_session, run, "ok", n_requests=3, odds_remaining=4000, finished_at=NOW - timedelta(minutes=2))
    client = TestClient(create_app(factory, clock=lambda: NOW))
    r = client.get("/healthz")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok" and body["last_status"] == "ok" and body["credits_remaining"] == 4000
    assert 119 <= body["seconds_since"] <= 121


def test_healthz_stale_is_503(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    run = start_run(db_session, NOW - timedelta(minutes=30))
    finish_run(db_session, run, "ok", finished_at=NOW - timedelta(minutes=30))
    client = TestClient(create_app(factory, clock=lambda: NOW))
    assert client.get("/healthz").status_code == 503
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_health.py -v`
Expected: FAIL with import error

- [ ] **Step 3: Implement health, scheduler, CLI**

`harness/health.py`:
```python
from datetime import datetime, timezone
from typing import Callable

from fastapi import FastAPI, Response
from sqlalchemy import desc
from sqlalchemy.orm import sessionmaker

from harness.db.models import Run

STALE_AFTER_S = 20 * 60


def create_app(session_factory: sessionmaker, clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> FastAPI:
    app = FastAPI(title="harness")

    @app.get("/healthz")
    def healthz(response: Response) -> dict:
        with session_factory() as s:
            last = s.query(Run).order_by(desc(Run.started_at)).first()
        now = clock()
        if last is None:
            response.status_code = 503
            return {"status": "error", "last_run_at": None, "last_status": None, "seconds_since": None,
                    "credits_remaining": None}
        since = (now - last.started_at).total_seconds()
        status = "stale" if since > STALE_AFTER_S else ("error" if last.status == "error" else "ok")
        if status != "ok":
            response.status_code = 503
        return {"status": status, "last_run_at": last.started_at.isoformat(), "last_status": last.status,
                "seconds_since": int(since), "credits_remaining": last.odds_remaining}

    return app
```

`harness/scheduler.py`:
```python
from apscheduler.schedulers.background import BackgroundScheduler

from harness.config.settings import Settings
from harness.db.engine import make_engine, make_session_factory
from harness.feeds.espn import EspnClient
from harness.feeds.http import HttpClient
from harness.feeds.odds_api import OddsApiClient
from harness.recorder.tick import Recorder
from harness.venues.kalshi.public import KalshiPublic


def build_recorder(settings: Settings) -> Recorder:
    http = HttpClient(settings.http_timeout_s)
    odds = OddsApiClient(http, settings.odds_api_base_url, settings.odds_api_key(), settings.odds_api_bookmakers)
    espn = EspnClient(http, settings.espn_base_url)
    kalshi = KalshiPublic(http, settings.kalshi_base_url, settings.kalshi_sleep_s)
    factory = make_session_factory(make_engine(settings.database_url))
    return Recorder(settings, factory, odds, espn, kalshi)


def build_scheduler(recorder: Recorder, heartbeat_s: int) -> BackgroundScheduler:
    sched = BackgroundScheduler(timezone="UTC")
    sched.add_job(recorder.maybe_tick, "interval", seconds=heartbeat_s, id="maybe_tick",
                  max_instances=1, coalesce=True, misfire_grace_time=60)
    return sched
```

`harness/cli.py`:
```python
import logging
import signal
import time

import typer
import uvicorn

from harness.config.settings import get_settings
from harness.db.engine import make_engine, make_session_factory
from harness.db.schema import create_schema
from harness.logging_setup import configure_logging

app = typer.Typer(no_args_is_help=True)
log = logging.getLogger("harness")


@app.command("init-db")
def init_db() -> None:
    configure_logging()
    s = get_settings()
    create_schema(make_engine(s.database_url))
    log.info("schema created")


@app.command("tick-once")
def tick_once() -> None:
    configure_logging()
    from harness.scheduler import build_recorder

    run = build_recorder(get_settings()).maybe_tick()
    log.info("run %s status=%s n=%s credits=%s", run.id, run.status, run.n_requests, run.credits_used)


@app.command("run")
def run() -> None:
    configure_logging()
    from harness.scheduler import build_recorder, build_scheduler

    s = get_settings()
    sched = build_scheduler(build_recorder(s), s.heartbeat_s)
    sched.start()
    stop = {"flag": False}

    def _stop(*_):
        stop["flag"] = True

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    log.info("scheduler started heartbeat=%ss", s.heartbeat_s)
    while not stop["flag"]:
        time.sleep(1)
    sched.shutdown(wait=True)
    log.info("scheduler stopped")


@app.command("serve")
def serve(port: int = 8080, host: str = "0.0.0.0") -> None:
    configure_logging()
    from harness.health import create_app

    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    uvicorn.run(create_app(factory), host=host, port=port, log_config=None)


if __name__ == "__main__":
    app()
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_health.py -v && pytest -q`
Expected: PASS

- [ ] **Step 5: Manual smoke against real APIs (no DB needed for the first two)**

```bash
echo -n "$ODDS_KEY" > secrets/odds_api_key && chmod 600 secrets/odds_api_key
export DATABASE_URL=postgresql+psycopg://harness:harness@localhost:5433/harness_test ODDS_API_KEY_FILE=$PWD/secrets/odds_api_key
harness init-db && harness tick-once
psql "$DATABASE_URL" -c "select id,status,n_requests,credits_used,odds_remaining,budget_exhausted from runs order by id desc limit 3;"
psql "$DATABASE_URL" -c "select source,endpoint,count(*) from raw_responses group by 1,2 order by 3 desc;"
```
Expected: one `ok` run; rows for espn (2), odds_api featured (2) plus alternates for events inside 36 h, kalshi `/markets` (7+ pages), `/markets/trades` for tickers with volume, and orderbooks only if a game is within 3 h.

- [ ] **Step 6: Commit**

```bash
git add harness/scheduler.py harness/health.py harness/cli.py tests/test_health.py
git commit -m "feat: scheduler heartbeat, cli, healthz"
```

---

### Task 12: Docker image, Compose, and NAS deployment

**Files:**
- Create: `Dockerfile`, `docker-compose.yml`, `docs/runbooks/phase0-deploy.md`

**Interfaces:**
- Produces: services `postgres` (16, healthcheck, volume `pgdata`), `app-run` (`harness run`), `app-serve` (`harness serve`, port 8080 bound to `127.0.0.1` on the host), both `restart: unless-stopped`, secrets mounted read-only from `./secrets`.

- [ ] **Step 1: Write the Dockerfile**

```dockerfile
FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=UTC PIP_NO_CACHE_DIR=1
RUN apt-get update && apt-get install -y --no-install-recommends tzdata && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY harness ./harness
RUN pip install .
USER nobody
ENTRYPOINT ["harness"]
```

- [ ] **Step 2: Write docker-compose.yml**

```yaml
services:
  postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: harness
      POSTGRES_PASSWORD: harness
      POSTGRES_DB: harness
    volumes:
      - ./pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U harness -d harness"]
      interval: 10s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  app-run:
    build: .
    command: ["run"]
    env_file: .env
    environment:
      ODDS_API_KEY_FILE: /run/secrets/odds_api_key
    volumes:
      - ./secrets/odds_api_key:/run/secrets/odds_api_key:ro
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped

  app-serve:
    build: .
    command: ["serve", "--port", "8080"]
    env_file: .env
    environment:
      ODDS_API_KEY_FILE: /run/secrets/odds_api_key
    volumes:
      - ./secrets/odds_api_key:/run/secrets/odds_api_key:ro
    ports:
      - "127.0.0.1:8080:8080"
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped
```

- [ ] **Step 3: Write the deploy runbook**

`docs/runbooks/phase0-deploy.md`:
```markdown
# Phase 0 deploy (NAS)

1. Copy the repo to the NAS (git clone or rsync). Confirm `docker compose version` works and note `uname -m` (x86_64 or aarch64; python:3.12-slim is multi-arch).
2. `cp .env.example .env` and keep the Postgres URL as-is.
3. `mkdir -p secrets && printf '%s' "<ODDS_API_KEY>" > secrets/odds_api_key && chmod 600 secrets/odds_api_key`.
4. `docker compose build && docker compose up -d postgres && docker compose run --rm app-run init-db`.
5. `docker compose run --rm app-run tick-once` and read the JSON log line `tick ok n=... credits=...`.
6. `docker compose up -d` then `curl -s localhost:8080/healthz` → `{"status":"ok",...}` within 60 s.
7. Verify data: `docker compose exec postgres psql -U harness -c "select source,endpoint,count(*) from raw_responses group by 1,2 order by 3 desc;"`.
8. Credit check after the first game day: `select date_trunc('day', started_at), sum(credits_used), min(odds_remaining) from runs group by 1 order by 1;` Expect well under 100k/day.
9. Logs: `docker compose logs -f app-run`. Stop: `docker compose down` (data persists in ./pgdata).

Must be running before Wed 2026-09-09 19:20 CT (NE at SEA).
```

- [ ] **Step 4: Build and smoke locally**

Run: `docker compose build && docker compose up -d postgres && docker compose run --rm app-run init-db && docker compose run --rm app-run tick-once && docker compose up -d && sleep 45 && curl -s localhost:8080/healthz`
Expected: build succeeds; tick logs `tick ok`; healthz returns 200 with `"status":"ok"`.

- [ ] **Step 5: Commit**

```bash
git add Dockerfile docker-compose.yml docs/runbooks/phase0-deploy.md
git commit -m "feat: docker image, compose, phase 0 deploy runbook"
```

- [ ] **Step 6: Deploy to the NAS per the runbook and confirm `/healthz` is ok and `raw_responses` is growing. Record the deploy time in `docs/runbooks/phase0-deploy.md` under a "Deployed" heading and commit.**

---

## Self-review

**Spec coverage (phase 0 scope):** §4.1 cadences (Task 9), burst mode (Task 9 `interval_for`), §4.2 job semantics (Task 11 scheduler flags; overlap prevention via `max_instances=1`; budget in Task 10; UTC-only internals with CT in cadence; partitions in Task 3), §5.1 Odds API featured + alternates with `bookmakers=` and credit headers (Task 5, 10), §5.2 Kalshi public endpoints (Task 7), §5.4 ESPN (Task 6), §5.5 `raw_responses`/`runs` with `(source, fetched_at)` and BRIN indexes (Task 3), §13 fetch/parse split with fixtures (Tasks 5–7), §14 secrets as file mounts, redaction, `restart: unless-stopped`, Postgres healthcheck, two processes from one image (Tasks 2, 12), §15 phase 0 deliverable (Task 12). Clock-skew check and WS recorder are phase 1 per spec §15. Encrypted backups are phase 4.

**Deviations from spec, deliberate:** ladder selection uses event date and price band rather than "games inside T−3h" because game matching does not exist until phase 1; the superset is bounded by the 400 cap and the "any kickoff within 3 h" gate. Alternates are polled for all events within 36 h rather than only events with a Kalshi rung in band, for the same reason.

**Placeholder scan:** none.

**Type consistency:** `FetchResult` fields used identically in Tasks 4–10; `MarketSummary` field order matches between Task 7 and Task 9 tests; `store.finish_run` keyword names match Task 10 and Task 11 usage; `Recorder.__init__` signature matches `build_recorder` in Task 11 and `_recorder` in Task 10 tests.
