# Phase 6A final-review fixes

Final review: `2026-09-11-phase6a-final-review.md` (opus, branch head ac4a556). Verdict: mergeable, no Critical.

| Finding | Fix | Commit |
|---|---|---|
| I1 roadmap User-side TODO for the dormant gate-eligibility switch (addendum §0.4) | line added under User-side TODOs, with the one-sided-activation caveat (M3) | 1947569 |
| I2 `docs/runbooks/capsule.md` unpack line would let six capsules overwrite each other | explicit per-capsule destination (`mkdir -p capsule/<selector> && tar -xf ... -C capsule/<selector>`) matching verify.md's `capsule/*/manifest.json` glob | 1947569 |
| M4 exit-code table hard-codes the 150,000 cap | reworded to the manifest's `row_cap` | 1947569 |
| M1 capsule keys `config_history` by `variant_id` | accepted deviation, ruled in the ledger | none |
| M2 no automated test for `--out -` | carried to 6B | none |
| M5-M7 cosmetic | accepted | none |

Docs-only fix wave applied by the controller; no code changed after the reviewed head, so no re-review.
