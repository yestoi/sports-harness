"""§1.9(b): opportunity episodes, and the rule that decides where one ends.

An episode opens on the first candidate sighting for `(run, arm, variant, venue_market_id,
side)` and extends while the previous sighting is within
`gap_rule_s = max(600, 3 x cadence_in_force)` (6D §1.7b's rule); a longer hole opens a new
episode. **Re-entry after a cancel inside the window is the same episode**, and after
`gap_rule_s` a new one -- which is why a sighting carries its kind and an episode counts its
re-entries rather than reading a cancel as an ending.

The rule and its parameters are stored on every episode rather than assumed by the reader, so
a re-count under another rule needs no new data, and `report.render` prints both **before** any
arm outcome is read (§1.9b).
"""
from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime

#: The floor of §1.9(b)'s rule: ten minutes, whatever the cadence was.
GAP_FLOOR_S = 600

#: What a sighting can be. Anything not named here is a candidate sighting.
RE_ENTRY = "re_entry"
CANCEL = "cancel"
CANDIDATE = "candidate"


def gap_rule_s(cadence_in_force: int) -> int:
    """`max(600, 3 x cadence_in_force)` (§1.9b, 6D §1.7b).

    `cadence_in_force` is the interval that was actually scheduled for the slice, never a
    nominal one: zero or negative is not a cadence and is refused rather than collapsing the
    rule to its floor unnoticed.
    """
    cadence = int(cadence_in_force)
    if cadence <= 0:
        raise ValueError(
            f"cadence_in_force must be the interval actually scheduled, got {cadence_in_force!r}")
    return max(GAP_FLOOR_S, 3 * cadence)


@dataclass(frozen=True, slots=True)
class Episode:
    """One opportunity episode, with the rule it was counted under stored beside it."""

    started_at: datetime
    ended_at: datetime
    sightings: tuple[datetime, ...]
    kinds: tuple[str, ...]
    cadence_in_force: int
    gap_rule_s: int

    @property
    def candidates(self) -> int:
        """Every sighting row in the episode: §1.9's \"4 candidate rows\" across 2 episodes."""
        return len(self.sightings)

    @property
    def re_entries(self) -> int:
        return sum(1 for kind in self.kinds if kind == RE_ENTRY)

    @property
    def span_s(self) -> int:
        return int((self.ended_at - self.started_at).total_seconds())


def _sighting(row) -> tuple[datetime, str]:
    """`(at, kind)` from a datetime, a mapping or anything carrying `.at`/`.kind`."""
    if isinstance(row, datetime):
        return row, CANDIDATE
    if isinstance(row, dict):
        return row["at"], str(row.get("kind") or CANDIDATE)
    return row.at, str(getattr(row, "kind", None) or CANDIDATE)


def episodes_for(sightings: Iterable, *, cadence_in_force: int) -> list[Episode]:
    """The episodes `sightings` fall into, in their own stamp order.

    The hole is measured from the **previous sighting**, not from the episode's start, so an
    episode may span far longer than its own rule as long as no single hole exceeds it.
    """
    rule = gap_rule_s(cadence_in_force)
    rows: Sequence[tuple[datetime, str]] = sorted((_sighting(row) for row in sightings),
                                                  key=lambda row: row[0])
    out: list[Episode] = []
    current: list[tuple[datetime, str]] = []

    def close() -> None:
        if current:
            out.append(Episode(
                started_at=current[0][0], ended_at=current[-1][0],
                sightings=tuple(at for at, _kind in current),
                kinds=tuple(kind for _at, kind in current),
                cadence_in_force=int(cadence_in_force), gap_rule_s=rule))

    for at, kind in rows:
        if current and (at - current[-1][0]).total_seconds() > rule:
            close()
            current = []
        current.append((at, kind))
    close()
    return out
