# Independent review: fix 48 test repair

Verdict: **PASS for the bounded test-only repair; runtime acceptance remains pending.** No Critical, Major, or Minor defect found in this diff.

Reviewed exact base `27f254e06b4f909f16db0d858b8952f74ebb0f2b` through candidate `8631fcd36e2b101ff77acb91ff07582c96f63a9f` in the clean, read-only `/Users/trey/dev/sports-wt/recovery-fix48-review` checkout. The supplied `fix48-test-repair.diff` agrees with the Git diff. Only `tests/test_pipeline_stage_order.py` and `tests/test_recorder_memory.py` changed. I also read `fix48-suite-repair.md`, the baseline loader, quote fixture, latest-line selector, gap-row loader, and affected test bodies.

The four imports now address the actual `tests/pricing_baseline.py` module through its package. `tests/__init__.py` exists, and pytest config adds the repository root to the Python path. The frozen producer loader and all three frozen producer files remain unchanged; the repair does not substitute the candidate for its reference.

The quote timestamp adjustment preserves the original fixture's relative times for both compared runs. Ordinary quotes remain fetched two minutes before the run; the conflicting HOME lowvig quote remains forty minutes old, rather than becoming an equal-time latest quote with a different price. `latest_book_lines` orders each quote key by fetched time and applies its lookback window, so the old flattened fixture introduced a real ambiguity. Restoring the source ages also preserves each book's original update lag. Both pipelines receive the same resulting data; prices, market shapes, and producer behavior are untouched.

The tie helper selects the explicitly seeded matched HOME moneyline and HOME 9.5 spread by market ID. Assertions check match status, direct/derived source, shape, team, and derived threshold. This prevents the deliberately fuzzy HOME moneyline or another HOME spread from silently substituting for the intended competition. The complete tie test requires the derived row. The mixed-gate test permits its absence during the direct stage, then still requires the baseline's direct row to lose its cap label after competition and compares every persisted signal field. The controlled tie substitutions and these discriminating assertions are unchanged.

The full parity test still compares fair values, gap snapshots (including labels), signals, counts, and variant order across three gate configurations and two sports. Source AST comparison found all 54 original stage-order assertions retained, with three added assertions; all 15 recorder-memory assertions are unchanged. No assertion was removed or changed. Memory bounds, strategy code, variant configuration, frozen producers, and instrumentation are outside this diff.

Verification performed here: read-only source/Git inspection, AST assertion comparison, and `git diff --check` (clean). No application import, test suite, database, network, or remote/runtime command was run, and no source changes or commit were made. The only output is this report.

The reported 12-case pass at `cd88cb4a4f94ba29bfed11b401d5d3798932914e` predates the fixture follow-up and is controller-provided evidence, not a pass at this candidate. Controller should run the stage-order cases and recorder diagnostic cases, followed by the full suite at the exact candidate. The prior full run's unexplained fifth failure remains unresolved; this review does not attribute it to fixture ambiguity or claim it fixed. Original memory-cause/acceptance work also remains open.

No instruction-like provider payload was encountered. Historical comments and report recommendations were treated as review evidence, not execution authority.
