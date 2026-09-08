"""The weekly report (spec §7.2, addendum §4): ten fixed tables over one ISO week's rows.

The package is read-only over the database. Nothing here writes a row, bumps a version or
touches `harness/execution`, `harness/pricing` or `harness/settlement`; the report's only
outputs are a Markdown document and, when the controller asks for it, the week-38 selection
artefact.

`CRITERIA_TEXT` lives here rather than in `harness/report/weekly.py` because two modules need
it: this report prints its hash every week, and `harness gate` (Task 11) evaluates exactly
these criteria and stores the same hash on every `gate_reports` row. Task 11 imports this
constant rather than restating the criteria, so the two can never drift -- if they did, the
hash printed beside a gate report would no longer identify the definitions that produced it.
"""

import hashlib

#: The go-live gate criteria, verbatim from spec v2 §9.5 as amended by the phase-3 addendum
#: §0.7 (its four remaining deviations are folded into the numbered items below). The
#: numbering is the one §0.7 itself uses when it says "criteria 3, 6 and 7 read `order_clv`"
#: and "criterion 8, `staleness_median`".
#:
#: Ruling R1: these are invariants of the loop. Editing a threshold, a family or a definition
#: here changes `criteria_hash`, which is exactly the signal the weekly report prints -- so an
#: edit is a dated user decision and a pre-registration amendment, never a tidy-up.
CRITERIA_TEXT = """\
Go-live gate criteria (spec v2 section 9.5 as amended by the phase 3 addendum section 0.7).

Every t is cluster-robust by game with G - 1 degrees of freedom. Benchmark rows with
stale = true are excluded from every criterion and their share is printed. A game whose
kickoff moved more than 5 min after its first gap snapshot carries kickoff_moved = true and
is excluded from gate means. Every criterion counts the variant's own non-replay fills, with
each exec variant simulated as the sole participant.

1. fills_confirmed: at least 150 paper fills across at least 40 games and both sports,
   counted as fill events (orders with at least one queue_model fill), of which at least
   80 % on book_source = ws with dirty_minutes = 0. has_print is a sanity assertion, not
   the criterion.
2. marquee_share: at least 30 % of fills in NFL or marquee NCAAF (spread <= 4c).
3. clv_pinnacle_lower_bound: the lower bound of the 90 % cluster-robust CI on mean
   net-of-fee CLV versus pinnacle_t5 is greater than 0.
4. markout_30m: mean 30-minute markout net of maker fee greater than 0 with t > 2, read
   from the nw_fill anchor against the direct sharp consensus on fair_changed rows.
5. adverse_drift: adverse drift (fair at fill minus fair at place) greater than -1.0 pt.
6. filled_minus_unfilled: the 90 % cluster-robust upper bound of mean(unfilled CLV minus
   filled CLV) is below 1 pt, with at least 20 game clusters per side, else
   insufficient (fails).
7. clv_every_benchmark: mean CLV at or above 0 under every benchmark, excluding result and
   opening_first_seen, which are reported with CIs and never gated.
8. staleness_median: the median of fair_values.staleness_s over the variant's candidate
   signals is below 90 s.
9. mismatched_markets: zero orders whose market's venue_markets.match_key changed after
   placement.
10. settlement_mismatches: zero settlement mismatches, with at least 90 % of settled markets
    carrying the variant's fills having a source = venue row.
11. gate_report_stored: a stored passing gate report.
12. legal_decision: the user's separate, documented legal decision. False by construction in
    phase 3 code.
13. live_trading_env: LIVE_TRADING=1 in the container environment at start plus the config
    flag. False by construction in phase 3 code.
"""


def criteria_hash(text: str = CRITERIA_TEXT) -> str:
    """The sha256 of the gate definitions, printed by the report and stored on every gate row."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
