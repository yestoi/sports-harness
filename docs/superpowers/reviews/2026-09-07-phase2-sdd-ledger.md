# SDD ledger — plan: docs/superpowers/plans/2026-09-07-phase2-pricing-signals.md
Spec: docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md (v2). Branch: phase2-pricing-signals (from main @ be52929). Test DB: DATABASE_URL_TEST=postgresql+psycopg://harness:harness@localhost:5433/harness_test; manual runs use harness_dev. NAS stack live (do not run docker locally; the Mac compose stack is down). Secrets present in secrets/.

Ruling: implement on branch in place (as phases 0/1). Cost if wrong: none to main.

## Pre-flight scan
| Pair / task | Produces vs consumes | Finding |
|---|---|---|
| T1 FairValue/MarketGapSnapshot/Signal/StrategyVariant columns ↔ T6/T7/T9/T10/T11 | column names listed in T1 interfaces | consistent by construction; implementers must use T1 names verbatim |
| T2 FeeModel, fee_per_contract(model, role, p, contracts) ↔ T7 gaps (100-contract reference), T8 strategy | same signature | consistent |
| T3 BookFair/Consensus/consensus() ↔ T4 direct_fair, T6 compute_fair_values | same | consistent |
| T4 Line/LineKey/latest_book_lines, spread_pair/total_pair/ml_pair, direct_fair, soft_fair ↔ T6/T7 | T4 code not fully listed; interfaces stated | implementer designs internals; reviewer checks against T6 usage |
| T5 MarginModel.from_main_lines(sport, home_point, p_home_cover, total_line, p_over); p_margin_over(model, team_is_home, threshold) ↔ T6 | same | consistent; T6 must know home/away for the side team via games.home_team_id |
| T6 compute_fair_values(session, run_id, now, lookback_s, game_ids) -> FairCounts ↔ T9 pipeline | same | consistent |
| T7 build_gap_snapshots(session, run_id, now, tz, fee_model) -> int ↔ T9 | same | consistent |
| T8 Variant/load_variants/register_variants/GapRow/SignalRow/run_strategy(rows, variant, now, state) ↔ T9/T10 | same | consistent; GapRow needs sport/game_id/market_type/side_team_id/fee_type/fee_multiplier joined from venue_markets+games (T9/T10 build it) |
| T8 variant YAML schema ↔ T12 committed YAMLs | six files | T8 creates them |
| T9 price_and_signal(session, run_id, now, settings, budget_s) ↔ tick | called only when summaries non-empty | consistent with gap alignment |
| T11 dashboard mounts /healthz from harness/health.py ↔ existing tests/test_health.py | keep create_app or re-export | reviewer to check no regression |
| Global: filters are labels; decision derived; nothing skipped | T8 emits one SignalRow per GapRow | consistent |
| Global: Decimal everywhere; NormalDist for CDF | T5 uses float internally then quantizes | acceptable (documented) |
Task 1: dispatched (base be52929, implementer impl-p2-task-1, model sonnet)
Task 1: reported DONE (commit 7aaf6b9); reviewer dispatched
Task 1: complete (commits be52929..7aaf6b9, review clean)
Tasks 2+3: batched dispatch (base 7aaf6b9, implementer impl-p2-task-2-3, model haiku) — two commits, one review
Tasks 2+3: reported DONE (commits 872f241, 0428f54); reviewer dispatched
Tasks 2+3: complete (commits 7aaf6b9..0428f54, review clean). Ruling: consensus.newest_ts restricted to group-member books (folded into Task 4). Minor (deferred): fee_model_for duplicates rate literals; no docstrings.
Task 4: dispatched (base 0428f54, implementer impl-p2-task-4, model sonnet)
Task 4: reported DONE (commit 8c4e231); concerns accepted (staleness in T6; partial pairs omitted; lookback filter); reviewer dispatched
Task 4: complete (commits 0428f54..8c4e231, review clean). Minor (deferred): spreads-vs-alternate preference untested; direct_fair `now` unused (staleness in T6).
Task 5: dispatched (base 8c4e231, implementer impl-p2-task-5, model sonnet)
Task 5: reported DONE (commit 3c63159); reviewer dispatched
Task 5: review found 2 gaps (unclipped inv_cdf inputs; KeyError on unknown sport). Fix round 1 sent.
Task 5: fix round 1/5 (2 addressed; commits 3c63159..4ba30b7)
Task 5: complete (commits 8c4e231..4ba30b7, review clean after 1 fix round)
Task 6: dispatched (base 4ba30b7, implementer impl-p2-task-6, model sonnet)
Task 6: reported DONE (commit 6127f19); games count semantics = candidates examined (accepted); reviewer dispatched
Task 6: review found 3 (main spread could anchor on alternates; main total no outward search; no per-game isolation). Ruling: fix all three; spread_pair/total_pair gain a  kwarg. Fix round 1 sent.
Task 6: review found 3 (main spread could anchor on alternates; main total no outward search; no per-game isolation). Ruling: fix all three; spread_pair/total_pair gain a markets kwarg. Fix round 1 sent.
Task 6: fix round 1/5 (findings 2,3 addressed; finding 1 main-spread fixed). Re-review flagged total_pair default now including alternate_totals as an unrequested change. Ruling: ACCEPT — symmetric with spread_pair and the verified data (18/1026 total rungs have a sharp line only when alternates count); the main-line anchor still uses ("totals",). Cost if wrong: more direct totals priced from thin Pinnacle alternates (they still require Pinnacle).
Task 6: complete (commits 4ba30b7..bcc58e1)
Task 7: dispatched (base bcc58e1, implementer impl-p2-task-7, model sonnet)
Task 7: reported DONE (commit dd25411). Ruling: VenueMarket has no fee_type/fee_multiplier columns (phase 1 omission); use the constant KALSHI_FOOTBALL fee model in gaps and strategy (all football series verified quadratic_with_maker_fees ×1); GapRow drops fee_type/fee_multiplier. Reviewer dispatched.
Task 7: complete (commits bcc58e1..dd25411, review clean). Minor (deferred): shape mapping duplicated between fair.py and gaps.py.
Task 8: dispatched (base dd25411, implementer impl-p2-task-8, model opus)
Task 8: reported DONE (commit cc219e9, 209 tests). Rulings: (1) retire replaced variants by renaming to name#oldid + active=False (accepted; partial unique index deferred); (2) plan's worked example edge_min 0.025 was wrong, formula gives 0.035 (accepted); (3) register_variants should deactivate names absent from the YAML dir — add in fix round; (5) velocity uses prev snapshot fair (≤15 min) rather than a strict 5-min window (accepted, documented). Reviewer dispatched (opus).
Task 8 review notes: stale_s default 180 vs spec §6.2 "older than 90 s" — Ruling: 180 stands (featured lines are fetched at ≥120 s cadence; 90 s would label every row stale); spec to be amended in the final docs commit. LABEL_ORDER omits match confidence (all rows already matched/fuzzy/manual; fuzzy should be a label — add in fix round), taker-imbalance (needs trade-tape signal, phase 3), shadow veto (phase 3+).
Task 8: review Important ×3 (positions records last not max edge; deleted YAML stays active; min_contracts/cap_per_bet untested) + minors. Ruling: fix round 1 covers the three Importants, name-length collision, strict thresholds per spec §6.4, and a new match_confidence label (LABEL_ORDER now 17). Deferred minors: cap_per_bet never False (documented), prev_fair_ts unused, register_variants commits internally, disagreement_ok is a null check.
Task 8: fix round 1/5 (6 items; commit 51e25e0; 220 tests controller-verified); re-review dispatched. Note for Task 9: GapRow.match_status must be populated from venue_markets.
Task 8: re-review found new breakage — global pruning would deactivate live variants on a replay registration. Ruling: prune only primary/secondary tiers, opt-out via prune=False for replay. Fix round 2 sent.
Task 8: fix round 2/5 (pruning scoped; commit 6f744c3; 223 tests); re-review dispatched
Task 8: complete (commits dd25411..6f744c3, review clean after 2 fix rounds)
Task 9: dispatched (base 6f744c3, implementer impl-p2-task-9, model sonnet)
Task 9: reported DONE (commit acecbfa, 228 tests); concerns accepted (decision counts, coarse budget, no CLI tests); reviewer dispatched
Task 9: complete (commits 6f744c3..acecbfa, review clean). Minor (deferred): tick-level pricing test is a wiring smoke test; no negative-path test for "markets not refreshed".
Task 10: dispatched (base acecbfa, implementer impl-p2-task-10, model sonnet)
Note: variant ids (from `variants register` on harness_dev @ acecbfa, for the Task 12 pre-registration doc): sharp_direct=f259ca109084 (primary), sharp_plus_derived=49af716f8708, wide_band=c2bc45377328, constrained=ff363c8ac08d, nfl_only=e549e693e117, no_velocity=64ba3ef09642 (secondaries). Briefs for Tasks 11 and 12 generated.
Task 10: reported DONE (commit 190c6df, 232 tests); concerns noted (run_strategy `now` unused in body; --variant required with --file and unvalidated against YAML name); reviewer dispatched
Ruling: `secrets/dashboard_token` is generated with `openssl rand -hex 32` (local file created now, mode 600, never printed); Task 11 adds a `deploy-nas` step that creates it on the NAS if absent and scp's it otherwise, and mounts it read-only into app-serve only — mounting a nonexistent file would make Docker create a directory, so the file must exist before `compose up`. Cost if wrong: an unauthenticated /unkill on a LAN-only, 127.0.0.1-bound port.
Task 10: review PASS/PASS (190c6df); one moderate finding: --file path ignores --variant. Fix round 1 dispatched to impl-p2-task-10 (validate names match, raise ValueError). Deferred: none.
Task 10: fix round 1 done (f3b81f5, 233 tests); scoped re-review dispatched
Task 10: complete (commits 190c6df..f3b81f5, re-review ADDRESSED, 233 tests)
Task 11: dispatched (base f3b81f5, implementer impl-p2-task-11, model sonnet)
Task 11: reported DONE (commit 225976f, 241 tests); concerns accepted (two sessions per page render; 24h window on match report/unmatched); reviewer dispatched
Task 11: complete (commit 225976f, review clean, 241 tests). Nits deferred: ExitStack per create_dashboard call; two sessions per render.
Task 12: controller-executed (Makefile step landed in T9; doc from scratchpad draft; NAS table to be pasted after deploy)
Task 12: complete (commit 2abfbab: pre-registration doc + spec stale_s amendment). Final whole-branch review dispatched (opus) on be52929..2abfbab
Final review: FIX-THEN-MERGE (report final-review-report.md). Ruling: one fix wave covers F1 (active_variants tier filter), F2 (price-once uses run.started_at; latest_book_lines upper bound), F3 (variant order rotated by run_id + record variants_run), F4 (n_groups on GapRow → disagreement_ok label; cap_per_bet vs uncapped stake), F5 (no-fair reason recorded), plus the two-active-primaries guard. Deferred to phase 3: F6 per-day cap semantics (matters only at paper execution), fee basis note, key-number adjustment, NO side, duplicate-quote drop. Cost if wrong: a week of labelled but slightly mis-sized paper signals, all replayable.
Final fix wave dispatched (fix-p2-final, sonnet) on 2abfbab
Final fix wave done (0e4fff1, b088912, 4809bbe by fix-p2-final; 39c36d3 controller: idempotent ALTER for no_fair_reason; 251 tests). Scoped re-review dispatched on 2abfbab..39c36d3
