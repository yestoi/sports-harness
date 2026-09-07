import math
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP
from statistics import pstdev

FOUR = Decimal("0.0001")
GROUPS = {"pinnacle": ("pinnacle",), "bol": ("betonlineag", "lowvig")}
WEIGHTS = {"pinnacle": Decimal("0.65"), "bol": Decimal("0.35")}


@dataclass(frozen=True)
class BookFair:
    book: str
    fair_p: Decimal
    last_update: datetime | None


@dataclass(frozen=True)
class Consensus:
    fair_p: Decimal
    n_groups: int
    disagreement: Decimal
    newest_ts: datetime | None
    group_fairs: dict


def _clip(p: float) -> float:
    return min(max(p, 0.0001), 0.9999)


def consensus(fairs: list[BookFair], require: str = "pinnacle") -> Consensus | None:
    by_group: dict[str, list[Decimal]] = {}
    newest: datetime | None = None
    for bf in fairs:
        for g, members in GROUPS.items():
            if bf.book in members:
                by_group.setdefault(g, []).append(bf.fair_p)
        if bf.last_update and (newest is None or bf.last_update > newest):
            newest = bf.last_update
    if require not in by_group:
        return None
    group_fairs = {g: (sum(v) / len(v)).quantize(FOUR, rounding=ROUND_HALF_UP) for g, v in by_group.items()}
    wsum = sum(WEIGHTS[g] for g in group_fairs)
    logit = sum(float(WEIGHTS[g] / wsum) * math.log(_clip(float(p)) / (1 - _clip(float(p)))) for g, p in group_fairs.items())
    fair = Decimal(str(_clip(1 / (1 + math.exp(-logit))))).quantize(FOUR, rounding=ROUND_HALF_UP)
    dis = Decimal(str(pstdev([float(p) for p in group_fairs.values()]))).quantize(FOUR, rounding=ROUND_HALF_UP) if len(group_fairs) > 1 else Decimal("0.0000")
    return Consensus(fair, len(group_fairs), dis, newest, group_fairs)
