"""The plain layer: one pure template per surface section, and one reading per row.

Spec §1.2 is the brief. Every section of every surface opens with one to three plain-English
sentences, written here in the builder and rendered verbatim by the front end, which never
composes its own. That is what makes the plain layer deterministic, unit-testable and unable to
drift from the evidence beneath it -- and it is why the numbers in a sentence come from the same
formatters the evidence uses, so a sentence can never round differently from the table under it.

Three rules bind every template:

* **Uncertainty is words.** `confidence_phrase` is bound to the report's own cluster thresholds
  (`GREY_CLUSTERS`, `FLAG_CLUSTERS`), imported rather than restated. Below 10 game clusters the
  sentence states *no estimate at all*: "too few games to say anything" and nothing else.
* **No template raises.** A builder section that failed hands its template `{}`, and a surface
  that loses its sentence because a key was missing has lost the layer that explains it.
* **Outside text is sanitized on the way in, not here.** These functions receive payload values
  the builder has already passed through `harness.telemetry.sanitize_reason`; the one exception
  is `reason_phrase`, which sanitizes an unrecognised code itself because that is the one
  deliberate passthrough of an unknown string (ruling B-(e), item 9).
"""

import json
from decimal import Decimal
from pathlib import Path

from harness.report.gate import FAILED, INSUFFICIENT, PASSED
from harness.report.tables import FLAG_CLUSTERS, GREY_CLUSTERS
from harness.telemetry import sanitize_reason

#: What an absent number prints, in a sentence and in a cell alike.
PLACEHOLDER = "--"

GLOSSARY_PATH = Path(__file__).resolve().parent / "static" / "glossary.json"


def load_glossary() -> dict[str, dict]:
    """The static glossary the front end also ships. Read here so a test can assert that every
    term a surface renders has an entry."""
    return json.loads(GLOSSARY_PATH.read_text())


# --- formatters ------------------------------------------------------------------------------
# Shared with the evidence: a sentence and the table under it are formatted by the same code, so
# "56.1 %" in the sentence is the "56.1 %" in the cell.

def fmt_prob(value) -> str:
    """A probability or a probability difference, as a percentage at one decimal."""
    return PLACEHOLDER if value is None else f"{float(value) * 100:.1f} %"


def fmt_pct(value) -> str:
    """A share, as a whole percentage."""
    return PLACEHOLDER if value is None else f"{round(float(value) * 100)} %"


def fmt_money(value) -> str:
    return PLACEHOLDER if value is None else f"${float(value):,.2f}"


def fmt_int(value) -> str:
    return PLACEHOLDER if value is None else f"{int(value):,}"


def fmt_age(seconds) -> str:
    """An age in the largest unit that keeps it readable, never more precise than it is."""
    if seconds is None:
        return PLACEHOLDER
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min"
    if seconds < 86400:
        return f"{seconds // 3600} h"
    return f"{seconds // 86400} d"


# --- uncertainty in words --------------------------------------------------------------------

def confidence_phrase(n_clusters) -> str:
    """Spec §1.2's three bands, bound to the report's own flags. Clusters count games, not bets:
    bets in the same game rise and fall together."""
    if n_clusters is None or int(n_clusters) < GREY_CLUSTERS:
        return "too few games to say anything"
    if int(n_clusters) < FLAG_CLUSTERS:
        return "enough to notice, not enough to trust"
    return "enough to take seriously"


def _estimate_clause(estimate, lo, hi, n_clusters) -> str:
    """The one place an estimate is allowed into a sentence, and only above the grey line."""
    if n_clusters is None or int(n_clusters) < GREY_CLUSTERS:
        return confidence_phrase(n_clusters)
    return (f"{fmt_prob(estimate)}, honest range {fmt_prob(lo)} to {fmt_prob(hi)}, "
            f"over {fmt_int(n_clusters)} games -- {confidence_phrase(n_clusters)}")


# --- reason codes in plain words --------------------------------------------------------------
# Spec §1.2's vocabulary table as a dict. A code outside it renders as itself (sanitized) and the
# builder writes it into the payload's `sentences_gaps`, so the next plan sees it.

REASON_PHRASES: dict[str, str] = {
    # skip reasons the executor writes to order_events.reason
    "post_only_reject": "the price we wanted was already gone",
    "kickoff": "too close to kickoff",
    "book_dirty": "the order book could not be trusted",
    "fair_stale": "our fair price was too old",
    "exec_capacity": "already at the open-order limit",
    "unmatched": "could not tell which game this market was",
    "no_target": "there was no price worth bidding",
    "no_book": "we had never seen this market's order book",
    "kill_switch": "the stop button was on",
    # cancel reasons
    "venue_move": "the market moved against us",
    "reprice": "moved our order to a new price",
    "edge_decay": "the edge shrank",
    "signal_rejected": "the signal went away",
    "expire": "kickoff arrived",
    "rejected": "the venue turned the order down",
    # the strategy's rejection reasons, which are its label names
    "has_fair": "we had no fair price for this market",
    "source_allowed": "this variant does not trade prices from that source",
    "sport_allowed": "this variant does not trade that sport",
    "match_confidence": "we were not confident which game this market was",
    "not_stale": "our fair price was too old",
    "price_band": "the price was outside the band this variant trades",
    "ttk": "it was the wrong distance from kickoff for this variant",
    "spread": "the bid-ask spread was too wide",
    "volume": "the market had traded too little",
    "velocity": "the price was moving too fast to trust",
    "disagreement_ok": "the sharp books disagreed too much with each other",
    "edge": "not enough edge",
    "min_contracts": "the bet would have been too small to place",
    "cap_per_bet": "hit a bankroll cap",
    "cap_per_game": "hit a bankroll cap",
    "cap_daily": "hit a bankroll cap",
    "max_open": "already at the open-order limit",
    "drawdown_stop": "this variant is in a drawdown stop",
}


def reason_phrase(code: str) -> str:
    """The plain phrase for a reason code, or the code itself when it is not in the table.

    The passthrough is sanitized: reason codes come from `order_events.reason`, which our own
    code writes, but this is the one place an unrecognised string is deliberately shown, and a
    guard two modules away is not where that safety should live."""
    if not code:
        return "no reason recorded"
    return REASON_PHRASES.get(code) or sanitize_reason(code)


def unknown_reason_codes(codes) -> list[str]:
    """The subset of `codes` outside `REASON_PHRASES`, sorted, deduplicated and sanitized --
    what a builder writes into `payload["sentences_gaps"]` so the next plan sees the vocabulary's
    blind spots (ruling A-I2, spec §1.2, ruling B-(e) item 9). Falsy codes are dropped: an empty
    or missing reason already renders as "no reason recorded" and is not a gap in the vocabulary."""
    return sorted({sanitize_reason(code) for code in codes
                   if code and code not in REASON_PHRASES})


# --- Pulse -------------------------------------------------------------------------------------

def pulse_status(section: dict) -> list[str]:
    status = section.get("status") or "FINE"
    rules = section.get("rules") or []
    unevaluated = section.get("not_evaluated") or []
    if status == "FINE" and not rules:
        lines = [f"Right now the machine reads {status}: everything it checks is fine."]
    else:
        lines = [f"Right now the machine reads {status}, on {fmt_int(len(rules))} "
                 f"rule{'' if len(rules) == 1 else 's'}."]
        for rule in rules[:4]:
            lines.append(pulse_rule_reading(rule))
    if unevaluated:
        names = ", ".join(r.get("name", "?") for r in unevaluated[:4])
        lines.append(f"So far not evaluated, and not counted either way: {names}.")
    return lines


def pulse_rule_reading(rule: dict) -> str:
    """One fired (or unevaluated) rule as a sentence: what it measures, what it read, and the
    threshold it is read against. The threshold is the builder's, imported from the code that
    enforces it -- never a number written here."""
    name = rule.get("name", "?")
    unit = rule.get("unit") or ""
    value, threshold = rule.get("value"), rule.get("threshold")
    if rule.get("level") == "not_evaluated" or value is None:
        return f"{name}: not evaluated, because the value it needs has not been recorded yet."
    # Seconds are shown exactly, not rounded into `fmt_age`'s minutes/hours: a 74 s reading
    # against a 60 s threshold both become "1 min" under `fmt_age`, which erases the very gap
    # the rule fired on.
    shown = {"s": lambda v: f"{fmt_int(v)} s", "fraction": fmt_pct,
             "gb": lambda v: f"{float(v):,.1f} GB", "count": fmt_int}.get(unit, fmt_int)
    limit = shown(threshold) if threshold is not None else PLACEHOLDER
    return f"{name}: {shown(value)} against a threshold of {limit}."


def pulse_tape(section: dict) -> list[str]:
    sources = section.get("sources") or []
    if not sources:
        return ["No tape activity has been recorded in the last 24 hours."]
    gappy = [src for src in sources if (src.get("gaps") or 0) > 0]
    quiet = [src for src in sources if not any(src.get("buckets") or [])]
    lines = [f"Over the last 24 hours {fmt_int(len(sources))} feeds were recording."]
    if gappy:
        names = ", ".join(src.get("source", "?") for src in gappy)
        lines.append(f"Gaps in the recorded stream: {names}. A gap is a stretch we did not see.")
    if quiet:
        names = ", ".join(src.get("source", "?") for src in quiet)
        lines.append(f"Silent for the whole window: {names}.")
    return lines


def research_reading(section: dict) -> str:
    """The spend tile in one sentence. Money is printed with its cap beside it, never alone: a
    figure with no ceiling next to it is not a reading."""
    if section.get("day_usd") is None:
        return "Research spend: not evaluated, because nothing has been recorded yet."
    dormant = " The worker is dormant until tomorrow." if section.get("dormant") else ""
    return (f"Research spend today: ${section['day_usd']:.2f} of ${section['daily_cap']:.2f}, "
            f"with ${section['day_reserved']:.2f} reserved. This week: "
            f"${section['week_usd']:.2f} of ${section['weekly_cap']:.2f}.{dormant}")


def veto_reading(section: dict) -> str:
    if section.get("veto_rate") is None:
        return "Veto rate: not evaluated, because no signal has been decided in 24 hours."
    return (f"The veto reduced or vetoed {fmt_pct(section['veto_rate'])} of "
            f"{fmt_int(section['decided_24h'])} decided signals in 24 hours. "
            "Decisions are post-hoc and change nothing that was placed.")


# --- Floor ---------------------------------------------------------------------------------------

def floor_board(section: dict) -> list[str]:
    games = section.get("games") or []
    if not games:
        return ["No game kicks off in the next 24 hours and none is in progress."]
    live = [g for g in games if g.get("status") == "in_progress"]
    orders = sum(g.get("open_orders") or 0 for g in games)
    lines = [f"{fmt_int(len(games))} games on the board, {fmt_int(len(live))} in progress."]
    lines.append(f"We have {fmt_int(orders)} simulated orders resting on them."
                 if orders else "We have no simulated orders resting on them.")
    return lines


def floor_funnel(section: dict) -> list[str]:
    if not section:
        return ["Nothing has come through the pipeline in the last 6 hours."]
    hours = section.get("window_h") or 6
    lines = [f"Over the last {fmt_int(hours)} hours: {fmt_int(section.get('ticks'))} price "
             f"checks became {fmt_int(section.get('candidates'))} wanted bets, "
             f"{fmt_int(section.get('orders'))} simulated orders and "
             f"{fmt_int(section.get('fills'))} fills."]
    leaks = (section.get("skipped") or []) + (section.get("cancelled") or [])
    top = sorted(leaks, key=lambda r: r.get("count") or 0, reverse=True)[:2]
    for row in top:
        lines.append(f"{fmt_int(row.get('count'))} were lost because "
                     f"{reason_phrase(row.get('reason'))}.")
    return lines


def floor_orders(section: dict) -> list[str]:
    orders = section.get("orders") or []
    if not orders:
        return ["No simulated order is resting right now."]
    front = [o for o in orders if (o.get("queue_remaining") or 0) == 0]
    lines = [f"{fmt_int(len(orders))} simulated orders are resting, none of them real money."]
    if front:
        lines.append(f"{fmt_int(len(front))} are at the front of the queue: the next trade at "
                     "that price would fill them.")
    return lines


# --- Study -----------------------------------------------------------------------------------------

def study_ledger(section: dict) -> list[str]:
    """Addendum 0.5: the snapshot's build time and the age of the report cells under it are two
    different numbers, and this layer states the second one on every week rather than only on a
    provisional one. It never calls a cell age "fresh": a cell's age is a fact about when the
    report ran, and "fresh" is a judgement the reader makes, not a word the surface supplies.
    """
    rows = section.get("rows") or []
    week = section.get("week") or "this week"
    if not rows:
        return [f"No report has been stored for {week} yet."]
    lines = [f"{week}: {fmt_int(len(rows))} strategy variants were scored."]
    if section.get("provisional"):
        lines.append("This week is still being counted, so every number here is provisional.")
    if section.get("cell_age_s") is not None:
        lines.append("These numbers come from the report run, not from this page: the cells "
                     f"were computed {fmt_age(section.get('cell_age_s'))} ago.")
    best = max(rows, key=lambda r: (r.get("n_clusters") or 0))
    lines.append(f"The best-sampled row is {best.get('row_key', '?')}: "
                 f"{confidence_phrase(best.get('n_clusters'))}.")
    return lines


def study_contrasts(section: dict) -> list[str]:
    rows = section.get("rows") or []
    benchmark = section.get("benchmark") or "the benchmark"
    if not rows:
        return ["No variant has enough scored bets this week to compare against the primary."]
    lines = [f"Each variant against the pre-registered original, measured on {benchmark}."]
    for row in rows[:3]:
        lines.append(f"{row.get('variant', '?')}: "
                     f"{_estimate_clause(row.get('estimate'), row.get('lo'), row.get('hi'), row.get('n_clusters'))}.")
    return lines


def study_equity(section: dict) -> list[str]:
    variants = section.get("variants") or []
    if not variants:
        return ["No equity was recorded for this week."]
    lines = ["Simulated cash per variant, and the same cash plus open positions valued at the "
             "current market."]
    for row in variants[:3]:
        coverage = row.get("min_coverage")
        marked = ("the marked line is drawn grey, because under half the open contracts had a "
                  "clean order book to value them against"
                  if coverage is not None and float(coverage) < 0.5
                  else f"open positions were valued against a clean book "
                       f"{fmt_pct(coverage)} of the time")
        lines.append(f"{row.get('variant', '?')}: {fmt_money(row.get('cash'))} of simulated "
                     f"cash; {marked}.")
    return lines


def study_declined(section: dict) -> list[str]:
    rows = section.get("rows") or []
    if not rows:
        return ["Nothing was turned down this week."]
    total = sum(r.get("count") or 0 for r in rows)
    lines = [f"{fmt_int(total)} chances were turned down this week. "
             "What they would have been worth is counted below, but never gated on."]
    for row in rows[:3]:
        lines.append(study_declined_reading(row))
    return lines


def study_declined_reading(row: dict) -> str:
    """One declined-reason row as a sentence, naming its estimator: rejected signals are scored
    from the market's own outcome at the variant's price target, skipped intents from the gap
    snapshot the intent was made on. Two estimators under one heading is exactly what the
    two-level-label rule exists to prevent (ruling B-I5)."""
    estimator = {"clv_rejected_gap_outcomes": "scored from the market's outcome at this "
                                              "variant's own target price",
                 "clv_skipped_intent_snapshot": "scored from the price snapshot the intent was "
                                                "made on"}.get(row.get("estimator"), "unscored")
    body = _estimate_clause(row.get("estimate"), row.get("lo"), row.get("hi"),
                            row.get("n_clusters"))
    return (f"{row.get('variant', '?')}, {reason_phrase(row.get('reason'))}: "
            f"{fmt_int(row.get('count'))} times ({fmt_pct(row.get('share'))}), {estimator} -- "
            f"{body}.")


# --- Gate ------------------------------------------------------------------------------------------

def gate_verdict(section: dict) -> list[str]:
    if not section:
        return ["No gate evaluation has been stored yet."]
    word = "PASSING" if section.get("passing") else "NOT PASSING"
    lines = [f"The go-live gate reads {word} for {section.get('variant', 'the gate variant')}, "
             f"as of the evaluation stored on {section.get('evaluated_at', 'an unknown date')}.",
             f"{fmt_int(section.get('n_pass'))} of {fmt_int(section.get('n_total'))} criteria "
             "are met.",
             "This is paper only. Trading real money is a separate legal decision, and nothing "
             "here makes it."]
    return lines


def gate_criterion(section: dict) -> list[str]:
    rows = section.get("criteria") or []
    if not rows:
        return ["No criteria are stored for this evaluation."]
    # The vocabulary is `harness.report.gate`'s, imported rather than restated: `evaluate_all`
    # stores `PASSED, FAILED, INSUFFICIENT = "passed", "failed", "insufficient"`, not
    # `pass`/`fail`, and a surface that spelled them itself would silently never match.
    failing = [r for r in rows if r.get("status") == FAILED]
    thin = [r for r in rows if r.get("status") == INSUFFICIENT]
    lines = [f"{fmt_int(len(rows))} criteria, each shown with the definition and threshold "
             "that were stored when it was evaluated."]
    if failing:
        lines.append("Not met: " + ", ".join(r.get("name", "?") for r in failing[:4]) + ".")
    if thin:
        lines.append("Not enough evidence yet: " + ", ".join(r.get("name", "?")
                                                             for r in thin[:4]) + ".")
    return lines


def _fmt_seconds(value) -> str:
    """A whole number of seconds, for a criterion measured as an age (`staleness_median`)."""
    return PLACEHOLDER if value is None else f"{float(value):.0f} s"


def _fmt_as_stored(value) -> str:
    """No conversion at all -- the criterion's own stored value already reads as words or a
    flag (`legal_decision`, `live_trading_env`), not a number in any unit this module knows."""
    return PLACEHOLDER if value is None else str(value)


#: Fix 54: `gate_criterion_reading` used to format *every* criterion's value with `fmt_prob` (a
#: fraction shown as a percentage), so `staleness_median` (an age in seconds) read "measured
#: 5900.0 %" for 59 s and `fill_events` (a plain count) read "measured 0.0 %" for zero fill
#: events. Each criterion is measured in its own unit, and this table names it -- keyed by
#: `harness.report.gate.CRITERIA`'s own names, so a rename there is the one place this table
#: would need to follow. A name not in the table keeps `fmt_prob` (the conservative fallback:
#: no criterion this brief covers changes sentence, and a brand-new criterion added later reads
#: as a probability difference, the commonest shape, until this table is updated for it).
_CRITERION_UNITS = {
    "fill_events": fmt_int,
    "mismatched_markets": fmt_int,
    "staleness_median": _fmt_seconds,
    "settlement": fmt_pct,
    "marquee_share": fmt_pct,
    "clv_pinnacle_lb": fmt_prob,
    "clv_every_benchmark": fmt_prob,
    "markout_30m": fmt_prob,
    "adverse_drift": fmt_prob,
    "filled_vs_unfilled": fmt_prob,
    "legal_decision": _fmt_as_stored,
    "live_trading_env": _fmt_as_stored,
}


def _safe_fmt(fmt, value) -> str:
    """`fmt` over a value that turns out not to be the unit's own type (a test fixture's
    illustrative comparison string such as `"> 0"` in place of a real stored number, say)
    prints as stored rather than raising: a criterion's sentence must never crash the whole
    Gate card over one badly-typed value, and the tables this module reads are graded
    evidence, not something this rendering layer can refuse to show."""
    if value is None:
        return PLACEHOLDER
    try:
        return fmt(value)
    except (TypeError, ValueError):
        return str(value)


def gate_criterion_reading(row: dict) -> str:
    """One criterion row as a sentence. `insufficient` is never dressed up as a near miss.
    Fix 54: a criterion in `_CRITERION_UNITS` has its value *and* its threshold formatted in
    that criterion's own unit, so the sentence never mixes a seconds value with an unformatted
    threshold or the reverse. A criterion not in the table keeps today's exact sentence shape
    (`fmt_prob` on the value, the threshold printed as stored, unformatted) -- the conservative
    fallback the table's own comment describes."""
    name = row.get("name", "?")
    if row.get("status") == INSUFFICIENT:
        return (f"{name}: not enough evidence to judge -- "
                f"{confidence_phrase(row.get('n'))}.")
    verdict = "meets" if row.get("status") == PASSED else "does not meet"
    fmt = _CRITERION_UNITS.get(name)
    if fmt is None:
        return (f"{name}: measured {fmt_prob(row.get('value'))}, which {verdict} the stored "
                f"threshold {row.get('threshold', PLACEHOLDER)}, over {fmt_int(row.get('n'))} "
                f"games.")
    return (f"{name}: measured {_safe_fmt(fmt, row.get('value'))}, which {verdict} the stored "
            f"threshold {_safe_fmt(fmt, row.get('threshold'))}, over "
            f"{fmt_int(row.get('n'))} games.")


# --- Ticket ------------------------------------------------------------------------------------------

#: The fixed vocabulary of design §2.2 (addendum §1.2): one sentence per reason a slot has no
#: draft, plus the ninth the addendum adds. A code outside this table renders as itself
#: (sanitized) and the builder writes it into `sentences_gaps`, exactly as `REASON_PHRASES`
#: above -- a fan reading a bare code is a gap in this vocabulary, and the next plan is meant to
#: see it rather than a blank line.
IDEA_PHRASES: dict[str, str] = {
    "no_anchor_priced": "No LSU or Saints price is fresh enough to build on.",
    "anchor_bye": "LSU and the Saints are both off this week.",
    "no_props_fresh": "No prop price inside the age limit.",
    "player_unmatched": "A prop we wanted names a player we cannot match to the stat feed.",
    "market_unsupported": "The market DraftKings offers is not one we grade.",
    "week_at_cap": "This week's $50 is fully recorded.",
    "not_built_yet": "The next card is built Saturday evening.",
    "builder_failed": "The builder could not finish; the reason is in the log.",
    "replacement_pending": "A replacement is being built; it lands within the hour.",
}


def idea_reason_phrase(code: str) -> str:
    """The plain sentence for one empty slot's reason code, or the code itself when the
    vocabulary has none for it.

    The passthrough is sanitized for `reason_phrase`'s reason: these codes are written by
    `harness/settlement/parlay_build.py`, which is our own code, but this is a place an
    unrecognised string is deliberately shown and the guard belongs here rather than two modules
    away.
    """
    if not code:
        return "no reason recorded"
    return IDEA_PHRASES.get(code) or sanitize_reason(code)


def unknown_idea_codes(codes) -> list[str]:
    """The subset of `codes` outside `IDEA_PHRASES`, sorted, deduplicated and sanitized -- what
    the Ticket builder writes into `payload["sentences_gaps"]`. `unknown_reason_codes`'s twin,
    kept separate because the two vocabularies are different tables: a code that is a gap here
    may be perfectly well spoken there."""
    return sorted({sanitize_reason(code) for code in codes
                   if code and code not in IDEA_PHRASES})


#: The fan's noun for each release-one stat. The grader speaks `pass_yds`; the slip says
#: "passing yards". One mapping, in the module that owns every other sentence, so the Ticket
#: builder never spells a stat name into prose itself.
_STAT_NOUNS = {"pass_yds": "passing yards", "rush_yds": "rushing yards",
               "rec_yds": "receiving yards", "receptions": "receptions",
               "anytime_td": "touchdown"}

#: Release one has exactly one player-stat source (addendum §4.1): ESPN's summary, recorded into
#: `player_stat_events` with `source = 'espn'`. The stat line and the correction note both name
#: it, and `stat_line`'s signature carries no source argument, so the display name lives here.
#: A second source is a signature change and a table in its place, not a string spelled at a
#: call site.
STAT_SOURCE = "ESPN"


def stat_noun(stat: str) -> str:
    """The fan's noun for a stat key, or the key itself when the table has none (the builder
    reports that in `sentences_gaps`, never a blank)."""
    return _STAT_NOUNS.get(stat) or sanitize_reason(stat or "")


def unknown_stat_keys(stats) -> list[str]:
    """The subset of `stats` outside `_STAT_NOUNS`, sorted, deduplicated and sanitized. A stat
    the grader writes and this module has no noun for renders its own key on the slip, and the
    builder reports it here rather than printing a blank where a yardage belongs."""
    return sorted({sanitize_reason(stat) for stat in stats
                   if stat and stat not in _STAT_NOUNS})


def fmt_stat(value) -> str:
    """A stat figure as the fan reads it: 208, not 208.00, and 10.5 kept when a line is a half.
    `Decimal` in, string out, so no float ever rounds a yardage differently from the grader."""
    if value is None:
        return PLACEHOLDER
    number = Decimal(str(value))
    if number == number.to_integral_value():
        return str(int(number))
    return str(number.normalize())


def stat_line(stat: str, value, line, needs_phrase: str, age_s: float) -> str:
    """`208 of 225 passing yards \u00b7 17 to go \u00b7 ESPN 40 s ago` (design §2.3).

    `needs_phrase` is `harness.parlay.needs.needs`'s output, unchanged: the two halves of the
    line come from the one function that decides what a leg still needs and from the nouns
    here, so the sentence and the grade can never disagree. An unknown stat renders its own key
    rather than a blank, and the builder reports it in `sentences_gaps`.

    A leg with no line at all -- `anytime_td`, whose market is "did it happen" -- has no
    "208 of 225" half to state, so the sentence opens with what the leg still needs.
    """
    source = f"{STAT_SOURCE} {fmt_age(age_s)} ago"
    if line is None:
        return f"{needs_phrase} \u00b7 {source}"
    return (f"{fmt_stat(value)} of {fmt_stat(line)} {stat_noun(stat)} \u00b7 "
            f"{needs_phrase} \u00b7 {source}")


def stat_unchanged(age_s: float) -> str:
    """A player absent from the latest stat update (design §2.3). Never a zero: the newest
    recorded value stands and the line says how old it is, because a player who has not touched
    the ball has no new row, and printing 0 there would be the surface inventing a fact."""
    return f"unchanged \u00b7 last seen {fmt_age(age_s)} ago"


def stat_pending_final(age_s: float) -> str:
    """A hung leg (addendum §1.3): the game is final and the stat never arrived. The age is the
    game's, measured from the final whistle we recorded."""
    return f"no final stat \u00b7 pending {fmt_age(age_s)}"


def stat_correction_note(previous, value) -> str:
    """`corrected from 12 to 8 \u00b7 ESPN` (design §2.3), when the newest row carries
    `correction = true`. No animation and no lamp replay: the number simply reads as corrected,
    with the source that corrected it."""
    return (f"corrected from {fmt_stat(previous)} to {fmt_stat(value)} \u00b7 "
            f"{STAT_SOURCE}")


def ticket_card(section: dict) -> list[str]:
    """The card's one to three sentences, plus the settlement sentence when there is one.

    Phase 4.6 adds the last line only: the three existing sentences are unchanged, word for
    word, because they are what the shipped surface renders today. The settlement sentence is
    the builder's own `settlement["text"]` (addendum §1.3) -- written once, in the builder, so
    the stamp, the tile and the sentence can never disagree about whether a return was computed
    by the harness or confirmed by the owner.
    """
    legs = section.get("legs") or []
    settlement = section.get("settlement") or {}
    tail = [settlement["text"]] if settlement.get("text") else []
    if not legs:
        return ["This card has no legs recorded."] + tail
    alive = [leg for leg in legs if leg.get("status") in ("pending", "alive")]
    missed = [leg for leg in legs if leg.get("status") == "miss"]
    if missed:
        return [f"Ouch. {missed[0].get('plain_text', 'A leg')} did not come in, so this one is "
                "done.", f"Staked {fmt_money(section.get('stake'))} of real fun money."] + tail
    if len(alive) == 1:
        return [f"One leg from glory: {alive[0].get('plain_text', 'the last leg')}.",
                f"{fmt_money(section.get('payout'))} if it lands."] + tail
    return [f"{fmt_int(len(alive))} legs still to come, for "
            f"{fmt_money(section.get('payout'))} on a {fmt_money(section.get('stake'))} stake.",
            "Real money, placed by hand at DraftKings. Nothing on this page is the research."
            ] + tail


def ticket_between(section: dict) -> list[str]:
    return [
        "No card is live right now.",
        f"The next one is built on {section.get('next_build_day', 'Friday')}, and it always "
        "carries an LSU or Saints leg.",
        f"{fmt_money(section.get('budget_left'))} of this week's $50 is still unspent.",
    ]
