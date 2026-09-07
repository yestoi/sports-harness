# Phase 2 pre-registration record

Spec §6.7 requires the primary and at most five secondary strategy variants to be registered in `strategy_variants` before the Week 2 kickoff (Friday 2026-09-12, 00:00 UTC). This record freezes the six variant ids. Each id is the hash of the frozen YAML in `harness/variants/`; changing any field produces a new id, so a later edit cannot be passed off as the pre-registered variant.

## Registered variants

| name | tier | variant_id | what it tests (delta from the primary) |
|---|---|---|---|
| sharp_direct | primary | f259ca109084 | Sharp-consensus (Pinnacle 0.65 / BOL 0.35) direct fair values only; all filters as labels; quarter-Kelly sizing on a $3,000 paper bankroll |
| sharp_plus_derived | secondary | 49af716f8708 | H4: adds margin-model derived fair values for ladder rungs without an exact sharp line |
| no_velocity | secondary | 64ba3ef09642 | Mandatory no-veto control: sharp-line velocity filter disabled (`velocity_max_pts: 1.0`) |
| wide_band | secondary | c2bc45377328 | Price band widened from 0.20–0.80 to 0.15–0.85 to trade the tails |
| nfl_only | secondary | e549e693e117 | Scope control: NFL only |
| constrained | secondary | ff363c8ac08d | Same as primary but bankroll caps enforced (`apply_caps: true`) for sizing realism |

Every other hypothesis is evaluated after the fact with `harness replay --file`, which registers a `replay`-tier variant and never touches the live rows.

## Provenance

- Source commit of the YAML files: 8d5feea (main, 2026-09-07; YAML files unchanged since 225976f)
- Registered on the NAS: 2026-09-07 ~04:00 UTC, `make deploy-nas` output (main @ 8d5feea):

```
name                    tier        variant_id    active
constrained             secondary   ff363c8ac08d  True
nfl_only                secondary   e549e693e117  True
no_velocity             secondary   64ba3ef09642  True
sharp_direct            primary     f259ca109084  True
sharp_plus_derived      secondary   49af716f8708  True
wide_band               secondary   c2bc45377328  True
```

- Ids first computed on the dev database at commit acecbfa on 2026-09-07; the NAS table must match exactly.

## Reporting rules (from spec §6.7)

Weeks 1–2 explore; week 3 confirms only what was selected in week 2. Benjamini–Hochberg at 10% across cells, empirical-Bayes shrinkage of cell means toward the grand mean, cells with n < 30 greyed.

## Amendment 2026-09-07 (measurement fix, variant ids unchanged)

The first hours of live pricing produced zero candidates: every direct fair value was labelled
`not_stale = false` with staleness 138-353 s while the books were a median 12 s old at fetch.
Cause: the tick priced with its start-time clock, and the book-line loader's `fetched_at <= now`
bound excluded the odds fetched seconds later in the same tick, so pricing used the previous
fetch (2-5 min old). Fixed on main the same day by reading the clock at pricing time. No variant
config changed, so the six ids above stand; signals before the fix carry the wrong label and
must be excluded from Week 1 reporting (or re-scored with `harness replay`).

## Amendment numbering (added 2026-09-07, append-only)

The "Amendment 2026-09-07 (measurement fix)" section above is Amendment 1. Amendment 2 is the staleness allowance of phase 3 Task 1 (`feed_kind`, `feed_lag_s`, `stale_allowance_s`) together with the frozen executor settings; it is appended when Task 1 deploys, with the deploy time, sha, run-id range, tables and criteria touched, and the re-scoring command. Amendment 3 is the NO-side registration (placeholder below). The six ids in the table above were registered before the first paper order, which is what spec §6.7 intends; the "before the Week 2 kickoff" sentence at the top of this record described the phase 2 deadline, not the freeze.

## Amendment protocol (added 2026-09-07; review F31)

1. A measurement amendment (a label's semantics, a staleness allowance, a benchmark definition, a bug fix that changes a label) records: the date and deploy sha, the run-id range affected, the report tables and gate criteria it touches, and the re-scoring command (`harness replay --from-run A --to-run B --variant <name>`, with `--execute` once phase 3 Task 13 is deployed). Variant ids are unchanged. The next report excludes or re-scores the pre-fix range and says which. Amendment 1 above is the template.
2. A strategy change (NO-side, key numbers, the price band, the adverse-selection seed, executor settings) is a new variant id or a new hashed `orders.config_hash`, never an edit of a registered config. The six ids are frozen through ISO weeks 37, 38 and 39.
3. Anything registered after Monday 2026-09-21 09:00 CT is exploratory for this dataset, cannot be confirmed in week 3, and is labelled "post-registration, exploratory" wherever it appears.
4. Gate criteria, thresholds, benchmark and BH families, the cell grid, success thresholds and the confirmation cut-off are invariants of the loop (ruling R1): only a dated user decision changes them.
5. A hotfix that changes a label's semantics or an executor setting is an amendment under item 1 and a journal ruling, not only a carried fix. The verification contract's "candidates > 0" expectation is a standing incentive to loosen thresholds; this protocol is the counterweight.

## Analysis plan (added 2026-09-07; rulings R13, F14, F16, F43)

- Unit of analysis: every cell reports `n_obs` and `n_clusters` (games); a cell is greyed below 10 game clusters and flagged below 30; "cells with n < 30" in the Reporting rules above means fewer than 30 games. Confidence intervals are cluster-robust by game with `SE^2 = (G/(G-1)) x sum_g (sum_{i in g} (x_i - xbar))^2 / N^2` and Student t critical values with G - 1 degrees of freedom; a t statistic is `xbar / SE`.
- Sign convention: the code stores `gap_mid = fair - mid` (`harness/pricing/gaps.py`); a positive value means the venue is cheap relative to the sharp fair. Spec table 4's "venue mid minus sharp fair" is the negative of the stored column; every table prints the convention in its header.
- Grid (table 4): price buckets {20-35, 35-50, 50-65, 65-80} x time-to-kickoff {> 24 h, 3-24 h, < 3 h} x sport {nfl, ncaaf} x market type {moneyline, spread, total}: 72 cells per fair source (direct, derived), with `feed_kind` and a `staleness_s` bucket (< 120, 120-300, 300-1000, > 1000) as strata; the headline H2 claim is made from `feed_kind = featured` rows. Panels: `gap_mid`, `gap_maker_net` (the exploitable quantity), and `clv_mid_p` versus `pinnacle_t5`.
- Families: (A) the 72 direct cells, Benjamini-Hochberg at q = 0.10 on two-sided p-values from the cluster-robust t; (B) the 72 derived cells, separately; (C) the five pre-registered variant contrasts of table 2 (paired differences against the primary on shared snapshots), Holm. Greyed cells are excluded from the family and the exclusion count is printed.
- Shrinkage: within sport x market type strata, `tau^2 = max(0, var(m_i) - mean(se_i^2))` with `se_i^2` the cluster-robust variance of the cell mean, `B_i = tau^2 / (tau^2 + se_i^2)`, `m_tilde_i = B_i m_i + (1 - B_i) m_bar`. When `tau^2 = 0` the report prints "no heterogeneity detected" and counts no cell as significant on the posterior interval.
- The spec §9.6 criterion "at least three cells whose 90 % CI excludes zero after shrinkage" is judged on the empirical-Bayes posterior interval `m_tilde_i +/- t_{0.95, G-1} x sqrt(B_i se_i^2)` (ruling R13); BH-adjusted intervals on the raw cell means are reported beside it.
- Selection artefact: the week-2 report (Monday 2026-09-21 09:00 CT, ISO week 38) writes `docs/reports/2026-w38-selected.json` listing the cells and contrasts it selects; it is committed before any week-3 data exists. The week-3 report (Monday 2026-09-28) evaluates only that set, on week-3 data only, with the posterior interval.
- Convergence lag (table 5): featured shapes only, expressed as the interval `[max(0, lag - interval), lag]` with the sampling floor as a column; the sharp move time is `fair_values.newest_book_ts`, the venue time is the first WebSocket mid crossing 50 % of the move; negative lags and the censored share are reported with a Kaplan-Meier median.
- Adverse selection floor: the week-3 report prints "insufficient" for H1 below 30 `queue_model` fill events across 10 games rather than a number.

## Calendar (added 2026-09-07; ruling R7)

Week 1 = ISO week 37 (paper orders from the phase 3 deploy through Sunday 2026-09-13); Week 2 = ISO week 38; Week 3 = ISO week 39. `harness report --week N` uses ISO weeks. Monday-night games belong to the following ISO week. Week-2 selection is frozen Monday 2026-09-21 09:00 CT; the week-3 confirmation report lands Monday 2026-09-28 09:00 CT.

## Correction (added 2026-09-07; review F17)

The table above describes `no_velocity` as the "mandatory no-veto control". That is wrong: `no_velocity` is the sharp-line velocity-filter control (`velocity_max_pts: 1.0` disables the filter). While the veto is shadow-only, the spec §6.7 no-veto control is the primary itself (identical signals); a live `no_veto` secondary is meaningful only under enforcement, which is a user gate and would replace a secondary by dated amendment (ruling R2). The variant id and its YAML are unchanged.

## Amendment 3 (NO-side), placeholder (added 2026-09-07; user decision U2)

To be filled when phase 3 Task 4b deploys, before 2026-09-16: date and deploy sha; the new secondary `sharp_two_sided` (`sides: [yes, no]`) and its variant id; `MAX_SECONDARY` raised from 5 to 6 under U2; the signal-replay counts by side over the recorded tape that preceded registration; the executed variant list; the statement that the six ids above are unchanged and that their signals are YES-only for the whole dataset; after Task 13 deploys, the `harness replay --execute` order and fill counts for the NO side.

## Amendment 3 placeholder, addition (added 2026-09-07, fix round 1; controller ruling D1)

From amendment 3 onward the go-live gate property reads the `sharp_two_sided` variant's `gate_reports` row (`gate_variant = true`), because U2's purpose is that the gate counts fills on the side retail flow hits; the primary's YES-only row is stored and reported beside it every week. Amendment 3 records the first evaluation date on which the gate row switched. Cost if wrong: a gate judged on a variant registered a week after the six ids; reversal: a dated user decision naming the primary as the gate variant.

## Amendment 3 placeholder, second addition (added 2026-09-07, fix round 2; user decision U5)

User decision U5 (2026-09-07) supersedes the controller inference in the previous addition: from amendment 3 onward the go-live gate is judged on `sharp_two_sided`, and the primary's row is stored and reported beside it. The switch is data-driven and gap-free: `Settings.gate_variant` defaults to `sharp_direct`, the gate row falls back to the active primary while the named variant is not registered (exactly one row marked at every evaluation), and the controller flips the setting to `sharp_two_sided` at the Task 4b deploy. Amendment 3 records the first evaluation date on which the gate row switched, citing U5.
