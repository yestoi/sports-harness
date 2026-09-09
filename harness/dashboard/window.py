"""R4's game window, as one bounded query the floor and ticket jobs re-evaluate per run.

The rule is the roadmap's own (and verify.md's "Game window" block): a window is open when any
matched game is `in_progress`, when a kickoff falls in the last 4 hours or the next 15 minutes,
or when an NFL kickoff is 60 to 100 minutes away. It is asked per snapshot build rather than
cached, because the cadence flip is what it decides and a stale answer is a surface running at
the wrong tempo.

The three clauses are OR-ed and `status` is unindexed, so Postgres scans `games` rather than
using `ix_games_sport_kick`. That is deliberate and cheap: `games` holds a few thousand rows a
season, and `exists` stops at the first match, so the scan is bounded and sits well inside the
2000 ms snapshot timeout. An index on `status` would buy nothing at this size.
"""

from datetime import datetime, timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

#: How far past a kickoff a game still counts as live for the window.
AFTER_KICKOFF = timedelta(hours=4)
#: How far ahead of a kickoff any sport opens the window.
BEFORE_KICKOFF = timedelta(minutes=15)
#: The NFL pre-game band, which opens earlier because the executor works those books first.
NFL_PREGAME_EARLY = timedelta(minutes=100)
NFL_PREGAME_LATE = timedelta(minutes=60)

_OPEN = text("""
    select exists (
        select 1 from games
        where status = 'in_progress'
           or (kickoff_utc >= :after and kickoff_utc <= :before)
           or (sport = 'nfl' and kickoff_utc >= :nfl_late and kickoff_utc <= :nfl_early)
    )
""")


def game_window_open(session: Session, now: datetime) -> bool:
    """Whether a game window is open at `now` (R4)."""
    return bool(session.execute(_OPEN, {
        "after": now - AFTER_KICKOFF,
        "before": now + BEFORE_KICKOFF,
        "nfl_late": now + NFL_PREGAME_LATE,
        "nfl_early": now + NFL_PREGAME_EARLY,
    }).scalar())
