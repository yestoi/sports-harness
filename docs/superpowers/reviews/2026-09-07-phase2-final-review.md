# Phase 2 final whole-branch review

Branch `phase2-pricing-signals`, base main @ be52929, HEAD 2abfbab.
Suite: `pgrep -f pytest` empty, then 241 passed, exit 0, output pristine.

## Verdict: FIX-THEN-MERGE

Four defects, all small and local. None is architectural. Units, sign conventions,
and the margin model are correct end to end; secrets are clean; no live-order path
exists anywhere in `harness/venues/`. The two blocking items both damage the dataset
the phase exists to produce, so they should land before merge.

---

## Findings, by severity

### 1. HIGH — a replay variant joins the live set forever

`harness/strategy/pipeline.py:135` calls `active_variants(session)`, and
`harness/strategy/variants.py:213-219` filters on `active` only, not on tier.
`harness/replay.py:53` registers a `--file` variant with tier `replay` and
`active=True` (`register_variants` sets `active=True` on every row it writes).
Pruning is deliberately limited to `LIVE_TIERS` (`variants.py:202`), so the row is
never retired.

Reproduced against the test DB: after registering the six committed YAMLs and then
one `--file` replay variant, `active_variants` returns seven, including
`('adhoc_experiment', 'replay')`.

Failure scenario: an operator runs `harness replay --file experiment.yaml` on
Saturday morning. Every tick from then on scores that post-hoc variant against live
gaps and writes `signals` rows with `replay=False`. The live variant set is now
1 primary + 6 secondaries, the pre-registration record in
`docs/superpowers/reviews/2026-09-07-phase2-preregistration.md` is false, and the
Benjamini-Hochberg correction in §6.7 is computed over the wrong number of cells.

Minimal fix: in `active_variants`, add `.where(StrategyVariant.tier.in_(LIVE_TIERS))`.

### 2. HIGH — `price-once --run-id <old>` prices a past run with today's books

`harness/cli.py:163` passes `datetime.now(timezone.utc)` as `now` regardless of which
run id was supplied. Two things then go wrong together:

- `harness/pricing/fair.py:36-48` selects candidate games from `[now-4h, now+8d]`, so
  an old run gets fair values for *today's* games.
- `harness/pricing/lines.py:43` bounds odds snapshots below only
  (`fetched_at >= since`); there is no `fetched_at <= now`. Even with a corrected
  `now`, the loader still returns rows newer than the run.

Failure scenario: a tick fails pricing at 13:00 and the operator backfills with
`harness price-once --run-id 8421` at 15:30. `fair_values` rows are written under run
8421 stamped `created_at=15:30` and derived from 15:30 books; `build_gap_snapshots`
joins them to that run's 13:00 quotes. The H2 mispricing map is then fitted on gaps
between a 15:30 fair and a 13:00 venue price. Nothing in the row marks it.

Minimal fix: in `price_once`, load the `Run` and use `run.started_at` as `now`
(error out if the run is missing), and add
`OddsSnapshot.fetched_at <= now` to the `latest_book_lines` WHERE clause. The second
half also removes the latent look-ahead from every other caller.

### 3. MEDIUM — variant truncation under budget is systematic, not random

`pipeline.py:141-144` breaks out of the variant loop when `budget_s` (default 20 s) is
spent, and `active_variants` orders by name (`variants.py:215`). On a busy NCAAF
Saturday the same alphabetically-late variants lose rows every time: `sharp_direct`,
`sharp_plus_derived` and `wide_band` are dropped while `constrained` and `nfl_only`
are always complete. Cross-variant comparisons then rest on non-random missingness
that no column records.

Minimal fix: rotate the variant order by `run_id % len(variants)`, and record the
variants actually scored in the returned dict so the gap is visible in run notes.

### 4. MEDIUM — two filter labels can never be False

- `run.py:173`: `disagreement_ok = row.disagreement is not None`. `consensus()`
  returns `Decimal("0.0000")` for a single group (`consensus.py:49`), and `fair.py`
  always writes that value, so the label is exactly equivalent to `has_fair` and can
  never reject on its own. Worse, a Pinnacle-only fair scores `disagreement = 0` and
  therefore gets `edge_min = edge_floor = 0.02`, the *loosest* threshold, precisely
  when there is no corroborating book. `GapRow` has no `n_groups` field, so the
  strategy cannot even tell one group from two — `market_gap_snapshots.n_groups` is
  stored (`gaps.py:170`) and then dropped at `pipeline.py:35-58`.
- `run.py:234`: `cap_per_bet = stake <= per_bet_cap * bankroll`, but `stake` was
  already clamped to that ceiling at `run.py:198`. Always True. Spec §9.6 wants the
  caps recorded so replay can measure what they cost; this one measures nothing.

Minimal fix: add `n_groups` to `GapRow` and to `_load_gap_rows`, set
`disagreement_ok = row.n_groups >= 2`; compute the uncapped stake and label
`cap_per_bet` against that before clamping.

### 5. LOW — no-fair rows carry no reason

`fair.py:192`, `fair.py:201` and `fair.py:216` all drop a shape with no `FairValue`
row; the resulting gap snapshot has `fair_p`, `fair_source`, `staleness_s` all NULL.
"No Pinnacle main line", "unmapped market type", "game outside the window", and
"pricing raised for this game" are indistinguishable in the record. Spec §9.6 asks
for an explicit labelled reason. The aggregate `no_sharp` count in run notes also
conflates all three. Cheap fix: a `no_fair_reason` string column on
`market_gap_snapshots`. Reasonable to defer to phase 3.

### 6. LOW — live caps are per-tick, replay caps are per-range

`price_and_signal` never passes `state`, so `run_strategy` builds a fresh
`StrategyState` every tick: for the `constrained` variant, `daily_cap: 0.15` and
`max_open: 25` are enforced within one tick only. `replay.py:94` carries one state
across the whole run range, so a three-week replay exhausts `max_open` after 25
candidates ever. Neither is "per day" as §6.5 specifies, and live and replay disagree.
Real exposure is unknowable until orders exist in phase 3; the honest fix now is to
say so in `constrained.yaml`'s comment and in the spec.

### 7. LOW / informational

- **Fee basis.** `run.py:186-188` prices the fee at a 100-contract reference (as the
  plan specifies). Actual per-contract fee at 12 contracts is 0.0050 versus the
  assumed 0.0044, so recorded `edge` overstates by up to ~0.6 probability points on
  small orders, always in the same direction.
- **Key numbers.** Spec §6.3 promises "key-number mass adjustment at 3 and 7"; the
  plan's Task 5 dropped it and `margin_model.py:60` documents the omission. Amend the
  spec the way `stale_s` was amended, or H4 will assume an adjustment that isn't there.
- **`now` is unused** inside `run_strategy`. Good for reproducibility, but
  `replay.py:98` silently substitutes wall-clock time when a `Run` row is missing,
  which becomes a look-ahead the moment `now` is used.
- **YES side only.** Every signal is `side="yes"`; buying NO is never evaluated.
  Derivable offline from `best_bid`/`best_ask`, and the plan scoped it this way.
- **`_primary_signals`** (`dashboard/app.py:145-147`) uses `scalar_one_or_none()`; two
  active primaries in the DB would 500 the whole page. `load_variants` prevents it at
  load time only.
- **Duplicate quote rows** for one market in one run are silently dropped by
  `gaps.py:187`; which one survives is arbitrary.

---

## End-to-end unit traces

Verified numerically. Margin model round-trips exactly: home −3.5 at 0.5200 cover
gives μ=4.177, and P(home margin > 3.5) returns 0.5200; the away complement returns
0.4800; the ladder is monotone (0.7855, 0.7152, 0.6073, 0.5200, 0.4317, 0.3198).

| Step | Moneyline (team T) | Spread (T, k=3.5) | Total (over, 44.5) |
|---|---|---|---|
| Kalshi contract | YES = T wins | YES = T margin > 3.5 | YES = total > 44.5 |
| Shape | `("moneyline", T, None, None)` | `("spread", T, None, 3.5)` | `("total", None, "over", 44.5)` |
| Book lookup | `ml_pair` h2h T / opp | `spread_pair` T at −3.5, opp at +3.5 | `total_pair` over/under at 44.5 |
| Sign flip | n/a | correct: Kalshi +k.5 ≡ book −k.5 | none needed |
| Devig | `devig([T, opp])[0]` | `devig([T@−3.5, opp@+3.5])[0]` | `devig([over, under])[0]` |
| Consensus | log-odds, Pinnacle 0.65 / BOL 0.35 | same | same |
| Units | Decimal 4dp probability | same | same |
| Derived fallback | `p_moneyline` = P(M>0) | `p_margin_over(±μ, 3.5)` | `p_total_over(44.5)` |
| Venue side | `yes_bid`/`yes_ask` in dollars | same | same |
| `gap_maker_net` | fair − bid − maker fee | same | same |
| `price_target` | `floor_cents(p0 − fee)` — floors down, correct for a maker bid | same | same |
| Edge sign | positive = we profit | same | same |

Worked moneyline: fair 0.6000, bid 0.5300, ask 0.5600 → edge_min 0.0200,
p0 = 0.5700, price_target 0.5600, fee 0.0044, edge 0.0356, stake $44.07,
78 contracts. `decision=rejected`, reason `edge`, because §6.3 requires
`price_target < best_ask` and 0.56 is not < 0.56. Correct.

Every step is consistent. No unit or sign defect found at any module boundary.

---

## Early-exit inventory

| Site | Exit | Recorded? |
|---|---|---|
| `fair.py:32` | empty explicit `game_ids` | caller-supplied, n/a |
| `fair.py:36-48` | game outside `[now-4h, now+8d]` or unmatched | gap row still written, `has_fair=False` |
| `fair.py:67` | `continue` on unknown market_type | gap row with `fair_source=None` ✓ |
| `fair.py:137,141` | no matched markets / no shapes | no gap rows either, consistent ✓ |
| `fair.py:162` | `continue` when no direct pair | falls through to derived ✓ |
| `fair.py:192,201` | no margin model | **no reason recorded** — finding 5 |
| `fair.py:216` | total with no `mu_total` | **no reason recorded** — finding 5 |
| `fair.py:265` | per-game exception, savepoint rollback | count in `notes.pricing.fair_errors` ✓ |
| `gaps.py:67` | no quotes for the run | `gaps: 0` in notes only |
| `gaps.py:61-65` | unmatched market, or kickoff > 4h ago | dropped, unlabelled |
| `gaps.py:187` | duplicate `(run_id, venue_market_id)` | silent |
| `run.py` | **none** — every row yields exactly one SignalRow ✓ | |
| `pipeline.py:125,131` | budget spent between stages | `budget_exhausted: True` ✓ |
| `pipeline.py:136` | no active variants | empty `signals` dict |
| `pipeline.py:142` | budget spent mid-loop | flagged, but **which** variants ran is not — finding 3 |
| `tick.py:303` | `summaries` empty | `notes.pricing` absent, documented |
| `tick.py:326` | pricing raised → rollback | warning + degraded status ✓ |
| `replay.py:95` | run has no gap snapshots | skipped, documented ✓ |
| `replay.py:97` | missing `Run` row | wall-clock substituted silently |

---

## Areas checked and clean

**Idempotency.** `_insert_fair_value` (`fair.py:74`), `build_gap_snapshots`
(`gaps.py:187`) and `_insert_signals` (`pipeline.py:96`) all use
`on_conflict_do_nothing` against real unique constraints — `uq_fair_value_row`
(`schema.py:51`, the functional index with the coalesces), `uq_gap_run_market`,
`uq_signal_key` (which includes `replay`). Re-running `price_and_signal` for the same
run id inserts nothing new. `direct_results[shape]` is populated regardless of insert
count (`fair.py:184`), so the derived fallback still behaves correctly on a re-run.

**Transactions.** `compute_fair_values` wraps each game in `session.begin_nested()`
and commits once at the end; a poisoned game rolls back to its savepoint and is
counted. The tick's raw rows are checkpointed before pricing (`tick.py:307`), pricing
failure rolls back and downgrades the run rather than killing it (`tick.py:326-329`),
and `finish_run` still writes. Stage-level commits mean a mid-pipeline failure leaves
a partial but consistent record that the next run resumes.

**Time.** One clock: `tick.py:286` takes `self.clock()` (tz-aware UTC) and threads it
to `compute_fair_values`, `build_gap_snapshots` and `_insert_signals`. Every compared
timestamp is timezone-aware. `stale_s: 180` in all six YAMLs matches the amended spec
§6.2. `velocity_max_pts: 0.02` is in probability units and matches `run.py:155`, which
compares a probability delta — the `_pts` suffix is misleading but the numbers agree.
Nothing validates the range, so a future edit to `2` would silently disable the filter;
a bounds check in `_validate` would be cheap. Quiet hours and TTK derive from
`now.astimezone(America/Chicago)` (`gaps.py:132`).

**Secrets.** The Odds API key travels as a query param but `HttpClient._redacted_url`
(`feeds/http.py:34-40`) rewrites it before `FetchResult.url` is stored, and
`FetchError` is built from the bare base URL with no params (`http.py:51`), so no
exception `repr` in `runs.notes` can carry it. `store_raw` receives an explicit
`params_json` that never contains the key. `.gitignore` excludes `secrets/*` except
`.gitkeep`, and `git ls-files secrets` confirms only `.gitkeep` is tracked. The
dashboard token is generated on the NAS with `openssl rand -hex 32 >` redirected to
the file (`Makefile:18`) and never echoed. `/healthz` returns no notes. The template
has no `|safe`, so Jinja autoescaping covers the free-text `/kill` reason. Compose
binds `127.0.0.1:${SERVE_PORT}` (`docker-compose.yml:44`).

**No live orders.** No `create_order` / `place_order` / portfolio call exists in
`harness/venues/`. The kill switch is currently inert — nothing outside
`dashboard/app.py` reads `KillSwitch` — which is correct for a phase with no execution,
but it must become a gate in phase 3.

**Replay reproducibility.** `replay` reuses `_load_gap_rows` verbatim, reads only
stored `market_gap_snapshots`, and `run_strategy` derives nothing from `now`
(staleness, TTK and velocity are all precomputed columns). Given the same gap rows,
replay reproduces live decisions exactly.

**Triage rulings.** All hold in code: `newest_ts` from group members only
(`consensus.py:41`); `markets=` kwarg on both pair helpers with `alternate_totals`
included by default (`lines.py:11`); one constant `KALSHI_FOOTBALL` everywhere;
retirement by `name[:51]#id` + `active=False` with pruning limited to
primary/secondary; `stale_s` 180 amended in the spec; velocity from the previous
snapshot within 15 min (`gaps.py:78`); one `StrategyState` per `replay()` call;
`--file` name must match `--variant` (`replay.py:44`); the token is NAS-generated and
never printed. Nothing in the code contradicts a ruling.

**Cross-task drift.** `fair._shapes_for_game` and `gaps._shape_for_market` are
duplicated but identical, and the `Decimal` threshold keys compare and hash by value
across both. `LABEL_ORDER` adds `match_confidence` beyond the plan's list, which spec
§6.4 requires and the code comments (`run.py:49`). `run_strategy` applies caps in
edge-descending order rather than the plan's "row order"; the docstring explains it
and it is the better rule, but it changes which rows claim the bankroll, so it is
worth recording as an intentional deviation.
