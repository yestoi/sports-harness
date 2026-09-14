import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.parse import urlsplit

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Game, OddsPropSnapshot, OddsSnapshot
from harness.matching.teams import resolve_team
from harness.normalize.players import candidates_for_game, match_player

#: Venue market key -> the short internal key stored in `odds_prop_snapshots.market_type`
#: (D22). The column is String(24), copied from `odds_snapshots`: `prop:receptions:alt` is 19
#: characters, the venue's `player_reception_yds_alternate` is 30, and widening a bulk table
#: is what D22 refused.
VENUE_PROP_MARKETS = {
    "player_pass_yds": "prop:pass_yds",
    "player_rush_yds": "prop:rush_yds",
    "player_reception_yds": "prop:rec_yds",
    "player_receptions": "prop:receptions",
    "player_anytime_td": "prop:anytime_td",
    "player_pass_yds_alternate": "prop:pass_yds:alt",
    "player_rush_yds_alternate": "prop:rush_yds:alt",
    "player_reception_yds_alternate": "prop:rec_yds:alt",
    "player_receptions_alternate": "prop:receptions:alt",
}
PROP_PREFIX = "prop:"
#: Addendum §3.3: the link allowlist, applied once at normalize time. Anything else is stored
#: null and counted. Never a render-time sanitizer: a link that reached a payload would already
#: be a link the page could be made to open.
LINK_MAX = 300
_SID_RE = re.compile(r"^[A-Za-z0-9_\-:.]{1,64}$")
_PROP_SIDES = {"over": "over", "under": "under", "yes": "yes", "no": "no"}


def valid_dk_link(url: str | None) -> str | None:
    """An `https` DraftKings link of at most 300 characters, or None.

    Byte for byte: the query string carries the venue's own outcome id, so the string is
    returned as it arrived rather than re-assembled from parts.
    """
    if not isinstance(url, str) or not url or len(url) > LINK_MAX:
        return None
    try:
        parts = urlsplit(url)
    except ValueError:
        return None
    host = (parts.hostname or "").lower()
    if parts.scheme != "https":
        return None
    if host != "sportsbook.draftkings.com" and not host.endswith(".draftkings.com"):
        return None
    return url


def valid_sid(sid: str | None) -> str | None:
    return sid if isinstance(sid, str) and _SID_RE.match(sid) else None


@dataclass(frozen=True)
class OddsUpsertResult:
    inserted: int = 0
    dropped_unknown_game: int = 0
    dropped_unresolved_team: int = 0
    link_rejected: int = 0
    prop_unmatched: int = 0


@dataclass(frozen=True)
class OddsRow:
    event_id: str
    book: str
    market_type: str
    outcome_name: str
    point: Decimal | None
    price: Decimal
    last_update: datetime | None
    description: str | None = None
    link: str | None = None
    sid: str | None = None


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
                mtype = VENUE_PROP_MARKETS.get(mk.get("key"), mk.get("key"))
                for oc in mk.get("outcomes", []) or []:
                    price = _dec(oc.get("price"))
                    if not book or not mtype or price is None or not oc.get("name"):
                        continue
                    out.append(OddsRow(eid, book, mtype, oc["name"], _dec(oc.get("point")),
                                       price, lu, description=oc.get("description"),
                                       link=oc.get("link"), sid=oc.get("sid")))
    return out


def upsert_odds_rows(session: Session, sport: str, rows: list[OddsRow], raw_id: int, run_id: int,
                     fetched_at: datetime) -> OddsUpsertResult:
    games = {g.odds_api_event_id: g.id for g in session.execute(
        select(Game).where(Game.odds_api_event_id.in_({r.event_id for r in rows}))).scalars()}
    inserted = no_game = no_team = link_rejected = prop_unmatched = 0
    cache: dict[str, int | None] = {}
    # One bounded roster read per game in this pass, never one per outcome (addendum §4.2).
    roster_cache: dict[int, list] = {}
    for r in rows:
        gid = games.get(r.event_id)
        if gid is None:
            no_game += 1
            continue
        team_id, side, player_name, player_id = None, None, None, None
        if r.market_type.startswith(PROP_PREFIX):
            # A prop outcome names a player, never a team: the team resolver is never called
            # for one (addendum §3.3).
            side = _PROP_SIDES.get((r.outcome_name or "").strip().lower())
            player_name = (r.description or "").strip()[:80] or None
            if side is None or player_name is None:
                prop_unmatched += 1
                continue
            if gid not in roster_cache:
                roster_cache[gid] = candidates_for_game(session, sport, gid)
            player_id = match_player(player_name, roster_cache[gid])
            if player_id is None:
                # §4.2: ambiguity never picks and an absent player is never guessed. The row is
                # still stored -- `player_name` keeps it auditable and `uq_odds_prop_row` keys
                # on it -- but with `player_id` null it can never be built into a leg.
                prop_unmatched += 1
        elif r.outcome_name in ("Over", "Under"):
            side = r.outcome_name.lower()
        else:
            # The existing game-line path, unchanged: the team cache and the
            # `dropped_unresolved_team` count stay exactly as they are today.
            if r.outcome_name not in cache:
                cache[r.outcome_name] = resolve_team(
                    session, sport, r.outcome_name,
                    sources=("odds_api", "espn_display"))[0]
            team_id = cache[r.outcome_name]
            if team_id is None:
                no_team += 1
                continue
        link = valid_dk_link(r.link)
        if r.link and link is None:
            link_rejected += 1
        if r.market_type.startswith(PROP_PREFIX):
            # D23: a prop row goes to its own table. `uq_odds_snapshot_row` on `odds_snapshots`
            # has no `where` clause, so two scorers in one market collide on it whatever
            # conflict target this statement names, and rebuilding a unique index on a bulk
            # table is not additive. `odds_prop_snapshots` keys on `player_name` instead. The
            # target is named explicitly rather than left to Postgres: an arbiter-less
            # `on conflict do nothing` swallows a violation of *any* index.
            stmt = insert(OddsPropSnapshot).values(
                raw_id=raw_id, book=r.book, game_id=gid, market_type=r.market_type,
                player_name=player_name, player_id=player_id, outcome_side=side, point=r.point,
                price_decimal=r.price, book_last_update=r.last_update, fetched_at=fetched_at,
                link=link, sid=valid_sid(r.sid))
            stmt = stmt.on_conflict_do_nothing(
                index_elements=[OddsPropSnapshot.raw_id, OddsPropSnapshot.book,
                                OddsPropSnapshot.market_type, OddsPropSnapshot.player_name,
                                text("coalesce(outcome_side, '')"), text("coalesce(point, 0)")])
            inserted += len(session.execute(stmt.returning(OddsPropSnapshot.id)).fetchall())
            continue
        # The game-line path, byte-identical to today's: `odds_snapshots` gains no column and
        # no index in this phase (D23), so nothing here changes but the branch above it.
        stmt = insert(OddsSnapshot).values(
            raw_id=raw_id, run_id=run_id, book=r.book, game_id=gid,
            market_type=r.market_type, outcome_team_id=team_id, outcome_side=side,
            point=r.point, price_decimal=r.price, book_last_update=r.last_update,
            fetched_at=fetched_at).on_conflict_do_nothing()
        inserted += len(session.execute(stmt.returning(OddsSnapshot.id)).fetchall())
    return OddsUpsertResult(inserted, no_game, no_team, link_rejected, prop_unmatched)
