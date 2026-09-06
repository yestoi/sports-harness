# Phase 1 Normalize-and-Match Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the phase 0 raw store into normalized, rebuildable tables: canonical teams and games, every sportsbook line, every Kalshi contract typed and matched to a game with a confidence score, every Kalshi quote, ladder, and trade print, plus a `harness reprocess` command that rebuilds all of it from `raw_responses`, a `harness match-report` that shows Week 1 match rate, and (conditional on a Kalshi API key) an append-only WebSocket recorder.

**Architecture:** Every normalizer is a pure `parse_*(raw_body, ...) -> [rows]` over one raw response kind, driven by an idempotent runner that walks `raw_responses` by id per source family and upserts with `ON CONFLICT DO NOTHING`, tracking progress in `normalize_state`. Team identity is the ESPN team id; aliases from ESPN, The Odds API, and Kalshi (names and Kalshi's per-team UUID) resolve names to ids, with a committed manual override file. Kalshi events match to games by unordered team pair plus date within one day. The recorder gains a Kalshi events fetch so titles are recorded. The tick calls the normalizer after each commit so normalized tables trail raw by one tick.

**Tech Stack:** Python 3.12, SQLAlchemy 2 (sync), psycopg 3, Postgres 16, pytest + respx, `websocket-client` (sync) and `cryptography` for the conditional WS task.

**Spec:** `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2) sections 4, 4.1, 5.1, 5.2, 5.3, 5.4, 5.5, 13, 15 phase 1. Phase 0 plan and ledger: `docs/superpowers/plans/2026-09-06-phase0-recorder.md`, `docs/superpowers/reviews/`.

## Global Constraints

- Python 3.12; sync code only. No asyncio (the WS task uses the sync `websocket-client` library).
- All timestamps tz-aware UTC in the DB; local time only inside cadence functions.
- `raw_responses` is never modified. Every normalized table is rebuildable from raw via `harness reprocess`; normalizers are pure functions over raw bodies; upserts are idempotent (unique constraints + `ON CONFLICT DO NOTHING` or `DO UPDATE`).
- Team identity = (sport, ESPN team id). ESPN ids are unique only within a sport (UCLA and the Seattle Seahawks are both id 26), so `teams` has a composite primary key and every alias and lookup is scoped by sport. `games.odds_api_event_id` is the canonical game identity (spec §5.5); ESPN and Kalshi map onto it.
- Kalshi markets: threshold from `floor_strike`, side from `yes_sub_title`/`custom_strike.football_team`, game from the event `title` split on " vs " plus the ticker date; ticker parsing only lowers confidence (spec §5.3). Confidence 1.0 only when both teams alias-resolve exactly and a unique game with that pair exists within ±1 day. Anything else is `unmatched` or `fuzzy` (< 1.0) and is never traded by later phases.
- Alias table must cover all 32 NFL and all FBS teams from ESPN on day one (spec §5.3).
- Kalshi WebSocket requires RSA-PSS signed headers even for public channels; the WS task is conditional on `secrets/kalshi_key_id` and `secrets/kalshi_private_key.pem` existing.
- Test output pristine. Commit after every task with the trailer:
  ```
  Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01Wp1gV1T1tYrgYDP3EJALci
  ```
- Dev environment: venv `.venv/` (uv, Python 3.12.13); `DATABASE_URL_TEST=postgresql+psycopg://harness:harness@localhost:5433/harness_test`; container `harness-pg-test`.

## Verified API facts (2026-09-06)

- **ESPN teams:** `GET https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=1000&groups=80` → `sports[0].leagues[0].teams[].team{id, abbreviation, displayName, shortDisplayName, name, nickname, location, slug}` (759 teams; `groups=80` returns the full list including FCS, verified 2026-09-06, so one pass suffices). NFL: `.../football/nfl/teams?limit=100` (32). Example NCAAF: `{'id': '2000', 'abbreviation': 'ACU', 'displayName': 'Abilene Christian Wildcats', 'shortDisplayName': 'Abilene Chrstn', 'name': 'Wildcats', 'location': 'Abilene Christian'}`. NFL: `{'id': '22', 'abbreviation': 'ARI', 'displayName': 'Arizona Cardinals', 'location': 'Arizona', 'name': 'Cardinals'}`. ESPN uses accents in some locations (e.g. "San José State").
- **Kalshi events:** `GET /events?series_ticker=KXNCAAFGAME&status=open&limit=200&cursor=` → `{events:[{event_ticker, series_ticker, title, sub_title, product_metadata, ...}], cursor}`. Titles: `"Alabama vs Kentucky"`, `"Alabama St. vs Troy"`, `"Arkansas-Pine Bluff vs Alcorn St."`, `"NY Giants vs LA Rams"`; sub_title `"ALA vs UK (Sep 12)"`. Event ticker date `26SEP12` (see phase 0 `event_date_from_ticker`).
- **Kalshi markets** (from phase 0): `yes_sub_title` is a short team name (`"UCLA"`, `"San Jose St."`, `"New York G"`), `custom_strike.football_team` is a stable per-team UUID on game and spread markets, `strike_type` `greater` with `floor_strike` `6.5` on spreads/totals, `structured` on game winners. Spread title `"Kansas City wins by over 6.5 points?"`; total title `"over 63.5 points?"` shape (verify exact wording from recorded raw data in Task 1).
- **Kalshi trades REST** (phase 0): `trade_id, ticker, created_time, count_fp, yes_price_dollars, no_price_dollars, taker_side, is_block_trade`.
- **Kalshi WS (verified live 2026-09-06 with a production key):** `wss://api.elections.kalshi.com/trade-api/ws/v2` works with the signed headers below (signature path `/trade-api/ws/v2`); keep `wss://external-api-ws.kalshi.com/` only as a fallback. The subscribe ack is `{"type":"subscribed","id":1,"msg":{"channel":"trade","sid":1}}`, one ack per channel, with `sid` INSIDE `msg`. An `orderbook_snapshot` arrives per ticker with `seq` starting at 1 for that sid, then deltas. Auth headers `KALSHI-ACCESS-KEY`, `KALSHI-ACCESS-TIMESTAMP` (ms), `KALSHI-ACCESS-SIGNATURE` = base64(RSA-PSS-SHA256 over `f"{timestamp}{METHOD}{path}"`) with `GET` and path `/trade-api/ws/v2`. Subscribe: `{"id": 1, "cmd": "subscribe", "params": {"channels": ["trade", "orderbook_delta"], "market_tickers": [...]}}` → one `{"type": "subscribed", "id": 1, "msg": {"channel": "trade", "sid": 1}}` per channel; `update_subscription` with `action` `add_markets`/`delete_markets` and `sids`. Server pings every 10 s; client must pong. Messages: `orderbook_snapshot {market_ticker, yes_dollars_fp: [[price, qty]...], no_dollars_fp}` first, then `orderbook_delta {market_ticker, price_dollars, delta_fp, side, ts_ms}`; `trade {trade_id, market_ticker, yes_price_dollars, no_price_dollars, count_fp, taker_side, is_block_trade, ts_ms}`; every message carries `sid` and `seq` (gaps = lost updates).
- **The Odds API names (measured 2026-09-06 on a live response):** all 32 NFL names equal ESPN `displayName`. Of 115 NCAAF names seen, 111 match ESPN display/location/short names after normalization; the 4 misses (App State, Hawai'i, Southern Miss, Sam Houston) are pre-seeded in `aliases_manual.yaml`. New misses surface in the match report.

## File structure

```
harness/db/models.py                 + Team, TeamAlias, Game, OddsSnapshot, VenueMarket, VenueQuote,
                                       OrderbookSnapshot, VenueTrade, OrderbookEvent, NormalizeState
harness/db/schema.py                 + indexes for the new tables
harness/matching/__init__.py
harness/matching/names.py            normalize_name (pure)
harness/matching/teams.py            seed_teams_from_espn, resolve_team, learn_alias, manual alias loading
harness/matching/aliases_manual.yaml committed overrides: {sport: {source: {raw_name: espn_team_id}}}
harness/matching/games.py            upsert_games_from_odds, link_espn_events, find_game_by_pair
harness/matching/kalshi.py           parse_event_title, classify_market, match_event
harness/normalize/__init__.py
harness/normalize/odds.py            parse_odds_body -> [OddsRow]; upsert
harness/normalize/espn.py            parse_scoreboard -> game links + status/scores
harness/normalize/kalshi.py          parse_markets_page, parse_orderbook, parse_trades_page; upserts
harness/normalize/runner.py          normalize_new(session) walks raw by source family; reprocess(session, from_raw_id)
harness/recorder/tick.py             + Kalshi events fetch (15 min); call normalize_new after each checkpoint
harness/venues/kalshi/public.py      + fetch_events_all(series)
harness/venues/kalshi/auth.py        sign_request(key_id, private_key_pem, method, path, ts_ms) -> headers
harness/venues/kalshi/ws.py          WsRecorder (sync websocket-client), append-only
harness/cli.py                       + seed-teams, reprocess, match-report, ws-record
tests/fixtures/espn_teams_*.json, kalshi_events_page.json, kalshi_markets_typed.json, kalshi_orderbook.json,
              kalshi_trades_page.json, odds_alternates_event.json
tests/test_names.py, test_teams.py, test_games.py, test_matching_kalshi.py, test_normalize_odds.py,
      test_normalize_kalshi.py, test_normalize_espn.py, test_runner.py, test_kalshi_events.py,
      test_kalshi_auth.py, test_kalshi_ws.py
```

---

### Task 1: Record Kalshi events in the tick, and capture typed-market fixtures from live data

**Files:**
- Modify: `harness/venues/kalshi/public.py` (add `fetch_events_all`)
- Modify: `harness/recorder/tick.py` (`_kalshi_events` source; key `kalshi_events:<series>`, interval 900 s always, no quiet-hours suppression needed since it is cheap)
- Create: `tests/fixtures/kalshi_events_page.json`, `tests/fixtures/kalshi_markets_typed.json`
- Test: `tests/test_kalshi_events.py`, extend `tests/test_tick.py`

**Interfaces:**
- Produces: `KalshiPublic.fetch_events_all(series_ticker, max_pages=10) -> list[FetchResult]` requesting `/events?series_ticker=X&status=open&limit=200[&cursor]`. Raw rows stored with `source="kalshi"`, `endpoint="/events"`, `params={"series_ticker": X}`. Non-200 pages are primary-source errors (same rule as markets).

- [ ] **Step 1: Capture fixtures from the live API (no key needed)**

```bash
K=https://api.elections.kalshi.com/trade-api/v2
curl -s "$K/events?series_ticker=KXNCAAFGAME&status=open&limit=3" | python3 -m json.tool > tests/fixtures/kalshi_events_page.json
python3 - <<'EOF'
import json, urllib.request
K="https://api.elections.kalshi.com/trade-api/v2"
out=[]
for s in ["KXNFLGAME","KXNFLSPREAD","KXNFLTOTAL","KXNCAAFGAME","KXNCAAFSPREAD","KXNCAAFTOTAL"]:
    d=json.load(urllib.request.urlopen(f"{K}/markets?series_ticker={s}&status=open&limit=2"))
    out.extend(d["markets"][:2])
json.dump({"cursor":"","markets":out}, open("tests/fixtures/kalshi_markets_typed.json","w"), indent=1)
print(len(out))
EOF
```
Then open `tests/fixtures/kalshi_markets_typed.json` and record, in a comment block at the top of `tests/test_matching_kalshi.py` (Task 5), the exact `title` wording for a total market and whether `custom_strike` is present on totals. Trim the fixture to 12 markets if larger.

- [ ] **Step 2: Write the failing tests**

`tests/test_kalshi_events.py`:
```python
import json
from pathlib import Path

import httpx
import respx

from harness.feeds.http import HttpClient
from harness.venues.kalshi.public import KalshiPublic

FIX = json.loads((Path(__file__).parent / "fixtures" / "kalshi_events_page.json").read_text())


@respx.mock
def test_fetch_events_all_follows_cursor():
    p1 = {"cursor": "c2", "events": FIX["events"][:1]}
    p2 = {"cursor": "", "events": FIX["events"][1:2]}
    route = respx.get("https://k/events").mock(side_effect=[httpx.Response(200, json=p1), httpx.Response(200, json=p2)])
    c = KalshiPublic(HttpClient(1, sleep=lambda s: None), "https://k", sleep_s=0, sleep=lambda s: None)
    pages = c.fetch_events_all("KXNCAAFGAME")
    assert len(pages) == 2
    q0 = dict(route.calls[0].request.url.params)
    assert q0 == {"series_ticker": "KXNCAAFGAME", "status": "open", "limit": "200"}
    assert dict(route.calls[1].request.url.params)["cursor"] == "c2"
```

Add to `tests/test_tick.py`:
```python
@respx.mock
def test_tick_records_kalshi_events(env_settings, db_session):
    respx.get(url__regex=r"https://e/.*").mock(return_value=httpx.Response(200, json={"events": []}))
    respx.get(url__regex=r"https://o/.*").mock(return_value=httpx.Response(200, json=[]))
    respx.get("https://k/markets").mock(return_value=httpx.Response(200, json={"cursor": "", "markets": []}))
    ev = respx.get("https://k/events").mock(return_value=httpx.Response(200, json={"cursor": "", "events": []}))
    rec, clock = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert ev.call_count == 6
    assert db_session.query(RawResponse).filter_by(run_id=run.id, source="kalshi", endpoint="/events").count() == 6
    clock["now"] = NOW + timedelta(seconds=60)
    rec.maybe_tick()
    assert ev.call_count == 6  # 15-minute interval, not due
```

- [ ] **Step 3: Run to verify they fail**

Run: `DATABASE_URL_TEST=... .venv/bin/pytest tests/test_kalshi_events.py tests/test_tick.py -v`
Expected: `AttributeError: fetch_events_all` and the tick test failing on `ev.call_count == 6`.

- [ ] **Step 4: Implement**

In `harness/venues/kalshi/public.py` add (mirroring `fetch_markets_all`):
```python
    def fetch_events_all(self, series_ticker: str, max_pages: int = 10) -> list[FetchResult]:
        pages: list[FetchResult] = []
        cursor = ""
        for _ in range(max_pages):
            params = {"series_ticker": series_ticker, "status": "open", "limit": "200"}
            if cursor:
                params["cursor"] = cursor
            r = self._http.get(f"{self._base}/events", params=params, redact_params=())
            pages.append(r)
            cursor = (r.body or {}).get("cursor", "") if isinstance(r.body, dict) else ""
            self._pause()
            if not cursor or r.status != 200:
                break
        return pages
```

In `harness/recorder/tick.py` add a method `_kalshi_events(self, session, run, now, ctx)` structured exactly like `_kalshi_markets` (per-series try/except, key `f"kalshi_events:{series}"`, `is_due(..., 900)`, store each page with endpoint `"/events"`, set state only when all pages are 200, else append `{key: "partial pagination: ..."}` to `ctx["errors"]`), and call it in `maybe_tick` right after `_kalshi_markets` with its own checkpoint commit.

- [ ] **Step 5: Run the tests, then the full suite**

Run: `DATABASE_URL_TEST=... .venv/bin/pytest -q`
Expected: all pass, no warnings.

- [ ] **Step 6: Commit**

```bash
git add harness/venues/kalshi/public.py harness/recorder/tick.py tests/
git commit -m "feat: record kalshi events; typed-market fixtures"
```

---

### Task 2: Normalized schema

**Files:**
- Modify: `harness/db/models.py`, `harness/db/schema.py`
- Test: `tests/test_schema_phase1.py`

**Interfaces:**
- Produces ORM models (all `Base`):
  - `Team(sport: str, id: int [ESPN id], display_name, location, name, abbreviation, short_display_name, popularity_tier: int = 0)` composite PK `(sport, id)`.
  - `TeamAlias(sport: str, source: str, raw_name: str, team_id: int)` PK `(sport, source, raw_name)`; index on `(sport, team_id)`.
  - `Game(id: int PK autoincrement, sport, home_team_id, away_team_id, kickoff_utc, odds_api_event_id: str | None unique, espn_event_id: str | None unique, status: str = "scheduled", home_score: int | None, away_score: int | None)`; unique `(sport, home_team_id, away_team_id, kickoff_date)` is NOT enforced (rescheduled games); index `(sport, kickoff_utc)`.
  - `OddsSnapshot(id BigInteger PK, raw_id, run_id, book, game_id, market_type ∈ {h2h, spreads, totals, alternate_spreads, alternate_totals}, outcome_team_id: int | None, outcome_side: str | None ∈ {over, under}, point: Numeric(6,1) | None, price_decimal: Numeric(10,4), book_last_update, fetched_at)`; unique `(raw_id, book, market_type, outcome_team_id, outcome_side, point)` implemented as a unique index on `(raw_id, book, market_type, coalesce(outcome_team_id,-1), coalesce(outcome_side,''), coalesce(point, 0))`; index `(game_id, market_type, fetched_at)`.
  - `VenueMarket(id PK, venue: str, ticker unique, event_ticker, series_ticker, game_id: int | None, market_type ∈ {moneyline, spread, total}, threshold: Numeric(6,1) | None, side_team_id: int | None, side: str | None ∈ {over}, kalshi_team_uuid: str | None, close_time, match_confidence: Numeric(3,2) = 0, match_status ∈ {matched, fuzzy, unmatched, manual}, match_reason: str, first_seen_raw_id, last_seen_at)`.
  - `VenueQuote(id BigInteger PK, raw_id, run_id, venue_market_id, yes_bid, yes_ask, no_bid, no_ask, yes_bid_size, yes_ask_size, volume, volume_24h, open_interest, updated_time, fetched_at)` unique `(raw_id, venue_market_id)`; index `(venue_market_id, fetched_at)`.
  - `OrderbookSnapshot(id BigInteger PK, raw_id unique, venue_market_id, fetched_at, yes_bids JSONB, no_bids JSONB)`; index `(venue_market_id, fetched_at)`.
  - `VenueTrade(venue, trade_id PK, ticker, ts, yes_price: Numeric(6,4), count: Numeric(12,2), taker_side, is_block: bool, source ∈ {rest, ws}, raw_id: int | None)`; index `(ticker, ts)`.
  - `OrderbookEvent(id BigInteger PK, ticker, ts, sid, seq, kind ∈ {snapshot, delta}, side, price, delta, raw JSONB)`; index `(ticker, ts)`.
  - `NormalizeState(family: str PK, last_raw_id: int)`.
- `create_schema` creates all, plus the indexes above; `drop_schema` drops the new tables too.

- [ ] **Step 1: Write the failing test**

`tests/test_schema_phase1.py`:
```python
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy import text

from harness.db.models import Game, OddsSnapshot, Team, TeamAlias, VenueMarket, VenueTrade

NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def test_phase1_tables_exist_and_roundtrip(db_session):
    db_session.add(Team(id=26, sport="ncaaf", display_name="UCLA Bruins", location="UCLA", name="Bruins",
                        abbreviation="UCLA", short_display_name="UCLA"))
    db_session.add(TeamAlias(sport="ncaaf", source="kalshi_name", raw_name="UCLA", team_id=26))
    g = Game(sport="ncaaf", home_team_id=26, away_team_id=26, kickoff_utc=NOW, odds_api_event_id="e1")
    db_session.add(g)
    db_session.flush()
    db_session.add(VenueMarket(venue="kalshi", ticker="T", event_ticker="E", series_ticker="KXNCAAFGAME", game_id=g.id,
                               market_type="moneyline", side_team_id=26, match_confidence=Decimal("1.00"),
                               match_status="matched", match_reason="pair+date", first_seen_raw_id=1, last_seen_at=NOW))
    db_session.add(VenueTrade(venue="kalshi", trade_id="t1", ticker="T", ts=NOW, yes_price=Decimal("0.2300"),
                              count=Decimal("5.00"), taker_side="yes", is_block=False, source="rest", raw_id=None))
    db_session.flush()
    names = set(db_session.execute(text("select tablename from pg_tables where schemaname='public'")).scalars())
    assert {"teams", "team_aliases", "games", "odds_snapshots", "venue_markets", "venue_quotes",
            "orderbook_snapshots", "venue_trades", "orderbook_events", "normalize_state"} <= names


def test_odds_snapshot_unique_index_treats_nulls_as_equal(db_session):
    from sqlalchemy.exc import IntegrityError
    import pytest
    row = dict(raw_id=1, run_id=1, book="pinnacle", game_id=None, market_type="h2h", outcome_team_id=None,
               outcome_side=None, point=None, price_decimal=Decimal("1.9"), book_last_update=NOW, fetched_at=NOW)
    db_session.add(OddsSnapshot(**row))
    db_session.flush()
    db_session.add(OddsSnapshot(**row))
    with pytest.raises(IntegrityError):
        db_session.flush()
```

- [ ] **Step 2: Run to verify it fails**

Run: `DATABASE_URL_TEST=... .venv/bin/pytest tests/test_schema_phase1.py -v`
Expected: ImportError on the new models.

- [ ] **Step 3: Implement the models and indexes**

Append to `harness/db/models.py` (imports already present: `BigInteger, Boolean, DateTime, Integer, Numeric, String, Text, JSONB`; add `Index, ForeignKey` if used):
```python
class Team(Base):
    __tablename__ = "teams"
    sport: Mapped[str] = mapped_column(String(8), primary_key=True)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)  # ESPN team id, unique only within a sport
    display_name: Mapped[str] = mapped_column(String(128), nullable=False)
    location: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    abbreviation: Mapped[str] = mapped_column(String(16), nullable=False)
    short_display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    popularity_tier: Mapped[int] = mapped_column(Integer, default=0, nullable=False)


class TeamAlias(Base):
    __tablename__ = "team_aliases"
    sport: Mapped[str] = mapped_column(String(8), primary_key=True)
    source: Mapped[str] = mapped_column(String(32), primary_key=True)
    raw_name: Mapped[str] = mapped_column(String(160), primary_key=True)
    team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (Index("ix_alias_sport_team", "sport", "team_id"),)


class Game(Base):
    __tablename__ = "games"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    sport: Mapped[str] = mapped_column(String(8), nullable=False)
    home_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    away_team_id: Mapped[int] = mapped_column(Integer, nullable=False)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    odds_api_event_id: Mapped[str | None] = mapped_column(String(64), unique=True)
    espn_event_id: Mapped[str | None] = mapped_column(String(32), unique=True)
    status: Mapped[str] = mapped_column(String(24), default="scheduled", nullable=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    __table_args__ = (Index("ix_games_sport_kick", "sport", "kickoff_utc"),)


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    book: Mapped[str] = mapped_column(String(32), nullable=False)
    game_id: Mapped[int | None] = mapped_column(Integer)
    market_type: Mapped[str] = mapped_column(String(24), nullable=False)
    outcome_team_id: Mapped[int | None] = mapped_column(Integer)
    outcome_side: Mapped[str | None] = mapped_column(String(8))
    point: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    price_decimal: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    book_last_update: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (Index("ix_odds_game_type_fetched", "game_id", "market_type", "fetched_at"),)


class VenueMarket(Base):
    __tablename__ = "venue_markets"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    venue: Mapped[str] = mapped_column(String(16), nullable=False)
    ticker: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    event_ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    series_ticker: Mapped[str] = mapped_column(String(32), nullable=False)
    game_id: Mapped[int | None] = mapped_column(Integer, index=True)
    market_type: Mapped[str] = mapped_column(String(16), nullable=False)
    threshold: Mapped[Decimal | None] = mapped_column(Numeric(6, 1))
    side_team_id: Mapped[int | None] = mapped_column(Integer)
    side: Mapped[str | None] = mapped_column(String(8))
    kalshi_team_uuid: Mapped[str | None] = mapped_column(String(64))
    close_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    match_confidence: Mapped[Decimal] = mapped_column(Numeric(3, 2), default=0, nullable=False)
    match_status: Mapped[str] = mapped_column(String(16), default="unmatched", nullable=False)
    match_reason: Mapped[str] = mapped_column(Text, default="", nullable=False)
    first_seen_raw_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class VenueQuote(Base):
    __tablename__ = "venue_quotes"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    run_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    yes_bid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    yes_ask: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    no_bid: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    no_ask: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    yes_bid_size: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    yes_ask_size: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    volume: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    volume_24h: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    open_interest: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    updated_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    __table_args__ = (UniqueConstraint("raw_id", "venue_market_id", name="uq_quote_raw_market"),
                      Index("ix_quotes_market_fetched", "venue_market_id", "fetched_at"))


class OrderbookSnapshot(Base):
    __tablename__ = "orderbook_snapshots"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    raw_id: Mapped[int] = mapped_column(BigInteger, unique=True, nullable=False)
    venue_market_id: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    yes_bids: Mapped[list] = mapped_column(JSONB, nullable=False)
    no_bids: Mapped[list] = mapped_column(JSONB, nullable=False)
    __table_args__ = (Index("ix_ob_market_fetched", "venue_market_id", "fetched_at"),)


class VenueTrade(Base):
    __tablename__ = "venue_trades"
    venue: Mapped[str] = mapped_column(String(16), primary_key=True)
    trade_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    yes_price: Mapped[Decimal] = mapped_column(Numeric(6, 4), nullable=False)
    count: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    taker_side: Mapped[str] = mapped_column(String(4), nullable=False)
    is_block: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    source: Mapped[str] = mapped_column(String(4), nullable=False)
    raw_id: Mapped[int | None] = mapped_column(BigInteger)
    __table_args__ = (Index("ix_trades_ticker_ts", "ticker", "ts"),)


class OrderbookEvent(Base):
    __tablename__ = "orderbook_events"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    ticker: Mapped[str] = mapped_column(String(64), nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sid: Mapped[int] = mapped_column(Integer, nullable=False)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    side: Mapped[str | None] = mapped_column(String(4))
    price: Mapped[Decimal | None] = mapped_column(Numeric(6, 4))
    delta: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    raw: Mapped[dict] = mapped_column(JSONB, nullable=False)
    __table_args__ = (Index("ix_obe_ticker_ts", "ticker", "ts"),)


class NormalizeState(Base):
    __tablename__ = "normalize_state"
    family: Mapped[str] = mapped_column(String(32), primary_key=True)
    last_raw_id: Mapped[int] = mapped_column(BigInteger, default=0, nullable=False)
```
(Add `from decimal import Decimal` and `from sqlalchemy import Index, UniqueConstraint` to the imports.)

In `harness/db/schema.py` `create_schema`, after the existing statements add:
```python
        conn.execute(text(
            "create unique index if not exists uq_odds_snapshot_row on odds_snapshots "
            "(raw_id, book, market_type, coalesce(outcome_team_id, -1), coalesce(outcome_side, ''), coalesce(point, 0))"))
```
and in `drop_schema` add the new table names to the drop list.

- [ ] **Step 4: Run the tests**

Run: `DATABASE_URL_TEST=... .venv/bin/pytest tests/test_schema_phase1.py tests/test_schema.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add harness/db tests/test_schema_phase1.py
git commit -m "feat: phase 1 normalized schema"
```

---

### Task 3: Name normalization and the team alias registry

**Files:**
- Create: `harness/matching/__init__.py`, `harness/matching/names.py`, `harness/matching/teams.py`, `harness/matching/aliases_manual.yaml`
- Create: `tests/fixtures/espn_teams_nfl.json` (trimmed to 4 teams), `tests/fixtures/espn_teams_ncaaf.json` (trimmed to 8 teams incl. "San José State", "Alabama State", "Arkansas-Pine Bluff", "LSU", "UCLA")
- Test: `tests/test_names.py`, `tests/test_teams.py`
- Modify: `pyproject.toml` (add `pyyaml>=6`)

**Interfaces:**
- `normalize_name(s: str) -> str` (pure): NFKD-strip accents, lowercase, replace `&` with `and`, expand leading `ny `→`new york `, `la `→`los angeles `, `sf `→`san francisco `, `st.`/`st ` (as a word) → `state` when NOT the first token (so "St. John's" stays), strip everything except `[a-z0-9 ]`, collapse spaces, strip. Examples: `"San José State"`→`"san jose state"`; `"Alabama St."`→`"alabama state"`; `"NY Giants"`→`"new york giants"`; `"Arkansas-Pine Bluff"`→`"arkansas pine bluff"`; `"Texas A&M"`→`"texas a and m"`.
- `seed_teams_from_espn(session, sport, body) -> int` (rows upserted): inserts `Team` rows keyed by ESPN id and aliases with sources `espn_display`, `espn_short`, `espn_location`, `espn_abbr`, `espn_slug`, each `raw_name` stored as the normalized form. Returns number of teams.
- `load_manual_aliases(session, path) -> int`: YAML `{sport: {source: {raw_name: team_id}}}` inserted with source prefixed `manual:`; upsert.
- `resolve_team(session, sport, raw_name, sources: tuple[str, ...] = ALL) -> tuple[int | None, str]`: normalized exact lookup across the given sources in priority order `manual:* → kalshi_uuid → kalshi_name → odds_api → espn_display → espn_location → espn_short → espn_abbr → espn_slug`; returns `(team_id, matched_source)` or `(None, "")`. Exact only; no fuzzy here.
- `learn_alias(session, sport, source, raw_name, team_id)`: upsert with normalized raw_name (used for `kalshi_name`, `kalshi_uuid`, `odds_api`).
- `resolve_fuzzy(session, sport, raw_name) -> tuple[int | None, float]`: token-set match against `espn_display`/`espn_location` aliases using `difflib.SequenceMatcher` ratio on normalized strings, returning the best `(team_id, ratio)` when ratio ≥ 0.90 and the runner-up is < 0.85, else `(None, best_ratio)`.

- [ ] **Step 1: Write failing tests**

`tests/test_names.py`:
```python
from harness.matching.names import normalize_name


def test_normalize_examples():
    assert normalize_name("San José State") == "san jose state"
    assert normalize_name("Alabama St.") == "alabama state"
    assert normalize_name("NY Giants") == "new york giants"
    assert normalize_name("LA Rams") == "los angeles rams"
    assert normalize_name("Arkansas-Pine Bluff") == "arkansas pine bluff"
    assert normalize_name("Texas A&M") == "texas a and m"
    assert normalize_name("St. John's") == "st johns"
    assert normalize_name("  Kansas  City ") == "kansas city"
```

`tests/test_teams.py`:
```python
import json
from pathlib import Path

from harness.matching.teams import learn_alias, load_manual_aliases, resolve_fuzzy, resolve_team, seed_teams_from_espn

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
NCAAF = json.loads((FIXD / "espn_teams_ncaaf.json").read_text())


def test_seed_and_resolve_exact(db_session):
    assert seed_teams_from_espn(db_session, "nfl", NFL) == 6
    assert seed_teams_from_espn(db_session, "ncaaf", NCAAF) == 8
    tid, src = resolve_team(db_session, "nfl", "New York Giants")
    assert tid == 19 and src == "espn_display"
    tid, src = resolve_team(db_session, "nfl", "NY Giants")
    assert tid == 19 and src == "espn_display"  # normalization expands NY
    tid, src = resolve_team(db_session, "ncaaf", "San Jose St.")
    assert tid == 23 and src == "espn_location"
    assert resolve_team(db_session, "ncaaf", "Nowhere Tech") == (None, "")


def test_learn_and_manual(db_session, tmp_path):
    seed_teams_from_espn(db_session, "nfl", NFL)
    learn_alias(db_session, "nfl", "kalshi_name", "New York G", 19)
    assert resolve_team(db_session, "nfl", "New York G") == (19, "kalshi_name")
    (tmp_path / "m.yaml").write_text("nfl:\n  kalshi_name:\n    'Jints': 19\n")
    assert load_manual_aliases(db_session, tmp_path / "m.yaml") == 1
    assert resolve_team(db_session, "nfl", "Jints") == (19, "manual:kalshi_name")


def test_fuzzy(db_session):
    seed_teams_from_espn(db_session, "ncaaf", NCAAF)
    tid, ratio = resolve_fuzzy(db_session, "ncaaf", "Arkansas Pine-Bluff Golden Lions")
    assert tid == 2029 and ratio >= 0.9
    tid, ratio = resolve_fuzzy(db_session, "ncaaf", "Zzz")
    assert tid is None
```
Fixture requirements (ids verified 2026-09-06): NFL fixture contains the Giants 19, Rams 14, Seahawks 26, Patriots 17, Chiefs 12, Saints 18; NCAAF fixture contains San José State 23, Arkansas-Pine Bluff 2029, Alabama State 2011, LSU 99, UCLA 26, Purdue 2509, Kentucky 96, Alabama 333. UCLA and the Seahawks share id 26 across sports, which is why every table is sport-scoped.

- [ ] **Step 2: Build the fixtures from the live ESPN endpoint**

```bash
python3 - <<'EOF'
import json, urllib.request
def grab(url, keep):
    d = json.load(urllib.request.urlopen(url))
    teams = [t for t in d["sports"][0]["leagues"][0]["teams"] if t["team"]["displayName"] in keep]
    d["sports"][0]["leagues"][0]["teams"] = teams
    return d
nfl = grab("https://site.api.espn.com/apis/site/v2/sports/football/nfl/teams?limit=100",
           {"New York Giants", "Los Angeles Rams", "New Orleans Saints", "Kansas City Chiefs", "Seattle Seahawks", "New England Patriots"})
json.dump(nfl, open("tests/fixtures/espn_teams_nfl.json", "w"), indent=1)
keep = {"San José State Spartans", "Alabama State Hornets", "Arkansas-Pine Bluff Golden Lions", "LSU Tigers",
        "UCLA Bruins", "Purdue Boilermakers", "Kentucky Wildcats", "Alabama Crimson Tide"}
nc = grab("https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams?limit=1000&groups=80", keep)
json.dump(nc, open("tests/fixtures/espn_teams_ncaaf.json", "w"), indent=1)
for t in nc["sports"][0]["leagues"][0]["teams"]: print(t["team"]["id"], t["team"]["displayName"])
EOF
```
If a name in `keep` does not match exactly (ESPN spelling), print all displayNames containing the location and adjust `keep`. The `groups=80` list already includes FCS teams (759 total, verified), so one production seeding pass covers FCS opponents that appear on Kalshi.

- [ ] **Step 3: Run to verify failure**

Run: `DATABASE_URL_TEST=... .venv/bin/pytest tests/test_names.py tests/test_teams.py -v`
Expected: ImportError.

- [ ] **Step 4: Implement**

`harness/matching/names.py`:
```python
import re
import unicodedata

_PREFIX = {"ny ": "new york ", "la ": "los angeles ", "sf ": "san francisco ", "nyc ": "new york "}
_ST = re.compile(r"\bst\.?(?=\s|$)")


def normalize_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    s = s.lower().replace("&", " and ")
    s = re.sub(r"\s+", " ", s).strip()
    for k, v in _PREFIX.items():
        if s.startswith(k):
            s = v + s[len(k):]
            break
    tokens = s.split(" ")
    if len(tokens) > 1:
        tokens = [tokens[0]] + [("state" if _ST.fullmatch(t) else t) for t in tokens[1:]]
    s = " ".join(tokens)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()
```
(Token rule: "st." is expanded to "state" only when it is not the first token, so "St. John's" keeps "st".)

`harness/matching/teams.py`:
```python
from difflib import SequenceMatcher
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Team, TeamAlias
from harness.matching.names import normalize_name

PRIORITY = ("kalshi_uuid", "kalshi_name", "odds_api", "espn_display", "espn_location", "espn_short", "espn_abbr", "espn_slug")


def _upsert_alias(session: Session, sport: str, source: str, raw_name: str, team_id: int) -> None:
    key = raw_name if source == "kalshi_uuid" else normalize_name(raw_name)
    if not key:
        return
    stmt = insert(TeamAlias).values(sport=sport, source=source, raw_name=key, team_id=team_id)
    session.execute(stmt.on_conflict_do_update(index_elements=["sport", "source", "raw_name"], set_={"team_id": team_id}))


def seed_teams_from_espn(session: Session, sport: str, body: dict) -> int:
    n = 0
    for entry in body["sports"][0]["leagues"][0]["teams"]:
        t = entry["team"]
        tid = int(t["id"])
        vals = dict(display_name=t["displayName"], location=t.get("location", ""), name=t.get("name", ""),
                    abbreviation=t.get("abbreviation", ""), short_display_name=t.get("shortDisplayName", ""))
        stmt = insert(Team).values(sport=sport, id=tid, **vals)
        session.execute(stmt.on_conflict_do_update(index_elements=["sport", "id"], set_=vals))
        _upsert_alias(session, sport, "espn_display", t["displayName"], tid)
        _upsert_alias(session, sport, "espn_short", t.get("shortDisplayName", ""), tid)
        _upsert_alias(session, sport, "espn_location", t.get("location", ""), tid)
        _upsert_alias(session, sport, "espn_abbr", t.get("abbreviation", ""), tid)
        _upsert_alias(session, sport, "espn_slug", (t.get("slug") or "").replace("-", " "), tid)
        n += 1
    session.flush()
    return n


def load_manual_aliases(session: Session, path: Path) -> int:
    data = yaml.safe_load(Path(path).read_text()) or {}
    n = 0
    for sport, by_source in data.items():
        for source, names in (by_source or {}).items():
            for raw_name, team_id in (names or {}).items():
                _upsert_alias(session, sport, f"manual:{source}", str(raw_name), int(team_id))
                n += 1
    session.flush()
    return n


def learn_alias(session: Session, sport: str, source: str, raw_name: str, team_id: int) -> None:
    _upsert_alias(session, sport, source, raw_name, team_id)


def resolve_team(session: Session, sport: str, raw_name: str, sources: tuple[str, ...] = PRIORITY) -> tuple[int | None, str]:
    key = normalize_name(raw_name)
    rows = list(session.execute(select(TeamAlias).where(TeamAlias.sport == sport, TeamAlias.raw_name == key)).scalars())
    if raw_name and "kalshi_uuid" in sources:
        rows += list(session.execute(select(TeamAlias).where(TeamAlias.sport == sport, TeamAlias.source == "kalshi_uuid",
                                                             TeamAlias.raw_name == raw_name)).scalars())
    by_source = {r.source: r.team_id for r in rows}
    for src in [x for x in by_source if x.startswith("manual:")]:
        return by_source[src], src
    for src in sources:
        if src in by_source:
            return by_source[src], src
    return None, ""


def resolve_fuzzy(session: Session, sport: str, raw_name: str) -> tuple[int | None, float]:
    key = normalize_name(raw_name)
    rows = session.execute(select(TeamAlias).where(TeamAlias.sport == sport,
                                                   TeamAlias.source.in_(("espn_display", "espn_location")))).scalars().all()
    scored = sorted(((SequenceMatcher(None, key, a.raw_name).ratio(), a.team_id) for a in rows), reverse=True)
    if not scored:
        return None, 0.0
    best_ratio, best_id = scored[0]
    runner_up = next((r for r, tid in scored[1:] if tid != best_id), 0.0)
    if best_ratio >= 0.90 and runner_up < 0.85:
        return best_id, best_ratio
    return None, best_ratio
```

`harness/matching/aliases_manual.yaml` (initial; seeded with the four Odds API college names that do not match ESPN after normalization, measured against a live featured response on 2026-09-06: 111 of 115 matched; extended as the match report reveals gaps):
```yaml
# {sport: {source: {raw_name: espn_team_id}}}. raw_name is normalized before lookup.
nfl:
  kalshi_name: {}
ncaaf:
  kalshi_name: {}
  odds_api:
    "Appalachian State Mountaineers": 2026   # ESPN: App State Mountaineers
    "Hawaii Rainbow Warriors": 62                 # ESPN: Hawai'i Rainbow Warriors
    "Southern Mississippi Golden Eagles": 2572    # ESPN: Southern Miss Golden Eagles
    "Sam Houston State Bearkats": 2534            # ESPN: Sam Houston Bearkats
```

Add `pyyaml>=6` to `pyproject.toml` dependencies and run `uv pip install -e . -p .venv/bin/python` (or `.venv/bin/pip install -e .`).

- [ ] **Step 5: Run tests, then full suite; commit**

```bash
git add harness/matching pyproject.toml tests/test_names.py tests/test_teams.py tests/fixtures/espn_teams_*.json
git commit -m "feat: name normalization and team alias registry"
```

---

### Task 4: Games from The Odds API, linked to ESPN

**Files:**
- Create: `harness/matching/games.py`, `harness/normalize/__init__.py`, `harness/normalize/espn.py`
- Test: `tests/test_games.py`, `tests/test_normalize_espn.py`

**Interfaces:**
- `upsert_games_from_odds(session, sport, body: list, raw_id) -> GamesResult(created: int, updated: int, unresolved: list[str])`: for each event, resolve `home_team`/`away_team` via `resolve_team(..., sources=("odds_api","espn_display","espn_location","espn_short"))`; on exact hit, `learn_alias(source="odds_api")`; on miss try `resolve_fuzzy` and learn as `odds_api` when ≥ 0.9 (log it); if still unresolved, record the name in `unresolved` and skip. Upsert `Game` by `odds_api_event_id` (update kickoff if changed).
- `find_game_by_pair(session, sport, team_a, team_b, date_utc: date, tolerance_days=1) -> list[Game]`: games whose `{home,away} == {a,b}` and `kickoff_utc::date` within tolerance.
- `link_espn_scoreboard(session, sport, body: dict) -> LinkResult(linked: int, status_updates: int, unresolved: list[str])`: for each ESPN event resolve both teams (`espn_display` exact from `competitors[].team.displayName`, else by ESPN `team.id` directly, which is authoritative), find the game by pair within ±1 day of `date`, set `espn_event_id`, `status` (`STATUS_SCHEDULED`→`scheduled`, `STATUS_IN_PROGRESS`→`in_progress`, `STATUS_FINAL`→`final`, else raw lowercased), and scores from `competitors[].score` when present. Games with no Odds API counterpart (ESPN-only) are created with `odds_api_event_id=None` so Kalshi FCS games still match.

- [ ] **Step 1: Write failing tests**

`tests/test_games.py`:
```python
import json
from datetime import date, datetime, timezone
from pathlib import Path

from harness.db.models import Game
from harness.matching.games import find_game_by_pair, upsert_games_from_odds
from harness.matching.teams import seed_teams_from_espn

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
ODDS = [{"id": "ev1", "sport_key": "americanfootball_nfl", "commence_time": "2026-09-21T00:20:00Z",
         "home_team": "Los Angeles Rams", "away_team": "New York Giants", "bookmakers": []},
        {"id": "ev2", "sport_key": "americanfootball_nfl", "commence_time": "2026-09-21T20:25:00Z",
         "home_team": "Kansas City Chiefs", "away_team": "Nowhere FC", "bookmakers": []}]


def test_upsert_games_and_find_by_pair(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    res = upsert_games_from_odds(db_session, "nfl", ODDS, raw_id=1)
    assert res.created == 1 and res.unresolved == ["Nowhere FC"]
    g = db_session.query(Game).filter_by(odds_api_event_id="ev1").one()
    assert g.home_team_id == 14 and g.away_team_id == 19  # Rams, Giants ESPN ids (verify in fixture)
    found = find_game_by_pair(db_session, "nfl", 19, 14, date(2026, 9, 20))
    assert [x.id for x in found] == [g.id]
    assert find_game_by_pair(db_session, "nfl", 19, 14, date(2026, 9, 25)) == []
    res2 = upsert_games_from_odds(db_session, "nfl", ODDS, raw_id=2)
    assert res2.created == 0 and res2.updated == 0
```

`tests/test_normalize_espn.py`:
```python
import json
from pathlib import Path

from harness.db.models import Game
from harness.matching.games import upsert_games_from_odds
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.espn import link_espn_scoreboard

FIXD = Path(__file__).parent / "fixtures"
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
SB = {"events": [{"id": "401", "date": "2026-09-21T00:20Z", "status": {"type": {"name": "STATUS_FINAL"}},
      "competitions": [{"competitors": [
          {"homeAway": "home", "score": "24", "team": {"id": "14", "displayName": "Los Angeles Rams"}},
          {"homeAway": "away", "score": "17", "team": {"id": "19", "displayName": "New York Giants"}}]}]}]}


def test_link_scoreboard_sets_espn_id_status_scores(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    upsert_games_from_odds(db_session, "nfl", [{"id": "ev1", "commence_time": "2026-09-21T00:20:00Z",
                           "home_team": "Los Angeles Rams", "away_team": "New York Giants"}], raw_id=1)
    res = link_espn_scoreboard(db_session, "nfl", SB)
    assert res.linked == 1
    g = db_session.query(Game).filter_by(odds_api_event_id="ev1").one()
    assert g.espn_event_id == "401" and g.status == "final" and (g.home_score, g.away_score) == (24, 17)


def test_link_creates_espn_only_game(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    res = link_espn_scoreboard(db_session, "nfl", SB)
    assert res.linked == 1
    assert db_session.query(Game).filter_by(espn_event_id="401", odds_api_event_id=None).count() == 1
```
(Verify the Rams' ESPN id in the fixture; adjust `14` if different.)

- [ ] **Step 2: Run to verify failure; Step 3: Implement**

`harness/matching/games.py`:
```python
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from harness.db.models import Game
from harness.matching.teams import learn_alias, resolve_fuzzy, resolve_team

log = logging.getLogger(__name__)
ODDS_SOURCES = ("odds_api", "espn_display", "espn_location", "espn_short")


@dataclass
class GamesResult:
    created: int = 0
    updated: int = 0
    unresolved: list[str] = field(default_factory=list)


def _resolve_odds_name(session: Session, sport: str, name: str) -> int | None:
    tid, src = resolve_team(session, sport, name, sources=ODDS_SOURCES)
    if tid is not None:
        if src != "odds_api":
            learn_alias(session, sport, "odds_api", name, tid)
        return tid
    tid, ratio = resolve_fuzzy(session, sport, name)
    if tid is not None:
        log.info("fuzzy odds_api alias %r -> %s (%.2f)", name, tid, ratio)
        learn_alias(session, sport, "odds_api", name, tid)
    return tid


def _ts(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00")).astimezone(timezone.utc)


def upsert_games_from_odds(session: Session, sport: str, body: list, raw_id: int) -> GamesResult:
    res = GamesResult()
    if not isinstance(body, list):
        return res
    for ev in body:
        try:
            eid, kick = ev["id"], _ts(ev["commence_time"])
            home, away = ev["home_team"], ev["away_team"]
        except (KeyError, ValueError, AttributeError):
            continue
        h = _resolve_odds_name(session, sport, home)
        a = _resolve_odds_name(session, sport, away)
        if h is None:
            res.unresolved.append(home)
        if a is None:
            res.unresolved.append(away)
        if h is None or a is None:
            continue
        g = session.execute(select(Game).where(Game.odds_api_event_id == eid)).scalar_one_or_none()
        if g is None:
            existing = find_game_by_pair(session, sport, h, a, kick.date())
            g = next((x for x in existing if x.odds_api_event_id is None), None)
            if g is None:
                session.add(Game(sport=sport, home_team_id=h, away_team_id=a, kickoff_utc=kick, odds_api_event_id=eid))
                res.created += 1
                continue
            g.odds_api_event_id = eid
        if g.kickoff_utc != kick or g.home_team_id != h or g.away_team_id != a:
            g.kickoff_utc, g.home_team_id, g.away_team_id = kick, h, a
            res.updated += 1
    session.flush()
    return res


def find_game_by_pair(session: Session, sport: str, team_a: int, team_b: int, date_utc: date, tolerance_days: int = 1) -> list[Game]:
    lo = datetime.combine(date_utc - timedelta(days=tolerance_days), datetime.min.time(), tzinfo=timezone.utc)
    hi = datetime.combine(date_utc + timedelta(days=tolerance_days + 1), datetime.min.time(), tzinfo=timezone.utc)
    pair = or_(and_(Game.home_team_id == team_a, Game.away_team_id == team_b),
               and_(Game.home_team_id == team_b, Game.away_team_id == team_a))
    return list(session.execute(select(Game).where(Game.sport == sport, pair, Game.kickoff_utc >= lo, Game.kickoff_utc < hi)
                                .order_by(Game.kickoff_utc)).scalars())
```

`harness/normalize/__init__.py`: empty. `harness/normalize/espn.py`:
```python
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from harness.db.models import Game, Team
from harness.matching.games import find_game_by_pair

_STATUS = {"STATUS_SCHEDULED": "scheduled", "STATUS_IN_PROGRESS": "in_progress", "STATUS_FINAL": "final",
           "STATUS_HALFTIME": "in_progress", "STATUS_END_PERIOD": "in_progress", "STATUS_POSTPONED": "postponed",
           "STATUS_CANCELED": "canceled", "STATUS_DELAYED": "delayed"}


@dataclass
class LinkResult:
    linked: int = 0
    status_updates: int = 0
    unresolved: list[str] = field(default_factory=list)


def _score(v) -> int | None:
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def link_espn_scoreboard(session: Session, sport: str, body: dict) -> LinkResult:
    res = LinkResult()
    if not isinstance(body, dict):
        return res
    for ev in body.get("events", []):
        try:
            eid = str(ev["id"])
            kick = datetime.fromisoformat(ev["date"].replace("Z", "+00:00")).astimezone(timezone.utc)
            comps = ev["competitions"][0]["competitors"]
            home = next(c for c in comps if c["homeAway"] == "home")
            away = next(c for c in comps if c["homeAway"] == "away")
            h, a = int(home["team"]["id"]), int(away["team"]["id"])
        except (KeyError, ValueError, IndexError, StopIteration, TypeError):
            continue
        if session.get(Team, (sport, h)) is None or session.get(Team, (sport, a)) is None:
            res.unresolved.append(f"{away.get('team', {}).get('displayName')} at {home.get('team', {}).get('displayName')}")
            continue
        g = session.query(Game).filter_by(espn_event_id=eid).one_or_none()
        if g is None:
            cands = find_game_by_pair(session, sport, h, a, kick.date())
            g = next((x for x in cands if x.espn_event_id is None), None)
            if g is None:
                g = Game(sport=sport, home_team_id=h, away_team_id=a, kickoff_utc=kick)
                session.add(g)
            g.espn_event_id = eid
            res.linked += 1
        status = _STATUS.get(ev.get("status", {}).get("type", {}).get("name", ""), "scheduled")
        hs, as_ = _score(home.get("score")), _score(away.get("score"))
        if (g.status, g.home_score, g.away_score) != (status, hs, as_):
            g.status, g.home_score, g.away_score = status, hs, as_
            res.status_updates += 1
        if g.kickoff_utc != kick:
            g.kickoff_utc = kick
    session.flush()
    return res
```

- [ ] **Step 4: Run tests, full suite; commit**

```bash
git add harness/matching/games.py harness/normalize tests/test_games.py tests/test_normalize_espn.py
git commit -m "feat: games from odds api linked to espn scoreboard"
```

---

### Task 5: Kalshi event matching and market classification

**Files:**
- Create: `harness/matching/kalshi.py`
- Test: `tests/test_matching_kalshi.py` (uses `tests/fixtures/kalshi_events_page.json`, `kalshi_markets_typed.json`, ESPN team fixtures)

**Interfaces:**
- `parse_event_title(title: str) -> tuple[str, str] | None`: split on `" vs "` (also accept `" vs. "` and `" @ "`), returning `(left, right)` stripped; `None` if not exactly two parts.
- `classify_market(m: dict) -> MarketClass(market_type, threshold, side_name, side_kind ∈ {team, over}, team_uuid) | None` from series prefix: `*GAME` → moneyline, `side_name=yes_sub_title`; `*SPREAD` → spread, `threshold=Decimal(floor_strike)`, `side_name` = team name parsed from title regex `^(.+?) wins by over ([0-9]+\.5) points\??$` (fall back to `yes_sub_title`); `*TOTAL` → total, `threshold=floor_strike`, `side_kind="over"`. `team_uuid = m.get("custom_strike", {}).get("football_team")`. Unknown series → `None`.
- `match_event(session, sport, event: dict, event_date: date) -> EventMatch(game_id | None, confidence: Decimal, reason: str, left_team_id, right_team_id)`: resolve both title names with `resolve_team(sources=("kalshi_name","espn_display","espn_location","espn_short"))`; exact both → candidates `find_game_by_pair(±1 day)`; exactly one → `(game_id, 1.00, "pair+date exact")`, and `learn_alias("kalshi_name", name, team_id)` for both; multiple → `(None, 0, "ambiguous: N games")`; none → `(None, 0, "no game for pair")`. If a name misses exact, try `resolve_fuzzy`; if both resolve with at least one fuzzy and a unique game exists → `(game_id, 0.80, "pair+date fuzzy(<ratio>)")` and do NOT learn the alias. If any name unresolved → `(None, 0, f"unresolved: {name}")`.
- `side_team_id_for(session, sport, mc: MarketClass, game: Game) -> int | None`: resolve `mc.side_name` against the two game teams only. A team hits when the normalized side name equals one of its display/location/short/abbr/name forms, or an existing `kalshi_name` alias points at it, or (Kalshi truncates names, e.g. `"New York G"`) the normalized side name is a prefix of at least 6 characters of one of those forms. Exactly one hit is required; two hits (both teams share the prefix) or zero return `None`. If `mc.team_uuid` is known via a `kalshi_uuid` alias, that wins outright. On success learn `kalshi_uuid` and `kalshi_name` aliases.
- Ticker date: `event_date_from_ticker(event_ticker)` from phase 0.

- [ ] **Step 1: Write failing tests**

`tests/test_matching_kalshi.py` (record the total-market title wording you observed in Task 1 at the top as a comment):
```python
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import Game
from harness.matching.kalshi import classify_market, match_event, parse_event_title, side_team_id_for
from harness.matching.teams import seed_teams_from_espn

FIXD = Path(__file__).parent / "fixtures"
NCAAF = json.loads((FIXD / "espn_teams_ncaaf.json").read_text())
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())


def test_parse_event_title():
    assert parse_event_title("Alabama St. vs Troy") == ("Alabama St.", "Troy")
    assert parse_event_title("NY Giants vs LA Rams") == ("NY Giants", "LA Rams")
    assert parse_event_title("Just one") is None


def test_classify_market_from_fixture():
    ms = json.loads((FIXD / "kalshi_markets_typed.json").read_text())["markets"]
    by_series = {m["event_ticker"].split("-")[0]: m for m in ms}
    mc = classify_market(by_series["KXNFLSPREAD"])
    assert mc.market_type == "spread" and mc.threshold == Decimal(str(by_series["KXNFLSPREAD"]["floor_strike"]))
    assert mc.side_kind == "team" and mc.side_name
    mc = classify_market(by_series["KXNFLTOTAL"])
    assert mc.market_type == "total" and mc.side_kind == "over" and mc.threshold is not None
    mc = classify_market(by_series["KXNCAAFGAME"])
    assert mc.market_type == "moneyline" and mc.side_name == by_series["KXNCAAFGAME"]["yes_sub_title"]
    assert classify_market({"event_ticker": "KXWEIRD-1", "ticker": "x"}) is None


def test_match_event_exact_and_learns_alias(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    kick = datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)
    g = Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=kick, odds_api_event_id="ev1")
    db_session.add(g)
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "title": "NY Giants vs LA Rams"}, date(2026, 9, 21))
    assert em.game_id == g.id and em.confidence == Decimal("1.00") and em.reason.startswith("pair+date exact")
    from harness.matching.teams import resolve_team
    assert resolve_team(db_session, "nfl", "NY Giants") == (19, "kalshi_name")


def test_match_event_unresolved_and_ambiguous(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    em = match_event(db_session, "nfl", {"event_ticker": "X-26SEP21AB", "title": "NY Giants vs Nowhere"}, date(2026, 9, 21))
    assert em.game_id is None and "unresolved" in em.reason
    kick = datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)
    db_session.add_all([Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=kick),
                        Game(sport="nfl", home_team_id=19, away_team_id=14, kickoff_utc=kick)])
    db_session.flush()
    em = match_event(db_session, "nfl", {"event_ticker": "X-26SEP21AB", "title": "NY Giants vs LA Rams"}, date(2026, 9, 21))
    assert em.game_id is None and em.reason.startswith("ambiguous")


def test_side_team_id_for_uses_game_teams_only(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    kick = datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)
    g = Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=kick)
    db_session.add(g)
    db_session.flush()
    from harness.matching.teams import learn_alias, resolve_team
    # truncated Kalshi name resolves by prefix against the game's two teams
    mc = classify_market({"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "ticker": "KXNFLGAME-26SEP21NYGLAR-NYG",
                          "yes_sub_title": "New York G", "title": "New York G wins"})
    assert side_team_id_for(db_session, "nfl", mc, g) == 19
    assert resolve_team(db_session, "nfl", "New York G") == (19, "kalshi_name")  # learned
    # a name that matches neither team returns None; a known uuid alias wins
    mc2 = classify_market({"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "ticker": "x", "yes_sub_title": "Jints",
                           "title": "Jints wins", "custom_strike": {"football_team": "uuid-giants"}})
    assert side_team_id_for(db_session, "nfl", mc2, g) is None
    learn_alias(db_session, "nfl", "kalshi_uuid", "uuid-giants", 19)
    assert side_team_id_for(db_session, "nfl", mc2, g) == 19
    # a prefix shared by both teams is ambiguous
    g2 = Game(sport="nfl", home_team_id=19, away_team_id=17, kickoff_utc=kick)  # Giants vs Patriots: "new " prefixes both
    db_session.add(g2)
    db_session.flush()
    mc3 = classify_market({"event_ticker": "KXNFLGAME-26SEP21NYGNE", "ticker": "y", "yes_sub_title": "New Yo", "title": "New Yo wins"})
    assert side_team_id_for(db_session, "nfl", mc3, g2) == 19  # "new yo" prefixes only the Giants
    mc4 = classify_market({"event_ticker": "KXNFLGAME-26SEP21NYGNE", "ticker": "z", "yes_sub_title": "New En", "title": "New En wins"})
    assert side_team_id_for(db_session, "nfl", mc4, g2) == 17
```

- [ ] **Step 2: Run to verify failure; Step 3: Implement**

`harness/matching/kalshi.py`:
```python
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

from sqlalchemy.orm import Session

from harness.db.models import Game, Team
from harness.matching.games import find_game_by_pair
from harness.matching.names import normalize_name
from harness.matching.teams import learn_alias, resolve_fuzzy, resolve_team

KALSHI_SOURCES = ("kalshi_name", "espn_display", "espn_location", "espn_short")
_VS = re.compile(r"\s+(?:vs\.?|@)\s+")
_SPREAD_TITLE = re.compile(r"^(?P<team>.+?) wins by over (?P<pts>\d+(?:\.5)?) points\??$", re.I)


@dataclass(frozen=True)
class MarketClass:
    market_type: str
    threshold: Decimal | None
    side_name: str
    side_kind: str
    team_uuid: str | None


@dataclass(frozen=True)
class EventMatch:
    game_id: int | None
    confidence: Decimal
    reason: str
    left_team_id: int | None
    right_team_id: int | None


def parse_event_title(title: str) -> tuple[str, str] | None:
    parts = _VS.split(title or "")
    if len(parts) != 2 or not all(p.strip() for p in parts):
        return None
    return parts[0].strip(), parts[1].strip()


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None else None
    except InvalidOperation:
        return None


def classify_market(m: dict) -> MarketClass | None:
    series = (m.get("event_ticker") or "").split("-")[0]
    uuid = (m.get("custom_strike") or {}).get("football_team")
    if series.endswith("GAME"):
        return MarketClass("moneyline", None, m.get("yes_sub_title") or "", "team", uuid)
    if series.endswith("SPREAD"):
        mt = _SPREAD_TITLE.match(m.get("title") or "")
        name = mt.group("team") if mt else (m.get("yes_sub_title") or "")
        return MarketClass("spread", _dec(m.get("floor_strike")), name, "team", uuid)
    if series.endswith("TOTAL"):
        return MarketClass("total", _dec(m.get("floor_strike")), "over", "over", None)
    return None


def _resolve(session: Session, sport: str, name: str) -> tuple[int | None, bool, float]:
    tid, _ = resolve_team(session, sport, name, sources=KALSHI_SOURCES)
    if tid is not None:
        return tid, True, 1.0
    tid, ratio = resolve_fuzzy(session, sport, name)
    return tid, False, ratio


def match_event(session: Session, sport: str, event: dict, event_date: date) -> EventMatch:
    parsed = parse_event_title(event.get("title") or "")
    if parsed is None:
        return EventMatch(None, Decimal("0"), "unparseable title", None, None)
    left, right = parsed
    lt, l_exact, lr = _resolve(session, sport, left)
    rt, r_exact, rr = _resolve(session, sport, right)
    if lt is None or rt is None:
        missing = left if lt is None else right
        return EventMatch(None, Decimal("0"), f"unresolved: {missing}", lt, rt)
    cands = find_game_by_pair(session, sport, lt, rt, event_date)
    if len(cands) != 1:
        reason = f"ambiguous: {len(cands)} games" if cands else "no game for pair"
        return EventMatch(None, Decimal("0"), reason, lt, rt)
    game = cands[0]
    if l_exact and r_exact:
        learn_alias(session, sport, "kalshi_name", left, lt)
        learn_alias(session, sport, "kalshi_name", right, rt)
        return EventMatch(game.id, Decimal("1.00"), "pair+date exact", lt, rt)
    return EventMatch(game.id, Decimal("0.80"), f"pair+date fuzzy({min(lr, rr):.2f})", lt, rt)


def side_team_id_for(session: Session, sport: str, mc: MarketClass, game: Game) -> int | None:
    if mc.side_kind != "team":
        return None
    if mc.team_uuid:
        tid, _ = resolve_team(session, sport, mc.team_uuid, sources=("kalshi_uuid",))
        if tid in (game.home_team_id, game.away_team_id):
            learn_alias(session, sport, "kalshi_name", mc.side_name, tid)
            return tid
    key = normalize_name(mc.side_name)
    known, _ = resolve_team(session, sport, mc.side_name, sources=("kalshi_name",))
    hits: list[int] = []
    for tid in (game.home_team_id, game.away_team_id):
        t = session.get(Team, (sport, tid))
        if t is None:
            continue
        names = {normalize_name(x) for x in (t.display_name, t.location, t.short_display_name, t.abbreviation, t.name)}
        prefix = len(key) >= 6 and any(n.startswith(key) for n in names)
        if known == tid or key in names or prefix:
            hits.append(tid)
    if len(hits) != 1:
        return None
    tid = hits[0]
    if mc.team_uuid:
        learn_alias(session, sport, "kalshi_uuid", mc.team_uuid, tid)
    learn_alias(session, sport, "kalshi_name", mc.side_name, tid)
    return tid
```
Note on `resolve_team` with `kalshi_uuid`: Task 3's implementation looks up the raw (un-normalized) uuid when `"kalshi_uuid"` is in `sources`; keep that behaviour.

- [ ] **Step 4: Run tests, full suite; commit**

```bash
git add harness/matching/kalshi.py tests/test_matching_kalshi.py
git commit -m "feat: kalshi event matching and market classification"
```

---

### Task 6: Odds and Kalshi normalizers (pure parsers + upserts)

**Files:**
- Create: `harness/normalize/odds.py`, `harness/normalize/kalshi.py`
- Create: `tests/fixtures/odds_alternates_event.json` (hand-written in the per-event shape: `{id, commence_time, home_team, away_team, bookmakers:[{key,last_update,markets:[{key:"alternate_spreads",outcomes:[{name,price,point}]}]}]}`), `tests/fixtures/kalshi_orderbook.json` (`{"orderbook_fp": {"yes_dollars": [["0.2000","501.98"]], "no_dollars": [["0.7500","2692.00"]]}}`), `tests/fixtures/kalshi_trades_page.json` (2 trades from the phase 0 verified shape)
- Test: `tests/test_normalize_odds.py`, `tests/test_normalize_kalshi.py`

**Interfaces:**
- `parse_odds_body(body, sport) -> list[OddsRow]` (pure) where `OddsRow(event_id, book, market_type, outcome_name, point: Decimal | None, price: Decimal, last_update: datetime | None)`; handles both the featured list shape and the single-event alternates dict shape (wrap dict in a list). `outcome_name` is the team name for h2h/spreads and `Over`/`Under` for totals.
- `upsert_odds_rows(session, sport, rows, raw_id, run_id, fetched_at) -> int`: resolve `event_id` → `Game` (by `odds_api_event_id`; skip rows whose game is unknown, counting them), map `outcome_name` to `outcome_team_id` via `resolve_team(sources=("odds_api","espn_display"))` or to `outcome_side` for Over/Under; insert with `ON CONFLICT DO NOTHING` on the unique index. Returns inserted count.
- `upsert_venue_markets(session, sport, markets: list[dict], events_by_ticker: dict[str, dict], raw_id, fetched_at) -> MarketsResult(new, updated, matched, fuzzy, unmatched)`: for each market, `classify_market`; look up the event by `event_ticker` in `events_by_ticker` (title from the recorded `/events` bodies, or fall back to `sub_title`-less matching with reason `"no event title recorded"`); `match_event` when the market's `VenueMarket` row does not yet have `game_id` (or is `fuzzy`/`unmatched`, re-tried each pass so new aliases can upgrade it); `side_team_id_for` on match; upsert `VenueMarket` by `ticker` (never downgrade a `manual` status; never lower confidence of a `matched` row).
- `insert_venue_quotes(session, markets, raw_id, run_id, fetched_at) -> int`: one `VenueQuote` per market in the page (all Decimal fields from the `*_dollars`/`*_fp` strings), `ON CONFLICT DO NOTHING`.
- `insert_orderbook(session, ticker, body, raw_id, fetched_at) -> bool`: `OrderbookSnapshot` with `yes_bids`/`no_bids` as lists of `[price, qty]` strings, keyed by `raw_id`.
- `insert_trades(session, body, raw_id) -> int`: `VenueTrade(source="rest")` rows from `trades[]`, `ts` from `created_time`, `yes_price` from `yes_price_dollars`, `count` from `count_fp`, `is_block` from `is_block_trade`, `ON CONFLICT DO NOTHING` on `(venue, trade_id)`.

- [ ] **Step 1: Write failing tests**

`tests/test_normalize_odds.py`:
```python
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import OddsSnapshot
from harness.matching.games import upsert_games_from_odds
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.odds import parse_odds_body, upsert_odds_rows

FIXD = Path(__file__).parent / "fixtures"
FEAT = json.loads((FIXD / "odds_featured_nfl.json").read_text())
ALT = json.loads((FIXD / "odds_alternates_event.json").read_text())
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def test_parse_featured_rows():
    rows = parse_odds_body(FEAT, "nfl")
    assert len(rows) == 6
    h2h = [r for r in rows if r.market_type == "h2h"]
    assert {r.outcome_name for r in h2h} == {"Seattle Seahawks", "New England Patriots"}
    tot = [r for r in rows if r.market_type == "totals"]
    assert {(r.outcome_name, r.point) for r in tot} == {("Over", Decimal("44.5")), ("Under", Decimal("44.5"))}
    assert rows[0].last_update == datetime(2026, 9, 6, 16, 0, tzinfo=timezone.utc)


def test_parse_alternates_dict_shape():
    rows = parse_odds_body(ALT, "nfl")
    assert rows and all(r.market_type.startswith("alternate_") for r in rows)


def test_upsert_is_idempotent_and_resolves_teams(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)  # fixture includes Seahawks(26) and Patriots(17)
    upsert_games_from_odds(db_session, "nfl", FEAT, raw_id=1)
    rows = parse_odds_body(FEAT, "nfl")
    assert upsert_odds_rows(db_session, "nfl", rows, raw_id=1, run_id=1, fetched_at=NOW) == 6
    assert upsert_odds_rows(db_session, "nfl", rows, raw_id=1, run_id=1, fetched_at=NOW) == 0
    snap = db_session.query(OddsSnapshot).filter_by(market_type="spreads").all()
    assert {s.point for s in snap} == {Decimal("-3.5"), Decimal("3.5")}
    assert all(s.outcome_team_id is not None and s.game_id is not None for s in snap)
```
(The NFL fixture from Task 3 already includes the Seahawks and Patriots.)

`tests/test_normalize_kalshi.py`:
```python
import json
from datetime import date, datetime, timezone
from decimal import Decimal
from pathlib import Path

from harness.db.models import Game, OrderbookSnapshot, VenueMarket, VenueQuote, VenueTrade
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.kalshi import insert_orderbook, insert_trades, insert_venue_quotes, upsert_venue_markets

FIXD = Path(__file__).parent / "fixtures"
KM = json.loads((FIXD / "kalshi_markets_page.json").read_text())["markets"]  # phase 0 fixture: NYG game + KC spread
NFL = json.loads((FIXD / "espn_teams_nfl.json").read_text())
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)
EVENTS = {"KXNFLGAME-26SEP21NYGLAR": {"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "title": "NY Giants vs LA Rams"},
          "KXNFLSPREAD-26SEP14DENKC": {"event_ticker": "KXNFLSPREAD-26SEP14DENKC", "title": "Denver vs Kansas City"}}


def test_upsert_venue_markets_matches_and_classifies(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)  # must include Giants, Rams, Chiefs (fixture) — Broncos absent on purpose
    db_session.add(Game(sport="nfl", home_team_id=14, away_team_id=19, kickoff_utc=datetime(2026, 9, 21, 0, 20, tzinfo=timezone.utc)))
    db_session.flush()
    res = upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=1, fetched_at=NOW)
    assert res.new == 2 and res.matched == 1 and res.unmatched == 1
    nyg = db_session.query(VenueMarket).filter_by(ticker="KXNFLGAME-26SEP21NYGLAR-NYG").one()
    assert nyg.market_type == "moneyline" and nyg.match_status == "matched" and nyg.side_team_id == 19
    kc = db_session.query(VenueMarket).filter_by(ticker="KXNFLSPREAD-26SEP14DENKC-KC7").one()
    assert kc.market_type == "spread" and kc.threshold == Decimal("6.5") and kc.match_status == "unmatched"
    assert "unresolved: Denver" in kc.match_reason
    res2 = upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=2, fetched_at=NOW)
    assert res2.new == 0


def test_quotes_orderbook_trades_idempotent(db_session):
    seed_teams_from_espn(db_session, "nfl", NFL)
    upsert_venue_markets(db_session, "nfl", KM, EVENTS, raw_id=1, fetched_at=NOW)
    assert insert_venue_quotes(db_session, KM, raw_id=1, run_id=1, fetched_at=NOW) == 2
    assert insert_venue_quotes(db_session, KM, raw_id=1, run_id=1, fetched_at=NOW) == 0
    q = db_session.query(VenueQuote).join(VenueMarket, VenueMarket.id == VenueQuote.venue_market_id).filter(VenueMarket.ticker == "KXNFLGAME-26SEP21NYGLAR-NYG").one()
    assert (q.yes_bid, q.yes_ask, q.volume) == (Decimal("0.2200"), Decimal("0.2300"), Decimal("1234.00"))
    ob = json.loads((FIXD / "kalshi_orderbook.json").read_text())
    assert insert_orderbook(db_session, "KXNFLGAME-26SEP21NYGLAR-NYG", ob, raw_id=5, fetched_at=NOW) is True
    assert insert_orderbook(db_session, "KXNFLGAME-26SEP21NYGLAR-NYG", ob, raw_id=5, fetched_at=NOW) is False
    tr = json.loads((FIXD / "kalshi_trades_page.json").read_text())
    assert insert_trades(db_session, tr, raw_id=6) == 2
    assert insert_trades(db_session, tr, raw_id=6) == 0
    t = db_session.query(VenueTrade).order_by(VenueTrade.ts).first()
    assert t.source == "rest" and t.yes_price == Decimal("0.2300") and t.taker_side in ("yes", "no")
```

- [ ] **Step 2: Run to verify failure; Step 3: Implement**

`harness/normalize/odds.py`:
```python
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Game, OddsSnapshot
from harness.matching.teams import resolve_team


@dataclass(frozen=True)
class OddsRow:
    event_id: str
    book: str
    market_type: str
    outcome_name: str
    point: Decimal | None
    price: Decimal
    last_update: datetime | None


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v is not None else None
    except InvalidOperation:
        return None


def _ts(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def parse_odds_body(body, sport: str) -> list[OddsRow]:
    events = body if isinstance(body, list) else ([body] if isinstance(body, dict) and "id" in body else [])
    out: list[OddsRow] = []
    for ev in events:
        eid = ev.get("id")
        if not eid:
            continue
        for bk in ev.get("bookmakers", []) or []:
            book, lu = bk.get("key"), _ts(bk.get("last_update"))
            for mk in bk.get("markets", []) or []:
                mtype = mk.get("key")
                for oc in mk.get("outcomes", []) or []:
                    price = _dec(oc.get("price"))
                    if not book or not mtype or price is None or not oc.get("name"):
                        continue
                    out.append(OddsRow(eid, book, mtype, oc["name"], _dec(oc.get("point")), price, lu))
    return out


def upsert_odds_rows(session: Session, sport: str, rows: list[OddsRow], raw_id: int, run_id: int, fetched_at: datetime) -> int:
    games = {g.odds_api_event_id: g.id for g in session.execute(
        select(Game).where(Game.odds_api_event_id.in_({r.event_id for r in rows}))).scalars()}
    inserted = 0
    cache: dict[str, int | None] = {}
    for r in rows:
        gid = games.get(r.event_id)
        if gid is None:
            continue
        team_id, side = None, None
        if r.outcome_name in ("Over", "Under"):
            side = r.outcome_name.lower()
        else:
            if r.outcome_name not in cache:
                cache[r.outcome_name] = resolve_team(session, sport, r.outcome_name, sources=("odds_api", "espn_display"))[0]
            team_id = cache[r.outcome_name]
            if team_id is None:
                continue
        stmt = insert(OddsSnapshot).values(raw_id=raw_id, run_id=run_id, book=r.book, game_id=gid, market_type=r.market_type,
                                           outcome_team_id=team_id, outcome_side=side, point=r.point, price_decimal=r.price,
                                           book_last_update=r.last_update, fetched_at=fetched_at).on_conflict_do_nothing()
        inserted += session.execute(stmt).rowcount
    return inserted
```

`harness/normalize/kalshi.py`:
```python
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Game, OrderbookSnapshot, VenueMarket, VenueQuote, VenueTrade
from harness.matching.kalshi import classify_market, match_event, side_team_id_for
from harness.venues.kalshi.public import event_date_from_ticker


@dataclass
class MarketsResult:
    new: int = 0
    updated: int = 0
    matched: int = 0
    fuzzy: int = 0
    unmatched: int = 0


def _dec(v) -> Decimal | None:
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _ts(v) -> datetime | None:
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


def upsert_venue_markets(session: Session, sport: str, markets: list[dict], events_by_ticker: dict[str, dict],
                         raw_id: int, fetched_at: datetime) -> MarketsResult:
    res = MarketsResult()
    for m in markets:
        ticker, et = m.get("ticker"), m.get("event_ticker", "")
        mc = classify_market(m)
        if not ticker or mc is None:
            continue
        vm = session.query(VenueMarket).filter_by(ticker=ticker).one_or_none()
        if vm is None:
            vm = VenueMarket(venue="kalshi", ticker=ticker, event_ticker=et, series_ticker=et.split("-")[0],
                             market_type=mc.market_type, threshold=mc.threshold, side=(None if mc.side_kind == "team" else "over"),
                             kalshi_team_uuid=mc.team_uuid, first_seen_raw_id=raw_id, last_seen_at=fetched_at,
                             match_status="unmatched", match_reason="new")
            session.add(vm)
            res.new += 1
        vm.last_seen_at = fetched_at
        vm.close_time = _ts(m.get("close_time")) or vm.close_time
        if vm.match_status in ("matched", "manual") and vm.game_id is not None:
            continue
        event = events_by_ticker.get(et)
        if event is None:
            vm.match_reason = "no event title recorded"
            res.unmatched += 1
            continue
        edate = event_date_from_ticker(et)
        if edate is None:
            vm.match_reason = "no date in ticker"
            res.unmatched += 1
            continue
        em = match_event(session, sport, event, edate)
        vm.match_reason = em.reason
        if em.game_id is None:
            vm.match_status, vm.match_confidence, vm.game_id = "unmatched", Decimal("0"), None
            res.unmatched += 1
            continue
        game = session.get(Game, em.game_id)
        side_team = side_team_id_for(session, sport, mc, game) if mc.side_kind == "team" else None
        if mc.side_kind == "team" and side_team is None:
            vm.match_status, vm.match_confidence, vm.game_id = "unmatched", Decimal("0"), None
            vm.match_reason = f"side team unresolved: {mc.side_name}"
            res.unmatched += 1
            continue
        vm.game_id, vm.side_team_id, vm.match_confidence = em.game_id, side_team, em.confidence
        vm.match_status = "matched" if em.confidence == Decimal("1.00") else "fuzzy"
        if vm.match_status == "matched":
            res.matched += 1
        else:
            res.fuzzy += 1
    session.flush()
    return res


def insert_venue_quotes(session: Session, markets: list[dict], raw_id: int, run_id: int, fetched_at: datetime) -> int:
    ids = {vm.ticker: vm.id for vm in session.query(VenueMarket).filter(
        VenueMarket.ticker.in_([m.get("ticker") for m in markets if m.get("ticker")])).all()}
    n = 0
    for m in markets:
        vmid = ids.get(m.get("ticker"))
        if vmid is None:
            continue
        stmt = insert(VenueQuote).values(
            raw_id=raw_id, run_id=run_id, venue_market_id=vmid,
            yes_bid=_dec(m.get("yes_bid_dollars")), yes_ask=_dec(m.get("yes_ask_dollars")),
            no_bid=_dec(m.get("no_bid_dollars")), no_ask=_dec(m.get("no_ask_dollars")),
            yes_bid_size=_dec(m.get("yes_bid_size_fp")), yes_ask_size=_dec(m.get("yes_ask_size_fp")),
            volume=_dec(m.get("volume_fp")), volume_24h=_dec(m.get("volume_24h_fp")), open_interest=_dec(m.get("open_interest_fp")),
            updated_time=_ts(m.get("updated_time")), fetched_at=fetched_at).on_conflict_do_nothing()
        n += session.execute(stmt).rowcount
    return n


def insert_orderbook(session: Session, ticker: str, body: dict, raw_id: int, fetched_at: datetime) -> bool:
    vm = session.query(VenueMarket).filter_by(ticker=ticker).one_or_none()
    ob = (body or {}).get("orderbook_fp") or {}
    if vm is None or not isinstance(ob, dict):
        return False
    stmt = insert(OrderbookSnapshot).values(raw_id=raw_id, venue_market_id=vm.id, fetched_at=fetched_at,
                                            yes_bids=ob.get("yes_dollars") or [], no_bids=ob.get("no_dollars") or []).on_conflict_do_nothing()
    return session.execute(stmt).rowcount == 1


def insert_trades(session: Session, body: dict, raw_id: int) -> int:
    n = 0
    for t in (body or {}).get("trades", []) if isinstance(body, dict) else []:
        ts, price, count = _ts(t.get("created_time")), _dec(t.get("yes_price_dollars")), _dec(t.get("count_fp"))
        if not t.get("trade_id") or not t.get("ticker") or ts is None or price is None or count is None:
            continue
        stmt = insert(VenueTrade).values(venue="kalshi", trade_id=t["trade_id"], ticker=t["ticker"], ts=ts, yes_price=price,
                                         count=count, taker_side=t.get("taker_side") or "yes", is_block=bool(t.get("is_block_trade")),
                                         source="rest", raw_id=raw_id).on_conflict_do_nothing()
        n += session.execute(stmt).rowcount
    return n
```

- [ ] **Step 4: Run tests, full suite; commit**

```bash
git add harness/normalize tests/test_normalize_odds.py tests/test_normalize_kalshi.py tests/fixtures
git commit -m "feat: odds and kalshi normalizers"
```

---

### Task 7: Normalization runner, `reprocess`, `seed-teams`, `match-report`, and tick integration

**Files:**
- Create: `harness/normalize/runner.py`
- Modify: `harness/cli.py`, `harness/recorder/tick.py`
- Test: `tests/test_runner.py`

**Interfaces:**
- Families and their raw selectors: `odds_featured` (`source=odds_api`, endpoint LIKE `/sports/%/odds`), `odds_alternates` (endpoint LIKE `/sports/%/events/%/odds`), `espn` (`source=espn`), `kalshi_events` (`endpoint=/events`), `kalshi_markets` (`endpoint=/markets`), `kalshi_orderbook` (endpoint LIKE `/markets/%/orderbook`), `kalshi_trades` (`endpoint=/markets/trades`). Sport is derived from the endpoint (`americanfootball_nfl`→`nfl`), from the ESPN endpoint path, or from the Kalshi series prefix (`KXNFL*`→`nfl`, `KXNCAAF*`→`ncaaf`).
- `normalize_new(session, batch: int = 500) -> dict[str, int]`: for each family in the order `espn, odds_featured, odds_alternates, kalshi_events, kalshi_markets, kalshi_orderbook, kalshi_trades` (games must exist before odds and markets; events before markets), select raw rows with `id > last_raw_id` and `http_status == 200` ordered by id, up to `batch`, apply the family's handler, advance `NormalizeState.last_raw_id`, commit per family. Returns counts per family. Kalshi events bodies are cached in-process as `{event_ticker: event}` (`self`-less: a module-level dict refreshed from the DB `raw_responses` on start via the latest `/events` bodies per series) so `kalshi_markets` can match.
- `reprocess(session, from_raw_id: int = 0, families: list[str] | None = None, truncate: bool = False) -> dict[str, int]`: optionally `TRUNCATE` the normalized tables (never raw), reset `NormalizeState`, then loop `normalize_new` until a pass processes zero rows.
- CLI: `harness seed-teams` (fetches both ESPN team lists live: NCAAF with `groups=80`, which includes FCS, and NFL; seeds; loads `harness/matching/aliases_manual.yaml`; prints counts), `harness reprocess [--from-raw-id N] [--family F ...] [--truncate]`, `harness match-report [--sport nfl|ncaaf]` printing per sport: venue markets total; matched/fuzzy/unmatched counts and percentages; the 30 most common `match_reason` strings for unmatched with an example ticker each; unresolved Odds API names from the last 7 days of `runs.notes` (see below); games without an ESPN link.
- Tick: after the final checkpoint in `maybe_tick` (before `finish_run`), call `normalize_new(session)` inside its own try/except (an exception is recorded as `{"normalize": repr(e)}` in `ctx["warnings"]`, never fails the tick) and add the returned counts to `notes["normalized"]`. Unresolved Odds names from `upsert_games_from_odds` are accumulated into `notes["unresolved_teams"]`.

- [ ] **Step 1: Write failing tests**

`tests/test_runner.py`:
```python
import json
from datetime import datetime, timezone
from pathlib import Path

from harness.db.models import Game, NormalizeState, OddsSnapshot, RawResponse, Run, VenueMarket, VenueQuote, VenueTrade
from harness.db.schema import ensure_partitions
from harness.matching.teams import seed_teams_from_espn
from harness.normalize.runner import normalize_new, reprocess

FIXD = Path(__file__).parent / "fixtures"
NOW = datetime(2026, 9, 9, 23, 0, tzinfo=timezone.utc)


def _raw(session, run_id, source, endpoint, params, body, ts=NOW):
    r = RawResponse(run_id=run_id, source=source, endpoint=endpoint, params=params, fetched_at=ts, http_status=200, body=body)
    session.add(r)
    session.flush()
    return r.id


def _seed_raw(db_session):
    ensure_partitions(db_session, NOW)
    seed_teams_from_espn(db_session, "nfl", json.loads((FIXD / "espn_teams_nfl.json").read_text()))
    run = Run(started_at=NOW, status="ok")
    db_session.add(run)
    db_session.flush()
    feat = [{"id": "ev1", "commence_time": "2026-09-21T00:20:00Z", "home_team": "Los Angeles Rams", "away_team": "New York Giants",
             "bookmakers": [{"key": "pinnacle", "last_update": "2026-09-09T22:00:00Z", "markets": [
                 {"key": "h2h", "outcomes": [{"name": "Los Angeles Rams", "price": 1.5}, {"name": "New York Giants", "price": 2.7}]}]}]}]
    _raw(db_session, run.id, "odds_api", "/sports/americanfootball_nfl/odds", {"markets": "featured"}, feat)
    _raw(db_session, run.id, "kalshi", "/events", {"series_ticker": "KXNFLGAME"},
         {"cursor": "", "events": [{"event_ticker": "KXNFLGAME-26SEP21NYGLAR", "title": "NY Giants vs LA Rams"}]})
    km = json.loads((FIXD / "kalshi_markets_page.json").read_text())
    _raw(db_session, run.id, "kalshi", "/markets", {"series_ticker": "KXNFLGAME"}, km)
    _raw(db_session, run.id, "kalshi", "/markets/trades", {"ticker": "KXNFLGAME-26SEP21NYGLAR-NYG", "min_ts": "x"},
         json.loads((FIXD / "kalshi_trades_page.json").read_text()))
    return run


def test_normalize_new_is_incremental_and_ordered(db_session):
    _seed_raw(db_session)
    counts = normalize_new(db_session)
    assert counts["odds_featured"] == 1 and counts["kalshi_markets"] == 1 and counts["kalshi_trades"] == 1
    assert db_session.query(Game).count() == 1
    assert db_session.query(OddsSnapshot).count() == 2
    assert db_session.query(VenueMarket).filter_by(match_status="matched").count() == 1
    assert db_session.query(VenueQuote).count() == 2
    assert db_session.query(VenueTrade).count() == 2
    again = normalize_new(db_session)
    assert sum(again.values()) == 0
    st = {s.family: s.last_raw_id for s in db_session.query(NormalizeState).all()}
    assert st["kalshi_trades"] > 0


def test_reprocess_truncate_rebuilds_same_counts(db_session):
    _seed_raw(db_session)
    normalize_new(db_session)
    before = (db_session.query(OddsSnapshot).count(), db_session.query(VenueQuote).count(), db_session.query(VenueTrade).count())
    reprocess(db_session, truncate=True)
    after = (db_session.query(OddsSnapshot).count(), db_session.query(VenueQuote).count(), db_session.query(VenueTrade).count())
    assert before == after and before[0] == 2
```

Add to `tests/test_tick.py`:
```python
@respx.mock
def test_tick_runs_normalizer_and_reports_counts(env_settings, db_session):
    respx.get(url__regex=r".*").mock(return_value=httpx.Response(200, json={"events": [], "markets": [], "trades": [], "cursor": ""}))
    rec, _ = _recorder(env_settings, db_session)
    run = rec.maybe_tick()
    assert "normalized" in run.notes and isinstance(run.notes["normalized"], dict)
```

- [ ] **Step 2: Run to verify failure; Step 3: Implement**

`harness/normalize/runner.py`:
```python
import logging
import re
from datetime import datetime

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from harness.db.models import NormalizeState, RawResponse
from harness.matching.games import upsert_games_from_odds
from harness.normalize.espn import link_espn_scoreboard
from harness.normalize.kalshi import insert_orderbook, insert_trades, insert_venue_quotes, upsert_venue_markets
from harness.normalize.odds import parse_odds_body, upsert_odds_rows

log = logging.getLogger(__name__)
FAMILIES = ("espn", "odds_featured", "odds_alternates", "kalshi_events", "kalshi_markets", "kalshi_orderbook", "kalshi_trades")
NORMALIZED_TABLES = ("odds_snapshots", "venue_quotes", "orderbook_snapshots", "venue_trades", "venue_markets", "games")
_EVENTS: dict[str, dict] = {}  # event_ticker -> event, refreshed from raw /events bodies
_OB_RE = re.compile(r"^/markets/([^/]+)/orderbook$")


def _sport_from_endpoint(endpoint: str, params: dict) -> str | None:
    if "americanfootball_nfl" in endpoint or endpoint.startswith("/nfl/"):
        return "nfl"
    if "americanfootball_ncaaf" in endpoint or endpoint.startswith("/college-football/"):
        return "ncaaf"
    s = (params or {}).get("series_ticker", "")
    if s.startswith("KXNFL"):
        return "nfl"
    if s.startswith("KXNCAAF"):
        return "ncaaf"
    return None


def _family_filter(family: str):
    src, ep = RawResponse.source, RawResponse.endpoint
    return {
        "espn": src == "espn",
        "odds_featured": (src == "odds_api") & ep.like("/sports/%/odds") & ~ep.like("/sports/%/events/%"),
        "odds_alternates": (src == "odds_api") & ep.like("/sports/%/events/%/odds"),
        "kalshi_events": (src == "kalshi") & (ep == "/events"),
        "kalshi_markets": (src == "kalshi") & (ep == "/markets"),
        "kalshi_orderbook": (src == "kalshi") & ep.like("/markets/%/orderbook"),
        "kalshi_trades": (src == "kalshi") & (ep == "/markets/trades"),
    }[family]


def _load_events_cache(session: Session) -> None:
    rows = session.execute(select(RawResponse).where(_family_filter("kalshi_events"), RawResponse.http_status == 200)
                           .order_by(RawResponse.id.desc()).limit(200)).scalars().all()
    for r in reversed(rows):
        for ev in (r.body or {}).get("events", []) if isinstance(r.body, dict) else []:
            if ev.get("event_ticker"):
                _EVENTS[ev["event_ticker"]] = ev


def _handle(session: Session, family: str, r: RawResponse, ctx: dict) -> None:
    sport = _sport_from_endpoint(r.endpoint, r.params)
    body = r.body
    if family == "espn" and sport:
        link_espn_scoreboard(session, sport, body)
    elif family in ("odds_featured", "odds_alternates") and sport:
        if family == "odds_featured":
            res = upsert_games_from_odds(session, sport, body, r.id)
            ctx.setdefault("unresolved_teams", []).extend(res.unresolved)
        upsert_odds_rows(session, sport, parse_odds_body(body, sport), r.id, r.run_id, r.fetched_at)
    elif family == "kalshi_events":
        for ev in (body or {}).get("events", []) if isinstance(body, dict) else []:
            if ev.get("event_ticker"):
                _EVENTS[ev["event_ticker"]] = ev
    elif family == "kalshi_markets" and sport:
        markets = (body or {}).get("markets", []) if isinstance(body, dict) else []
        upsert_venue_markets(session, sport, markets, _EVENTS, r.id, r.fetched_at)
        insert_venue_quotes(session, markets, r.id, r.run_id, r.fetched_at)
    elif family == "kalshi_orderbook":
        m = _OB_RE.match(r.endpoint)
        if m:
            insert_orderbook(session, m.group(1), body, r.id, r.fetched_at)
    elif family == "kalshi_trades":
        insert_trades(session, body, r.id)


def normalize_new(session: Session, batch: int = 500, ctx: dict | None = None) -> dict[str, int]:
    ctx = ctx if ctx is not None else {}
    if not _EVENTS:
        _load_events_cache(session)
    counts: dict[str, int] = {}
    for family in FAMILIES:
        state = session.get(NormalizeState, family) or NormalizeState(family=family, last_raw_id=0)
        session.add(state)
        rows = session.execute(select(RawResponse).where(_family_filter(family), RawResponse.http_status == 200,
                                                         RawResponse.id > state.last_raw_id)
                               .order_by(RawResponse.id).limit(batch)).scalars().all()
        n = 0
        for r in rows:
            try:
                _handle(session, family, r, ctx)
                n += 1
            except Exception:  # noqa: BLE001
                log.exception("normalize %s raw_id=%s failed", family, r.id)
                ctx.setdefault("normalize_errors", []).append({family: r.id})
            state.last_raw_id = r.id
        session.commit()
        counts[family] = n
    return counts


def reprocess(session: Session, from_raw_id: int = 0, families: list[str] | None = None, truncate: bool = False) -> dict[str, int]:
    if truncate:
        session.execute(text("truncate " + ", ".join(NORMALIZED_TABLES) + " restart identity cascade"))
    for family in families or FAMILIES:
        state = session.get(NormalizeState, family) or NormalizeState(family=family)
        state.last_raw_id = from_raw_id
        session.add(state)
    session.commit()
    _EVENTS.clear()
    total: dict[str, int] = {f: 0 for f in FAMILIES}
    while True:
        counts = normalize_new(session, batch=2000)
        for k, v in counts.items():
            total[k] += v
        if sum(counts.values()) == 0:
            return total
```
`games` truncation note: truncating `games` also resets `venue_markets.game_id` linkage via cascade on the truncate list; team aliases are NOT truncated (learned aliases are valuable and reproducible).

CLI additions in `harness/cli.py` (Typer):
```python
@app.command("seed-teams")
def seed_teams() -> None:
    configure_logging()
    import json, urllib.request
    from pathlib import Path
    from harness.matching.teams import load_manual_aliases, seed_teams_from_espn
    s = get_settings()
    factory = make_session_factory(make_engine(s.database_url))
    base = s.espn_base_url.rstrip("/")
    urls = [("nfl", f"{base}/nfl/teams?limit=100"), ("ncaaf", f"{base}/college-football/teams?limit=1000&groups=80")]
    with factory() as session:
        for sport, url in urls:
            body = json.load(urllib.request.urlopen(url, timeout=20))
            log.info("seeded %s teams from %s", seed_teams_from_espn(session, sport, body), url)
        n = load_manual_aliases(session, Path(__file__).parent / "matching" / "aliases_manual.yaml")
        session.commit()
        log.info("manual aliases loaded: %d", n)


@app.command("reprocess")
def reprocess_cmd(from_raw_id: int = 0, family: list[str] = typer.Option(None), truncate: bool = False) -> None:
    configure_logging()
    from harness.normalize.runner import reprocess
    s = get_settings()
    with make_session_factory(make_engine(s.database_url))() as session:
        log.info("reprocess done %s", reprocess(session, from_raw_id, list(family) if family else None, truncate))


@app.command("match-report")
def match_report(sport: str = "all") -> None:
    configure_logging()
    from sqlalchemy import func, text
    from harness.db.models import Game, VenueMarket
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
```
Tick integration in `harness/recorder/tick.py` `maybe_tick`, after the last checkpoint and before `finish_run`:
```python
            try:
                from harness.normalize.runner import normalize_new
                ctx["normalized"] = normalize_new(session, ctx=ctx)
            except Exception as e:  # noqa: BLE001
                log.exception("normalize failed")
                ctx["warnings"].append({"normalize": repr(e)})
```
and include `"normalized": ctx.get("normalized", {})`, `"unresolved_teams": sorted(set(ctx.get("unresolved_teams", [])))[:50]`, and `"normalize_errors": ctx.get("normalize_errors", [])` in `notes`. (Import at module top instead of inside the try if no circular import arises.)

- [ ] **Step 4: Run tests, full suite; run the real thing once**

```bash
export DATABASE_URL=postgresql+psycopg://harness:harness@localhost:5433/harness_test ODDS_API_KEY_FILE=$PWD/secrets/odds_api_key
.venv/bin/harness init-db && .venv/bin/harness seed-teams && .venv/bin/harness tick-once && .venv/bin/harness match-report
```
Expected with the fake key: ESPN and Kalshi normalize; the report shows NFL and NCAAF venue markets with a majority `matched` for games that exist from ESPN-only games (Odds API rows are 401 so no `odds_api_event_id`), and a list of unmatched reasons. Add the top unresolved Kalshi names to `aliases_manual.yaml`, run `harness reprocess --family kalshi_markets --from-raw-id 0`, and confirm the matched percentage rises. Record before/after percentages in the commit message.

- [ ] **Step 5: Commit**

```bash
git add harness/normalize/runner.py harness/cli.py harness/recorder/tick.py harness/matching/aliases_manual.yaml tests/test_runner.py tests/test_tick.py
git commit -m "feat: normalization runner, reprocess, seed-teams, match-report, tick integration"
```

---

### Task 8: Week 1 match-rate check and alias hardening (operational task, run after the first real game day)

**Files:**
- Modify: `harness/matching/aliases_manual.yaml`
- Create: `docs/runbooks/phase1-match-report.md`

**Interfaces:** none new.

- [ ] **Step 1: On the NAS after Sunday's games** run `harness match-report` and copy the output into the runbook under a dated heading.
- [ ] **Step 2: For every unmatched reason of the form `unresolved: <name>`**, look up the team's ESPN id (`curl -s ".../teams?limit=1000" | python3 -c ...` grep on displayName) and add `<name>: <id>` under `ncaaf: kalshi_name:` (or `nfl:`). For `side team unresolved: <name>` add the same. For `ambiguous` reasons, inspect the two games (likely a doubleheader or a rescheduled game) and add nothing; note it.
- [ ] **Step 3:** `harness reprocess --family kalshi_markets --from-raw-id 0` then `harness match-report` again. Acceptance: NFL `matched` ≥ 98%; NCAAF `matched + fuzzy` ≥ 95% and `fuzzy` ≤ 5%. If not met, repeat Step 2.
- [ ] **Step 4:** Commit the YAML and the runbook: `git commit -m "chore: week 1 alias hardening (nfl X%, ncaaf Y%)"`.

---

### Task 9: Kalshi request signing (needed by the WS recorder now and by phase 4 later)

**Files:**
- Create: `harness/venues/kalshi/auth.py`
- Modify: `harness/config/settings.py` (add `kalshi_key_id_file: Path = Path("/run/secrets/kalshi_key_id")`, `kalshi_private_key_file: Path = Path("/run/secrets/kalshi_private_key.pem")`, `kalshi_ws_url: str = "wss://api.elections.kalshi.com/trade-api/ws/v2"`, and methods `kalshi_key_id()`, `kalshi_private_key_pem()` reading the files; `has_kalshi_credentials() -> bool`)
- Modify: `pyproject.toml` (add `cryptography>=42`)
- Test: `tests/test_kalshi_auth.py`

**Interfaces:**
- `sign_request(key_id: str, private_key_pem: bytes, method: str, path: str, ts_ms: int) -> dict[str, str]` returning `{"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-TIMESTAMP": str(ts_ms), "KALSHI-ACCESS-SIGNATURE": base64(sig)}` where `sig = RSA-PSS(SHA256, salt_length=DIGEST_LENGTH)` over `f"{ts_ms}{method.upper()}{path}".encode()`. The path for WS is `/trade-api/ws/v2`; for REST it is the path without query string.

- [ ] **Step 1: Write the failing test**

`tests/test_kalshi_auth.py`:
```python
import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from harness.venues.kalshi.auth import sign_request


def test_sign_request_verifies_with_public_key():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
    headers = sign_request("kid-123", pem, "get", "/trade-api/ws/v2", 1788710400000)
    assert headers["KALSHI-ACCESS-KEY"] == "kid-123" and headers["KALSHI-ACCESS-TIMESTAMP"] == "1788710400000"
    sig = base64.b64decode(headers["KALSHI-ACCESS-SIGNATURE"])
    key.public_key().verify(sig, b"1788710400000GET/trade-api/ws/v2",
                            padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
```

- [ ] **Step 2: Run to verify failure; Step 3: Implement**

`harness/venues/kalshi/auth.py`:
```python
import base64

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


def sign_request(key_id: str, private_key_pem: bytes, method: str, path: str, ts_ms: int) -> dict[str, str]:
    key = serialization.load_pem_private_key(private_key_pem, password=None)
    msg = f"{ts_ms}{method.upper()}{path}".encode()
    sig = key.sign(msg, padding.PSS(mgf=padding.MGF1(hashes.SHA256()), salt_length=padding.PSS.DIGEST_LENGTH), hashes.SHA256())
    return {"KALSHI-ACCESS-KEY": key_id, "KALSHI-ACCESS-TIMESTAMP": str(ts_ms),
            "KALSHI-ACCESS-SIGNATURE": base64.b64encode(sig).decode()}
```
Settings additions as listed in Interfaces; `has_kalshi_credentials()` returns `self.kalshi_key_id_file.exists() and self.kalshi_private_key_file.exists()`.

- [ ] **Step 4: Run tests, full suite; commit**

```bash
git add harness/venues/kalshi/auth.py harness/config/settings.py pyproject.toml tests/test_kalshi_auth.py
git commit -m "feat: kalshi rsa-pss request signing and credential settings"
```

---

### Task 10: Kalshi WebSocket append-only recorder (conditional on credentials)

**Precondition:** the user has created a Kalshi API key; `secrets/kalshi_key_id` (the key id, no newline) and `secrets/kalshi_private_key.pem` exist and are mounted read-only into the container. If absent, `harness ws-record` exits 0 with a log line `kalshi credentials absent; ws recorder disabled` and the compose service stays defined but idle (restart policy `on-failure` so it does not loop).

**Files:**
- Create: `harness/venues/kalshi/ws.py`, `harness/recorder/ws_sink.py`
- Modify: `harness/cli.py` (`ws-record`), `docker-compose.yml` (`app-ws` service running `ws-record`, same image, secrets mounted, `restart: on-failure`), `docs/runbooks/phase0-deploy.md` (Kalshi key setup section), `pyproject.toml` (add `websocket-client>=1.8`)
- Test: `tests/test_kalshi_ws.py` (pure message handling and subscription planning; no live socket)

**Interfaces:**
- `select_ws_tickers(session, now) -> list[str]`: tickers of `venue_markets` with `match_status in (matched, fuzzy, manual)` whose game kicks off within the next 24 h or started less than 4 h ago, ordered by `volume_24h` of the latest quote desc, capped at `settings.ws_max_tickers` (default 500; Kalshi's per-connection limit is undocumented, so start at 500 and raise if no error).
- `WsSink(session_factory)` with `handle(msg: dict, received_at: datetime) -> str | None`: for `type == "trade"` insert `VenueTrade(source="ws", trade_id=msg.msg.trade_id, ts=from ts_ms, ...)` (`ON CONFLICT DO NOTHING ... RETURNING trade_id`, counting returned rows because psycopg3 reports rowcount -1 for that statement; REST and WS dedupe on `trade_id`); for `orderbook_snapshot` insert one `OrderbookEvent(kind="snapshot", raw=msg.msg, ts=received_at)`; for `orderbook_delta` insert `OrderbookEvent(kind="delta", side, price, delta, ts=from ts_ms or received_at)`; track `(sid → last seq)` and log a warning `seq gap sid=.. expected=.. got=..` on gaps (also insert an `OrderbookEvent(kind="gap")`). Commits every 100 messages or 2 s, whichever first. Returns the message type handled.
- `WsRecorder(settings, session_factory, sink, ws_factory=websocket.create_connection, clock=utcnow)` with `run_forever()`: connect with signed headers (`sign_request(key_id, pem, "GET", "/trade-api/ws/v2", now_ms)`; verified working), subscribe `{"id": 1, "cmd": "subscribe", "params": {"channels": ["trade", "orderbook_delta"], "market_tickers": tickers}}`, loop `recv()` with a 30 s timeout, pass JSON messages to the sink, reply to ping frames (websocket-client does this automatically when `enable_multithread`/`ping` handling is default; verify), every 5 minutes recompute `select_ws_tickers` and send `update_subscription` `add_markets` / `delete_markets` with the diff, reconnect with exponential backoff (1 s → 60 s) on any exception, and stop on SIGTERM. `settings.kalshi_ws_url` (verified) is tried first; the fallback host is kept for resilience only.

- [ ] **Step 1: Write failing tests**

`tests/test_kalshi_ws.py`:
```python
from datetime import datetime, timezone
from decimal import Decimal

from sqlalchemy.orm import sessionmaker

from harness.db.models import OrderbookEvent, VenueTrade
from harness.recorder.ws_sink import WsSink
from harness.venues.kalshi.ws import diff_subscriptions

NOW = datetime(2026, 9, 13, 17, 0, tzinfo=timezone.utc)


def test_sink_trade_and_delta_and_gap(db_session):
    factory = sessionmaker(bind=db_session.get_bind(), expire_on_commit=False)
    sink = WsSink(factory, commit_every=1)
    trade = {"type": "trade", "sid": 1, "seq": 1, "msg": {"trade_id": "t-1", "market_ticker": "K1", "yes_price_dollars": "0.3600",
             "no_price_dollars": "0.6400", "count_fp": "136.00", "taker_side": "no", "is_block_trade": False, "ts_ms": 1789234000000}}
    assert sink.handle(trade, NOW) == "trade"
    assert sink.handle(trade, NOW) == "trade"  # duplicate ignored
    assert db_session.query(VenueTrade).filter_by(trade_id="t-1").count() == 1
    snap = {"type": "orderbook_snapshot", "sid": 2, "seq": 1, "msg": {"market_ticker": "K1", "yes_dollars_fp": [["0.35", "10.00"]], "no_dollars_fp": []}}
    delta = {"type": "orderbook_delta", "sid": 2, "seq": 3, "msg": {"market_ticker": "K1", "price_dollars": "0.3500", "delta_fp": "-4.00", "side": "yes", "ts_ms": 1789234001000}}
    sink.handle(snap, NOW)
    sink.handle(delta, NOW)
    kinds = [e.kind for e in db_session.query(OrderbookEvent).order_by(OrderbookEvent.id).all()]
    assert kinds == ["snapshot", "gap", "delta"]
    d = db_session.query(OrderbookEvent).filter_by(kind="delta").one()
    assert (d.side, d.price, d.delta) == ("yes", Decimal("0.3500"), Decimal("-4.00"))


def test_diff_subscriptions():
    add, remove = diff_subscriptions(current=["A", "B"], wanted=["B", "C"])
    assert (add, remove) == (["C"], ["A"])
```

- [ ] **Step 2: Run to verify failure; Step 3: Implement**

`harness/recorder/ws_sink.py`:
```python
import logging
import time
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import sessionmaker

from harness.db.models import OrderbookEvent, VenueTrade

log = logging.getLogger(__name__)


def _dec(v):
    try:
        return Decimal(str(v)) if v not in (None, "") else None
    except InvalidOperation:
        return None


def _ts(ms, fallback: datetime) -> datetime:
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError):
        return fallback


class WsSink:
    def __init__(self, session_factory: sessionmaker, commit_every: int = 100, commit_interval_s: float = 2.0):
        self._factory = session_factory
        self._session = session_factory()
        self._pending = 0
        self._last_commit = time.monotonic()
        self._commit_every, self._interval = commit_every, commit_interval_s
        self._last_seq: dict[int, int] = {}

    def _maybe_commit(self, force: bool = False) -> None:
        if force or self._pending >= self._commit_every or time.monotonic() - self._last_commit >= self._interval:
            self._session.commit()
            self._pending, self._last_commit = 0, time.monotonic()

    def _check_seq(self, sid: int, seq: int, ticker: str, ts: datetime) -> None:
        last = self._last_seq.get(sid)
        if last is not None and seq != last + 1:
            log.warning("seq gap sid=%s expected=%s got=%s", sid, last + 1, seq)
            self._session.add(OrderbookEvent(ticker=ticker, ts=ts, sid=sid, seq=seq, kind="gap", raw={"expected": last + 1, "got": seq}))
            self._pending += 1
        self._last_seq[sid] = seq

    def handle(self, msg: dict, received_at: datetime) -> str | None:
        kind, body = msg.get("type"), msg.get("msg") or {}
        sid, seq = msg.get("sid"), msg.get("seq")
        ticker = body.get("market_ticker", "")
        if kind == "trade":
            price, count = _dec(body.get("yes_price_dollars")), _dec(body.get("count_fp"))
            if body.get("trade_id") and ticker and price is not None and count is not None:
                stmt = insert(VenueTrade).values(venue="kalshi", trade_id=body["trade_id"], ticker=ticker, ts=_ts(body.get("ts_ms"), received_at),
                                                 yes_price=price, count=count, taker_side=body.get("taker_side") or "yes",
                                                 is_block=bool(body.get("is_block_trade")), source="ws", raw_id=None
                                                 ).on_conflict_do_nothing().returning(VenueTrade.trade_id)
                # psycopg3 reports rowcount -1 for ON CONFLICT DO NOTHING; count returned rows instead (Task 6 ruling)
                self._pending += len(self._session.execute(stmt).fetchall())
        elif kind == "orderbook_snapshot":
            if sid is not None and seq is not None:
                self._last_seq[sid] = seq
            self._session.add(OrderbookEvent(ticker=ticker, ts=received_at, sid=sid or 0, seq=seq or 0, kind="snapshot", raw=body))
            self._pending += 1
        elif kind == "orderbook_delta":
            ts = _ts(body.get("ts_ms"), received_at)
            if sid is not None and seq is not None:
                self._check_seq(sid, seq, ticker, ts)
            self._session.add(OrderbookEvent(ticker=ticker, ts=ts, sid=sid or 0, seq=seq or 0, kind="delta", side=body.get("side"),
                                             price=_dec(body.get("price_dollars")), delta=_dec(body.get("delta_fp")), raw=body))
            self._pending += 1
        else:
            return None
        self._maybe_commit()
        return kind

    def close(self) -> None:
        self._maybe_commit(force=True)
        self._session.close()
```

`harness/venues/kalshi/ws.py`:
```python
import json
import logging
import signal
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

import websocket
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from harness.config.settings import Settings
from harness.db.models import Game, VenueMarket, VenueQuote
from harness.venues.kalshi.auth import sign_request

log = logging.getLogger(__name__)
CHANNELS = ["trade", "orderbook_delta"]
FALLBACK_URL = "wss://external-api-ws.kalshi.com/"


def diff_subscriptions(current: list[str], wanted: list[str]) -> tuple[list[str], list[str]]:
    c, w = set(current), set(wanted)
    return sorted(w - c), sorted(c - w)


def select_ws_tickers(session: Session, now: datetime, cap: int) -> list[str]:
    lo, hi = now - timedelta(hours=4), now + timedelta(hours=24)
    rows = session.execute(
        select(VenueMarket.ticker).join(Game, Game.id == VenueMarket.game_id)
        .where(VenueMarket.match_status.in_(("matched", "fuzzy", "manual")), Game.kickoff_utc >= lo, Game.kickoff_utc <= hi)
        .order_by(VenueMarket.last_seen_at.desc())).scalars().all()
    return list(rows)[:cap]


class WsRecorder:
    def __init__(self, settings: Settings, session_factory: sessionmaker, sink, ws_factory: Callable = websocket.create_connection,
                 clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc)):
        self.s, self.factory, self.sink, self.ws_factory, self.clock = settings, session_factory, sink, ws_factory, clock
        self._stop = False
        self._sids: list[int] = []
        self._current: list[str] = []

    def _headers(self) -> list[str]:
        ts_ms = int(self.clock().timestamp() * 1000)
        h = sign_request(self.s.kalshi_key_id(), self.s.kalshi_private_key_pem(), "GET", "/trade-api/ws/v2", ts_ms)
        return [f"{k}: {v}" for k, v in h.items()]

    def _connect(self):
        for url in (self.s.kalshi_ws_url, FALLBACK_URL):
            try:
                ws = self.ws_factory(url, header=self._headers(), timeout=30)
                log.info("ws connected %s", url)
                return ws
            except websocket.WebSocketBadStatusException as e:
                log.warning("ws handshake failed %s: %s", url, e)
        raise RuntimeError("ws connect failed on all urls")

    def _subscribe(self, ws, tickers: list[str]) -> None:
        ws.send(json.dumps({"id": 1, "cmd": "subscribe", "params": {"channels": CHANNELS, "market_tickers": tickers}}))
        self._current = list(tickers)

    def _resubscribe(self, ws, wanted: list[str], msg_id: int) -> None:
        add, remove = diff_subscriptions(self._current, wanted)
        for action, tickers in (("add_markets", add), ("delete_markets", remove)):
            if tickers and self._sids:
                ws.send(json.dumps({"id": msg_id, "cmd": "update_subscription",
                                    "params": {"sids": self._sids, "market_tickers": tickers, "action": action}}))
        self._current = list(wanted)

    def stop(self, *_):
        self._stop = True

    def run_forever(self) -> None:
        signal.signal(signal.SIGTERM, self.stop)
        signal.signal(signal.SIGINT, self.stop)
        backoff = 1.0
        while not self._stop:
            try:
                with self.factory() as session:
                    tickers = select_ws_tickers(session, self.clock(), self.s.ws_max_tickers)
                ws = self._connect()
                self._sids = []
                self._subscribe(ws, tickers)
                backoff, last_plan, msg_id = 1.0, time.monotonic(), 2
                while not self._stop:
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        continue
                    if not raw:
                        break
                    msg = json.loads(raw)
                    if msg.get("type") == "subscribed":
                        sid = (msg.get("msg") or {}).get("sid", msg.get("sid"))
                        if sid is not None:
                            self._sids.append(sid)
                    else:
                        self.sink.handle(msg, self.clock())
                    if time.monotonic() - last_plan >= 300:
                        with self.factory() as session:
                            wanted = select_ws_tickers(session, self.clock(), self.s.ws_max_tickers)
                        self._resubscribe(ws, wanted, msg_id)
                        msg_id, last_plan = msg_id + 1, time.monotonic()
                ws.close()
            except Exception as e:  # noqa: BLE001
                log.warning("ws loop error: %r; reconnecting in %.0fs", e, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
        self.sink.close()
```
Add `ws_max_tickers: int = 500` to `Settings`. CLI:
```python
@app.command("ws-record")
def ws_record() -> None:
    configure_logging()
    s = get_settings()
    if not s.has_kalshi_credentials():
        log.info("kalshi credentials absent; ws recorder disabled")
        return
    from harness.recorder.ws_sink import WsSink
    from harness.venues.kalshi.ws import WsRecorder
    factory = make_session_factory(make_engine(s.database_url))
    WsRecorder(s, factory, WsSink(factory)).run_forever()
```
Compose service:
```yaml
  app-ws:
    build: .
    command: ["ws-record"]
    env_file: .env
    environment:
      ODDS_API_KEY_FILE: /run/secrets/odds_api_key
      KALSHI_KEY_ID_FILE: /run/secrets/kalshi_key_id
      KALSHI_PRIVATE_KEY_FILE: /run/secrets/kalshi_private_key.pem
    volumes:
      - ./secrets/odds_api_key:/run/secrets/odds_api_key:ro
      - ./secrets/kalshi_key_id:/run/secrets/kalshi_key_id:ro
      - ./secrets/kalshi_private_key.pem:/run/secrets/kalshi_private_key.pem:ro
    depends_on:
      postgres:
        condition: service_healthy
    restart: on-failure
    stop_grace_period: 30s
```
Runbook: how to create a Kalshi API key (Kalshi account → Settings → API keys → download the private key; store as `secrets/kalshi_private_key.pem`, key id as `secrets/kalshi_key_id`, both `chmod 600` and `chown 65534:65534`), and the note that the two secret files must exist as files (not directories) before `compose up` or the service exits idle.

- [ ] **Step 4: Run tests, full suite. Manual smoke (only if credentials exist):** `harness ws-record` for 2 minutes during a game window, then `select kind, count(*) from orderbook_events group by 1;` and `select source, count(*) from venue_trades group by 1;` — expect `ws` trades and snapshot/delta events. Record the working WS URL in the runbook.

- [ ] **Step 5: Commit**

```bash
git add harness/venues/kalshi/ws.py harness/recorder/ws_sink.py harness/cli.py harness/config/settings.py docker-compose.yml docs/runbooks pyproject.toml tests/test_kalshi_ws.py
git commit -m "feat: kalshi websocket append-only recorder (conditional on credentials)"
```

---

## Self-review

**Spec coverage (phase 1 scope):** §5.3 canonical model, thresholds from `floor_strike`, matching via event title + ticker date, confidence rules, alias coverage, fixture of real tickers (Tasks 1, 2, 3, 5); §5.5 tables `teams, team_aliases, games, odds_snapshots, venue_markets, venue_quotes, orderbook_snapshots, venue_trades, orderbook_events` (Task 2); §5.4 ESPN scores settle games in paper mode later, statuses/scores captured now (Task 4); `harness reprocess` rebuildability (Task 7); §4.1 WS recorder as append-only, never driving execution (Task 10); §15 phase 1 "check Week 1 match rate; fix aliases" (Task 8). Not in phase 1 by design: `market_gap_snapshots`, `fair_values`, `benchmarks`, strategy variants (phase 2); `raw_responses` unchanged.

**Deviations, deliberate:** ESPN-only games are created so Kalshi FCS-vs-FBS contracts can still match even when The Odds API omits the game; the spec's "Odds API event id is the canonical identity" still holds where one exists. The WS recorder is conditional on a Kalshi API key because the WebSocket handshake requires RSA-signed headers even for public channels (verified against the docs), which the spec did not anticipate.

**Placeholder scan:** none. Fixture ESPN ids (Giants 19, Rams 14, Seahawks 26, Patriots 17, Chiefs 12, Saints 18, San José State 23, Arkansas-Pine Bluff 2029, UCLA 26, LSU 99) were verified against the live endpoint on 2026-09-06. Teams are keyed `(sport, id)` because ids collide across sports.

**Type consistency:** `resolve_team(session, sport, raw_name, sources=...) -> (int | None, str)` used identically in Tasks 3–7; `MarketClass`/`EventMatch` field names match between Task 5 and Task 6; `find_game_by_pair` signature `(session, sport, team_a, team_b, date_utc, tolerance_days=1)` in Tasks 4, 5, 6; `upsert_venue_markets(session, sport, markets, events_by_ticker, raw_id, fetched_at)` in Tasks 6 and 7; `normalize_new(session, batch=500, ctx=None)` in Task 7 tick integration; `sign_request` in Tasks 9 and 10.
