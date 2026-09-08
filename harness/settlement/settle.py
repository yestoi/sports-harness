"""Settling a finished game: the score-derived result, the venue's own result beside it, and
one ledger row per fill.

Two results are recorded for every market and neither overwrites the other (R11). The
`derived` row is what the final score says, computed here by `resolve_market`; the `venue` row
is what Kalshi settled the contract at, read from the settled `/markets` pages the recorder
already stores. A disagreement is a warning on the job run and a gate criterion, never a silent
correction: if our score-derived result and the venue's differ, one of the two is wrong about
the market and a paper P&L built on either is worth knowing about.

Money moves once per fill. `ledger` is keyed `(fill_id, kind)`, so a settlement pass that dies
half-way and is retried posts each fill exactly once, and the status update is bounded on the
statuses it may move (`filled`, `partially_filled`) so a retry can never resurrect a cancelled
order into a settled one. A cancelled order that was partially filled still gets its cash: the
contracts were bought, whatever happened to the rest of the order.

Every decision here is pure with an explicit `now`. `resolve_market` and `payout_for_side` take
no session at all.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Settlement, VenueSettlement
from harness.execution import store as exec_store
from harness.execution.book import side_p
from harness.recorder import store as raw_store
from harness.settlement.job import Budget, StageResult, current_ctx, register_stage

log = logging.getLogger(__name__)

ONE, HALF, ZERO = Decimal("1"), Decimal("0.5"), Decimal("0")
CENT = Decimal("0.01")
DERIVED, VENUE = "derived", "venue"
QUEUE_MODEL = "queue_model"
#: The only statuses a settlement may move. An order that was cancelled or expired keeps that
#: status forever: it is the record of what the executor did, not of what the game did.
SETTLEABLE = ("filled", "partially_filled")
#: The only results `venue_settlements.result` can hold (varchar(4)). Kalshi's `result` is free
#: text, so anything else is a warning and no row: a value we cannot store is a value we cannot
#: compare, and guessing at it would put a wrong result beside a right one.
#:
#: `void` is in the set even though it pays nothing. A voided market is settled as far as the
#: venue is concerned, so it needs its `source = venue` row or it can never satisfy the 48 h
#: "every derived row has a venue row" invariant or gate criterion 9 -- and a void against a
#: derived yes/no is a real disagreement, because the ledger paid on a market the venue refused
#: to settle. `tie` is ours, not Kalshi's, and is here so a derived tie can be compared.
VENUE_RESULTS = ("yes", "no", "tie", "void")
#: How far back `run_venue_result` reads the recorder's stored settled pages (the recorder keeps
#: an 8-day window, so 7 days always has one full football week in it).
SETTLED_BODY_WINDOW = timedelta(days=7)
#: A game this long past kickoff with no settlement row is stale, whatever ESPN says (F37).
STALE_AFTER = timedelta(hours=72)


@dataclass
class SettleCounts:
    games: int = 0
    markets: int = 0
    orders: int = 0
    mismatches: int = 0
    venue_rows: int = 0
    budget_exhausted: bool = False
    #: Settlement ledger rows this pass actually wrote. Additive to the brief's six fields so
    #: the shared `insert_ledger_fill`'s RETURNING has a counter to move (fix round 1, M2).
    ledger_rows: int = 0


# --- pure decisions --------------------------------------------------------------------


def resolve_market(market_type: str, threshold: Decimal | None, side_team_id: int | None,
                   home_team_id: int, away_team_id: int,
                   home_score: int, away_score: int) -> Decimal:
    """The YES payout of one market shape given the final score: 0, 0.5 or 1.

    Moneyline pays 1 for the side team's win, 0 for its loss and 0.5 for a tie -- the harness
    prices a tie as half a contract rather than voiding it, so a tied game still settles into a
    number the ledger and the `result` benchmark can carry.

    A spread market for team T at k.5 is the venue's "T wins by over k.5 points": it pays 1
    exactly when T's margin beats the threshold, which handles a negative k (T losing by less
    than |k|) without a second branch. A total at k.5 pays 1 when the two scores sum past it.
    Both thresholds are half-points in every football series the harness matches, so neither
    can land on the threshold; the strict comparison is the venue's own rule either way.
    """
    home, away = int(home_score), int(away_score)
    if market_type == "moneyline":
        margin = _margin(side_team_id, home_team_id, away_team_id, home, away)
        if margin > 0:
            return ONE
        return ZERO if margin < 0 else HALF
    if market_type == "spread":
        if threshold is None:
            raise ValueError("spread market has no threshold")
        margin = _margin(side_team_id, home_team_id, away_team_id, home, away)
        return ONE if Decimal(margin) > Decimal(threshold) else ZERO
    if market_type == "total":
        if threshold is None:
            raise ValueError("total market has no threshold")
        return ONE if Decimal(home + away) > Decimal(threshold) else ZERO
    raise ValueError(f"cannot resolve market type {market_type!r}")


def _margin(side_team_id: int | None, home_team_id: int, away_team_id: int,
            home_score: int, away_score: int) -> int:
    """The side team's score minus its opponent's."""
    if side_team_id == home_team_id:
        return home_score - away_score
    if side_team_id == away_team_id:
        return away_score - home_score
    raise ValueError(f"side team {side_team_id!r} is in neither side of the game")


def payout_for_side(payout_yes: Decimal, side: str) -> Decimal:
    """A YES payout expressed on one order's side: `1 - payout` for a NO order, so a tie pays
    both sides half. Raises on an unknown side, exactly as `side_p` does everywhere else."""
    return side_p(Decimal(payout_yes), side)


def _result_label(payout_yes: Decimal) -> str:
    if payout_yes == ONE:
        return "yes"
    if payout_yes == ZERO:
        return "no"
    return "tie"


# --- the settle stage ------------------------------------------------------------------

_FINAL_GAMES = text("""
select g.id, g.home_team_id, g.away_team_id, g.home_score, g.away_score
from games g
left join settlements s on s.game_id = g.id
where g.status = 'final' and g.home_score is not null and g.away_score is not null
  and s.game_id is null
order by g.id
""")

#: The markets one finished game settles. A confidently matched market is settled because it is
#: this game's market; a market that is no longer confidently matched is settled anyway when an
#: order was placed on it, so a match downgraded after a fill can never leave that fill unpaid.
#: No filter on `market_type`: a shape this harness cannot resolve raises out of `resolve_market`
#: and takes its game down loudly, rather than being dropped here with its fills left unpaid.
_GAME_MARKETS = text("""
select m.id, m.venue, m.ticker, m.market_type, m.threshold, m.side_team_id, m.side
from venue_markets m
where m.game_id = :game_id
  and (m.match_status in ('matched', 'manual')
       or exists (select 1 from orders o where o.venue_market_id = m.id))
order by m.id
""")

#: Every watched fill on one ticker that takes cash. `snapshot_cross` and `no_watcher` fills are
#: counterfactuals and hold no contracts; a replay fill holds contracts in a re-simulation, not
#: in the account, so the ledger -- which is the live record a report reads -- excludes it.
_TICKER_FILLS = text("""
select f.id as fill_id, f.contracts, f.prob, o.id as order_id, o.variant_id, o.side, o.replay
from fills f
join orders o on o.id = f.order_id
where o.ticker = :ticker and o.venue = :venue and o.replay = false
  and f.fill_method = :method
order by f.id
""")

#: The status update is deliberately not part of the fill loop: a replay order takes no cash but
#: must still leave `open`/`filled`, or it strands in the replay executor's positions forever
#: (`harness/execution/store.py` `_POSITIONS`). Bounded on the statuses it may move, so a retry
#: can never resurrect a cancelled or expired order.
_SETTLE_ORDERS_ON_TICKER = text(
    "update orders set status = 'settled' "
    "where ticker = :ticker and venue = :venue and status = any(:statuses) returning id")

_STALE_UNSETTLED = text("""
select count(*)
from orders o
join games g on g.id = o.game_id
left join settlements s on s.game_id = g.id
where o.replay = false and g.kickoff_utc < :cutoff and s.game_id is null
""")


def run_settlement(session: Session, now: datetime, kalshi, budget: Budget,
                   ctx: dict) -> SettleCounts:
    """Settle every final game that has no `settlements` row yet.

    One savepoint per game and one commit per game: a game whose markets cannot be resolved
    rolls back alone and lands in `ctx["errors"]`, and the games already settled in this pass
    are on disk when the next one fails or the budget runs out.

    `kalshi` is unused here -- a score-derived settlement needs no venue call -- and is part of
    the signature so the settlement entrypoints all take the same arguments.
    """
    del kalshi
    counts = SettleCounts()
    games = session.execute(_FINAL_GAMES).all()
    for seen, game in enumerate(games):
        # Checked before every game, the first included: a budget already spent by an earlier
        # stage is worth recording rather than spending on one more game past the job's period.
        if not budget.ok():
            counts.budget_exhausted = True
            log.info("settle budget spent with %d games left", len(games) - seen)
            break
        try:
            with session.begin_nested():
                settled, markets, orders, ledger_rows = _settle_game(session, now, game)
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one game must not cost the pass
            session.rollback()
            log.exception("settling game %s failed", game.id)
            ctx["errors"].append({"settle_game": game.id,
                                  "error": f"{type(exc).__name__}: {exc}"[:500]})
            continue
        counts.games += settled
        counts.markets += markets
        counts.orders += orders
        counts.ledger_rows += ledger_rows
    return counts


def _settle_game(session: Session, now: datetime, game) -> tuple[int, int, int, int]:
    """One game inside its own savepoint: the score, every market's derived result, and the
    cash and status of every fill on those markets."""
    inserted = session.execute(
        insert(Settlement)
        .values(game_id=game.id, home_score=game.home_score, away_score=game.away_score,
                source="espn", settled_at=now)
        .on_conflict_do_nothing(index_elements=["game_id"])
        .returning(Settlement.game_id)).first()
    markets = orders = ledger_rows = 0
    for market in session.execute(_GAME_MARKETS, {"game_id": game.id}).all():
        payout_yes = resolve_market(market.market_type, market.threshold, market.side_team_id,
                                    game.home_team_id, game.away_team_id,
                                    game.home_score, game.away_score)
        written = session.execute(
            insert(VenueSettlement)
            .values(venue=market.venue, ticker=market.ticker, source=DERIVED,
                    result=_result_label(payout_yes), payout=payout_yes, settled_at=now)
            .on_conflict_do_nothing(index_elements=["venue", "ticker", "source"])
            .returning(VenueSettlement.ticker)).first()
        markets += 0 if written is None else 1
        posted, moved = _settle_ticker(session, now, market.venue, market.ticker, payout_yes)
        ledger_rows += posted
        orders += moved
    return (0 if inserted is None else 1), markets, orders, ledger_rows


def _settle_ticker(session: Session, now: datetime, venue: str, ticker: str,
                   payout_yes: Decimal) -> tuple[int, int]:
    """Post one ledger row per live watched fill on `ticker`, then settle every order on it.

    Returns `(ledger rows written, orders moved)`, both counted from the rows the statements
    actually returned: a fill already paid by an earlier pass writes nothing, and an order with
    two fills is settled once.
    """
    posted = 0
    for fill in session.execute(
            _TICKER_FILLS, {"ticker": ticker, "venue": venue, "method": QUEUE_MODEL}).all():
        payout = payout_for_side(payout_yes, fill.side)
        contracts = Decimal(fill.contracts)
        # The executor's own idempotent ledger insert, keyed `(fill_id, kind)`: one writer for
        # the table means one place where a cash row's shape can drift.
        row_id = exec_store.insert_ledger_fill(
            session, ts=now, variant_id=fill.variant_id, kind="settlement",
            order_id=fill.order_id, fill_id=fill.fill_id, ticker=ticker, side=fill.side,
            contracts=contracts, price=fill.prob, payout=payout,
            cash_delta=(contracts * payout).quantize(CENT, rounding=ROUND_HALF_UP),
            replay=fill.replay)
        posted += 0 if row_id is None else 1
    moved = session.execute(_SETTLE_ORDERS_ON_TICKER,
                            {"ticker": ticker, "venue": venue,
                             "statuses": list(SETTLEABLE)}).all()
    return posted, len(moved)


def stale_unsettled(session: Session, now: datetime) -> int:
    """Non-replay orders whose game kicked off more than 72 h ago and still has no settlement.

    A number the dashboard shows and the operator acts on: it is either a game ESPN never
    marked final or a settlement pass that has been failing quietly.
    """
    return int(session.execute(_STALE_UNSETTLED, {"cutoff": now - STALE_AFTER}).scalar() or 0)


# --- the venue_result stage ------------------------------------------------------------

_PENDING_VENUE_ROWS = text("""
select d.venue, d.ticker, d.result
from venue_settlements d
left join venue_settlements v
  on v.venue = d.venue and v.ticker = d.ticker and v.source = 'venue'
where d.source = 'derived' and v.ticker is null
order by d.ticker
""")

#: The newest non-empty `result` the recorder's stored settled pages carry for each ticker.
#: The settled pages are the only place these bodies live -- the normalizer skips them (R11) --
#: so this is a JSONB scan, bounded to one week of `/markets?status=settled` rows and run once
#: per pass for every pending ticket rather than once per ticker. `jsonb_array_elements` raises
#: on a non-array, so a body that is not shaped like a market page is replaced with an empty one.
_STORED_RESULTS = text("""
select distinct on (t.ticker) t.ticker, t.result, t.raw_id
from (
    select m.value->>'ticker' as ticker, m.value->>'result' as result,
           r.id as raw_id, r.fetched_at
    from raw_responses r
    cross join lateral jsonb_array_elements(
        case when jsonb_typeof(r.body->'markets') = 'array'
             then r.body->'markets' else '[]'::jsonb end) m
    where r.source = 'kalshi' and r.endpoint = '/markets'
      and r.params->>'status' = 'settled' and r.fetched_at > :since
) t
where t.ticker = any(:tickers) and coalesce(t.result, '') <> ''
order by t.ticker, t.fetched_at desc, t.raw_id desc
""")

_NEWEST_RUN = text("select id from runs order by id desc limit 1")


def run_venue_result(session: Session, now: datetime, kalshi, budget: Budget, ctx: dict) -> int:
    """Record the venue's own result beside every derived one that does not have it yet.

    The stored settled pages are read first, because they cost nothing: the recorder already
    fetched them. A ticker they do not cover is fetched market by market when a client was given,
    and that body is stored like any other response so the fetch is auditable. A market whose
    `result` is still empty gets no row at all -- Kalshi has not settled it yet -- and is retried
    on the next pass rather than recorded as a blank disagreement.

    One savepoint and one commit per ticker. `_PENDING_VENUE_ROWS` is ordered by ticker, so
    without the savepoint the first ticker that raised would be first again on the next pass and
    every ticker after it would never get a venue row at all; with it, the failure is one entry
    in `ctx["errors"]` and the pass carries on. The per-ticker commit also means a pass that
    dies at market 600 of a CFB Saturday keeps the 599 results and the fetches it paid for.
    """
    pending = session.execute(_PENDING_VENUE_ROWS).all()
    if not pending:
        return 0
    stored = {row.ticker: (row.result, row.raw_id) for row in session.execute(
        _STORED_RESULTS, {"since": now - SETTLED_BODY_WINDOW,
                          "tickers": [row.ticker for row in pending]}).all()}
    run_id = session.execute(_NEWEST_RUN).scalar()
    written = 0
    for seen, row in enumerate(pending):
        result, raw_id = stored.get(row.ticker, (None, None))
        if not result and kalshi is not None:
            if not budget.ok():
                log.info("venue_result budget spent with %d tickers left",
                         len(pending) - seen)
                break
            result, raw_id = _fetch_result(session, kalshi, row.ticker, run_id, ctx)
            session.commit()  # keep the stored body whatever the rest of this ticker does
        result = _usable_result(result, row.ticker, ctx)
        if result is None:
            continue
        try:
            with session.begin_nested():
                inserted = session.execute(
                    insert(VenueSettlement)
                    .values(venue=row.venue, ticker=row.ticker, source=VENUE, result=result,
                            payout=_venue_payout(result), settled_at=now, raw_id=raw_id)
                    .on_conflict_do_nothing(index_elements=["venue", "ticker", "source"])
                    .returning(VenueSettlement.ticker)).first()
            session.commit()
        except Exception as exc:  # noqa: BLE001 - one ticker must not cost the stage
            session.rollback()
            log.exception("venue result for %s failed", row.ticker)
            ctx["errors"].append({"venue_result": row.ticker,
                                  "error": f"{type(exc).__name__}: {exc}"[:500]})
            continue
        if inserted is None:
            continue
        written += 1
        # Only a result we could store is a result we can compare: an unusable one never gets
        # this far, so a mismatch always means the venue and the score genuinely disagree.
        if result != row.result:
            log.warning("settlement mismatch on %s: derived %s, venue %s",
                        row.ticker, row.result, result)
            ctx["warnings"].append({"settlement_mismatch": row.ticker})
    return written


def _usable_result(result: str | None, ticker: str, ctx: dict) -> str | None:
    """`yes`, `no` or `tie`, or None with a warning recorded.

    `venue_settlements.result` is `varchar(4)` and Kalshi's `result` is free text, so a longer
    value would raise `StringDataRightTruncation` on the insert. An empty one simply means the
    venue has not settled the market yet and is retried silently next pass; a non-empty one we
    cannot store is worth a warning, because it is a shape of answer nobody here anticipated.
    """
    if not result:
        return None
    if result in VENUE_RESULTS:
        return result
    log.warning("unexpected venue result on %s: %r", ticker, result[:32])
    ctx["warnings"].append({"unexpected_venue_result": ticker, "result": result[:16]})
    return None


def _fetch_result(session: Session, kalshi, ticker: str, run_id: int | None,
                  ctx: dict) -> tuple[str | None, int | None]:
    """One `GET /markets/{ticker}`, stored. Returns the market's `result` and the raw row's id."""
    if run_id is None:
        return None, None
    try:
        response = kalshi.fetch_market(ticker)
    except Exception as exc:  # noqa: BLE001 - one unreachable market must not end the pass
        log.exception("fetch_market failed for %s", ticker)
        ctx["errors"].append({"fetch_market": ticker,
                              "error": f"{type(exc).__name__}: {exc}"[:500]})
        return None, None
    raw_id = raw_store.store_raw(session, run_id, "kalshi", f"/markets/{ticker}", {}, response)
    if response.status != 200:
        return None, raw_id
    return _result_from_body(response.body), raw_id


def _result_from_body(body) -> str | None:
    """`GET /markets/{ticker}` answers `{"market": {...}}`; a page body answers
    `{"markets": [...]}`. Both are accepted so a stored body of either shape reads the same
    way, and a raw body that is neither reads as "no result yet"."""
    if not isinstance(body, dict):
        return None
    market = body.get("market")
    if not isinstance(market, dict):
        markets = body.get("markets")
        market = markets[0] if isinstance(markets, list) and markets else body
    if not isinstance(market, dict):
        return None
    result = market.get("result")
    return result if isinstance(result, str) and result else None


def _venue_payout(result: str) -> Decimal | None:
    """The payout one venue result implies. Only `VENUE_RESULTS` ever reach here, and `void`
    carries no payout at all -- the column is nullable for exactly that case."""
    if result == "yes":
        return ONE
    if result == "no":
        return ZERO
    return HALF if result == "tie" else None


# --- stages ----------------------------------------------------------------------------


def settle_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    ctx = current_ctx()
    counts = run_settlement(session, now, ctx.get("kalshi"), budget, ctx)
    return StageResult("settle",
                       {"games": counts.games, "markets": counts.markets,
                        "orders": counts.orders, "ledger_rows": counts.ledger_rows},
                       counts.budget_exhausted, None)


def venue_result_stage(session: Session, now: datetime, budget: Budget) -> StageResult:
    ctx = current_ctx()
    before = _mismatch_count(ctx)
    rows = run_venue_result(session, now, ctx.get("kalshi"), budget, ctx)
    return StageResult("venue_result",
                       {"venue_rows": rows, "mismatches": _mismatch_count(ctx) - before},
                       not budget.ok(), None)


def _mismatch_count(ctx: dict) -> int:
    """Counted by key, not by list length: the stage adds two kinds of warning and only one of
    them is a derived-versus-venue disagreement."""
    return sum(1 for w in ctx["warnings"] if "settlement_mismatch" in w)


register_stage("settle", settle_stage)
register_stage("venue_result", venue_result_stage)
