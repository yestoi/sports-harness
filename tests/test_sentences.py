"""Every sentence template, the three confidence bands, the reason vocabulary and the
glossary's coverage of it."""

import json

import pytest

from harness.dashboard import sentences as s


def test_the_three_confidence_bands():
    assert s.confidence_phrase(0) == "too few games to say anything"
    assert s.confidence_phrase(9) == "too few games to say anything"
    assert s.confidence_phrase(10) == "enough to notice, not enough to trust"
    assert s.confidence_phrase(29) == "enough to notice, not enough to trust"
    assert s.confidence_phrase(30) == "enough to take seriously"
    assert s.confidence_phrase(None) == "too few games to say anything"


def test_the_bands_are_the_reports_own_thresholds():
    from harness.report.tables import FLAG_CLUSTERS, GREY_CLUSTERS

    assert s.confidence_phrase(GREY_CLUSTERS - 1) == "too few games to say anything"
    assert s.confidence_phrase(GREY_CLUSTERS) == "enough to notice, not enough to trust"
    assert s.confidence_phrase(FLAG_CLUSTERS) == "enough to take seriously"


def test_a_greyed_cell_yields_a_sentence_with_no_estimate():
    """Spec §1.2: below 10 clusters no estimate is stated in the sentence, at all."""
    out = s.study_contrasts({"benchmark": "pinnacle_t5", "rows": [
        {"variant": "sharp_two_sided", "estimate": 0.031, "lo": -0.02, "hi": 0.08,
         "n_clusters": 4}]})
    text = " ".join(out)
    assert "too few games" in text
    assert "3.1" not in text and "0.031" not in text


def test_a_well_sampled_cell_states_the_estimate_and_the_honest_range():
    out = s.study_contrasts({"benchmark": "pinnacle_t5", "rows": [
        {"variant": "sharp_two_sided", "estimate": 0.031, "lo": -0.002, "hi": 0.064,
         "n_clusters": 44}]})
    text = " ".join(out)
    assert "3.1 %" in text and "honest range" in text and "enough to take seriously" in text


def test_every_reason_code_the_system_can_emit_has_a_phrase():
    """The executor's skip and cancel reasons and the strategy's rejection reasons, read from
    the code that emits them rather than restated here."""
    from harness.execution import plan
    from harness.strategy.run import ANNOTATION_LABELS, CAP_LABELS, FILTER_LABELS

    # Enumerated, never `dir(plan)`: that module also imports `YES` (= "yes") and
    # `ROUND_HALF_UP`, both upper-case strings, and a scan would demand a plain phrase for each.
    # These are the thirteen reason constants `harness/execution/plan.py` actually defines.
    executor = {getattr(plan, name) for name in (
        "KILL_SWITCH", "UNMATCHED", "FAIR_STALE", "VENUE_MOVE", "EDGE_DECAY", "SIGNAL_REJECTED",
        "REPRICE", "KICKOFF", "BOOK_DIRTY", "POST_ONLY_REJECT", "EXEC_CAPACITY", "NO_TARGET",
        "REJECTED")}
    # `expire` is written to `order_events.kind`, not held as a `plan` constant, so it joins here.
    codes = set(FILTER_LABELS) | set(CAP_LABELS) | set(ANNOTATION_LABELS) | executor | {"expire"}
    missing = sorted(c for c in codes if c not in s.REASON_PHRASES)
    assert missing == [], f"no reason_phrase for: {missing}"


def test_an_unknown_code_renders_as_itself_and_is_sanitized():
    assert s.reason_phrase("brand_new_reason") == "brand_new_reason"
    assert s.reason_phrase("<script>alert(1)</script>") == "scriptalert(1)/script"


def test_every_vocabulary_row_has_a_glossary_entry():
    glossary = s.load_glossary()
    for term in ("paper", "fair value", "staleness_s", "CLV", "pinnacle_t5", "consensus_close",
                 "kalshi_last_trade_pre_kick", "result", "markout", "intent", "skip",
                 "queue_ahead_at_place", "queue_remaining", "queue_model", "no_watcher",
                 "snapshot_cross", "has_print", "n_clusters", "honest range", "variant",
                 "gate variant", "primary", "kill switch", "order book", "tape",
                 "dashboard snapshot", "provisional", "gate criteria"):
        assert term in glossary, f"glossary.json has no entry for {term!r}"
        entry = glossary[term]
        assert set(entry) == {"term", "plain", "what", "why"}
        assert all(entry[k].strip() for k in entry)


def test_the_glossary_is_valid_json_and_carries_no_markup():
    raw = s.GLOSSARY_PATH.read_text()
    json.loads(raw)
    assert "<" not in raw and "http" not in raw


def test_pulse_status_names_every_fired_rule_with_its_value_and_threshold():
    out = s.pulse_status({"status": "WATCH", "rules": [
        {"name": "heartbeat_watch", "level": "watch", "value": 74.0, "threshold": 60.0,
         "unit": "s"}], "not_evaluated": []})
    text = " ".join(out)
    assert "WATCH" in text and "heartbeat_watch" in text and "74" in text and "60" in text


def test_pulse_status_says_so_when_a_rule_could_not_be_evaluated():
    out = s.pulse_status({"status": "FINE", "rules": [], "not_evaluated": [
        {"name": "disk_free", "level": "not_evaluated", "value": None, "threshold": 0.25,
         "unit": "fraction"}]})
    text = " ".join(out)
    assert "FINE" in text and "disk_free" in text and "not evaluated" in text


def test_a_reading_is_one_sentence_per_row():
    reading = s.gate_criterion_reading({"name": "clv_positive", "status": "insufficient",
                                        "value": None, "threshold": "> 0", "n": 6})
    # The three status strings are `harness.report.gate`'s own, not this module's.
    assert (s.gate_criterion_reading({"name": "x", "status": "passed", "value": 0.02,
                                      "threshold": "> 0", "n": 44}).count("meets") == 1)
    assert "does not meet" in s.gate_criterion_reading(
        {"name": "x", "status": "failed", "value": -0.02, "threshold": "> 0", "n": 44})
    assert reading.count(".") >= 1 and "\n" not in reading
    assert "too few games to say anything" in reading


def test_every_template_answers_an_empty_section_without_raising():
    """A surface with nothing to show still gets a sentence; no template may raise on an empty
    or partial section, because a builder section that failed hands it `{}`."""
    for fn in (s.pulse_status, s.pulse_tape, s.floor_board, s.floor_funnel, s.floor_orders,
               s.study_ledger, s.study_contrasts, s.study_equity, s.study_declined,
               s.gate_verdict, s.gate_criterion, s.ticket_card, s.ticket_between):
        out = fn({})
        assert isinstance(out, list) and all(isinstance(line, str) for line in out)


def test_the_formatters_are_the_ones_the_evidence_uses():
    assert s.fmt_prob(0.5612) == "56.1 %"
    assert s.fmt_money(1204.5) == "$1,204.50"
    assert s.fmt_int(1204) == "1,204"
    assert s.fmt_age(74) == "1 min"
    assert s.fmt_age(3) == "3 s"
    assert s.fmt_pct(0.62) == "62 %"
    assert s.fmt_prob(None) == "--" and s.fmt_money(None) == "--" and s.fmt_int(None) == "--"
