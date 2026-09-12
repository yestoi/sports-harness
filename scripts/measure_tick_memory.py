"""Finding 49's measurement rig: how much a recorder tick retains, tick over tick.

`app-run` read 78 MiB right after the 2026-09-12 00:47 CT restart, 1.82 GiB after one tick and
3.08 GiB after two, with no settle run in that window, so the growth belongs to the tick itself
(hotfix brief 48/49, controller addendum 01:20 CT). This module holds the two halves of the
measurement that are not test plumbing:

* `synthetic_day` builds Odds/ESPN/Kalshi bodies for a slate of `n_games` whose shape matches a
  Saturday on the NAS -- three sharp books a market, a ladder of alternate spreads and totals per
  game, and a moneyline/spread/total Kalshi market set per game -- so the tick normalizes and
  prices a realistic number of rows rather than a toy one.
* `measure_ticks` drives any zero-argument "run one tick" callable under `tracemalloc` and
  reports the traced total and RSS at chosen tick numbers.

The acceptance number is `tracemalloc`'s traced total, not RSS: traced total counts live Python
allocations and is deterministic, while RSS also carries allocator arenas the interpreter has
freed but not returned to the kernel. RSS is reported alongside it for the operator's benefit,
which is also why the tick writes it as `recorder.rss_mb` (`harness.telemetry.rss_mb`).

Run standalone for a longer series than the test's:

    PYTHONPATH=. DATABASE_URL_TEST=... .venv/bin/python -m pytest tests/test_recorder_memory.py -s
"""

import tracemalloc
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from harness.telemetry import rss_mb

#: Sharp books `harness.pricing.direct.SHARP_BOOKS` consults, plus the soft book
#: `build_gap_snapshots` reads for `soft_minus_sharp`. Three sharp groups give every shape an
#: `n_groups >= 2` consensus, which is what the live slate produces and what the strategy's
#: `disagreement_ok` filter needs to reach the priced branch rather than the unpriceable one.
BOOKS = ("pinnacle", "betonlineag", "lowvig", "draftkings")
#: Alternate spread and total rungs per game, per book. The live Saturday slate carries roughly
#: this many ladder rungs an event; they are what turns ~85 games into 3,000+ derived fair values.
ALT_SPREADS = (-13.5, -10.5, -7.5, -6.5, -4.5, -2.5, -1.5, 1.5, 2.5, 4.5, 6.5, 7.5)
ALT_TOTALS = (37.5, 39.5, 41.5, 43.5, 45.5, 46.5, 48.5, 50.5, 52.5, 54.5, 56.5, 58.5)
#: Kalshi spread and total strikes per game. Deliberately *off* the alternate rungs above, so
#: most shapes have no sharp line of their own and fall to the margin model -- which is the
#: NAS's own split (190 direct against 3,079 derived a tick on the 2026-09-12 Saturday slate)
#: and the reason stage 1 spends the whole pricing budget there.
K_SPREADS = (0.5, 3.5, 5.5, 8.5, 9.5, 11.5, 12.5, 14.5, 15.5, 16.5)
K_TOTALS = (38.5, 40.5, 42.5, 44.5, 47.5, 49.5, 51.5, 53.5, 55.5, 57.5)

_MONTHS = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")


@dataclass(frozen=True)
class Team:
    """Just the fields the synthetic bodies and the Kalshi matcher need off `teams`."""

    id: int
    display_name: str
    name: str
    abbreviation: str


@dataclass(frozen=True)
class SyntheticDay:
    """One slate's worth of feed bodies, keyed the way the recorder asks for them."""

    sport: str
    espn: dict
    odds_featured: list
    odds_alternates: dict[str, dict]
    kalshi_markets: dict[str, dict]
    kalshi_events: dict[str, dict]
    games: int


#: Distinct city/nickname words for `synthetic_teams`. The committed ESPN team fixtures carry
#: eight teams each, far short of the forty a twenty-game slate needs, and the measurement does
#: not care whose names they are -- only that no name is a prefix of another, since
#: `harness.matching.kalshi.side_team_id_for` accepts a prefix match of six characters or more.
_CITIES = ("Ashford", "Brightwater", "Calderon", "Dunmore", "Everhart", "Fairlane", "Granholm",
           "Harkness", "Ironvale", "Juniper", "Kestrel", "Larkspur", "Maribel", "Northgate",
           "Oakhaven", "Pemberly", "Quarryhill", "Ridgemont", "Stonebrook", "Thornwick",
           "Umberland", "Verdance", "Westmarch", "Yarrowfield")
_NICKS = ("Comets", "Drifters", "Foxes", "Gliders", "Herons", "Ibexes", "Jackals", "Kites",
          "Lynxes", "Marlins", "Nighthawks", "Otters", "Pumas", "Quails", "Ravens", "Sable")


def synthetic_teams(n: int, first_id: int = 9000) -> tuple[dict, list["Team"]]:
    """`n` distinct teams as an ESPN `/teams` body and the `Team` records that match it.

    Returns the body first because `harness.matching.teams.seed_teams_from_espn` has to have
    written the aliases before any Kalshi title resolves to a team.
    """
    if n > len(_CITIES) * len(_NICKS):
        raise ValueError(f"only {len(_CITIES) * len(_NICKS)} distinct synthetic names available")
    entries, teams = [], []
    for i in range(n):
        city, nick = _CITIES[i % len(_CITIES)], _NICKS[i // len(_CITIES)]
        abbr = f"{city[:2].upper()}{nick[0].upper()}"
        display = f"{city} {nick}"
        team_id = first_id + i
        entries.append({"team": {"id": str(team_id), "displayName": display, "location": city,
                                 "name": nick, "abbreviation": abbr, "shortDisplayName": city,
                                 "slug": display.lower().replace(" ", "-")}})
        teams.append(Team(id=team_id, display_name=display, name=nick, abbreviation=abbr))
    return {"sports": [{"leagues": [{"teams": entries}]}]}, teams


def _event_date_code(when: date) -> str:
    return f"{when.year % 100:02d}{_MONTHS[when.month - 1]}{when.day:02d}"


def _odds_bookmakers(home: Team, away: Team, book_last_update: datetime, *,
                     spread: float, total: float) -> list[dict]:
    """One featured `bookmakers` block: h2h, spreads and totals for every book in `BOOKS`.

    Each book is nudged off the others by its index so `consensus` sees real disagreement
    rather than an identical set, which is what the live slate looks like.
    """
    stamp = book_last_update.isoformat().replace("+00:00", "Z")
    blocks = []
    for i, book in enumerate(BOOKS):
        skew = 0.01 * i
        blocks.append({
            "key": book,
            "last_update": stamp,
            "markets": [
                {"key": "h2h", "outcomes": [
                    {"name": home.display_name, "price": round(1.68 + skew, 4)},
                    {"name": away.display_name, "price": round(2.30 - skew, 4)},
                ]},
                {"key": "spreads", "outcomes": [
                    {"name": home.display_name, "price": round(1.92 + skew, 4), "point": spread},
                    {"name": away.display_name, "price": round(1.98 - skew, 4), "point": -spread},
                ]},
                {"key": "totals", "outcomes": [
                    {"name": "Over", "price": round(1.91 + skew, 4), "point": total},
                    {"name": "Under", "price": round(1.99 - skew, 4), "point": total},
                ]},
            ],
        })
    return blocks


def _alternates_body(event_id: str, home: Team, away: Team, commence: datetime,
                     book_last_update: datetime) -> dict:
    stamp = book_last_update.isoformat().replace("+00:00", "Z")
    blocks = []
    for i, book in enumerate(BOOKS):
        skew = 0.01 * i
        spread_outcomes = []
        for point in ALT_SPREADS:
            spread_outcomes.append({"name": home.display_name, "price": round(1.90 + skew, 4), "point": point})
            spread_outcomes.append({"name": away.display_name, "price": round(2.00 - skew, 4), "point": -point})
        total_outcomes = []
        for point in ALT_TOTALS:
            total_outcomes.append({"name": "Over", "price": round(1.88 + skew, 4), "point": point})
            total_outcomes.append({"name": "Under", "price": round(2.02 - skew, 4), "point": point})
        blocks.append({
            "key": book,
            "last_update": stamp,
            "markets": [
                {"key": "alternate_spreads", "outcomes": spread_outcomes},
                {"key": "alternate_totals", "outcomes": total_outcomes},
            ],
        })
    return {
        "id": event_id,
        "commence_time": commence.isoformat().replace("+00:00", "Z"),
        "home_team": home.display_name,
        "away_team": away.display_name,
        "bookmakers": blocks,
    }


def _quote(i: int) -> dict:
    """A Kalshi quote block inside the price band, with volume above every variant's floor."""
    yes_bid = 0.30 + (i % 20) * 0.01
    return {
        "yes_bid_dollars": f"{yes_bid:.4f}",
        "yes_ask_dollars": f"{yes_bid + 0.01:.4f}",
        "no_bid_dollars": f"{1 - yes_bid - 0.01:.4f}",
        "no_ask_dollars": f"{1 - yes_bid:.4f}",
        "volume_fp": "1500.00",
        "volume_24h_fp": "800.00",
        "open_interest_fp": "2500.00",
        "status": "open",
    }


def synthetic_day(teams: list[Team], n_games: int, now: datetime, sport: str = "nfl") -> SyntheticDay:
    """Build one slate of `n_games` games from `teams`, kicking off a day after `now`.

    A day out is inside `_candidate_games`' eight-day window, inside the 36 h horizon
    `Recorder._odds` fetches alternates for -- without which the ladder never reaches the tape
    and the derived pricing this measurement exists to exercise has nothing to work on -- and
    far enough from kickoff that no variant's `min_ttk_min` filter trips.
    """
    if len(teams) < 2 * n_games:
        raise ValueError(f"{n_games} games need {2 * n_games} teams, got {len(teams)}")
    kickoff = (now + timedelta(days=1)).replace(minute=20, second=0, microsecond=0)
    book_last_update = now - timedelta(minutes=1)
    stamp = kickoff.isoformat().replace("+00:00", "Z")
    date_code = _event_date_code(kickoff.date())

    espn_events, odds_events = [], []
    alternates: dict[str, dict] = {}
    markets: dict[str, list] = {f"KXNFLGAME": [], "KXNFLSPREAD": [], "KXNFLTOTAL": []}
    events: dict[str, list] = {f"KXNFLGAME": [], "KXNFLSPREAD": [], "KXNFLTOTAL": []}
    if sport == "ncaaf":
        markets = {k.replace("NFL", "NCAAF"): v for k, v in markets.items()}
        events = {k.replace("NFL", "NCAAF"): v for k, v in events.items()}

    for g in range(n_games):
        away, home = teams[2 * g], teams[2 * g + 1]
        event_id = f"ev{g:04d}"
        espn_events.append({
            "id": f"4018{g:05d}",
            "date": stamp,
            "name": f"{away.display_name} at {home.display_name}",
            "status": {"type": {"name": "STATUS_SCHEDULED"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"abbreviation": home.abbreviation,
                                              "displayName": home.display_name}},
                {"homeAway": "away", "team": {"abbreviation": away.abbreviation,
                                              "displayName": away.display_name}},
            ]}],
        })
        spread = -3.5 - (g % 5)
        total = 44.5 + (g % 6)
        odds_events.append({
            "id": event_id,
            "sport_key": f"americanfootball_{sport}",
            "commence_time": stamp,
            "home_team": home.display_name,
            "away_team": away.display_name,
            "bookmakers": _odds_bookmakers(home, away, book_last_update, spread=spread, total=total),
        })
        alternates[event_id] = _alternates_body(event_id, home, away, kickoff, book_last_update)

        codes = f"{away.abbreviation}{home.abbreviation}"
        title = f"{away.display_name} vs {home.display_name}"
        for series_suffix, strikes in (("GAME", None), ("SPREAD", K_SPREADS), ("TOTAL", K_TOTALS)):
            series = f"KX{'NCAAF' if sport == 'ncaaf' else 'NFL'}{series_suffix}"
            event_ticker = f"{series}-{date_code}{codes}"
            events[series].append({
                "event_ticker": event_ticker, "series_ticker": series,
                "title": title if series_suffix == "GAME" else f"{title}: {series_suffix.title()}",
            })
            if series_suffix == "GAME":
                for side in (home, away):
                    markets[series].append({
                        "ticker": f"{event_ticker}-{side.abbreviation}",
                        "event_ticker": event_ticker,
                        "title": f"{side.display_name} wins",
                        "yes_sub_title": side.display_name,
                        "strike_type": "structured",
                        "close_time": stamp,
                        **_quote(g + side.id),
                    })
                continue
            for j, strike in enumerate(strikes):
                if series_suffix == "SPREAD":
                    ticker = f"{event_ticker}-{home.abbreviation}{str(strike).replace('.', '')}"
                    body = {"title": f"{home.display_name} wins by over {strike} points?",
                            "yes_sub_title": home.display_name}
                else:
                    ticker = f"{event_ticker}-T{str(strike).replace('.', '')}"
                    body = {"title": f"Over {strike} total points?", "yes_sub_title": "Over"}
                markets[series].append({
                    "ticker": ticker, "event_ticker": event_ticker, "strike_type": "greater",
                    "floor_strike": strike, "close_time": stamp, **body, **_quote(g + j),
                })

    return SyntheticDay(
        sport=sport,
        espn={"events": espn_events},
        odds_featured=odds_events,
        odds_alternates=alternates,
        kalshi_markets={s: {"cursor": "", "markets": m} for s, m in markets.items()},
        kalshi_events={s: {"cursor": "", "events": e} for s, e in events.items()},
        games=n_games,
    )


# --- the measurement itself --------------------------------------------------------------

#: Excluded from the traced total, because it is the database driver's cache and not the
#: recorder's retention. psycopg prepares a statement on its `prepare_threshold`-th execution
#: (default 5) and holds the built query bytes until `prepared_max` (default 100) evicts them,
#: so the cache is bounded by construction -- but it fills at whichever tick each statement
#: happens to cross the threshold, which depends on what ran in the process before. Measured on
#: 2026-09-12 it was worth 417 KiB against a 553 KiB harness total in one `make test` run and
#: 1,231 KiB against 558 KiB in another, while the harness's own retained allocation was the
#: same to the kilobyte in both: without this filter the criterion measures psycopg.
_DRIVER_CACHE = tracemalloc.Filter(False, "*/psycopg/_queries.py")


def _totals(snapshot) -> tuple[float, float]:
    """(traced KiB excluding the driver cache, driver cache KiB) for one snapshot."""
    total = sum(s.size for s in snapshot.statistics("filename"))
    kept = sum(s.size for s in snapshot.filter_traces([_DRIVER_CACHE]).statistics("filename"))
    return kept / 1024, (total - kept) / 1024


@dataclass(frozen=True)
class TickSample:
    tick: int
    traced_kb: float
    peak_kb: float
    rss_mb: float | None
    driver_kb: float = 0.0

    def line(self) -> str:
        rss = "n/a" if self.rss_mb is None else f"{self.rss_mb:8.1f}"
        return (f"tick {self.tick:3d}  traced {self.traced_kb:10.1f} KiB  "
                f"peak {self.peak_kb:10.1f} KiB  rss {rss} MiB  "
                f"driver cache {self.driver_kb:9.1f} KiB")


@dataclass(frozen=True)
class MemoryReport:
    samples: list[TickSample]
    top: list[str]
    grew: list[str] = field(default_factory=list)

    def creep_kb_per_tick(self, baseline_tick: int) -> float:
        """KiB of traced total added per tick between `baseline_tick` and the last sample.

        This, not the fraction `growth_after` returns, is what "the tick retains nothing" means.
        A fraction is measured against the baseline total, and that total is whatever the process
        happened to be carrying when this file started running -- 528 KiB inside `make test` and
        835 KiB behind three other test files, for the same rig on the same day -- so the same
        few kilobytes of creep read as 5.9 % in one process and 2.2 % in the next. Per-tick
        growth has no such scale in it: retention that matters is per tick by definition, and
        this number is directly comparable across runs.
        """
        base = self.at(baseline_tick)
        ticks = self.samples[-1].tick - base.tick
        if ticks <= 0:
            return 0.0
        return (self.samples[-1].traced_kb - base.traced_kb) / ticks

    def at(self, tick: int) -> TickSample:
        for s in self.samples:
            if s.tick == tick:
                return s
        raise KeyError(tick)

    def growth_after(self, baseline_tick: int) -> float:
        """Fractional growth in traced total from `baseline_tick` to the last sample.

        The baseline is never the first tick: tick 1 warms every lazily built cache the process
        has (SQLAlchemy's compiled-statement cache, the popularity table, the normalizer's event
        cache, psycopg's prepared statements), and counting that warm-up as a leak would make
        the criterion unmeetable for reasons that have nothing to do with retention. How many
        ticks the warm-up takes depends on the cache: see the caller for the tick it picks and
        why.
        """
        base = self.at(baseline_tick).traced_kb
        if base <= 0:
            return 0.0
        return (self.samples[-1].traced_kb - base) / base

    def text(self) -> str:
        blocks = [s.line() for s in self.samples] + [""] + self.top
        if self.grew:
            blocks += ["", "grew since the baseline tick (driver cache excluded):"] + self.grew
        return "\n".join(blocks)


class StagePeaks:
    """Per-tick peak allocation of named stages, in KiB.

    Traced *total* after a tick says what the tick kept; it says nothing about what the tick
    had to hold at once, and on the NAS it is the second number that matters -- CPython hands
    an arena back to the kernel only when it empties, so a stage that peaks at a gigabyte
    leaves RSS there long after the objects are gone. `wrap` measures that peak per stage.
    """

    def __init__(self) -> None:
        self.ticks: list[dict[str, float]] = []
        self._current: dict[str, float] = {}

    def wrap(self, name: str, fn):
        def inner(*args, **kwargs):
            tracemalloc.reset_peak()
            before = tracemalloc.get_traced_memory()[0]
            result = fn(*args, **kwargs)
            peak = tracemalloc.get_traced_memory()[1]
            # The largest reading wins when a stage runs more than once in a tick.
            self._current[name] = max(self._current.get(name, 0.0), (peak - before) / 1024)
            tracemalloc.reset_peak()
            return result

        return inner

    def end_tick(self) -> None:
        self.ticks.append(dict(self._current))
        self._current = {}

    def series(self, name: str) -> list[float]:
        return [t.get(name, 0.0) for t in self.ticks]

    def growth(self, name: str, first: int = 1) -> float:
        """Fractional growth of `name`'s peak from tick `first` (1-based) to the last tick."""
        values = self.series(name)[first - 1:]
        if not values or values[0] <= 0:
            return 0.0
        return (values[-1] - values[0]) / values[0]

    def text(self, names: tuple[str, ...]) -> str:
        head = "tick  " + "  ".join(f"{n:>14}" for n in names)
        lines = [head + "   (peak KiB)"]
        for i, tick in enumerate(self.ticks, start=1):
            lines.append(f"{i:4d}  " + "  ".join(f"{tick.get(n, 0.0):14.1f}" for n in names))
        return "\n".join(lines)


def measure_ticks(run_tick, ticks: int, checkpoints: tuple[int, ...] = (1, 2, 5, 10),
                  top_n: int = 12, baseline_tick: int = 2) -> MemoryReport:
    """Run `run_tick()` `ticks` times under `tracemalloc`, sampling at `checkpoints` and last.

    A `gc.collect()` runs before each sample so the traced total counts what is genuinely
    reachable rather than what the cyclic collector has not got to yet -- SQLAlchemy's
    identity map and instance state are full of cycles, and an uncollected cycle would read
    as retention that a later generation sweep would have cleared anyway.

    `report.grew` is the answer to the only question a failure here raises: *what* grew. It
    diffs `baseline_tick`'s snapshot against the last one, driver cache excluded, so the reader
    gets allocation sites rather than a number to argue with.
    """
    import gc

    wanted = set(checkpoints) | {ticks, baseline_tick}
    tracemalloc.start()
    samples: list[TickSample] = []
    snapshot = None
    baseline_snapshot = None
    try:
        for i in range(1, ticks + 1):
            run_tick()
            if i not in wanted:
                continue
            gc.collect()
            _, peak = tracemalloc.get_traced_memory()
            # A snapshot per checkpoint, not one at the end: the traced total this criterion is
            # asserted on has the driver's prepared-statement cache taken out of it, and only a
            # snapshot can say how much of the total that cache is.
            snapshot = tracemalloc.take_snapshot()
            traced_kb, driver_kb = _totals(snapshot)
            samples.append(TickSample(i, traced_kb, peak / 1024, rss_mb(), driver_kb))
            if i == baseline_tick:
                baseline_snapshot = snapshot
        top, grew = [], []
        if snapshot is not None:
            for stat in snapshot.statistics("lineno")[:top_n]:
                top.append(f"  {stat.size / 1024:9.1f} KiB  {stat.count:7d} blocks  {stat.traceback[0]}")
        if snapshot is not None and baseline_snapshot is not None:
            diff = snapshot.filter_traces([_DRIVER_CACHE]).compare_to(
                baseline_snapshot.filter_traces([_DRIVER_CACHE]), "lineno")
            for stat in diff[:top_n]:
                if stat.size_diff <= 0:
                    break
                grew.append(f"  {stat.size_diff / 1024:+9.1f} KiB  {stat.count_diff:+7d} blocks  "
                            f"{stat.traceback[0]}")
    finally:
        tracemalloc.stop()
    return MemoryReport(samples=samples, top=top, grew=grew)
