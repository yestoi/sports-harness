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
    rows = section.get("rows") or []
    week = section.get("week") or "this week"
    if not rows:
        return [f"No report has been stored for {week} yet."]
    lines = [f"{week}: {fmt_int(len(rows))} strategy variants were scored."]
    if section.get("provisional"):
        lines.append("This week is still being counted, so every number here is provisional"
                     f"; the cells were computed {fmt_age(section.get('cell_age_s'))} ago.")
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


def gate_criterion_reading(row: dict) -> str:
    """One criterion row as a sentence. `insufficient` is never dressed up as a near miss."""
    name = row.get("name", "?")
    if row.get("status") == INSUFFICIENT:
        return (f"{name}: not enough evidence to judge -- "
                f"{confidence_phrase(row.get('n'))}.")
    verdict = "meets" if row.get("status") == PASSED else "does not meet"
    return (f"{name}: measured {fmt_prob(row.get('value'))}, which {verdict} the stored "
            f"threshold {row.get('threshold', PLACEHOLDER)}, over {fmt_int(row.get('n'))} games.")


# --- Ticket ------------------------------------------------------------------------------------------

def ticket_card(section: dict) -> list[str]:
    legs = section.get("legs") or []
    if not legs:
        return ["This card has no legs recorded."]
    alive = [leg for leg in legs if leg.get("status") in ("pending", "alive")]
    missed = [leg for leg in legs if leg.get("status") == "miss"]
    if missed:
        return [f"Ouch. {missed[0].get('plain_text', 'A leg')} did not come in, so this one is "
                "done.", f"Staked {fmt_money(section.get('stake'))} of real fun money."]
    if len(alive) == 1:
        return [f"One leg from glory: {alive[0].get('plain_text', 'the last leg')}.",
                f"{fmt_money(section.get('payout'))} if it lands."]
    return [f"{fmt_int(len(alive))} legs still to come, for "
            f"{fmt_money(section.get('payout'))} on a {fmt_money(section.get('stake'))} stake.",
            "Real money, placed by hand at DraftKings. Nothing on this page is the research."]


def ticket_between(section: dict) -> list[str]:
    return [
        "No card is live right now.",
        f"The next one is built on {section.get('next_build_day', 'Friday')}, and it always "
        "carries an LSU or Saints leg.",
        f"{fmt_money(section.get('budget_left'))} of this week's $50 is still unspent.",
    ]
