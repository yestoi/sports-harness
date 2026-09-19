"""§1.11: the milestone's completion evidence and its dated recommendation.

This module renders a report. It **decides nothing**: production adoption of a holding policy
is §0.14a's dated decision and the pacing profile's activation is §0.14c's, both the user's, and
both questions are carried here verbatim and unanswered.

Four rules it exists to keep:

* **It refuses rather than recommends.** `render` raises `MissingEvidence` while any of
  `EVIDENCE_SECTIONS` is empty, before it emits a recommendation. A recommendation printed over
  a hole in the evidence is worse than no report.
* **An explicitly unavailable arm is a complete input (§9).** Arm C reported unavailable *with
  its reason* is evidence; arm C silently absent is a hole. Nothing invents a faster-history
  observation to fill one.
* **A negative finding closes the milestone (§1.11).** Positive returns are not a completion
  requirement, and neither is a passing go-live gate: an infeasible or economically unproductive
  maker configuration closes 6D.1 with a supported negative finding.
* **The dormant veto profile closes the milestone's veto component (I11, D16).** U10 asked for
  the pacing to be implemented and verified inside the unchanged caps; the preflight against
  stored arrivals and the prepared §0.8 amendment record are that verification. The boundary
  instant is written at the user's activation, which changes H9's decided population and is not
  this report's to take.

6D.1 is marked done only when this evidence exists - never when its CLI and its tests are
finished.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from harness.experiments.execution_viability import EXP_LABEL, veto_profile

#: §1.11's sections, in the order §1.11 states them and the order they are printed in.
EVIDENCE_SECTIONS: tuple[str, ...] = ("baseline_proof", "arm_results", "book_health",
                                      "veto_pacing", "forecast", "recommendation")

#: U10's four conclusions. Nothing outside this tuple may be printed as a recommendation.
RECOMMENDATIONS: tuple[str, ...] = ("retain", "revise", "stop", "insufficient evidence")

#: The heading each section is printed under; the numbering is §1.11's order.
SECTION_TITLES: dict[str, str] = {
    "baseline_proof": "## 1. Stateful baseline behaviour and isolation, proven",
    "arm_results": "## 2. Comparative paper results for the feasible arms",
    "book_health": "## 3. Cause-specific book health and fresh-outcome coverage",
    "veto_pacing": "## 4. Veto pacing",
    "forecast": "## 5. Sample-accrual forecast",
    "recommendation": "## 6. Recommendation",
}

#: What each section has to carry, quoted in the refusal so the hole is nameable.
_SECTION_REQUIREMENT: dict[str, str] = {
    "baseline_proof": ("proven stateful baseline behaviour and isolation, with mismatches "
                       "explicitly resolved or bounded"),
    "arm_results": ("comparative paper results for the feasible arms, or an explicit reason an "
                    "arm could not be measured"),
    "book_health": "a cause-specific book-health diagnosis and a fresh-outcome coverage report",
    "veto_pacing": ("the veto pacing's implemented, preflighted, dormant state and its prepared "
                    "amendment record"),
    "forecast": "the sample-accrual forecast (§1.10)",
    "recommendation": "the dated recommendation and the reason it rests on",
}

#: §0.14a's question, verbatim. Answered by the user, with a date, and only by the user.
ADOPTION_QUESTION = (
    "6D.1 has measured arm A against arm B (and arm C where it was feasible) on identical tape, "
    "state and resources. Which holding policy do you adopt for the prospective period, and "
    "from what date?")

#: I11/D16's sentence, on one line because it is quoted as one.
VETO_STATUS = (
    "implemented, preflighted against stored arrivals under unchanged caps, shipped dormant, "
    "with the §0.8 amendment record and boundary fields prepared; the boundary instant is "
    "written at the user's activation (I11, D16)")

#: §1.11's completion clause, in the words the milestone is judged by.
NEGATIVE_FINDING = (
    "Positive returns are not a completion requirement, and neither is a passing go-live gate: "
    "an infeasible or economically unproductive maker configuration closes this milestone with "
    "a supported negative finding (§1.11).")

#: The five things any adoption proposal has to carry (§1.11). Printed whether or not one is
#: made, so an absent proposal is visibly absent rather than quietly missing its fields.
PROPOSAL_FIELDS = ("exact settings", "hash", "effective date", "rollback",
                   "affected measurement populations")


class MissingEvidence(RuntimeError):
    """A required §1.11 section was empty when a recommendation was asked for."""


@dataclass(frozen=True, slots=True)
class Evidence:
    """One rendering's inputs. Every section is a string the caller computed from the record.

    `arm_c` is separate from `arm_results` because §9's permission is specific: an arm reported
    unavailable **with its reason** is a complete input, and an arm that is simply absent is
    not. `reason` is the recommendation's own justification, `adoption_proposal` is empty unless
    a proposal carrying all of `PROPOSAL_FIELDS` is being made.
    """

    baseline_proof: str
    arm_results: str
    book_health: str
    veto_pacing: str
    forecast: str
    recommendation: str
    arm_c: str = ""
    reason: str = ""
    run_id: str = ""
    manifest_hash: str = ""
    adoption_proposal: str = ""


def recommend(*, comparable_arms: int, accrual_identified: bool, mature_outcomes: int,
              markout_sign: str | None, baseline_resolved: bool,
              arm_b_better: bool | None = None) -> tuple[str, str]:
    """The recommendation and the reason it rests on, by a rule stated here in full.

    The rule, in order, and deliberately conservative - every uncertain branch lands on
    "insufficient evidence" rather than on a conclusion:

    1. fewer than two arms with comparable rows -> insufficient evidence (there is no comparison);
    2. an unresolved baseline proof -> insufficient evidence (§1.3's mismatches bound every
       number that follows);
    3. no mature markout outcome, or no measured sign -> insufficient evidence (an empty
       `exp_outcome` is an **unavailable** outcome, never a zero markout);
    4. a negative measured sign -> stop (a supported negative finding, which §1.11 says closes
       the milestone);
    5. accrual unidentified -> revise (the configuration may be productive, but it cannot accrue
       the sample the prospective period needs);
    6. arm B measurably better than arm A -> revise; not better -> retain; unknown -> insufficient
       evidence.

    This is a recommendation on stated inputs. The decision is §0.14a's, and the user's.
    """
    if comparable_arms < 2:
        return ("insufficient evidence",
                f"only {comparable_arms} arm(s) produced comparable rows on this run: there is "
                "no A-against-B comparison to conclude from")
    if not baseline_resolved:
        return ("insufficient evidence",
                "the stateful baseline proof carries mismatches that are neither explained nor "
                "bounded, so every arm number that follows is unbounded too (§1.3f)")
    if mature_outcomes <= 0 or markout_sign is None:
        return ("insufficient evidence",
                "no mature markout outcome exists for these fills: an empty exp_outcome is an "
                "unavailable outcome, never a zero markout, so the economics are unmeasured")
    if markout_sign == "negative":
        return ("stop",
                f"the measured post-repair markout sign is negative over {mature_outcomes} "
                "mature outcomes: this maker configuration is economically unproductive, which "
                "§1.11 records as a supported negative finding")
    if not accrual_identified:
        return ("revise",
                "the economics are measured but accrual is unidentified: the configuration "
                "cannot accrue the prospective sample at the observed distinct-game rate")
    if arm_b_better is None:
        return ("insufficient evidence",
                "the arms are measured but no comparison between them is supplied, so neither "
                "retaining nor revising the holding policy is supported")
    if arm_b_better:
        return ("revise",
                f"arm B's admission and fill rate beat arm A's over {mature_outcomes} mature "
                "outcomes at a markout sign that is not negative")
    return ("retain",
            f"arm B does not beat arm A over {mature_outcomes} mature outcomes, so the holding "
            "policy in force is the one the evidence supports")


def _require(evidence: Evidence) -> None:
    """§1.11's refusal: name the first empty section rather than emit a recommendation."""
    for name in EVIDENCE_SECTIONS:
        if not str(getattr(evidence, name) or "").strip():
            raise MissingEvidence(
                f"{name} is empty: §1.11 requires {_SECTION_REQUIREMENT[name]} before any "
                "recommendation is emitted, and this report refuses to emit one without it")
    if not evidence.arm_c.strip():
        raise MissingEvidence(
            "arm_results is incomplete: arm C carries neither a comparative result nor an "
            "explicit reason it could not be measured. §9 admits an arm reported unavailable "
            "with its reason as a complete input; it does not admit an absent one")
    if evidence.recommendation not in RECOMMENDATIONS:
        raise ValueError(
            f"recommendation must be one of {RECOMMENDATIONS} - retain, revise, stop or "
            f"insufficient evidence (U10) - not {evidence.recommendation!r}")


def render(evidence: Evidence, *, now: datetime) -> str:
    """§1.11's report: the six sections in order, then the two questions held for the user.

    Raises `MissingEvidence` while any required section is empty, and `ValueError` for a
    recommendation outside `RECOMMENDATIONS`. Nothing in the output adopts a policy or turns a
    setting on.
    """
    _require(evidence)
    out: list[str] = [
        "# 6D.1 execution viability - decision report",
        "",
        EXP_LABEL,
        "",
        f"prepared: {now.isoformat()}",
        f"run: {evidence.run_id or '(not supplied)'}  manifest hash: "
        f"{evidence.manifest_hash[:12] or '(not supplied)'}",
        "",
        "This report **recommends**; it adopts nothing and turns nothing on. Production "
        "adoption of a holding policy is §0.14a's and the pacing profile's activation is "
        "§0.14c's: each is the user's dated decision, and both questions are carried below, "
        "verbatim and unanswered.",
        "",
        SECTION_TITLES["baseline_proof"],
        "",
        evidence.baseline_proof,
        "",
        SECTION_TITLES["arm_results"],
        "",
        evidence.arm_results,
        "",
        f"arm C: {evidence.arm_c}",
        "",
        "No arm's gap is filled by invention: zero invented faster-history observations stand "
        "in for an arm that could not be measured, and an arm reported unavailable with its "
        "reason is a complete input (§9, §1.11).",
        "",
        SECTION_TITLES["book_health"],
        "",
        evidence.book_health,
        "",
        SECTION_TITLES["veto_pacing"],
        "",
        evidence.veto_pacing,
        "",
        f"Status: {VETO_STATUS}.",
        "",
        "The dormant state closes this milestone's veto component: U10 asked for the pacing to "
        "be implemented and verified within the unchanged caps, and the preflight against "
        "stored arrivals plus the prepared amendment record is that verification. Turning it "
        "on changes H9's decided population, so 6D.1 neither waits on it nor is incomplete "
        "without it (§1.11, §0.14c).",
        "",
        SECTION_TITLES["forecast"],
        "",
        evidence.forecast,
        "",
        f"{SECTION_TITLES['recommendation']}: {evidence.recommendation}",
        "",
        f"reason: {evidence.reason or '(not supplied)'}",
        "",
        f"The four conclusions this report may reach are {', '.join(RECOMMENDATIONS)} (U10). "
        "It reaches one of them; it does not act on it.",
        "",
        NEGATIVE_FINDING,
        "",
        "Any production adoption proposal carries all five of: "
        f"{', '.join(PROPOSAL_FIELDS)}.",
    ]
    if evidence.adoption_proposal.strip():
        out += ["", evidence.adoption_proposal]
    else:
        out += ["", "adoption proposal: none is made here. Adoption remains §0.14a's dated "
                    "decision, and a proposal is carried only when all five fields above are "
                    "exact."]
    out += [
        "",
        "## Completion",
        "",
        "6D.1 is marked done only when the evidence in sections 1-5 exists - never when its "
        "CLI and tests are finished (§1.11).",
        "",
        "## The user's dated decisions - unanswered here",
        "",
        f"(a) §0.14a, production holding-policy adoption: \"{ADOPTION_QUESTION}\"",
        "",
        f"(c) §0.14c, the veto pacing profile: \"{veto_profile.AMENDMENT_QUESTION}\"",
        "",
        "Both questions are unanswered in this report by design. No setting is written here, "
        "no policy is adopted here, and no boundary instant is recorded here.",
    ]
    return "\n".join(out)
