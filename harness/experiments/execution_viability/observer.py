"""Arm C's bounded faster-observation cohort (addendum §1.6(e)-(i), §4.2, §4.6).

**What this is.** Arm C is the only arm that cannot be reconstructed from tape: historical
record cannot say what an unrecorded faster fetch would have returned, so C is run
prospectively. This module is that collection and nothing else -- it places no order, decides
no action and writes no row outside `exp_observation` and the hashed file tree of §2.

**Off by default, twice.** `observer_pass` is registered in `harness/research/worker.py`'s
`PASS_MODULES` as a *string*; `load_passes()` imports this module at run time and the pass
returns immediately unless `Settings.exp_observer_enabled` is set **and** a frozen `exp_run`
row exists. Activation is §4.6's journaled checklist, whose steps 0, 6 and 7 are the user's.
Nothing here schedules, grants or enables anything.

**Cost, bounded in code.** The featured endpoint returns every event of a sport, so one call
per sport per interval costs `CREDITS_PER_CALL = 3` (unique markets returned x one region)
whatever the cohort size: six credits per 120 s interval for two sports, ~4,320 a day. Two
checks run before **every** call (I9): the recorder's own guard on the provider's balance -
`harness/recorder/tick.py:1411`'s expression, reproduced verbatim in `budget_ok` - and the
per-run cap `Settings.exp_observer_credit_cap = 60,000`. Over either one the observer goes
dormant for the rest of the run, labels every scheduled-but-unmade read `exp_skipped_budget`
with a null `credits_remaining`, and never retries.

**The shared aggregate is the provider's counter, not a row of ours (I9).** Every call parses
`x-requests-last` / `x-requests-remaining` with `harness.feeds.odds_api.parse_credit_headers`
onto the `exp_observation` rows it writes, so the observer and the recorder read one
authoritative balance. No member of §1.1(c)'s refusal list is written and `harness_exp` holds
no privilege on any of them. The first call of a run is judged against the recorder's freshest
`recorder.credits_remaining` sample (§3 row 5's own query); an unknown balance is a refusal.

**Prices (I12).** The cohort's markets are priced through `harness/pricing/`'s **pure**
arithmetic called as functions on the fetched quotes - `direct.direct_fair` over
`lines.ml_pair`/`spread_pair`/`total_pair`, with `fair.stale_allowance_s` deciding which
quotes are fresh enough to price. No pricing entry point is called and nothing is written to
the production pricing table: the observer's prices exist only in `exp_observation.fair_p`.

**Arm identity (ruling D27).** `exp_observation` has no `arm_id` column and this task adds no
DDL, so arm C's rows are identified by `source = 'exp_observer'`.

**The call itself is unchanged (gate 5).** `OddsApiClient.fetch_featured(sport)` is called as
the recorder calls it: same URL, same `FEATURED_MARKETS`, same bookmakers string.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from harness.config.settings import Settings
from harness.execution.plan import confidently_matched
from harness.experiments.execution_viability import EXP_DB_ROLE, storage
from harness.feeds.http import FetchResult, HttpClient
from harness.feeds.odds_api import FEATURED_MARKETS, OddsApiClient, parse_credit_headers
from harness.matching.teams import resolve_team
from harness.normalize.odds import parse_odds_body
from harness.pricing.direct import direct_fair
from harness.pricing.fair import stale_allowance_s
from harness.pricing.lines import Line, LineKey, ml_pair, spread_pair, total_pair
from harness.recorder.cadence import SPORTS
from harness.research.worker import PassFn, register_closer, register_pass

log = logging.getLogger("harness.exp")

#: §1.6(i): one featured call costs unique markets returned x regions, one region. Three.
CREDITS_PER_CALL: int = 3
#: §1.6(i): the status every scheduled-but-unmade read carries once the observer is dormant.
SKIPPED_BUDGET: str = "exp_skipped_budget"
#: The status of a read that was made.
OBSERVED: str = "exp_observed"
#: Ruling D27: arm C's rows are identified by their source, not by an `arm_id` column.
OBSERVER_SOURCE: str = "exp_observer"

#: §1.6(f)'s cohort bounds and §1.6(e)'s two sports, in the recorder's own order. `SPORTS`
#: maps the internal key to the endpoint's: {"nfl": "americanfootball_nfl", ...}.
OBSERVED_SPORTS: tuple[str, ...] = tuple(SPORTS)
COHORT_MAX: int = 8
COHORT_PER_SPORT_MAX: int = 4
KICKOFF_MIN_H: int = 24
KICKOFF_MAX_H: int = 120
#: The venue market shapes a sharp book prices directly (`harness/pricing/fair.py`'s shapes).
DIRECT_FAIR_MARKETS: tuple[str, ...] = ("moneyline", "spread", "total")
#: The featured Odds API market keys, the only ones this module reads out of a response.
FEATURED_KEYS: tuple[str, ...] = ("h2h", "spreads", "totals")


def budget_ok(s, *, credits_used: int, remaining: int | None) -> tuple[bool, str | None]:
    """The two checks of §1.6(i), both before every call.

    (1) The recorder's own guard, on the provider's balance:
        `remaining is None or remaining < credits_watch_fraction * odds_monthly_credits`
        (`harness/recorder/tick.py:1411`). Unknown is a refusal, not a pass.
    (2) The run cap: `credits_used + CREDITS_PER_CALL > exp_observer_credit_cap`.
    """
    if remaining is None or remaining < s.credits_watch_fraction * s.odds_monthly_credits:
        return False, "credits_watch_fraction"
    if credits_used + CREDITS_PER_CALL > s.exp_observer_credit_cap:
        return False, "exp_observer_credit_cap"
    return True, None


# --- §1.6(f): the cohort ------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CohortMarket:
    """One confidently matched venue market of a cohort game (§1.6f)."""
    venue_market_id: int
    market_type: str
    threshold: Decimal | None
    side_team_id: int | None
    side: str | None


@dataclass(frozen=True, slots=True)
class CohortGame:
    game_id: int
    sport: str
    #: The provider's event id: the key the fetched payload is joined on, and the identity
    #: `sha256(seed || canonical_game_id)` orders the stratum by.
    canonical_game_id: str
    kickoff_utc: datetime
    home_team_id: int
    away_team_id: int
    markets: tuple[CohortMarket, ...]


@dataclass(frozen=True, slots=True)
class CohortSelection:
    games: tuple[CohortGame, ...]        # <= 8, <= 4 per sport
    strata: dict[str, str]               # sport -> "selected: n" | "unavailable: <reason>"


#: §1.6(f)'s eligible population: games of the two sports whose kickoff sits in the 24-120 hour
#: window, with their venue markets. Bounded by `ix_games_sport_kick (sport, kickoff_utc)` and
#: joined on `venue_markets.game_id`, which is indexed. Confidence is applied in Python through
#: `plan.confidently_matched`, the same predicate the executor uses.
_ELIGIBLE = text(
    "select g.id as game_id, g.sport, g.odds_api_event_id, g.kickoff_utc, "
    "g.home_team_id, g.away_team_id, vm.id as venue_market_id, vm.market_type, "
    "vm.threshold, vm.side_team_id, vm.side, vm.match_status "
    "from games g join venue_markets vm on vm.game_id = g.id "
    "where g.sport = any(:sports) and g.kickoff_utc >= :lo and g.kickoff_utc <= :hi "
    "and g.odds_api_event_id is not null "
    "order by g.sport, g.kickoff_utc, g.id, vm.id")


def _order_key(seed: int, canonical_game_id: str) -> str:
    """§1.6(f): `sha256(seed || canonical_game_id)`, so a seed fixes the stratum's order."""
    return hashlib.sha256(f"{seed}|{canonical_game_id}".encode()).hexdigest()


def _eligible_games(session: Session, *, lo: datetime, hi: datetime) -> dict[str, list]:
    """The eligible games per sport, each with its confidently matched direct-fair markets."""
    by_game: dict[int, dict] = {}
    for row in session.execute(_ELIGIBLE, {"sports": list(OBSERVED_SPORTS),
                                           "lo": lo, "hi": hi}).all():
        if not confidently_matched(row.match_status):
            continue
        if row.market_type not in DIRECT_FAIR_MARKETS:
            continue
        entry = by_game.setdefault(row.game_id, {"row": row, "markets": []})
        entry["markets"].append(CohortMarket(
            venue_market_id=row.venue_market_id, market_type=row.market_type,
            threshold=row.threshold, side_team_id=row.side_team_id, side=row.side))
    per_sport: dict[str, list] = {sport: [] for sport in OBSERVED_SPORTS}
    for entry in by_game.values():
        row = entry["row"]
        per_sport.setdefault(row.sport, []).append(CohortGame(
            game_id=row.game_id, sport=row.sport,
            canonical_game_id=row.odds_api_event_id, kickoff_utc=row.kickoff_utc,
            home_team_id=row.home_team_id, away_team_id=row.away_team_id,
            markets=tuple(entry["markets"])))
    return per_sport


def select_cohort(session: Session, *, now: datetime, seed: int,
                  freeze_at: datetime) -> CohortSelection:
    """§1.6(f): at most eight games, at most four per sport, kickoff 24-120 h from the freeze.

    Deterministic in `seed`: the stratum is ordered by `sha256(seed || canonical_game_id)` and
    the first four taken. An **unavailable stratum is recorded as unavailable** and never
    backfilled with convenient winners or anchor teams (§1.6f), which is what `strata` carries
    into the manifest and the report.
    """
    lo = freeze_at + timedelta(hours=KICKOFF_MIN_H)
    hi = freeze_at + timedelta(hours=KICKOFF_MAX_H)
    per_sport = _eligible_games(session, lo=lo, hi=hi)
    games: list[CohortGame] = []
    strata: dict[str, str] = {}
    for sport in OBSERVED_SPORTS:
        candidates = sorted(per_sport.get(sport, []),
                            key=lambda g: _order_key(seed, g.canonical_game_id))
        room = min(COHORT_PER_SPORT_MAX, COHORT_MAX - len(games))
        picked = candidates[:max(0, room)]
        if picked:
            strata[sport] = f"selected: {len(picked)}"
        else:
            strata[sport] = (f"unavailable: {len(candidates)} eligible games in the window")
        games.extend(picked)
    log.debug("exp observer cohort: seed=%s games=%s strata=%s", seed, len(games), strata)
    return CohortSelection(games=tuple(games), strata=strata)


# --- the run's own bounded state -----------------------------------------------------------------


@dataclass(slots=True)
class ObserverRun:
    """What one prospective run knows between intervals.

    `credits_used` and `remaining` are the two numbers `budget_ok` is asked about, and
    `dormant` is the refusal that, once taken, is never retried for the rest of the run
    (§1.6i). The object is rebuilt from the database the first time a process sees the run, so
    a restart mid-run resumes the same accounting rather than starting the cap over.
    """
    run_id: str
    games: tuple[CohortGame, ...]
    seed: int
    credits_used: int = 0
    remaining: int | None = None
    dormant: str | None = None
    last_observed_at: datetime | None = None


#: §4.2/§7 item 3(vi): the one bounded read that decides whether the pass does anything at all.
#: One row on `exp_run`'s primary key ordering, never a scan of the table's body.
_FROZEN_RUN = text("select run_id, manifest from exp_run where status = 'frozen' "
                   "order by created_at desc limit 1")
_FROZEN_RUN_BY_ID = text("select run_id, manifest from exp_run "
                         "where run_id = :r and status = 'frozen'")
#: §3 row 5's own three queries, which are also how a restarted process recovers its counters.
_RUN_CREDITS = text("select coalesce(sum(credits), 0) from exp_observation where run_id = :r")
_RUN_BALANCE = text("select credits_remaining from exp_observation where run_id = :r "
                    "and credits_remaining is not null order by observed_at desc limit 1")
_RUN_LAST_AT = text("select max(observed_at) from exp_observation where run_id = :r")
_RECORDER_BALANCE = text("select value from metric_samples "
                         "where name = 'recorder.credits_remaining' order by ts desc limit 1")
#: The frozen cohort, resolved from the manifest's canonical ids.
_COHORT_BY_ID = text(
    "select g.id as game_id, g.sport, g.odds_api_event_id, g.kickoff_utc, "
    "g.home_team_id, g.away_team_id, vm.id as venue_market_id, vm.market_type, "
    "vm.threshold, vm.side_team_id, vm.side, vm.match_status "
    "from games g join venue_markets vm on vm.game_id = g.id "
    "where g.odds_api_event_id = any(:ids) order by g.sport, g.id, vm.id")


def recorder_balance(session: Session) -> int | None:
    """The recorder's freshest `recorder.credits_remaining` sample, or None.

    §3 row 5's third query, read on `ix_metric_samples_name_ts`. It is the only balance the
    observer can know **before** its own first call of a run, and `budget_ok` treats None as a
    refusal, so a deployment whose recorder has never published one observes nothing.
    """
    value = session.execute(_RECORDER_BALANCE).scalar()
    return None if value is None else int(value)


def _cohort_from_manifest(session: Session, ids: list[str]) -> tuple[CohortGame, ...]:
    """The frozen cohort's games and their confidently matched direct-fair markets (§1.6g)."""
    if not ids:
        return ()
    by_game: dict[int, dict] = {}
    for row in session.execute(_COHORT_BY_ID, {"ids": list(ids)}).all():
        if not confidently_matched(row.match_status):
            continue
        if row.market_type not in DIRECT_FAIR_MARKETS:
            continue
        entry = by_game.setdefault(row.game_id, {"row": row, "markets": []})
        entry["markets"].append(CohortMarket(
            venue_market_id=row.venue_market_id, market_type=row.market_type,
            threshold=row.threshold, side_team_id=row.side_team_id, side=row.side))
    order = {value: index for index, value in enumerate(ids)}
    games = [CohortGame(
        game_id=entry["row"].game_id, sport=entry["row"].sport,
        canonical_game_id=entry["row"].odds_api_event_id,
        kickoff_utc=entry["row"].kickoff_utc, home_team_id=entry["row"].home_team_id,
        away_team_id=entry["row"].away_team_id, markets=tuple(entry["markets"]))
        for entry in by_game.values()]
    games.sort(key=lambda g: (OBSERVED_SPORTS.index(g.sport) if g.sport in OBSERVED_SPORTS
                              else len(OBSERVED_SPORTS), order.get(g.canonical_game_id, 0)))
    return tuple(games)


def begin_run(session: Session, s: Settings, *, run_id: str,
              now: datetime) -> ObserverRun | None:
    """The run's state, rebuilt from its frozen manifest and its own rows.

    Returns None where the run is not frozen. The cohort is the manifest's -- §1.6(g) freezes
    the game list before any observation is opened, so the pass never re-selects one.
    """
    row = session.execute(_FROZEN_RUN_BY_ID, {"r": run_id}).first()
    if row is None:
        return None
    manifest = dict(row.manifest or {})
    games = _cohort_from_manifest(session, list(manifest.get("cohort") or []))
    used = int(session.execute(_RUN_CREDITS, {"r": run_id}).scalar() or 0)
    balance = session.execute(_RUN_BALANCE, {"r": run_id}).scalar()
    return ObserverRun(
        run_id=run_id, games=games, seed=int(manifest.get("selection_seed") or 0),
        credits_used=used,
        remaining=int(balance) if balance is not None else recorder_balance(session),
        last_observed_at=session.execute(_RUN_LAST_AT, {"r": run_id}).scalar())


# --- §1.6(e): one interval -------------------------------------------------------------------


def _payload_and_raw(body) -> tuple[list | dict | None, bytes]:
    """The parsed response and the bytes stored under §2's hashed file tree.

    `HttpClient.get` parses the response and keeps no bytes (`FetchResult.body` is typed
    `dict | list | None`), and this task may not change `harness/feeds/http.py`, so a live
    response is re-serialised canonically here and `body_sha256` is the digest of **what was
    stored**. A caller that does hand over bytes (a fixture, or a future client that keeps
    them) has them written through untouched.
    """
    if isinstance(body, (bytes, bytearray)):
        return json.loads(bytes(body)), bytes(body)
    if isinstance(body, str):
        return json.loads(body), body.encode()
    return body, json.dumps(body, sort_keys=True, separators=(",", ":"),
                            default=str).encode()


def _lines_for_game(session: Session, sport: str, rows, *, fetched_at: datetime,
                    cache: dict) -> dict:
    """The fetched quotes as `harness/pricing/lines.py`'s own `Line` objects.

    Nothing is written: `parse_odds_body` is the recorder's pure parser and the team resolver
    is a read. A quote whose outcome cannot be resolved to a team is dropped rather than
    guessed, exactly as the normalize path drops it.
    """
    out: dict[LineKey, Line] = {}
    for row in rows:
        if row.market_type not in FEATURED_KEYS:
            continue
        team_id, side = None, None
        if (row.outcome_name or "") in ("Over", "Under"):
            side = row.outcome_name.lower()
        else:
            if row.outcome_name not in cache:
                cache[row.outcome_name] = resolve_team(
                    session, sport, row.outcome_name,
                    sources=("odds_api", "espn_display"))[0]
            team_id = cache[row.outcome_name]
            if team_id is None:
                continue
        key = LineKey(row.book, row.market_type, team_id, side, row.point)
        out[key] = Line(key, row.price, row.last_update, fetched_at)
    return out


def _fair_p(lines: dict, game: CohortGame, market: CohortMarket, *, now: datetime,
            s: Settings) -> Decimal | None:
    """One market's direct fair probability, from the pure arithmetic only (I12).

    `fair.stale_allowance_s("featured", ttk, s)` is the derivation §1.6(e) names: a quote
    older than the featured cadence plus this deployment's tick budget is not priced, so a
    book that stopped updating cannot carry an observation forward.
    """
    if not lines:
        return None
    ttk_minutes = max(0.0, (game.kickoff_utc - now).total_seconds() / 60.0)
    allowance = stale_allowance_s("featured", ttk_minutes, s)
    fresh = {key: line for key, line in lines.items()
             if line.last_update is None
             or (now - line.last_update).total_seconds() <= allowance}
    team_id = market.side_team_id
    opponent = game.away_team_id if team_id == game.home_team_id else game.home_team_id
    if market.market_type == "moneyline":
        if team_id is None:
            return None
        pairs = ml_pair(fresh, team_id, opponent)
    elif market.market_type == "spread":
        if team_id is None or market.threshold is None:
            return None
        pairs = spread_pair(fresh, team_id, opponent, market.threshold)
    else:
        if market.threshold is None:
            return None
        pairs = total_pair(fresh, market.threshold)
    result = direct_fair(pairs, now)
    return None if result is None else result[0].fair_p


def _row(*, run_id: str, observed_at: datetime, available_at: datetime, sport: str,
         game_id: int | None, venue_market_id: int | None, fair_p: Decimal | None,
         credits: int, credits_last: int | None, credits_remaining: int | None,
         status: str, body_path: str | None, body_sha256: str | None) -> dict:
    """One `exp_observation` row (§2). `available_at >= observed_at` by construction."""
    return {"run_id": run_id, "observed_at": observed_at, "available_at": available_at,
            "sport": sport, "game_id": game_id, "venue_market_id": venue_market_id,
            "fair_p": fair_p, "source": OBSERVER_SOURCE, "credits": credits,
            "credits_last": credits_last, "credits_remaining": credits_remaining,
            "status": status, "body_path": body_path, "body_sha256": body_sha256}


def _scheduled_reads(run: ObserverRun, sport: str) -> list[tuple[CohortGame | None,
                                                                 CohortMarket | None]]:
    """The reads one call of `sport` covers: one per cohort market of that sport.

    A sport in the cohort with no market left to read still has **one** scheduled read, so the
    call's credits always have a row to land on and a refusal always has a row to label.
    """
    reads = [(game, market) for game in run.games if game.sport == sport
             for market in game.markets]
    return reads or [(None, None)]


def observe_once(session: Session, now: datetime, s: Settings, client: OddsApiClient, *,
                 run: ObserverRun, writer) -> int:
    """One interval: one `fetch_featured` per sport in the cohort. Returns credits spent.

    §1.6(e): the featured endpoint returns every event of the sport, so the cost is per sport
    per interval and **not** per game -- eight games cost exactly what two do. §1.6(i): the two
    budget checks run before every call, and the first refusal makes the run dormant, after
    which each scheduled-but-unmade read gets its own `exp_skipped_budget` row with a null
    `credits_remaining` and no call is ever made again.
    """
    sports = [sport for sport in OBSERVED_SPORTS
              if any(game.sport == sport for game in run.games)]
    table = writer.table("exp_observation")
    spent = 0
    for sport in sports:
        if run.dormant is None:
            ok, reason = budget_ok(s, credits_used=run.credits_used, remaining=run.remaining)
            if not ok:
                run.dormant = reason
                log.warning("exp observer dormant for run %s: %s (used=%s remaining=%s)",
                            run.run_id, reason, run.credits_used, run.remaining)
        if run.dormant is not None:
            writer.insert(table, [
                _row(run_id=run.run_id, observed_at=now, available_at=now, sport=sport,
                     game_id=None if game is None else game.game_id,
                     venue_market_id=None if market is None else market.venue_market_id,
                     fair_p=None, credits=0, credits_last=None, credits_remaining=None,
                     status=SKIPPED_BUDGET, body_path=None, body_sha256=None)
                for game, market in _scheduled_reads(run, sport)])
            continue
        # Gate 5: the call is the recorder's, unchanged -- same URL, same markets string
        # (FEATURED_MARKETS), same bookmakers string.
        result: FetchResult = client.fetch_featured(SPORTS[sport])
        credits = parse_credit_headers(result.headers)
        run.remaining = credits.remaining
        run.credits_used += CREDITS_PER_CALL
        spent += CREDITS_PER_CALL
        payload, raw = _payload_and_raw(result.body)
        body_path, body_sha256 = storage.write_body(
            s, run.run_id, f"featured-{sport}-{int(now.timestamp())}", raw)
        # The response is in hand at `fetched_at`; the interval it belongs to is `now`. Taking
        # the later of the two keeps §2's `available_at >= observed_at` true by construction.
        available_at = max(now, result.fetched_at)
        parsed = parse_odds_body(payload, sport)
        by_event: dict[str, list] = {}
        for parsed_row in parsed:
            by_event.setdefault(parsed_row.event_id, []).append(parsed_row)
        cache: dict[str, int | None] = {}
        rows: list[dict] = []
        for game, market in _scheduled_reads(run, sport):
            fair = None
            if game is not None and market is not None:
                lines = _lines_for_game(session, sport,
                                        by_event.get(game.canonical_game_id, []),
                                        fetched_at=result.fetched_at, cache=cache)
                fair = _fair_p(lines, game, market, now=now, s=s)
            rows.append(_row(
                run_id=run.run_id, observed_at=now, available_at=available_at, sport=sport,
                game_id=None if game is None else game.game_id,
                venue_market_id=None if market is None else market.venue_market_id,
                fair_p=fair, credits=0, credits_last=credits.last,
                credits_remaining=credits.remaining, status=OBSERVED,
                body_path=body_path, body_sha256=body_sha256))
        # §3 row 5 sums `credits` per run against the cap, so the call's three credits are
        # carried once, by the first row of the call, and never once per market: the cost is
        # per call and a per-row copy would multiply the run's spend by the cohort size.
        rows[0]["credits"] = CREDITS_PER_CALL
        writer.insert(table, rows)
        log.debug("exp observer read run=%s sport=%s rows=%s markets=%s remaining=%s",
                  run.run_id, sport, len(rows), FEATURED_MARKETS, credits.remaining)
    run.last_observed_at = now
    return spent


# --- §4.2: the registered pass ---------------------------------------------------------------

#: One `ObserverRun` per run id, for the life of the process. The counters it holds are
#: rebuilt from the database the first time the run is seen, so this is a cache and never the
#: authority (§1.6i's cap is evaluated against `exp_observation`'s own rows at that point).
_RUNS: dict[str, ObserverRun] = {}
_CLIENT: OddsApiClient | None = None
_HTTP: HttpClient | None = None
_ENGINE = None


def _engine(s: Settings):
    """§1.1(b)'s role's engine, built once per process.

    `ExperimentWriter.open` builds its own when it is not given one, which in a loop that runs
    every `exp_observe_interval_s` seconds and never exits would be a fresh connection pool per
    interval. The caller passes None while the secret is absent, so the writer's own
    secret-first refusal (I1) is the one that fires.
    """
    global _ENGINE
    if _ENGINE is None:
        from harness.db.engine import make_engine

        _ENGINE = make_engine(s.exp_database_url(EXP_DB_ROLE))
    return _ENGINE


def _client(s: Settings) -> OddsApiClient:
    """The Odds client, built once per process and closed by `close_client`.

    Tests replace this function; no test of this milestone constructs a real one, reads a
    secret or makes an outbound call.
    """
    global _CLIENT, _HTTP
    if _CLIENT is None:
        _HTTP = HttpClient(timeout_s=s.http_timeout_s)
        _CLIENT = OddsApiClient(_HTTP, s.odds_api_base_url, s.odds_api_key(),
                                s.odds_api_bookmakers)
    return _CLIENT


def close_client() -> None:
    """`worker.close_passes()`'s teardown: neither pool outlives the process."""
    global _CLIENT, _HTTP, _ENGINE
    if _HTTP is not None:
        _HTTP.close()
    if _ENGINE is not None:
        _ENGINE.dispose()
    _CLIENT, _HTTP, _ENGINE = None, None, None


def observer_pass(session: Session, now: datetime, s: Settings) -> dict:
    """The registered pass (§4.2). Inert unless enabled **and** a frozen `exp_run` exists.

    Both refusals are logged once per pass at `debug`, never once per market. Nothing here
    activates anything: §4.6's checklist is journaled, and its steps 0, 6 and 7 are the user's.
    """
    if not s.exp_observer_enabled:
        log.debug("exp observer: inert, exp_observer_enabled is false")
        return {"status": "inert", "reason": "exp_observer_enabled is false"}
    row = session.execute(_FROZEN_RUN).first()
    if row is None:
        log.debug("exp observer: inert, no frozen exp_run row")
        return {"status": "inert", "reason": "no frozen exp_run row"}
    run_id = str(row.run_id)
    run = _RUNS.get(run_id)
    if run is None:
        run = begin_run(session, s, run_id=run_id, now=now)
        if run is None or not run.games:
            log.debug("exp observer: inert, run %s froze no cohort", run_id)
            return {"status": "inert", "reason": "the frozen run has no cohort"}
        _RUNS[run_id] = run
    if (run.last_observed_at is not None
            and (now - run.last_observed_at).total_seconds() < s.exp_observe_interval_s):
        return {"status": "waiting", "run": run_id,
                "interval_s": s.exp_observe_interval_s}
    client = _client(s)
    writer = storage.ExperimentWriter.open(
        s, run_id=run_id, engine=_engine(s) if s.has_exp_db_password() else None)
    try:
        spent = observe_once(session, now, s, client, run=run, writer=writer)
        writer.commit()
    finally:
        writer.close()
    return {"status": "dormant" if run.dormant else "observed", "run": run_id,
            "credits": spent, "credits_used": run.credits_used,
            "remaining": run.remaining, "reason": run.dormant}


#: The registry's own contract, checked here: `worker.PassFn` is what `load_passes()` returns.
_PASS: PassFn = observer_pass

register_pass("exp_observer", _PASS)
register_closer("exp_observer", close_client)
