# Normalizer split: the decision rule, and the evidence it reads

Phase 6D, addendum §1.3(d). Written 2026-09-13, before any of the evidence exists. Nothing here
is adopted: 6D measures, and a split becomes a decision under the addendum's §8 with its own
rollback only if the rule below is met.

## The rule, stated before the numbers

A normalizer process or job split is proposed **if and only if**, across one game day:

1. `normalize.backlog_age_s` exceeds one cadence in force on more than **10 %** of priced runs, and
2. `recorder.phase_ms{normalize}` is the largest of the three phases on those same runs.

Both halves, or neither. A backlog with the fetch phase dominating is a fetch problem; a large
normalize phase with no backlog is work getting done.

## The denominator

`priced_runs` — runs carrying a `notes->'pricing'` block — for the window judged, published
beside `total_runs` and `non_skipped_runs` (`harness.ops.coverage.eligible_runs`). On
2026-09-11 those were 243, 562 and 2,220; the exhaustion figure quoted as "109/243" is over the
first. No "10 % trigger" is claimed until the three numbers are published for the window they
judge.

## The evidence

| Metric | Where | What it says |
|---|---|---|
| `normalize.backlog_ids{family}` | `metric_samples`, per tick | rows written by this tick minus the family's watermark |
| `normalize.backlog_age_s{family}` | `metric_samples`, per tick | how old the oldest unprocessed row is |
| `recorder.phase_ms{phase}` | `metric_samples`, per tick | fetch / normalize / pricing, the tick's own division |
| `priced_runs` | `coverage.eligible_runs` | the denominator above |

The age sample was kept rather than abandoned: §1.3(c) required its plan to be looked at first,
and `EXPLAIN (ANALYZE, BUFFERS)` on the read is a `Limit` over a `Merge Append` of per-partition
index-only scans on `raw_responses_*_pkey`, one row returned, 20 shared buffer hits
(`tests/test_coverage_denominator.py::test_the_oldest_unprocessed_read_prints_its_plan` prints
it on every run). `harness.normalize.runner.NORMALIZE_AGE_ENABLED` is the one-line switch if a
later plan says otherwise; the id lag beside it is exact and costs nothing either way. The age
is an upper bound on the family's own backlog age, because the first row after a family's
watermark may belong to another family — the direction that cannot understate a backlog.

## If the rule is met

The proposal is written then, with its queue, its budget, its coverage statement and the
raw-recording guarantee (`raw_responses` stays append-only and the source of truth), and becomes
a decision under §8 with its rollback. **This milestone builds none of it.**

## If the rule is not met

This document records that the evidence did not support a split, with the numbers and the window
they came from.

## Status at the time of writing

Not yet judged: the metrics above ship with 6D and the first game day after the deploy is the
first window that can be read. The controller journals the four numbers on that day.
