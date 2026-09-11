"""Stadium geography for the NWS forecast source (phase 5, addendum §1.2).

`games` has no venue column and `teams` has no coordinates and no roof, so the geography is a
committed table rather than a query. Three files, and the coverage test over their union is what
keeps a team from being silently absent:

* `stadiums.yaml`      -- one row per team, keyed (sport, team_id)
* `neutral_sites.yaml` -- per-game overrides, keyed (sport, home, away, kickoff date)
* `stadiums_missing.txt` -- the teams with no row, each with a reason
* `roster.txt`         -- the set the coverage test judges against

The roster is a file and not a database read on purpose. The addendum's phrase is "every `teams`
row with a `popularity_tier`", and `popularity_tier` is defaulted to 0 by the seeder and set by
nothing in the codebase, so it cannot be read as a filter without inventing one. The set that can
ever host a game the harness prices is the NFL clubs and the FBS programmes, and committing it
makes coverage a reviewable fact instead of a property of whichever database the test ran against.

The three geography loaders (`load_stadiums`, `load_neutral_sites`, `load_missing`) are
`functools.lru_cache`d: the files are read-only at run time, `stadium_for` calls the first two on
every due game, and re-parsing a 2,020-line YAML per game was spending a growing share of the
weather source's deliberately small 20 s budget on a file that never changes (fix round 2, I3).
A test that rewrites one of these files under `stadiums`'s package path must call the loader's
own `.cache_clear()` first.
"""
import functools
import importlib.resources
from dataclasses import dataclass
from datetime import date

import yaml

#: D2's vocabulary. `dome` is never fetched and never gets a `weather_points` row; `retractable`
#: is fetched and labelled, because a forecast over a closed roof is a different fact from the
#: same forecast over an open one and only the label tells them apart.
ROOFS = ("open", "dome", "retractable")

_PACKAGE = "harness.weather"


@dataclass(frozen=True)
class Stadium:
    sport: str
    team_id: int
    abbreviation: str
    name: str
    lat: float
    lon: float
    roof: str
    source: str


def is_outdoor(stadium: Stadium) -> bool:
    """Whether this venue gets a forecast at all."""
    return stadium.roof != "dome"


def _read(name: str) -> str:
    return importlib.resources.files(_PACKAGE).joinpath(name).read_text()


def _stadium(sport: str, team_id: int, row: dict) -> Stadium:
    return Stadium(sport=sport, team_id=team_id, abbreviation=str(row["abbreviation"]),
                   name=str(row["name"]), lat=float(row["lat"]), lon=float(row["lon"]),
                   roof=str(row["roof"]), source=str(row["source"]))


def load_roster() -> list[tuple[str, int, str, str]]:
    """`(sport, team_id, abbreviation, display_name)` for every team the coverage test covers."""
    rows = []
    for line in _read("roster.txt").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        sport, team_id, abbreviation, name = line.split("\t", 3)
        rows.append((sport, int(team_id), abbreviation, name))
    return rows


@functools.lru_cache(maxsize=1)
def load_stadiums() -> dict[tuple[str, int], Stadium]:
    parsed = yaml.safe_load(_read("stadiums.yaml")) or {}
    out: dict[tuple[str, int], Stadium] = {}
    for key, row in parsed.items():
        sport, team_id = key.split(":")
        out[(sport, int(team_id))] = _stadium(sport, int(team_id), row)
    return out


@functools.lru_cache(maxsize=1)
def load_neutral_sites() -> dict[tuple[str, int, int, date], Stadium]:
    parsed = yaml.safe_load(_read("neutral_sites.yaml")) or {}
    out: dict[tuple[str, int, int, date], Stadium] = {}
    for key, row in parsed.items():
        sport, home, away, day = key.split(":")
        out[(sport, int(home), int(away), date.fromisoformat(day))] = _stadium(
            sport, int(home), row)
    return out


@functools.lru_cache(maxsize=1)
def load_missing() -> dict[tuple[str, int], str]:
    out: dict[tuple[str, int], str] = {}
    for line in _read("stadiums_missing.txt").splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        sport, team_id, _abbreviation, reason = line.split("\t", 3)
        out[(sport, int(team_id))] = reason
    return out


def stadium_for(sport: str, home_team_id: int, away_team_id: int,
                kickoff_date: date) -> Stadium | None:
    """The venue this game is played at: a neutral-site override first, then the home team's
    own stadium, then None -- which the caller records once per game and skips."""
    neutral = load_neutral_sites().get((sport, home_team_id, away_team_id, kickoff_date))
    if neutral is not None:
        return neutral
    return load_stadiums().get((sport, home_team_id))
