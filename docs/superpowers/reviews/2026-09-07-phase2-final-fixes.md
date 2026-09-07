# Phase 2 final fix wave

Branch `phase2-pricing-signals`. Applies the six findings from
`final-review-report.md` (verdict: FIX-THEN-MERGE). Suite: `pgrep -f pytest` empty
before every run; full suite green, exit 0, output pristine, on every run below.

---

## F1 (HIGH) — a replay variant joins the live set forever

**Changed:** `harness/strategy/variants.py`, `active_variants()` now filters on
`StrategyVariant.tier.in_(LIVE_TIERS)` in addition to `active`, matching the tier
list pruning already uses. A `replay` row registered via `register_variants(...,
prune=False)` stays active in the table (so `harness replay --file` can reuse it
across a range) but never appears in the live set the pipeline scores.

**Test:** `tests/test_variants.py::test_registering_a_replay_variant_without_pruning_leaves_the_live_set_alone`
registers a replay-tier variant with `prune=False` and asserts it is absent from
`active_variants()` while the live rows (`primary`, `secondary`) remain, and
separately confirms the replay row itself is still `active=True` in the table.
`test_pruning_never_touches_a_replay_tier_row` updated the same way.

**Deviation:** none.

---

## F2 (HIGH) — `price-once --run-id <old>` prices a past run with today's books

**Changed:**
- `harness/cli.py`, `price_once`: when `--run-id` is given, the `Run` row is loaded
  and its `started_at` is used as `now` (erroring out via `typer.Exit(1)` if the run
  doesn't exist). When no `--run-id` is given, the latest run is still chosen
  automatically (unchanged) and `now` stays wall-clock, since that run is
  effectively live.
- `harness/pricing/lines.py`, `latest_book_lines`: added `OddsSnapshot.fetched_at <=
  now` alongside the existing lookback floor, so no caller can see a book fetched
  after its `now`, regardless of which `now` it passes.

**Test:**
- `tests/test_cli.py` (new file): `test_price_once_with_run_id_uses_that_runs_started_at_not_wall_clock`
  monkeypatches `harness.strategy.pipeline.price_and_signal` to capture the `now`
  argument the CLI passes, and confirms it equals the run's `started_at`, not
  wall-clock time. `test_price_once_with_unknown_run_id_errors_without_pricing`
  confirms a missing run exits 1 without calling pricing.
  `test_price_once_without_run_id_uses_wall_clock_for_the_latest_run` confirms the
  unchanged auto-selection path.
- `tests/test_lines.py::test_snapshot_fetched_after_now_is_excluded_even_within_lookback`
  seeds snapshots at `t0` and `t0+1h`, prices with `now=t0+30min` and a lookback wide
  enough to otherwise include both, and asserts only the `t0` row is used.

**Deviation:** none.

---

## F3 (MEDIUM) — variant truncation under budget is systematic, not random

**Changed:** `harness/strategy/pipeline.py`, `price_and_signal`: the variant list
(already sorted by name) is rotated by `run_id % len(variants)` before scoring, so a
budget cutoff drops a different tail each run instead of always the same
alphabetically-late variants. The result dict now carries `variants_run` (names
actually scored, in order) and `variants_skipped` (names dropped by the budget), so
the gap is visible in run notes rather than only inferable from a boolean.

**Test:** `tests/test_pipeline.py::test_variant_order_rotates_by_run_id_and_the_skip_is_recorded`
registers two variants (`tests/fixtures/variants_rotation/`, new fixture: `tiny`
primary + `tiny2` secondary) and monkeypatches `time.monotonic` to a deterministic
counter so a `budget_s` of 4 reliably allows exactly one variant per run. Run id 1001
(`% 2 == 1`) scores `tiny2` first; run id 1002 (`% 2 == 0`) scores `tiny` first —
`variants_run` differs between the two, and `variants_skipped` names the other.

**Deviation:** none.

---

## F4 (MEDIUM) — two filter labels can never be False

**Changed:** `harness/strategy/run.py`:
- `GapRow` gained `n_groups: int`, populated in `harness/strategy/pipeline.py`'s
  `_load_gap_rows` from `snap.n_groups` (a column `market_gap_snapshots` already
  had). `replay.py` shares this loader, so replay picked it up with no separate
  change.
- `disagreement_ok` is now `row.n_groups >= 2` instead of `row.disagreement is not
  None` — `consensus()` always writes `0.0000` (never `None`) for a single book
  group, so the old check was exactly equivalent to `has_fair`.
- Rows with `n_groups < 2` price against `edge_ceiling` (the strictest threshold)
  instead of running the disagreement-scaled floor formula, which would otherwise
  land at `edge_floor` — the loosest threshold — precisely when there's no
  corroborating book.
- `_Draft` gained `uncapped_stake`; `cap_per_bet` is now labelled against it instead
  of against the already-clamped `stake`, so it can be `False`. The recorded
  `SignalRow.stake` is unchanged — still clamped to the per-bet cap.

**Test:** updated `tests/test_strategy.py`:
- `gap_row()` fixture now defaults `n_groups=2` (matching its existing
  `disagreement=0.0100` default).
- The `no_disagreement` case in `test_sport_scope_ttk_spread_and_volume_labels` now
  passes `n_groups=1` (a `disagreement=None` alone no longer trips the label).
- `test_the_per_bet_cap_clamps_a_large_kelly_stake` updated: `cap_per_bet` is now
  `False` for the large-stake row (decision stays `candidate` since `sharp_direct`
  has `apply_caps: false`).
- New `test_the_per_bet_cap_rejects_when_the_variant_enforces_it` (constrained
  variant, `apply_caps: true`): same large-stake row now rejects with
  `rejection_reason == "cap_per_bet"`.
- New `test_a_single_group_fair_uses_the_edge_ceiling_not_the_floor`: `n_groups=1`
  and `n_groups=0` both price at `edge_ceiling` and fail `disagreement_ok`.

Verified by hand that no other existing cap-label test's fair values push the
uncapped Kelly stake past the `constrained` variant's per-bet cap (checked
numerically for every `fair_p` used against that variant), so the changed
`cap_per_bet` semantics don't flip any of them.

**Deviation:** none. `LABEL_ORDER` unchanged (17 entries, same order).

---

## F5 (LOW) — no-fair rows carry no reason

**Changed:** No `FairValue` row is ever created for a shape with no fair value
(`fair_p` is `NOT NULL` on that table), so the reason is recorded on
`market_gap_snapshots` instead, per the review's own suggested cheap fix:
- `harness/db/models.py`: `MarketGapSnapshot.no_fair_reason: str | None` (new
  column, `String(32)`; created automatically by `Base.metadata.create_all` — no
  manual DDL in `harness/db/schema.py` references this table's columns, so no
  schema.py change was needed).
- `harness/pricing/fair.py`: `FairCounts` gained `errored_game_ids: frozenset[int]`,
  populated by `compute_fair_values` from the game ids whose per-game
  `session.begin_nested()` block raised and rolled back.
- `harness/pricing/gaps.py`: `build_gap_snapshots` takes a new
  `errored_game_ids: frozenset[int] = frozenset()` parameter and, for any row with
  no fair value, sets `no_fair_reason` to `"unmapped_market_type"` (the venue
  market's shape isn't moneyline/spread/total), `"pricing_error"` (the game is in
  `errored_game_ids`), or `"no_sharp_line"` (recognized shape, game didn't error,
  just no Pinnacle-backed line to price from) — checked in that order.
- `harness/strategy/pipeline.py`: `price_and_signal` passes
  `fair_counts.errored_game_ids` through to `build_gap_snapshots`.

**Test:** `tests/test_gaps.py`:
- Extended `test_build_gap_snapshots` to assert the existing "draw" market's row has
  `no_fair_reason == "unmapped_market_type"` and every other row's is `None`.
- New `test_no_fair_reason_is_no_sharp_line_when_there_is_no_pinnacle_backed_line`:
  a moneyline market quoted only by a non-sharp book (`draftkings`, not in
  `SHARP_BOOKS`) with no spreads data to derive a margin model from → `no_sharp_line`.
- New `test_no_fair_reason_is_pricing_error_when_the_game_raised`: monkeypatches
  `MarginModel.from_main_lines` to always raise, confirms `errored_game_ids ==
  {game.id}`, and that the game's markets (moneyline included) get
  `"pricing_error"` while the same game's unmapped "draw" market still gets
  `"unmapped_market_type"` (order-of-checks matters when both conditions could
  otherwise apply).

**Deviation:** the review suggested attaching the reason to `FairValue`'s
`model_json`/notes column "if there is one" — there isn't one usable here, because
no `FairValue` row exists at all for a shape with no fair value (`fair_p` is
non-nullable). Used the review's own fallback (a reason column on
`market_gap_snapshots`) instead, and additionally distinguished `pricing_error`
from `no_sharp_line` (the review's early-exit inventory treats fair.py:265's
per-game exception as separately "counted... ✓" already, but rows arising from it
were still indistinguishable from an ordinary missing-line row without this).

---

## Guard — two active primaries must not 500 the dashboard

**Changed:** `harness/dashboard/app.py`, `_primary_signals`: replaced
`scalar_one_or_none()` (which raises `MultipleResultsFound` given two active
`primary` rows) with `order_by(StrategyVariant.variant_id).limit(1)`, picking the
lowest `variant_id` deterministically.

**Test:** `tests/test_dashboard.py::test_page_and_summary_survive_two_active_primary_variants`
seeds a second active `primary` `StrategyVariant` row alongside the fixture's
existing one and asserts both `/` and `/api/summary` return 200.

**Deviation:** none.

---

## Commits

1. `fix: scope live variants to live tiers and bound pricing to the run's clock` — F1, F2
2. `fix: rotate variant order and label disagreement/cap honestly` — F3, F4
3. `fix: record no-fair reasons, tolerate duplicate primaries` — F5, guard

Each ends with the required `Co-Authored-By` / `Claude-Session` trailer.
