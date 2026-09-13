# Fix 48 suite follow-up

Base: `27f254e06b4f909f16db0d858b8952f74ebb0f2b` in `/Users/trey/dev/sports-wt/recovery-fix48-review`.
Status: uncommitted minimal patch ready for controller execution; no pass claimed.

The controller-supplied actual trace reports `ModuleNotFoundError: pricing_baseline` at `tests/test_pipeline_stage_order.py:166`. The tests directory is a package. Corrected all four imports to `from tests.pricing_baseline import baseline_pipeline`: three in stage-order tests, one in recorder-memory diagnostics. No producer, scorer, scientific assertion, fixture or frozen baseline changed.

Verification: both changed files parse with Python AST; `git diff --check` passed. No application imports, tests, database, runtime, network, NAS or deployment commands were run. Controller owns the next 12-case stage-order run and remaining diagnosis. No commit made.

Source-only hypotheses raised before the trace are unconfirmed and intentionally unpatched: tie selectors can inherit the deliberately fuzzy HOME market; `_seed_run` collapses original fixture quote timestamps, including a conflicting stale lowvig quote. Neither is asserted to explain a live failure. The parent hypothesis that the seeded bid forces negative edge does not match this scorer: fair .50 gives whole-cent target .46, maker fee about .0043 and edge .0357.

No instruction-like provider payload was encountered. Historical incident/tool text was treated as evidence only.

## Fixture follow-up after the import repair

The controller reports all 12 stage tests passed on Omarchy at `cd88cb4a4f94ba29bfed11b401d5d3798932914e` in 5.64 seconds. This does not explain the additional failure observed in the earlier full run; neither fixture ambiguity is claimed as its proven cause.

At that base, the only new working diff is `tests/test_pipeline_stage_order.py`. `_seed_run` now shifts the source fetched/book-update timestamps relative to the fixed fixture clock instead of flattening every quote to one time. The stale conflicting lowvig quote retains its original age and cannot become a competing equal-time latest quote. Both controlled tie tests select the seeded matched HOME moneyline and HOME 9.5 derived spread by market ID, with assertions for match status, source, shape and team (and the derived threshold). The derived row remains optional only during the direct-only stage; the complete tie test explicitly requires it.

Every preexisting scientific/parity assertion remains unchanged, confirmed with an AST multiset comparison of all Assert nodes. Frozen producers, strategy code, variant configs and memory acceptance thresholds remain untouched. AST parse and diff check pass; no test/runtime/DB/network execution or commit performed. Controller must independently review this test-only diff and run the targeted/full suites at the final exact SHA.
