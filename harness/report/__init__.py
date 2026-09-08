"""The weekly report (spec §7.2, addendum §4): ten fixed tables over one ISO week's rows.

The package is read-only over the database. Nothing here writes a row, bumps a version or
touches `harness/execution`, `harness/pricing` or `harness/settlement`; the report's only
outputs are a Markdown document and, when the controller asks for it, the week-38 selection
artefact. (`harness/report/gate.py` is the one exception and says so: it stores the gate rows
it evaluates.)

`CRITERIA_TEXT` and `criteria_hash` live here rather than in `harness/report/weekly.py`
because two modules need them: this report prints the hash every week, and `harness gate`
stores it on every `gate_reports` row. There is exactly one identity behind both. Task 11
made `harness/report/gate.py`'s `CRITERIA` tuple the source -- each criterion carries its own
definition text, the function that computes it and its threshold -- and this module renders
that tuple into `CRITERIA_TEXT` and delegates `criteria_hash()` to it. So the hash printed
beside a weekly report and the hash stored on a gate row are the same sha256 of the same
sorted definition strings, and neither can drift from the definitions that produced it.

Where Task 10's original wording of a criterion differed from the phase-3 brief's, the brief's
definition won and the rendering below follows it (controller ruling, Task 11).
"""

#: The preamble of the rendered criteria: the rules that bind every criterion rather than any
#: one of them (spec v2 §9.5 as amended by the phase-3 addendum §0.7).
#:
#: Ruling R1: these are invariants of the loop. Editing a threshold, a family or a definition
#: changes `criteria_hash`, which is exactly the signal the weekly report prints -- so an edit
#: is a dated user decision and a pre-registration amendment, never a tidy-up.
CRITERIA_PREAMBLE = """\
Go-live gate criteria (spec v2 section 9.5 as amended by the phase 3 addendum section 0.7).

Every t is cluster-robust by game with G - 1 degrees of freedom. Benchmark rows with
stale = true are excluded from every criterion and their share is printed. A game whose
kickoff moved more than 5 min after its first gap snapshot carries kickoff_moved = true and
is excluded from gate means. Every criterion counts the variant's own non-replay fills, with
each exec variant simulated as the sole participant.
"""


def render_criteria(criteria=None) -> str:
    """The criteria as text: the preamble, then each definition numbered in `CRITERIA` order.

    `harness.report.gate` is imported inside the function, not at module scope: `gate` imports
    `harness.report.stats`, and a top-level import here would make the package's `__init__`
    depend on its own submodule at import time.
    """
    if criteria is None:
        from harness.report.gate import CRITERIA as criteria
    lines = [CRITERIA_PREAMBLE]
    for i, criterion in enumerate(criteria, start=1):
        lines.append(f"{i}. {criterion.name}: {criterion.definition}.")
    return "\n".join(lines) + "\n"


def criteria_hash() -> str:
    """The sha256 of the gate definitions, printed by the report and stored on every gate row."""
    from harness.report.gate import criteria_hash as _criteria_hash

    return _criteria_hash()


def __getattr__(name: str):
    """`CRITERIA_TEXT` as a lazily rendered module attribute (PEP 562).

    Rendering it on access rather than at import keeps the `gate` import out of this module's
    body, and means a test that patches `gate.CRITERIA` sees the patched text here too.
    """
    if name == "CRITERIA_TEXT":
        return render_criteria()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
