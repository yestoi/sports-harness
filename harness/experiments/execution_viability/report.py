"""§1.9(c)-(f): the reporting contract -- two tables, two captions, and no summed portfolio.

Three rules this module exists to keep:

* **Separation (§0.10, §1.9e).** Registered historical performance and exploratory arm results
  are printed in *separate tables with separate captions*, and every exploratory cell carries
  `exp_label(run_id, arm_id, manifest_hash)` on its own line. No exploratory number can be
  read as a registered variant's performance by anyone who reads only the row.
* **No summed portfolio (§1.5).** Two portfolio identities are never added together:
  `sum_rows` refuses, by name, rather than returning a number whose meaning nobody can state.
* **Nothing travels alone (§1.9c/d).** The order-weighted result is primary and its
  game-weighted and market-side-weighted sensitivities sit beside it; the source age travels
  with every outcome number; `cluster_ci(..., level=0.90)` is used unchanged, one-cluster
  `nan` convention intact; the episode rule and its parameters are printed **before** any arm
  outcome; and the concentration, instant-count and charter lines are printed even when a run
  did not supply them, as an explicit "not supplied" rather than an omission.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

from harness.experiments.execution_viability import exp_label
from harness.report.stats import cluster_ci

#: The confidence level §1.9(c) fixes, passed to `cluster_ci` unchanged.
CI_LEVEL = 0.90


class PortfolioSumRefused(RuntimeError):
    """Two portfolio identities were asked to be added together (§1.5).

    A portfolio identity is `(arm, portfolio)`: its liquidity was conserved inside itself and
    nowhere else, so a sum across two of them counts the same recorded print twice.
    """


#: §1.9(d)'s column list, in the order it states them: what the row is, how much of it there
#: was, what rested, what it cost, how fresh the inputs were, and only then the outcome -- with
#: the order-weighted number marked primary and its two sensitivities beside it.
COLUMNS: tuple[tuple[str, str], ...] = (
    ("arm_id", "arm"),
    ("portfolio", "portfolio"),
    ("games", "eligible games"),
    ("markets", "eligible markets"),
    ("observations", "observations (done/scheduled)"),
    ("placements", "placements"),
    ("episodes", "episodes"),
    ("orders", "orders"),
    ("fills", "filled orders"),
    ("filled_games", "filled games"),
    ("partial_fills", "partial fills"),
    ("contracts", "contracts"),
    ("clean_resting_s", "clean resting (s)"),
    ("dirty_resting_s", "dirty resting (s)"),
    ("capacity_exclusions", "capacity exclusions"),
    ("queue_tenure_s", "queue tenure (s)"),
    ("repricing_loss", "repricing loss"),
    ("source_age_s", "source age at decision"),
    ("markout_1800", "markout 1800 s (order-weighted, primary)"),
    ("markout_1800_game", "markout 1800 s (game-weighted)"),
    ("markout_1800_market_side", "markout 1800 s (market-side-weighted)"),
    ("clv", "mature net CLV"),
    ("adverse_drift", "adverse drift"),
    ("ci", "game-clustered CI (level 0.90)"),
    ("missing", "missing outcomes"),
    ("censored", "censored outcomes"),
    ("resources", "resources (wall clock, rows, credits)"),
)

#: §1.9(b)'s rule, its parameters and its re-entry statement, printed above the first outcome.
EPISODE_RULE = (
    "Episode rule (frozen before the run): an episode opens on the first candidate sighting "
    "for (run, arm, variant, market, side) and extends while the previous sighting is within "
    "gap_rule_s = max(600 s, 3 x cadence in force); a longer hole opens a new one, and a "
    "re-entry after a cancel inside the hole is the same episode.")

_MISSING = "-"


def _identity(row) -> tuple:
    return (row.get("arm_id"), row.get("portfolio"))


def sum_rows(rows: Sequence[dict]) -> dict:
    """The one summary this module will produce: **one** portfolio identity's rows added up.

    Two identities raise `PortfolioSumRefused` (§1.5): a print the portfolio ledger capped
    inside one identity was never available to the other, so the sum would double-count it.
    """
    identities = {_identity(row) for row in rows}
    if len(identities) > 1:
        named = ", ".join(sorted(f"{arm}/{portfolio}" for arm, portfolio in identities))
        raise PortfolioSumRefused(
            f"refusing to add {len(identities)} portfolio identities ({named}): liquidity is "
            "conserved inside one identity and a sum across two counts one print twice (§1.5)")
    out: dict = {}
    for row in rows:
        for key, value in row.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                out[key] = out.get(key, 0) + value
    out["arm_id"], out["portfolio"] = next(iter(identities), (None, None))
    return out


def _ci_cell(row) -> str:
    """`cluster_ci` unchanged, printed with its own `nan` where a single game supplied it."""
    values, clusters = row.get("values"), row.get("clusters")
    if not values or clusters is None:
        return _MISSING
    ci = cluster_ci(list(values), list(clusters), level=CI_LEVEL)
    def num(x):
        return "nan" if x is None or math.isnan(x) else f"{x:.4f}"
    return (f"{num(ci.mean)} [{num(ci.lo)}, {num(ci.hi)}] "
            f"(obs {ci.n_obs}, games {ci.n_clusters})")


def _cell(row, key) -> str:
    if key == "ci":
        return _ci_cell(row)
    value = row.get(key)
    if value is None:
        return _MISSING
    if key == "source_age_s":
        # §1.9(d): the age travels **with** the number, in the same cell and on the same line.
        return f"{value} s"
    return str(value)


def arm_table(rows: Iterable[dict], *, run_id: str, manifest_hash: str,
              exploratory: bool | None = None) -> str:
    """§1.9(d)'s per-variant, per-arm table: one line per portfolio identity, never a sum.

    `exploratory` decides which rows carry `exp_label`: `None` reads each row's own
    `registered` flag, and `render` passes it explicitly so a row in the exploratory table is
    labelled whatever the flag says.
    """
    rows = list(rows)
    header = " | ".join(title for _key, title in COLUMNS)
    lines = [header, "-" * len(header)]
    for row in rows:
        cells = [_cell(row, key) for key, _title in COLUMNS]
        labelled = exploratory if exploratory is not None else not row.get("registered", False)
        if labelled:
            cells.append(exp_label(run_id, str(row.get("arm_id")), manifest_hash))
        lines.append(" | ".join(cells))
    return "\n".join(lines)


def render(registered: Iterable[dict], exploratory: Iterable[dict], *, run_id: str,
           manifest_hash: str, concentration: dict | None = None,
           charter: Sequence[str] | None = None, instants: int | None = None,
           live_loop_estimate: int | None = None) -> str:
    """The whole report: the rule first, then the two tables, then the charter status.

    `instants` and `live_loop_estimate` are ruling C1's pair -- the resolved instant count is
    meaningless without the live loop estimate beside it -- and `concentration` is §1.9(c)'s
    maximum contribution by game and the allocation ledger's allocated-against-requested
    contracts. A run that supplied none of them prints that it did not; none of the three is
    ever silently absent.
    """
    out: list[str] = [
        f"6D.1 execution viability, run={run_id} manifest={manifest_hash[:12]}",
        "",
        EPISODE_RULE,
    ]
    if instants is None or live_loop_estimate is None:
        out.append("Resolved instants: not supplied for this render; the resolved instant "
                   "count is printed beside the live loop estimate (ruling C1).")
    else:
        out.append(f"Resolved instants: {instants}, beside a live loop estimate of "
                   f"{live_loop_estimate} loops over the same window (ruling C1).")
    if concentration:
        parts = ", ".join(f"{name}: {value}" for name, value in sorted(concentration.items()))
        out.append(f"Concentration: {parts}.")
    else:
        out.append("Concentration: not supplied for this render; the maximum contribution by "
                   "game and the allocated-against-requested contracts are printed beside "
                   "every result.")
    out += ["", "Registered results (recorded performance of registered variant ids)"]
    registered = list(registered)
    out.append(arm_table(registered, run_id=run_id, manifest_hash=manifest_hash,
                         exploratory=False) if registered else "  (no registered rows)")
    out += ["", "Exploratory results (6D.1 arms; not a registered variant's performance)"]
    exploratory = list(exploratory)
    out.append(arm_table(exploratory, run_id=run_id, manifest_hash=manifest_hash,
                         exploratory=True) if exploratory else "  (no exploratory rows)")
    out.append("")
    if charter:
        out.append("Charter status carried forward: " + "; ".join(charter) + ".")
    else:
        out.append("Charter status: not supplied for this render; the mispricing map, the "
                   "convergence lag and the H9 sample stay visible here and this milestone "
                   "expands none of them.")
    return "\n".join(out)
