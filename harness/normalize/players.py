"""ESPN player stats, rosters, game logs and the player identity map (addendum §4.1, §4.2).

Pure functions plus one upsert and one bounded read: no client, no settings and no `datetime.now`,
so the recorder, the builder, the settle stage and the tests can all import this module.

The identity map is the piece that decides whether a real prop is buildable, and its rule is the
addendum's: **exactly one candidate matches, or the outcome is `player_unmatched`**. Ambiguity
never picks (D14); the box score's own athlete ids are the live key.
"""
import logging
import unicodedata
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from harness.db.models import Player

log = logging.getLogger(__name__)

#: Box-score category -> the category's own stat key -> the internal stat name. The keys are
#: read out of the category's own `keys` list, never by column position: ESPN orders `passing`
#: as `C/ATT, YDS, ...` and `receiving` as `REC, YDS, ...`, so index 1 means two different
#: things in two categories of one body.
_CATEGORY_STATS = {
    "passing": {"YDS": "pass_yds"},
    "rushing": {"YDS": "rush_yds"},
    "receiving": {"YDS": "rec_yds", "REC": "receptions"},
}

#: The scoring-play types that are a touchdown the *scorer* is credited with: rushing,
#: receiving, kick/punt return, fumble recovery and interception return (addendum §4.3, the
#: recorded `market_defs.anytime_td` rule). A passing touchdown is the receiver's, never the
#: passer's, and the passer is never the play's `athlete`.
_TD_TYPES = {"TD", "RUSHTD", "RUSH", "REC", "RECTD", "KR", "PR", "FR", "INT", "IR", "FUMR"}
#: Scoring plays that are certainly not a touchdown: decided, and credited to no one.
_NON_TD_TYPES = {"FG", "PAT", "XP", "2PT", "SF", "D2P"}
#: The text markers that decide a touchdown when the type abbreviation is one we do not know.
_TD_TEXT = ("pass from", "yd run", "yd rush", "return", "fumble recovery", "touchdown")
#: The count of plays the parser could not decide. The caller logs it and leaves the leg
#: pending; an undecidable play is never a miss and never a credit (plan review MI-2).
UNDECIDABLE = "__undecidable__"

#: Name suffixes dropped before a comparison (addendum §4.2).
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}
#: Punctuation that vanishes (`Ja'Marr` -> `jamarr`, `St.` -> `st`) as against punctuation that
#: becomes a space (`Amon-Ra` -> `amon ra`).
_DROPPED = str.maketrans("", "", ".,'’ʼ`")

#: ESPN's own game-log stat names -> the internal stat names. Names, not column labels: two
#: game-log groups both label a column `YDS`.
_GAMELOG_STATS = {
    "passingYards": "pass_yds",
    "rushingYards": "rush_yds",
    "receivingYards": "rec_yds",
    "receptions": "receptions",
}


@dataclass(frozen=True)
class StatLine:
    """One carded stat of one athlete as the box score currently reports it."""
    player_espn_id: str
    stat: str
    value: Decimal


def _dec(value) -> Decimal | None:
    """ESPN reports a stat as a string, sometimes `-` or `--` and sometimes `19/28`; only a
    number is a stat line (the measured game-log bodies write an absent stat as `-`)."""
    try:
        return Decimal(str(value).strip().replace(",", ""))
    except (InvalidOperation, AttributeError, ValueError):
        return None


def parse_summary_stats(body) -> list[StatLine]:
    """The carded stat lines of one summary body (addendum §4.1).

    Reads each category's own `keys` list to find the column index and skips a category whose
    key is absent rather than guessing a position. A body that is not the expected shape yields
    an empty list: this parser never raises on outside data.
    """
    if not isinstance(body, dict):
        return []
    out: list[StatLine] = []
    for team in (body.get("boxscore") or {}).get("players") or []:
        for category in (team or {}).get("statistics") or []:
            wanted = _CATEGORY_STATS.get(str((category or {}).get("name", "")).lower())
            if not wanted:
                continue
            keys = category.get("keys") or []
            columns = {stat: keys.index(key) for key, stat in wanted.items() if key in keys}
            for entry in category.get("athletes") or []:
                espn_id = str(((entry or {}).get("athlete") or {}).get("id") or "")
                stats = entry.get("stats") or []
                if not espn_id:
                    continue
                for stat, index in columns.items():
                    if index >= len(stats):
                        continue
                    value = _dec(stats[index])
                    if value is not None:
                        out.append(StatLine(espn_id, stat, value))
    return out


def parse_scoring_tds(body) -> dict[str, int]:
    """Athlete id -> qualifying touchdowns scored, from `scoringPlays` (addendum §4.3).

    Rushing, receiving, return and recovery count; a passing touchdown counts for the receiver
    (the play's own `athlete`) and never for the passer, who is named only in the play text. A
    play whose type cannot be decided -- or a touchdown with no scorer on it -- is credited to
    no one and counted under `__undecidable__`, so the caller can log it and leave the leg
    pending (plan review MI-2). A decided non-touchdown (a field goal, an extra point) is
    neither scored nor undecidable.
    """
    scored: dict[str, int] = {}
    if not isinstance(body, dict):
        return scored
    for play in body.get("scoringPlays") or []:
        if not isinstance(play, dict):
            scored[UNDECIDABLE] = scored.get(UNDECIDABLE, 0) + 1
            continue
        abbr = str(((play.get("type") or {}).get("abbreviation") or "")).upper().replace(" ", "")
        lowered = str(play.get("text") or "").lower()
        if abbr in _NON_TD_TYPES:
            continue
        is_td = abbr in _TD_TYPES or any(marker in lowered for marker in _TD_TEXT)
        espn_id = str(((play.get("athlete") or {}).get("id") or ""))
        if not is_td or not espn_id:
            scored[UNDECIDABLE] = scored.get(UNDECIDABLE, 0) + 1
            continue
        scored[espn_id] = scored.get(espn_id, 0) + 1
    return scored


def parse_roster(body) -> list[dict]:
    """`espn_id`, `name` and `position` per rostered athlete (addendum §4.1).

    ESPN groups the roster (`athletes[].items[]`); a flat `athletes[]` is accepted too. An entry
    without an id or a display name is skipped rather than stored half-formed.
    """
    if not isinstance(body, dict):
        return []
    rows: list[dict] = []
    for group in body.get("athletes") or []:
        items = (group or {}).get("items") if isinstance(group, dict) else None
        for athlete in (items if isinstance(items, list) else [group]):
            if not isinstance(athlete, dict):
                continue
            espn_id = str(athlete.get("id") or "")
            name = str(athlete.get("displayName") or "").strip()
            if not espn_id or not name:
                continue
            position = athlete.get("position")
            abbreviation = position.get("abbreviation") if isinstance(position, dict) else None
            rows.append({"espn_id": espn_id[:16], "name": name[:80],
                         "position": str(abbreviation)[:6] if abbreviation else None})
    return rows


def parse_gamelog(body, athlete_id: str | None = None) -> dict[str, list[Decimal]]:
    """Stat -> that stat's per-game values, in the source's own order (ESPN lists newest first).

    The shape is the one the plan's evidence task measured on the pinned v3 path (addendum §4.1;
    the user's ruling, journal 184 item 3): a top-level `names` column list whose order differs
    per position -- so a stat is found by ESPN's own name and never by index -- an absent stat
    written `-`, and a player with no games answering `{"filters": [...]}` and nothing else.

    **An empty mapping stays the expected path** and the caller's line reads `no season data
    yet`, so every body this function does not recognise -- a body whose columns it cannot name,
    a body of the wrong type, or a body whose walk raises -- yields `{}` rather than a guess.
    The v3 host is browser-facing and less stable than the recorder's, so nothing here can raise
    into a tick; `athlete_id` is carried only to name the athlete in that one WARNING.
    """
    if not isinstance(body, dict):
        return {}
    names = body.get("names")
    season_types = body.get("seasonTypes")
    if not isinstance(names, list) or not isinstance(season_types, list):
        return {}
    columns = {index: _GAMELOG_STATS[name] for index, name in enumerate(names)
               if isinstance(name, str) and name in _GAMELOG_STATS}
    if not columns:
        return {}
    out: dict[str, list[Decimal]] = {}
    try:
        for season in season_types:
            for category in (season or {}).get("categories") or []:
                for event in (category or {}).get("events") or []:
                    stats = (event or {}).get("stats") or []
                    for index, stat in columns.items():
                        if index >= len(stats):
                            continue
                        value = _dec(stats[index])
                        if value is not None:
                            out.setdefault(stat, []).append(value)
    except Exception as exc:  # fail soft by ruling: an unreadable body is `no season data yet`
        log.warning("espn gamelog: unreadable body for athlete %s: %r",
                    athlete_id or "unknown", exc)
        return {}
    return out


def normalize_name(name: str) -> str:
    """The comparison key of a player name (addendum §4.2).

    Diacritics folded, case dropped, `'` and `.` removed, every other punctuation turned into a
    space, whitespace collapsed, and a trailing generational suffix (`Jr.`, `Sr.`, `II`, `III`,
    `IV`, `V`) dropped -- DraftKings and ESPN disagree about all five.
    """
    if not isinstance(name, str):
        return ""
    folded = unicodedata.normalize("NFKD", name).translate(_DROPPED)
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    cleaned = "".join(ch if ch.isalnum() else " " for ch in folded).lower()
    tokens = cleaned.split()
    while len(tokens) > 1 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return " ".join(tokens)


def _initial_match(query: list[str], candidate: list[str]) -> bool:
    """An initial standing in for a first name, in either direction: the venue writes
    `M. Harrison` where the roster has `Marvin Harrison Jr.`, and a roster occasionally carries
    the initial itself. The last token must be equal either way."""
    if len(query) < 2 or len(candidate) < 2 or query[-1] != candidate[-1]:
        return False
    return ((len(query[0]) == 1 and candidate[0].startswith(query[0]))
            or (len(candidate[0]) == 1 and query[0].startswith(candidate[0])))


def match_player(name: str, candidates: list) -> int | None:
    """The one candidate this name can only be, or `None` (addendum §4.2, D14).

    `candidates` is the `(id, name)` sequence of the game's two rosters. Every candidate that an
    exact normalized match *or* the initial rule admits is a survivor; exactly one survivor
    returns its id and anything else -- nobody, or two people the name fits -- returns `None`.
    Ambiguity never picks, even when one of the two is certainly the right answer: a prop leg
    built on the wrong player is a wrong ticket, and an unmatched outcome is only a leg not
    built.
    """
    key = normalize_name(name)
    if not key:
        return None
    tokens = key.split()
    hits = []
    for candidate_id, candidate_name in candidates:
        other = normalize_name(candidate_name)
        if other and (other == key or _initial_match(tokens, other.split())):
            hits.append(candidate_id)
    return hits[0] if len(hits) == 1 else None


def upsert_players(session: Session, sport: str, team_id: int | None, rows: list[dict],
                   now) -> int:
    """Store one team's roster, returning the number of rows written (addendum §4.2).

    Bounded by the roster body itself -- about 90 rows for an NFL team, a few more for a college
    one -- and keyed by `uq_players_sport_espn`, so re-running a week's fetch updates in place
    rather than growing the table. `now` is the caller's fixed moment; this module never reads
    the clock.
    """
    unique: dict[tuple[str, str], dict] = {}
    for row in rows:
        espn_id = str(row.get("espn_id") or "")
        name = str(row.get("name") or "").strip()
        if not espn_id or not name:
            continue
        # A body repeating one athlete (a two-way player listed in two groups) would make
        # `on conflict do update` touch one row twice in a single statement, which Postgres
        # refuses; the last spelling wins.
        unique[(sport, espn_id[:16])] = {
            "sport": sport, "espn_id": espn_id[:16], "name": name[:80], "team_id": team_id,
            "position": (str(row["position"])[:6] if row.get("position") else None),
            "updated_at": now,
        }
    if not unique:
        return 0
    stmt = insert(Player).values(list(unique.values()))
    stmt = stmt.on_conflict_do_update(
        index_elements=["sport", "espn_id"],
        set_={"name": stmt.excluded.name, "team_id": stmt.excluded.team_id,
              "position": stmt.excluded.position, "updated_at": stmt.excluded.updated_at})
    written = len(session.execute(stmt.returning(Player.id)).fetchall())
    session.flush()
    return written


_PLAYERS_OF_GAME = text("""
    -- One game's two rosters: `games` by primary key (one row), `players` by
    -- uq_players_sport_espn's leading (sport) column then team_id; bounded to two teams.
    select p.id, p.name from players p
    join games g on g.id = :game_id
    where p.sport = :sport and p.team_id in (g.home_team_id, g.away_team_id)
""")


def candidates_for_game(session: Session, sport: str, game_id: int) -> list[tuple[int, str]]:
    """The `(id, name)` pairs of the two teams playing this game (addendum §4.2).

    Bound: one game's two `team_id`s. `players` is a small table the roster fetch fills weekly --
    about 90 rows a team, so about 180 here -- and this is a walk of it, not a bulk read. Called
    once per `game_id` in a normalize pass and cached by the caller, never once per outcome.
    """
    return [(row.id, row.name) for row in session.execute(
        _PLAYERS_OF_GAME, {"sport": sport, "game_id": game_id})]
