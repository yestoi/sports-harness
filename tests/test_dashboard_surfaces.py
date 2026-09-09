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


@pytest.mark.parametrize("name", ("pulse", "floor", "study", "gate", "ticket"))
def test_no_surface_composes_its_own_sentence(name):
    """Spec §1.2: sentences are written server-side by templates and rendered verbatim. A
    surface that built one in the browser could drift from the evidence under it."""
    body = (STATIC / "js" / f"{name}.mjs").read_text()
    assert "payload.sentences" in body
    assert "confidence_phrase" not in body and "honest range" not in body


def test_the_pulse_module_renders_every_section_of_its_payload():
    body = (STATIC / "js" / "pulse.mjs").read_text()
    for key in ("status", "tape", "vitals", "storage", "invariants", "operator_events",
                "snapshots", "build"):
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
