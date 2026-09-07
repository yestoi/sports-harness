# Experiment-design and statistics review — phase 3 build and the autonomous phases 4–6

Reviewer lens: will the data and analyses produced over the next three weeks answer the pre-registered questions, and does building phases 3–6 autonomously put the pre-registration or the statistics at risk?

Date: 2026-09-07. Read-only review. Read in the order the panel lead asked: v2 spec §1, §3, §6.2–6.7, §7, §9.5–9.7, §10, §12, §13; the 2026-09-06 adversarial review; the phase 3 addendum and plan (Tasks 1–14, with 4, 8–11 in detail); the phase 2 pre-registration record and final review; the roadmap, autopilot design, `SKILL.md`, `verify.md`; code: `harness/strategy/run.py`, `harness/strategy/pipeline.py`, `harness/strategy/variants.py`, `harness/pricing/gaps.py`, `harness/pricing/fair.py`, `harness/pricing/consensus.py`, `harness/pricing/lines.py`, `harness/pricing/fees.py`, `harness/replay.py`, `harness/recorder/cadence.py`, `harness/venues/kalshi/ws.py` (subscription scope), `harness/recorder/ws_sink.py` and `harness/normalize/kalshi.py` (trade normalisation), `harness/db/models.py`, `harness/config/settings.py`, the six variant YAMLs, and the phase 3 SDD ledger.

Resolved items from the first panel (power analysis, clustering by game, subgroup cells, CLV on snapshots, benchmarks plural, veto in shadow with a control) are not re-raised except where the phase 3 plan now departs from them.

## Headline

The dataset design is sound and reproducible, but four things in the phase 3 plan would quietly make the go-live gate and the week-3 analysis measure the wrong thing: the plan rewrote two gate criteria (feed staleness became feed lag; mismatched markets became settlement mismatches) and lets realised results gate; the fill-realism audit is true by construction under the queue model; markouts and adverse drift at short horizons are identically zero at the 15-minute fair-value cadence; and the shrinkage and multiple-testing procedure is under-specified in exactly the ways that over-reject on re-signalled snapshots. Separately, the roadmap's planned amendments (`no_veto`, NO-side, key numbers) cannot be registered under the current variant validator without retiring all six pre-registered ids, and the WebSocket subscription scope (24 h lookahead) means the paper-execution dataset will only exist inside the last day before kickoff.

## Findings, most severe first

Severity key: MUST-FIX before the loop starts (the phase 3 plan is about to be executed autonomously and these change what it builds); FIX in phase N; NOTE.

### F1. MUST-FIX — the plan substitutes gate criteria and lets realised outcomes gate

Where: `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md` Task 11; `docs/superpowers/specs/2026-09-07-phase3-paper-execution-design.md` §0 (no amendment listed) and §4; v2 spec §9.5.

What is wrong:

1. Spec §9.5 requires "median feed staleness < 90 s". Task 11 evaluates "median feed lag < 90 s". These are different columns with different magnitudes: `staleness_s` is pricing time minus the sharp book's own `last_update` (910–932 s on every direct fair in the plan's own "verified data facts"); `feed_lag_s` (new in Task 1) is pricing time minus our `fetched_at` (20–95 s). The substitution turns a criterion that is unreachable at the current alternates cadence into one that passes today. Nothing in addendum §0 records the change.
2. Spec §9.5 "zero mismatched-market incidents" (a matching failure: an order on a market whose ticker resolves to the wrong game or threshold) became "zero settlement mismatches" (ESPN-derived result disagrees with Kalshi's result). Both are useful; they are not the same criterion.
3. "Mean CLV ≥ 0 under every benchmark" includes benchmark type `result` (0/1 outcome). CLV against `result` is realised P&L per contract, which §3 and §9.5 say never gates. It also includes `opening_first_seen`, which measures movement since the opener, not closing value.
4. "≥ 150 paper fills confirmed" counts `fills` rows; a partially filled order that completes over five prints is five rows. The intent is 150 fill events across 40 games.
5. "CLV of filled minus unfilled not significantly negative" passes automatically when n is small (absence of evidence). "Adverse drift > −1.0 pt" passes by construction at the current cadence (see F4). "t > 2" does not say cluster-robust.
6. `feed_lag_s` is the right *data-quality* metric for table 8; it is not the gate's staleness.

Exact change:

- Addendum §0: add item 7 listing every gate criterion whose measurement differs from §9.5 wording, with the reason, or restore the wording. My recommendation: keep `staleness_s` as the gate quantity (median over the fair values used at placement of orders that filled), state plainly that it is unreachable while alternates poll every 900 s, and make the fix a user decision (Odds API cadence versus an amended threshold). The loop must not choose.
- Task 11: gate benchmark family = {`pinnacle_t5`, `consensus_t5`, `consensus_t60`, `consensus_t180`, `kalshi_mid_t5`, `kalshi_last_trade_pre_kick`, `novig_mid_t5` where covered}; `result` and `opening_first_seen` are reported, never gated. Count fill events as orders with ≥ 1 confirmed fill and distinct games among them. Replace "not significantly negative" with an equivalence-style bound: the 90% cluster-robust upper bound of mean(unfilled CLV − filled CLV) < 1.0 pt AND ≥ 20 game clusters on each side, else the criterion reads `insufficient` and fails. Add "zero mismatched-market incidents" as its own criterion (count of orders on markets whose `match_status` changed or that were re-matched after placement) alongside settlement mismatches. Define t as mean over cluster-robust SE by game.
- `gate_reports.criteria_json` stores, per criterion, the definition text and the SQL or function name that computed it, plus a hash of the criteria set; a change in the hash between two gate reports is printed in the Monday report.
- `.claude/skills/autopilot/SKILL.md` Gates: add "changing a gate criterion's definition, threshold, or benchmark family; changing a BH family, a cell grid, or a success threshold". These are user gates.

Cost if ignored: the first stored `passed = true` gate report will rest on a criterion the user never approved, and the sentence "median staleness < 90 s" in the spec will be false about the number that passed it.

### F2. MUST-FIX — the fill-realism audit is true by construction

Where: addendum §2 "Audit"; plan Task 4 `confirmed()`; spec §7.2 table 6; Task 11 gate input.

What is wrong: `confirmed` is "a print with `yes_price ≤ prob` and `taker_side = no` exists in [placed_at, filled_at + 60 s]". The queue model creates a fill *only* from such a print (Task 4 rule 1), so the print that produced the fill always satisfies the predicate. Deltas never produce fills; `snapshot_cross` fills are excluded. The confirmed share of queue-model fills is 100% by construction, so table 6 measures nothing and the gate's "confirmed by trade prints" clause is vacuous.

Exact change (table 6 and the gate input become a validity panel that the model does not assume):

1. At-versus-through composition: share of fill-triggering prints with `yes_price < prob` (through). A print below our resting bid cannot happen if our bid were on the book; such fills are counterfactual ("we would have been hit first"). Report the share; a high share means the tape and the assumed book disagree.
2. Book coverage at placement: share of orders placed with a WebSocket book at place versus a REST ladder versus none, and the share of order-minutes on a `dirty` book (seq gap). No decision on a dirty book is already the rule; the share must be visible.
3. Queue accuracy: where a REST ladder and a WebSocket book exist within 60 s of each other, `queue_ahead_at_place` versus the ladder's size at our price (median absolute difference, share within 10%).
4. Queue-consumption ratio: contracts printed at or through our price before our first fill ÷ `queue_ahead_at_place` (the FIFO assumption's footprint; values far below 1 mean the model is filling us ahead of the queue).
5. Optimistic bracket: `snapshot_cross` fill rate ÷ `queue_model` fill rate per variant (both already stored).
6. Tape source: share of fill-triggering prints with `source = rest` (coarser timestamps, watermark minus 5 s replays) versus `ws`.

Gate input: share of fills on non-dirty books with WebSocket coverage ≥ 90%, and the at/through composition printed next to the fill count. Keep the `confirmed` column (it is cheap) but label it "tautological under queue_model; meaningful for snapshot_cross only".

Cost if ignored: the one validity check the spec put between the paper fills and the gate reads 100% every week and the gate passes on fiction, which is the failure mode the first panel called out.

### F3. MUST-FIX — unit of analysis: re-signalled ticks and reprice chains inflate n in tables 1, 2, 3 and the grey rule

Where: plan Task 10 (`weekly_tables`, `cluster_ci`); addendum §4 "Every cell: point estimate, n, 90% cluster-robust CI by game"; spec §6.7 "cells with n < 30 shown greyed".

What is wrong: the strategy emits one signal per matched market per tick (`run_strategy`, one row per gap row), so a market listed all week produces ~60 candidate rows per weekday at the 15-minute cadence; the live evidence (42 candidates in six hours, mostly the same markets) is about 2–5 distinct markets per weekday. A reprice is cancel + place, so one market produces several `orders` rows per hour. The plan's tables count observations and grey at n < 30 observations; a cell with 200 snapshots from three games is not grey but has three clusters. The cluster-robust CI is right (the first panel required it), but the displayed n and the grey rule are wrong, and the normal critical value under-covers with few clusters.

Exact change:

- Every cell shows `n_obs` and `n_clusters` (games). Grey when `n_clusters < 10`; flag when `n_clusters < 30`. The n < 30 wording in §6.7 and the pre-registration record becomes "fewer than 30 games".
- `cluster_ci`: use the t distribution with G − 1 degrees of freedom instead of `NormalDist` (Cameron and Miller 2015, *Journal of Human Resources* 50(2):317–372, recommend T(G−1) critical values and the wild cluster bootstrap when clusters are few; the bootstrap is optional here, the T(G−1) rule is one line).
- Table 1 (funnel): add distinct markets and distinct (market, day) next to the tick counts.
- Table 2 (CLV per variant): variants share the same gap snapshots, so the comparison is a paired difference per snapshot (variant − primary, clustered by game), one row per pre-registered contrast. Never two independent CIs side by side. Also state that for the four non-executed variants CLV is snapshot CLV at the variant's own `price_target`, not fill CLV.
- Table 3 (filled versus unfilled): collapse a reprice chain to one order episode per (variant, market, side) between two non-reprice events; report episodes and games.
- Table 4: the per-snapshot estimand within a TTK bucket is acceptable (it weights game-days by their tick count); say so in the header and show `n_clusters`.

Cost if ignored: cells look precise with three games behind them; the week-3 "three cells whose CI excludes zero" is met by tick count, not by evidence.

### F4. MUST-FIX — markouts and adverse drift are identically zero at short horizons; the gate's 30-minute markout is undefined

Where: addendum §3 "Markouts"; plan Task 9 `markout_at`; spec §9.5 (adverse drift, 30-minute markout), §6.3 (AS).

What is wrong: `markout_at` picks "the last `fair_values` row at or before the horizon". Fair values are written once per pricing run: every 15 minutes on weekdays, 5 minutes at weekends, 2 minutes inside T−3h, with the alternates line itself refreshing every 900 s outside T−3h. So the fair at +1 and +5 minutes is the same row used at placement, and `adverse_drift = fair at first fill − fair_p_at_place` is exactly 0 for any fill inside the tick (most fills). The gate criterion "adverse drift > −1.0 pt" then passes by construction, and the 1m/5m fair markouts carry no information. The gate says "mean 30-minute markout net of maker fee" without saying whether the markout is against the sharp fair or the venue mid (`markouts` stores both), and §6.3's trailing AS feeds this number back into pricing.

Exact change:

1. On every `markouts` row store `fair_row_id`, `fair_book_ts` (= `fair_values.newest_book_ts`), and `fair_changed = (fair_row_id ≠ order.fair_row_id_at_place)`; select the fair by `newest_book_ts ≤ horizon` (the sharp book's own timestamp), falling back to `created_at`.
2. Define the gate markout as `fair_30m − fill_price − fee_per_contract(maker, fill_price)` where `fair_30m` is the direct sharp consensus whose book timestamp is ≤ placed_at + 30 min; report the venue-mid version next to it. Gate and table 3 use fills with `fair_changed = true` at 30 m and print the share where it is false.
3. Table 3 splits adverse drift by `feed_kind` and by staleness at place (≤ 220 s versus > 220 s): with a 900 s stale fair, a "drift" is often information that existed before placement.
4. Freeze the AS seed at 0.01 for the three-week dataset. The trailing-AS update (§6.3) changes the primary's prices under an unchanged `variant_id`; either pre-register the switch-on date and the bucket definition or leave it off until the gate review. Record `as_at_place` on orders (the SDD ledger already rules this in).

Cost if ignored: H1 (adverse selection) is answered with a zero that was never measured, and the gate criterion that exists to catch adverse selection cannot fail.

### F5. MUST-FIX — BH and empirical-Bayes shrinkage as specified over-reject on autocorrelated snapshots; the family, the grid and the "selected in week 2" step do not exist

Where: plan Task 10 (`bh_reject`, `eb_shrink`); addendum §4 table 4; pre-registration record "Reporting rules"; spec §6.7, §9.6.

What is wrong:

1. `eb_shrink` uses `τ² = max(0, var(means) − mean(var/n))` and weight `n_i/(n_i + σ²/τ²)`. With hundreds of near-duplicate snapshots per game, `var/n` is far smaller than the true cell variance, so τ² is inflated, nothing shrinks, and the raw cell means pass through. The shrinkage exists to prevent exactly the false positives this produces.
2. The cell grid is not defined anywhere. `price_bucket` in code is 5 c (12 buckets in band); TTK buckets are unnamed; with sport × market type × 12 × (say) 4 TTK × 2 fair sources the family is ~576 cells, not "dozens". Which cells form the BH family, and where p-values come from, is unstated.
3. §6.7 says week 3 confirms "anything selected in week 2", but nothing writes down what was selected, and the week-3 report as planned recomputes everything over all data.
4. The success criterion "CI excludes zero after shrinkage" does not say which interval: the raw CI recentred on the shrunk mean, or the posterior interval (narrower by the shrinkage factor). The two disagree.
5. Sign: code stores `gap_mid = fair − mid` (`gaps.py`), spec table 4 says "venue mid − sharp fair".

Exact change (write into the pre-registration record as "Analysis plan", before the week-1 report):

- Shrinkage: `se_i²` = cluster-robust variance of the cell mean; `τ² = max(0, var(m_i) − mean(se_i²))`; `B_i = τ²/(τ² + se_i²)`; `m̃_i = B_i·m_i + (1 − B_i)·m̄`; posterior interval `m̃_i ± t_{G−1}·sqrt(B_i·se_i²)`. If τ² = 0, print "no heterogeneity detected" and count no cell as significant on the posterior interval. Shrink within sport × market type strata (four to six strata) rather than toward one grand mean across NFL moneylines and CFB totals (NOTE-level, but cheap).
- Grid: price buckets {20–35, 35–50, 50–65, 65–80}; TTK buckets {> 24 h, 3–24 h, < 3 h} (these match the feed's cadence regimes, so staleness does not confound TTK silently); sport × market type. 4 × 3 × 2 × 3 = 72 cells per fair source. The 5 c × hourly grid stays available as exploratory output.
- Families: (A) the 72 direct cells of table 4, BH at q = 0.10 on two-sided p-values from the cluster-robust t; (B) the 72 derived cells, separately; (C) the five pre-registered variant contrasts in table 2, Holm. Greyed cells (G < 10) are excluded from the family and the exclusion count printed.
- Selection artefact: the week-2 report (Mon 2026-09-21) writes `docs/reports/2026-w38-selected.json` listing the cells and contrasts it selects; the operator unit commits it before any week-3 data exists. The week-3 report evaluates only those, on week-3 data only, and the §9.6 criterion is judged on that set with the posterior interval.
- Table 4 panels: `gap_mid`, `gap_maker_net` (the exploitable quantity), and `clv_mid_p` versus `pinnacle_t5` (does the mid converge toward the sharp close, or was the sharp book wrong). Print the sign convention in the header.

Cost if ignored: the week-3 map will show many "significant" cells that are artefacts of tick count, the confirmation step will be a recomputation rather than a confirmation, and the numbers will look pre-registered.

### F6. MUST-FIX — the pre-registration is not protected against code changes, and the planned amendments cannot be expressed without retiring all six ids

Where: `harness/strategy/variants.py` (`REQUIRED_KEYS`, `_validate`, `MAX_SECONDARY`, `variant_id_for`); `docs/superpowers/reviews/2026-09-07-phase2-preregistration.md`; roadmap phase 5 "Variants" and phase 6 items 1–3; `harness/db/models.py` (`Signal`, `Run`).

Facts from the code:

- `_validate` rejects unknown keys and requires every key in `REQUIRED_KEYS`; `variant_id_for` hashes the whole dict. Adding any key (`sides` for NO-side, a `veto` flag) means every YAML gains the key and every one of the six ids changes. The record's own guarantee ("changing any field produces a new id") then works against it.
- `MAX_SECONDARY = 5` and five secondaries are registered. `load_variants` raises on a sixth, so the roadmap's "register `no_veto` as a secondary" cannot run as written.
- The record labels `no_velocity` "Mandatory no-veto control". It is a velocity-filter control; the §6.7 no-veto control does not exist yet.
- Phase 6 item 2 (key numbers at 3 and 7) changes `derived` fair values in code. `sharp_plus_derived` would produce different signals under an unchanged id; nothing on `signals` or `runs` records the pricing code version (`Signal` has no `config_hash`, despite spec §5.5; `Run` has no build sha; `settings.build_sha` reaches only the dashboard).
- Executor settings (`exec_expiry_s`, `exec_cancel_venue_move_pts`, `exec_reprice_fair_move_pts`, `exec_kickoff_cutoff_min`, the 1 h intake window, `exec_variants`) are strategy in effect and live in `Settings`, outside any hash.

Exact change:

1. `variants.py`: add `OPTIONAL_KEYS` with defaults applied *after* hashing (hash the YAML as written): `sides: [yes]`, `veto: shadow`, `margin_model: normal_v1`. The six ids survive; new variants opt in and get new ids. One test: hashing the six committed YAMLs still yields the six recorded ids.
2. Pre-registration record: correct the `no_velocity` description; add "while the veto is shadow, the `no_veto` control is the primary itself (identical signals); a live `no_veto` secondary is only meaningful under enforcement, which is a user gate and would replace `no_velocity`". Roadmap phase 5: drop "register `no_veto` as a secondary"; keep the amendment sentence.
3. Key numbers: a new variant (`sharp_plus_derived_kn`, `margin_model: key_numbers_v1` hashed) alongside the old; the old model keeps running for the dataset. NO-side: evaluate by replay first (`best_bid`/`best_ask` and the NO ladder are recorded; `replay --execute` can fill NO bids from the same tape); a live NO-side variant registered after 2026-09-21 is exploratory for this dataset (see F10).
4. Record `build_sha` on `runs` and `fair_values` (or a `pricing_version` constant bumped on any change to `harness/pricing/`), and `orders.config_hash` = hash of (variant_id, `ExecSettings`, executor code version). Reports and the gate group by it; a change mid-week is printed.
5. Freeze the executor settings in Amendment 2 with the deploy time.

Cost if ignored: the first autonomous phase that adds a variant key silently retires the pre-registration, or the loop edits a pre-registered variant's behaviour without changing its id, and no column can show it happened.

### F7. MUST-FIX (phase 3 design) — the WebSocket lookahead confines paper execution to the last 24 hours before kickoff

Where: `harness/config/settings.py` (`ws_lookahead_hours = 24`, `ws_max_tickers = 500`); `harness/venues/kalshi/ws.py` `select_ws_tickers`; `harness/recorder/cadence.py` `select_ladders` (REST ladders only for today's games inside T−3h); addendum §1 "Live book"; plan Task 3 `load_book`, Task 5 `Skip(book_dirty)`.

What is wrong: the executor needs a book at placement (`queue_ahead_at_place`, dirty checks). WebSocket books exist only for matched markets kicking off within 24 h (cap 500 tickers); REST ladders exist only inside T−3h. Weekday candidates are overwhelmingly for weekend games days away, so they will have no book. The plan does not say whether a missing book is `Skip(book_dirty)` or a placement with an unknown queue; either way the paper-fill, markout and adverse-selection dataset covers only the final day, and `order_events(kind = skipped)` dominates the funnel. This also bears on (a): expected fills fall further.

Exact change: in `select_ws_tickers`, subscribe first to every ticker with an open paper order or a candidate signal of an exec variant in the last hour (any horizon), then fill the remainder with the 24 h set up to the cap. Add to the addendum §1 the rule for a missing book (my recommendation: place with `queue_ahead_at_place = NULL`, `book_source = none`, and exclude those orders from fill simulation until a book exists; report the share in table 6 item 2). Add to `verify.md`: share of orders placed without a book.

Cost if ignored: three weeks of paper execution produce fills from roughly one day per game, and the go-live gate's fill count is unreachable for reasons that have nothing to do with the strategy.

### F8. FIX in phase 3 — CLV definitions and the benchmark set

Where: plan Task 8 (`compute_benchmarks`, `compute_gap_outcomes`); addendum §3; spec §10.

1. There is no net-of-fee CLV in probability points, yet the gate says "net-of-fee CLV vs `pinnacle_t5`" and reads the 90% lower bound in points. Add `clv_target_p_net = p_bench − p_used − fee_per_contract(maker, p_used)`. The ROI column as written, `p_bench/p_used − 1 − fee/p_used`, is not the ROI of a fee-inclusive cost, which is `p_bench/(p_used + fee) − 1`; the difference is second order, but pick one and name it.
2. `p_used` in `clv_target_p` is "the primary's `price_target` from `signals` (fallback `best_bid`)". Mixing a maker target and the venue bid in one column makes the mispricing map's target-CLV a blend of two quantities. Store `p_used_kind ∈ {target, best_bid}` or two columns.
3. `pinnacle_t5` for non-main thresholds must come from `alternate_spreads`/`alternate_totals` pairs (use `spread_pair`/`total_pair`, which search both markets); `source_ts` is the book's `last_update`, and the stale flag comes from it, not from `fetched_at`.
4. `novig_mid_t5` from the Odds API `novig` bookmaker is a devigged two-way pair of exchange asks, not a mid. Call it `novig_devig_t5` (or store both asks) and note coverage is main lines only. H8 (Kalshi versus Novig lead/lag) is not testable with the Novig side sampled every 120–900 s; report it as "not collected" or descriptive only, never as a cross-correlation "result".
5. `kalshi_mid_t5` and `kalshi_last_trade_pre_kick` measure the venue's self-convergence; fine, label them so.
6. Benchmarks are "computed exactly once (unique key) and never updated". If the T−5 snapshot is stale (flag) because the tick missed, a later backfill is impossible by design; accept, but print the stale share per benchmark type in table 8 and exclude stale benchmarks from gate means.

### F9. FIX in phase 5 — the veto week-1 paired design and the seven-day swap study

Where: roadmap phase 5(d) and operator calendar row "Seven days after the veto goes live"; spec §7.1, §9.7 H9; `research_notes` (spec §5.5).

What the seven-day study can conclude: programmatic validity (valid JSON, every claim cites a stored snippet, veto rate in band, trap cases) and blind pairwise preference between models on ~100–200 frozen cases. What it cannot: outcome accuracy. At a 5–15% veto rate one week yields ~10–25 vetoed candidates and a handful of vetoed fills; CLV and 30-minute markout by decision will carry CIs of several points. The spec's own promotion bar is ≥ 100 vetoed signals. The judge design compares candidates to *frozen Opus outputs*, so it measures similarity to Opus at default effort, not correctness, and a Claude judge on Claude outputs is a same-family judge.

Exact change (write into the phase 5 addendum before the study runs):

1. Pre-register the swap rule: swap only if programmatic checks pass, blind pairwise preference with position randomisation is ≥ 60% on ≥ 50 disagreement cases, and the outcome table shows the candidate is not worse (an equivalence bound, not a win). Never on cost or on the outcome table alone.
2. Define the trap cases now: (i) stale-status trap — a candidate whose fair pre-dates a recorded injury or inactive change (from `news_events` or ESPN status) where `veto`/`reduce` is right; (ii) injection trap — a retrieved snippet containing instructions, where following them is a fail; (iii) no-news control — where any `veto` is a false positive. Score each model on these separately from the preference judge.
3. The outcome analysis is within-primary by decision label: vetoed versus proceeded candidates' snapshot CLV, and markouts where filled, clustered by game, reported with `n_clusters`. A separate `no_veto` variant adds nothing in shadow mode (identical signals) and should not consume a secondary slot (F6).
4. Give the judge the ground truth where it exists (post-hoc CLV sign at `pinnacle_t5`) as a second, separate score, so preference and correctness are not conflated.
5. What phase 3 must log for the joins to work (already planned, keep them): `orders.gap_snapshot_id`, `intents.signal_id`, `markouts` for unfilled orders, `build_sha` (F6). Phase 5 must add: `research_notes.subject_id = signal_id`, prompt hash, model id and effort, the frozen feature vector, snippet ids, latency, and both models' outputs keyed to the same call id.

### F10. MUST-FIX — an amendment protocol that keeps autonomous changes honest under §6.7

Where: pre-registration record (new section); `SKILL.md` plan-next and hotfix units; roadmap phases 5–6.

§6.7 requires variants "registered before Week 1 kickoff" (the record says before Week 2) and "weeks 1–2 explore, week 3 confirms anything selected in week 2". Anything the loop registers later is not pre-registered in that sense. The protocol:

1. Measurement amendments (a label's semantics, a staleness allowance, a benchmark definition, a bug fix that changes a label) record: date and deploy sha, the run id range affected, which report tables and gate criteria they touch, and the re-scoring command (`harness replay`). Variant ids unchanged. The report excludes or re-scores the pre-fix range and says so. Amendment 2 (Task 1) fits this shape; make it the template.
2. Strategy changes (NO-side, key numbers, band, AS, executor settings) are new variant ids or new hashed settings, never edits. The six ids are frozen for the three weeks.
3. Anything registered after the week-2 report (Mon 2026-09-21 09:00 CT) is exploratory for this dataset and cannot be confirmed in week 3. NO-side and key numbers (phase 6) fall here; the report labels them "post-registration, exploratory".
4. The loop may not change a gate criterion, a BH family, a cell grid, a success threshold, or the confirmation cut-off. Add to `SKILL.md` Gates.
5. A hotfix that changes a label's semantics or an executor setting is an amendment (item 1) and a journal ruling, not only a carried fix. `verify.md`'s "candidates > 0" expectation creates a standing incentive to loosen thresholds; this rule is the counterweight.

### F11. FIX in phase 3 — what the verification contract cannot see

Where: `docs/superpowers/autopilot/verify.md` "Phase 3 additions".

The contract checks counts and freshness. None of the following would change a count: a sign flip in CLV; a benchmark that used a post-target snapshot; a markout taken from the future book; fills on dirty books; a `taker_side` default; a redeploy that changed pricing mid-week. Add these queries and expected values:

- `benchmarks`: `count(*) where source_ts > target_ts` = 0; stale share by type printed.
- `fills`: `count(*) where filled_at < placed_at or filled_at > kickoff − 10 min` = 0.
- `markouts`: `count(*) where at_ts > horizon_ts` = 0; share of 30 m rows with `fair_changed = false` printed (F4).
- Orders: share placed without a book, share of order-minutes dirty (F7, F2).
- `orderbook_events`: `gap` rows per hour per ticker.
- `venue_trades`: both normalisers write `taker_side = "yes"` when Kalshi omits the field (`harness/normalize/kalshi.py:129`, `harness/recorder/ws_sink.py:70`). Change to NULL; a defaulted seller becomes a phantom buyer for H3's imbalance and is invisible to the fill model. Verification prints the NULL share.
- Sanity: |mean `clv_mid_p` versus `pinnacle_t5`| < 5 pts over the last week (a sign flip shows as a large bias).
- Provenance: `runs.build_sha` since the deploy time equals `DEPLOY_SHA` (F6).
- Reproducibility: `harness replay --execute` over the last game day reproduces the live order and fill counts exactly. The plan builds this as a test; make it a verification item, because it is the strongest dataset check available.

### F12. NOTE — week naming and the confirmation calendar

`harness report --week N` uses ISO weeks (Mon–Sun) while the spec's "Week 1/2/3" are football weeks. Define the mapping once in the pre-registration record: Week 1 = ISO 37 (paper orders from the phase 3 deploy, ~2026-09-08/09, through Sun 09-13), Week 2 = ISO 38, Week 3 = ISO 39; the week-2 report and `selected.json` land Mon 2026-09-21 09:00 CT; the week-3 confirmation report lands Mon 2026-09-28. Monday Night games belong to the following ISO week; keep it consistent.

### F13. NOTE — the two exec variants fill from the same prints

`exec_variants = [sharp_direct, constrained]` place the same orders on the same markets and both are filled by the same taker print. Tables 1 and 6 must never sum across variants; the funnel's fill count is per variant. Print this in the table header.

## Item-by-item answers to the panel's questions

### (a) Are the §9.6 week-3 criteria reachable, and what makes them unreachable?

- Mispricing map with three cells whose 90% CI excludes zero after shrinkage: reachable. It needs snapshots, not fills. Roughly 80 games a week (60–70 FBS with a Kalshi listing plus 16 NFL) give ≥ 20 game clusters in most of the 72-cell grid by week 3. What makes it unreachable or meaningless: the fine 5 c grid, shrinkage on `var/n` (F5), and greying by observations (F3). What makes it false: the pre-fix staleness range not excluded (Amendment 2 handles it if the report actually filters).
- Measured convergence lag per venue (table 5): reachable but quantised. The sharp side is sampled every 15 min on weekdays, 5 min at weekends, 2 min inside T−3h, 20 s in the NFL T−100..T−60 window; the "10 minutes after any venue mid move ≥ 2 pts" burst in spec §4.1 is not implemented (`cadence.py`). Use `fair_values.newest_book_ts` as the sharp move time and the first WebSocket mid crossing 50% as the venue time (precise); report negative lags (venue moved first) and the censored share (never covered 50% before kickoff). A median over uncensored lags is biased downward; use a Kaplan–Meier median or print the censored share beside it.
- Adverse-selection estimate for maker fills: the fragile one. On current evidence distinct candidate markets are 2–5 per weekday and more on game days; fills need a book (F7), bids rest 3–4 c below fair, and the `venue_move` cancel removes the order when the market comes to it. Expect tens of confirmed fills a week, not hundreds. Pre-register the floor: ≥ 30 fills across ≥ 10 games, else the week-3 report prints "insufficient" for H1 rather than a number. The go-live gate's 150 fill events across 40 games is realistically a five-to-eight-week target; "mid-October" is optimistic.

### (b) Unit of analysis and clustering

See F3. Summary of the right unit per table: table 1 ticks plus distinct markets; table 2 paired differences on shared snapshots; table 3 order episodes; table 4 snapshots within TTK bucket, clustered by game; table 5 moves; table 6 orders; all with `n_clusters` and T(G−1).

### (c) CLV definitions and the benchmark set

See F8 and F1 item 3. Mid versus target: `clv_mid_p` answers "does the venue converge to the sharp close" (H2's convergence half); `clv_target_p_net` answers "would a maker at our price have beaten the close after fee" (the gate). Keep both; never gate on `result`.

### (d) Markout horizons and adverse drift at 900 s staleness

See F4. The 1 m and 5 m fair markouts are the same row as at placement on weekdays; only the venue-mid markouts from the WebSocket book are informative at those horizons. The 30 m and 120 m fair markouts are informative only when a fresher book timestamp exists; store `fair_changed` and condition on it.

### (e) BH and empirical Bayes on the actual grid

See F5. The real grid is hundreds of cells, not dozens; the method-of-moments τ² must use cluster-robust `se²`, not `var/n`; define the interval used for the success criterion; write the family and the selection artefact down before the week-1 report.

### (f) Gate implementability and gameability

See F1 (criteria rewritten in the plan), F6 item 5 (executor settings outside any hash), F4 item 4 (AS feedback), F13 (which variant's fills count). Everything the loop can choose that moves a gate number must be hashed into `orders.config_hash` and printed in the gate report.

### (g) The `confirmed` share as a validity check

See F2. It is not one under the queue model. The replacement panel is listed there.

### (h) Veto week-1 paired-shadow and the swap study

See F9.

### (i) Autonomous amendments versus §6.7

See F6 and F10. Compatible only with the optional-keys change (so ids survive), the "after 2026-09-21 is exploratory" rule, and the list of things the loop may not change.

### (j) What verification cannot see

See F11.

## What is sound

- CLV on every snapshot, filters as labels with nothing dropped, a pure `run_strategy` with an explicit clock, hashed variant configs, and `replay --execute` reproducing paper orders and fills from stored rows: this is the right skeleton, and it makes every finding above fixable after the fact by re-scoring rather than by re-recording.
- Benchmarks are plural, computed once at kickoff + 5 min with a stale flag, and the gate is expected to fail in phase 3; the legal decision and live trading are never autonomous.
- Cluster-robust CIs by game, BH and shrinkage are in the plan as pure functions with tests on fixed vectors, so the corrections in F3 and F5 are small edits to code that will exist, not new machinery.

## Questions only the user can answer

1. Odds API cadence: stay on the 100k-credit tier (alternates every 900 s, so the spec's "median staleness < 90 s" gate criterion is unreachable by construction) or move to the cadence the spec assumed? This decides whether the criterion is amended or the feed is.
2. May the loop amend any gate criterion definition or threshold at all? My recommendation is no (F1, F10); the roadmap's authorizations do not mention it either way.
3. Confirm the confirmation cut-off: week-2 selection frozen Mon 2026-09-21 09:00 CT, week-3 confirmation Mon 2026-09-28, given that paper orders start ~2026-09-08/09 rather than at Week 1 kickoff.

## References

- Cameron, A. C., and Miller, D. L. (2015). A Practitioner's Guide to Cluster-Robust Inference. *Journal of Human Resources* 50(2):317–372. Few clusters: T(G−1) critical values; wild cluster bootstrap. ([JHR](https://jhr.uwpress.org/content/50/2/317.abstract), [author PDF](https://cameron.econ.ucdavis.edu/research/Cameron_Miller_Cluster_Robust_October152013.pdf))
- Benjamini, Y., and Hochberg, Y. (1995). Controlling the false discovery rate. *JRSS-B* 57(1):289–300.
- DerSimonian, R., and Laird, N. (1986). Meta-analysis in clinical trials. *Controlled Clinical Trials* 7:177–188 (method-of-moments τ² with per-cell variances, the form recommended in F5).
- Le (2026), arXiv 2602.19520, and Bürgi, Deng, Whelan (2026), as cited in the v2 spec §3, for the priors on calibration and maker adverse selection that make H1 the load-bearing hypothesis.
