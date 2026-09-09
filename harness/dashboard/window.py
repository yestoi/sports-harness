"""R4's game window, as one bounded query the floor and ticket jobs re-evaluate per run.

The rule is the roadmap's own (and verify.md's "Game window" block): a window is open when any
matched game is `in_progress`, when a kickoff falls in the last 4 hours or the next 15 minutes,
or when an NFL kickoff is 60 to 100 minutes away. It is asked per snapshot build rather than
cached, because the cadence flip is what it decides and a stale answer is a surface running at
the wrong tempo. `ix_games_sport_kick` (sport, kickoff_utc) serves it; `games` is a few thousand
rows a season, so this is a small index read, not a scan.
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
