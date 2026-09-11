## Unit: hotfix

Two entry paths, both journaled:
- **A reproduced verification failure.** Re-run the failed check once, 10 minutes after the FAIL. A pass is `transient`:
  journal it, remove the item from Carried fixes, fix nothing, unless the same item also failed within the last 24 h.
- **A review finding cited by id.** The brief cites the finding id and the covering test fails before the fix.

Batching. Carried fixes ship in batches, one branch `fix-<date>-<area>` per area, where an area is the set of files the
fixes touch (recorder socket, REST clients, matching, dashboard, compose). One implementer brief lists every finding in
the batch with its own covering tests; one reviewer; one deploy; one verification covering every row the findings name.
Batches for disjoint areas run in parallel (Parallel work) and share a deploy when their reviews finish close
together (Unit: deploy). A reproduced failure that stops data (recorder down, no signals, `app-serve` unhealthy)
ships alone, ahead of every batch.

A hotfix changes code under `harness/` and tests only. It never edits `verify.md` expectations, health thresholds, cadence,
gate criteria or variant YAMLs, and never adds a table or a dependency; those are phase work (carry them to the next
plan-next) or a gate. The only exceptions are what a pre-populated finding names by id (the compose Postgres tuning, the U1
cadence flip). A hotfix that changes a label's semantics or an executor setting is a pre-registration amendment and a journal ruling.

Implementer `sonnet` (`opus` when the batch touches the executor, pricing, settlement, the venue adapter or the recorder's
WebSocket path and the plan-next entry or the finding marks it judgment-heavy); reviewer `opus` for those paths, `sonnet`
otherwise; fix rounds and re-review as in Unit: phase step 3; `make test`; `--ff-only` merge; the deploy unit
(preconditions apply); re-verify only the failed items or the verify.md rows the findings name. Three fix rounds without a
passing verification, or the same failed item twice running, is a gate. Remove each item when its rows pass.
