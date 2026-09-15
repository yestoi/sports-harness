# Phase 6D final-review fixes

Final review: `2026-09-13-phase6d-final-review.md` (opus, branch head 1442e26, Tasks 1-10 merged). Verdict: mergeable, 0 Critical, 1 Important, 1 Minor diff. Reviewer's scoped runs: 746 passed / 0 failed on the 6D-touched files; T8's sharded suite at 57d55e1: 3,415 passed / 6 xfailed / 1 deselected, pristine (the later commits touch only `docs/superpowers/autopilot/` and the addendum).

| Finding | Fix | Commit |
|---|---|---|
| I1 verify.md's 6D `alembic_version` row named `0009_phase6d_sustained_evaluation`, an id that cannot exist (`version_num` is `String(32)`; T1 built `0009_phase6d_sustained_eval`) | row reads the `_eval` id with the reason, and the D9 renumber keeps the stem | 0dac5ba |
| M addendum §3 rows 7 and 12 text defects (single `episode_gap_rule_s`, 6 h `FUNNEL_WINDOW`; the freshness exemptions) and a §8 D10 footnote on the `_eval` stem | annotated per the reviewer's diff; verify.md's Task 10 clarification (1442e26) confirmed against the code | 0dac5ba |
| Documented limits: `episodes.truncated` source `"recorder"` for `kind="intent"`; `coverage.record` swallows its own two statements (logged; surfaces as an unclosed scheduled cell in verify row 2) | stand as limits | none |
| Pending by design: the policy comparison run and any adoption (after 6B; the user's dated decision, §0.15a); the post-deploy §4 read-backs; the D9 renumber to 0012 after 6B's 0011 | carried in the roadmap 6D row | none |
