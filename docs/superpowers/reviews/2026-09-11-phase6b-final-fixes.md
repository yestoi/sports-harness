# Phase 6B final-review fixes

Final review: `2026-09-11-phase6b-final-review.md` (opus, branch head fd70692 after the integration round). Verdict: mergeable, 0 Critical, 2 Important, 14 Minor. Full suite at fd70692: 3,373 passed (unsharded, 37:07), pristine.

| Finding | Fix | Commit |
|---|---|---|
| IMP-1 `NO_WATCHER_CUTOFF_FIXED_AT` placeholder (2026-09-15 00:00Z) precedes the deploy | set to the release instant in the release commit on `main` (the controller duty `checks.py` and verify.md already assign; user ruling journal 206) | release commit (pending) |
| IMP-2 `tests/test_execution_regressions.py` cites the retired `test_book.py:84` `test_seq_gap_marks_dirty` (ledger line 51) | docstrings re-pointed at the two subscription-level guards (`test_book.py:343`, `:710`); the `plan.py:616` line cite | 6628d1a |
| m-4 NULL-expiry orders have no elapsed measure while `_clamped` grants the whole period (T6 F6) | recorded as a documented limit in `dirty_time.py`'s module docstring; behaviour unchanged | 6628d1a |
| m-1 `_order_key` omits `queue_ahead_at_place` (R14 misses a queue-depth-only divergence) | still open, documented limit (ledger 96); carried | none |
| m-2 interval writes without their own savepoint (T6 F4) | still open, documented limit; §3 row 5 is the detector | none |
| m-3 `open_interval` one SELECT per stepped market per step (T6 F5) | still open; measure `exec.loop_ms` after deploy | none |
| m-5 no sweeper closes an interval left open across an executor restart (T6 F7) | still open, over-reports dirtiness (safe direction); detector §3 row 5 | none |
| m-6 `exec.nw_pending` absent from `CAPSULE_METRIC_NAMES` | still open observation | none |
| m-7 `heartbeat["tape_deferred"]` has no reader; m-8 `_note_backoff` O(tickers x working) | still open observations (ledger 144) | none |
| m-9 `tables.py:1107` reads the now-NULL `traded_at_price` (post-boundary orders drop out) | documented in C3's manifest entry; 6C's report work reads the reconciliation columns | none |
| m-10 `_scratch_engine` admin URL by string surgery (worker-sandbox socket errors) | superseded on `main` by fix 61 (`make_url`); resolved by the merge | main |
| m-11 C1-C6 `config_hashes`/ranges/`deploy_sha` placeholders; m-12 Amendment 6 excludes nothing while unfilled | D11: filled by the controller at merge time in the same commit as the exclusions (see the merge-main brief) | merge commit (pending) |
| m-13 Task 11's incomplete `EXECUTOR_VERSION` pin list | fixed on the branch | 0e3f3b3 |
| m-14 `tests/conftest.py` fixture resolution at the main merge (fix 56 `truncate_all` / `reset all`) | named in the merge-main brief | merge commit (pending) |
