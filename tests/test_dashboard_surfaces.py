"""The five surface modules: the glossary covers their labels, none writes markup, none
composes a sentence, and each renders the payload keys its builder produces."""

import json
import re
from pathlib import Path

import pytest

STATIC = Path(__file__).resolve().parents[1] / "harness" / "dashboard" / "static"
SURFACES = ("pulse", "floor", "study", "gate", "ticket", "how")


def _labels(name: str) -> list[str]:
    """The `technical` half of each LABELS entry, read out of the module's source: the test
    suite has no JavaScript runtime, so the declaration is parsed rather than executed."""
    body = (STATIC / "js" / f"{name}.mjs").read_text()
    block = body.split("export const LABELS", 1)[1].split("];", 1)[0]
    return re.findall(r"technical:\s*\"([^\"]+)\"", block)


@pytest.mark.parametrize("name", SURFACES)
def test_every_surface_exports_render_and_labels(name):
    body = (STATIC / "js" / f"{name}.mjs").read_text()
    assert "export function render(" in body
    assert "export const LABELS" in body


@pytest.mark.parametrize("name", SURFACES)
def test_every_technical_name_a_surface_shows_has_a_glossary_entry(name):
    glossary = json.loads((STATIC / "glossary.json").read_text())
    missing = [term for term in _labels(name) if term not in glossary]
    assert missing == [], f"{name}.mjs shows {missing} with no glossary entry"


@pytest.mark.parametrize("name", SURFACES)
def test_no_surface_writes_markup(name):
    body = (STATIC / "js" / f"{name}.mjs").read_text()
    for word in ("innerHTML", "outerHTML", "insertAdjacentHTML"):
        assert word not in body


def _without_labels(body: str) -> str:
    """`LABELS` is a declaration of vocabulary, not a composed sentence: the ban below is about
    the client building prose, and the label pair for the interval spells the very phrase
    (review-T18-notes.md C2)."""
    head, _, rest = body.partition("export const LABELS")
    return head + rest.partition("];")[2]


@pytest.mark.parametrize("name", ("pulse", "floor", "study", "gate", "ticket"))
def test_no_surface_composes_its_own_sentence(name):
    """Spec §1.2: sentences are written server-side by templates and rendered verbatim. A
    surface that built one in the browser could drift from the evidence under it."""
    body = (STATIC / "js" / f"{name}.mjs").read_text()
    assert "payload.sentences" in body
    assert "confidence_phrase" not in _without_labels(body)
    assert "honest range" not in _without_labels(body)


def test_the_pulse_module_renders_every_section_of_its_payload():
    body = (STATIC / "js" / "pulse.mjs").read_text()
    for key in ("status", "tape", "vitals", "storage", "invariants", "operator_events",
                "snapshots", "build", "research"):
        assert f"payload.{key}" in body


def test_the_pulse_module_shows_no_money():
    """Spec §2.1 never-shown, checked at the client too, since a payload key could be added
    later and the surface must not start drawing it."""
    body = (STATIC / "js" / "pulse.mjs").read_text().lower()
    for word in ("pnl", "clv", "equity", "profit"):
        assert word not in body


def test_the_floor_module_shows_no_quality_figure():
    body = (STATIC / "js" / "floor.mjs").read_text().lower()
    for word in ("clv", "markout"):
        assert word not in body


def test_the_study_module_inserts_the_markdown_as_text_beside_its_hash():
    """Ruling A-I8 / B-I3 / D11: no renderer is vendored, the document goes into a <pre> as
    textContent, and its sha256 is shown beside it."""
    body = (STATIC / "js" / "study.mjs").read_text()
    assert "markdown_sha256" in body
    assert "createElement(\"pre\")" in body or 'el("pre"' in body
    assert "textContent" in body or "text(" in body


def test_the_ticket_module_shows_no_paper_figure():
    body = (STATIC / "js" / "ticket.mjs").read_text().lower()
    for word in ("paper", "variant", "clv"):
        assert word not in body


def test_the_how_page_covers_the_eight_pipeline_steps_and_the_three_jobs():
    body = (STATIC / "how.html").read_text()
    for step in ("record", "match", "price", "decide", "paper order",
                 "fill from the recorded tape", "settle", "judge"):
        assert step in body
    for job in ("is it alive and honest", "what is it doing right now",
                "is the strategy any good"):
        assert job in body
    assert "never mix" in body     # the fun money and the research


def test_no_surface_renders_a_control():
    for name in SURFACES:
        body = (STATIC / "js" / f"{name}.mjs").read_text()
        assert "<form" not in body and 'el("form"' not in body
        assert "/kill" not in body and "/unkill" not in body
        assert 'method: "POST"' not in body and "fetch(\"/kill" not in body


# --- final-review-B fix wave: source-level checks the runtime-free suite can still make ---------

def test_the_shell_reads_the_envelope_error_and_names_a_failed_build():
    """B-C1: a crash-looping builder writes a fresh `generated_at` over a stale payload, so the
    age-based staleness flag can never catch it -- the failure is visible only in the
    envelope's own `error` field, and this is the one place that must read it."""
    body = (STATIC / "js" / "app.mjs").read_text()
    assert "body.error" in body
    assert "shell-stale" in body


def test_floors_venue_tripwire_reads_ok_only_on_an_explicit_true():
    """B-C2: `data.tripwire_ok !== false` reads `true` from an absent or failed section
    (`undefined !== false`); a production-order tripwire must never do that."""
    body = (STATIC / "js" / "floor.mjs").read_text()
    assert "tripwire_ok === true" in body
    assert "tripwire_ok !== false" not in body


def test_pulses_vitals_sparklines_are_keyed_by_the_tiles_own_metric_field():
    """B-C3: the sparkline map is keyed by `metric_samples.name`, which `tile.technical` (the
    glossary key) never carries -- the builder now writes that name separately as
    `tile.metric`, and this is the field the sparkline lookup must key on."""
    body = (STATIC / "js" / "pulse.mjs").read_text()
    assert "vitals.sparklines || {})[tile.metric]" in body
    assert "vitals.sparklines || {})[tile.technical]" not in body


def test_floor_marks_every_guarded_section_as_unavailable_when_it_failed():
    """B-I1: `section()` collapses a guarded `{"error": ...}` read to the same `null` as
    "nothing to show yet"; every card must tell the two apart with its own `sectionFailed`
    check, mirroring `pulse.mjs` and `ticket.mjs`."""
    body = (STATIC / "js" / "floor.mjs").read_text()
    assert "const sectionFailed" in body
    for key in ("board", "funnel", "orders", "fills", "exposure", "vitals", "venue"):
        assert f"sectionFailed(payload.{key})" in body, f"floor.mjs never guards {key}"


def test_pulses_storage_card_never_draws_an_arc_it_did_not_earn():
    """B-I2: an unmeasured or failed storage read must render "not evaluated" rather than
    `storageArc` clamping an undefined share to a 0 % bar."""
    body = (STATIC / "js" / "pulse.mjs").read_text()
    assert "measured === false" in body
    assert "not evaluated" in body


def test_study_labels_the_snapshot_build_time_apart_from_the_cell_time():
    """Addendum 0.5 and §3's deterministic stand-in for the Chrome walker: the two label
    strings the verify row greps for in the served module have to be in the module."""
    body = (STATIC / "js" / "study.mjs").read_text()
    assert "snapshot built" in body
    assert "report cells from" in body
    # Both halves come from the payload, not from the browser's own clock.
    assert "payload.now" in body and "payload.generated_at" in body and "payload.cell_age_s" in body


def test_pulse_shows_the_study_cell_age_in_its_ages_panel():
    body = (STATIC / "js" / "pulse.mjs").read_text()
    assert "cell_age_s" in body
    assert "cells from" in body
