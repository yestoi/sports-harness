import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Game, OrderbookSnapshot, VenueMarket, VenueQuote, VenueTrade
from harness.matching.kalshi import classify_market, match_event, side_team_id_for
from harness.venues.kalshi.public import event_date_from_ticker

log = logging.getLogger(__name__)


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


def _side(v) -> str | None:
    s = v.strip().lower() if isinstance(v, str) else ""
    return s if s in ("yes", "no") else None


def taker_side_of(d: dict) -> tuple[str | None, str | None, str | None]:
    """(canonical, outcome, book) taker sides for one print.

    Kalshi deprecated `taker_side` on trades (removable since 2026-05-14) in favour of
    `taker_outcome_side`/`taker_book_side`, so the canonical side is `taker_outcome_side or
    taker_side`. A canonical `None` means the print carries no usable side: callers drop it rather
    than defaulting to "yes", which would record every print as a YES taker on the day Kalshi
    finally removes the field.
    """
    outcome, book = _side(d.get("taker_outcome_side")), _side(d.get("taker_book_side"))
    return outcome or _side(d.get("taker_side")), outcome, book


def upsert_venue_markets(session: Session, sport: str, markets: list[dict], events_by_ticker: dict[str, dict],
                         raw_id: int, fetched_at: datetime) -> MarketsResult:
    res = MarketsResult()
    tickers = [t for m in markets if (t := m.get("ticker"))]
    existing = {vm.ticker: vm for vm in
               session.query(VenueMarket).filter(VenueMarket.ticker.in_(tickers)).all()} if tickers else {}
    for m in markets:
        ticker, et = m.get("ticker"), m.get("event_ticker", "")
        mc = classify_market(m)
        if not ticker or mc is None:
            continue
        vm = existing.get(ticker)
        if vm is None:
            vm = VenueMarket(venue="kalshi", ticker=ticker, event_ticker=et, series_ticker=et.split("-")[0],
                             market_type=mc.market_type, threshold=mc.threshold, side=(None if mc.side_kind == "team" else "over"),
                             kalshi_team_uuid=mc.team_uuid, first_seen_raw_id=raw_id, last_seen_at=fetched_at,
                             match_status="unmatched", match_reason="new")
            session.add(vm)
            existing[ticker] = vm
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
            updated_time=_ts(m.get("updated_time")), fetched_at=fetched_at).on_conflict_do_nothing().returning(VenueQuote.id)
        n += len(session.execute(stmt).fetchall())
    return n


def insert_orderbook(session: Session, ticker: str, body: dict, raw_id: int, fetched_at: datetime) -> bool:
    vm = session.query(VenueMarket).filter_by(ticker=ticker).one_or_none()
    ob = (body or {}).get("orderbook_fp") or {}
    if vm is None or not isinstance(ob, dict):
        return False
    stmt = insert(OrderbookSnapshot).values(raw_id=raw_id, venue_market_id=vm.id, fetched_at=fetched_at,
                                            yes_bids=ob.get("yes_dollars") or [], no_bids=ob.get("no_dollars") or []).on_conflict_do_nothing().returning(OrderbookSnapshot.id)
    return len(session.execute(stmt).fetchall()) == 1


def insert_trades(session: Session, body: dict, raw_id: int, ctx: dict | None = None) -> int:
    n, dropped = 0, 0
    for t in (body or {}).get("trades", []) if isinstance(body, dict) else []:
        ts, price, count = _ts(t.get("created_time")), _dec(t.get("yes_price_dollars")), _dec(t.get("count_fp"))
        if not t.get("trade_id") or not t.get("ticker") or ts is None or price is None or count is None:
            continue
        side, outcome, book = taker_side_of(t)
        if side is None:
            dropped += 1
            continue
        stmt = insert(VenueTrade).values(venue="kalshi", trade_id=t["trade_id"], ticker=t["ticker"], ts=ts, yes_price=price,
                                         count=count, taker_side=side, taker_outcome_side=outcome, taker_book_side=book,
                                         is_block=bool(t.get("is_block_trade")),
                                         source="rest", raw_id=raw_id).on_conflict_do_nothing().returning(VenueTrade.trade_id)
        n += len(session.execute(stmt).fetchall())
    if dropped:
        # One line per raw response, not per print: a page carries up to 1,000 trades.
        log.warning("kalshi trades: %d prints dropped, taker side missing (raw_id=%s)", dropped, raw_id)
        if ctx is not None:
            ctx["taker_side_missing"] = ctx.setdefault("taker_side_missing", 0) + dropped
    return n
