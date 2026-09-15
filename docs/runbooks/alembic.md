# Alembic runbook

The SQLAlchemy models and `create_schema` are the schema authority. Alembic records additive
history. A migration never changes a view or an existing index, and DROP, `ALTER COLUMN ... TYPE`
and a non-concurrent index on a bulk table are **gates**, never written by the loop.

## Normal deploy

`make deploy-nas` runs, in order: push, build, `up -d postgres app-backup`, `backup-precheck`,
`harness migrate ensure`, `init-db`, `up -d` the rest. `ensure` picks one of three branches and
prints which: `stamped` (a populated pre-Alembic database: head is stamped and the baseline is
never executed), `upgraded` (an empty database), `current` (upgrade from the stored revision).

`alembic.ini` and `migrations/` ship in the image through two separate `COPY` lines and are
pushed to the NAS by the same `tar` that carries `harness/`. There is no `alembic` binary on the
path anywhere: `harness/db/migrate.py` builds the `Config` in code, so every operation is a
`harness migrate ...` subcommand.

    harness migrate ensure     # the deploy's guarded one-time stamp; prints its branch
    harness migrate current    # the recorded revision, or `none`
    harness migrate upgrade    # apply what is unapplied
    harness migrate stamp      # record head without executing it

## Revisions

| Revision | Follows | Adds | `downgrade()` |
|---|---|---|---|
| `0004_phase5` | `0003_brin_autosummarize` | the research layer's ten tables (`futures_snapshots`, `weather_points`, `weather_snapshots`, `veto_queue`, `research_notes`, `veto_decisions`, `research_spend`, `report_annotations`, `rfqs`, `rfq_quotes`) and the `veto_h9` view | `pass` (additive only, roadmap invariant 5; see the module docstring for why rolling one of these back is never a schema operation) |
| `0005_rfq_lookup` | `0004_phase5` | `ix_fair_leg_lookup` on `fair_values (game_id, market_type, coalesce(outcome_team_id, -1), coalesce(outcome_side, ''), coalesce(threshold, -9999), created_at desc) where fair_source = 'direct'`, built CONCURRENTLY — the covering index the RFQ quote's `_LEG` lookup and `rfq_grade`'s `_CLOSING_LEG` lateral both read (fix 35, journal 109's incident; the `coalesce(...)` columns are a round 1 correction, review Important 1 — the bare columns compared with `is not distinct from` were never chosen by the planner; see `docs/runbooks/research.md`'s "Fix 35: cheap quotes and the quote rate limit") | `pass` (additive only, roadmap invariant 5) |
| `0006_quotes_run_index` | `0005_rfq_lookup` | `ix_quotes_run_market` on `venue_quotes (run_id, venue_market_id)`, built CONCURRENTLY — the index the pricing read (`build_gap_snapshots` in `harness/pricing/gaps.py`) rides to fetch a run's quotes. Fix 42: from 13:03 CT on 2026-09-11 no index on this 3.58 M row bulk table led with `run_id`, so the planner walked `ix_quotes_market_fetched` once per matched market with `run_id` as a filter, past the 30 s statement timeout on every run — `runs.status = degraded` and no fair values. `if not exists` matters: the controller built the index by hand on the NAS at 14:15 CT, so the revision and `init-db` both find it present | `pass` (additive only, roadmap invariant 5) |
| `0007_raw_events_lookup` | `0006_quotes_run_index` | `ix_raw_source_endpoint_id` on `raw_responses (source, endpoint, id)`, built through the partitioned recipe (parent metadata-only, each partition CONCURRENTLY, then attach — `harness/db/schema.py`'s `_ensure_partitioned_concurrent_indexes`, since Postgres 16 refuses `create index concurrently` on a partitioned parent). Fix 45: `_load_events_cache` (`harness/normalize/runner.py`) walked the primary key backwards across every partition of this 2.2 GB+/week table with its predicates only a filter, past the 30 s statement timeout on the NAS at 23:23/23:26/23:27 CT on 2026-09-11 and 00:06 CT on 2026-09-12 — `runs.status = degraded`, no new games or markets from Kalshi events that tick | drops the parent index, which drops every attached partition child with it (not `pass`: see the module docstring) |
| `0008_positions_open_fill` | `0007_raw_events_lookup` | re-issues the `positions` view with `harness/db/schema.py`'s `OPEN_FILL_SQL` predicate: a fill is open while its order is not `settled` **and** no `ledger` row with `kind = 'settlement'` exists for it. Carried fix 56 (second row): order 157 filled 38.92 YES, was cancelled `fair_stale`, and its fill was settled for payout 0 on 2026-09-13 — `settle.py`'s `SETTLEABLE` moves only filled/partially_filled orders, so the order stays `cancelled` and the paid fill read as open forever (legacy exposure, the executor's caps through `store.load_positions`, and `open_stake`/`mtm_open`). The deployed database gets the new text from `init-db`; this revision exists so a *migrated* database's catalogue matches a `create_schema` one, since `0001_baseline` holds the view's previous text and is not edited. The “Adding a migration later” rule below still stands — a view's home is `create_schema` — and this one statement is a controller ruling for this fix, not a new pattern the loop may repeat | `pass` (nothing additive to undo; the previous text is in `0001_baseline`, which a code rollback's `init-db` restores through `create_schema`) |
| `0012_phase6d_sustained_eval` | `0011_phase6b_execution` | `ix_intents_created` on `intents (created_at)`, built CONCURRENTLY — the index the 24 h bound of `intents_without_order_or_skip` needs (fix 51, 6D §1.9, D9: the check was the fourth one recording `skip: timeout` on 2026-09-13, because `intents` carried no index on any time column and `ix_intents_key` leads on `variant_id`). The same revision carries 6D's additive tables in its later passes: `coverage_samples` (Task 4) and `opportunity_episodes`/`intent_episodes` (Task 8) with their plain indexes. The number and the parent are D9 applied at merge time (4.6 addendum): the file was written as `0009_phase6d_sustained_eval` on `0008_positions_open_fill`, and when this branch merged `main` it was renumbered `0012_phase6d_sustained_eval` on `0011_phase6b_execution` — the file, its `down_revision` and `HEAD_REVISION` together. The id is `..._eval`, not `..._evaluation`: Alembic creates `alembic_version.version_num` as `varchar(32)` with no option to widen, and the longer spelling is 33 characters, so every upgrade to it aborts with `StringDataRightTruncation` | `pass` (additive only, roadmap invariant 5) |

The stamp moves from `0003_brin_autosummarize` to `0004_phase5`, from `0004_phase5` to
`0005_rfq_lookup`, from `0005_rfq_lookup` to `0006_quotes_run_index`, from
`0006_quotes_run_index` to `0007_raw_events_lookup`, from `0007_raw_events_lookup` to
`0008_positions_open_fill`, and from `0008_positions_open_fill` through
`0009_score_correction`, `0010_phase46_fun_tickets` and `0011_phase6b_execution` to
`0012_phase6d_sustained_eval`, and from there to `0013_nw_executor_version` (the pinned head), only under the
**full** `make deploy-nas` recipe, whose `harness migrate ensure` step is the only place
`upgrade_head` runs. A `make deploy-nas-app` deploy during a phase legitimately leaves
`alembic_version` reading the prior revision: that recipe runs `init-db` and never
`migrate ensure`, and whatever the revision adds is created (or already exists) either way, since
`create_schema` and the migration are additive mirrors of each other.

## Rolling back

1. Confirm no game window is open (verify.md, Game window).
2. `git checkout <previous sha>` on the Mac.
3. `make deploy-nas-app`, never `make deploy-nas`: the app-only recipe skips the migrate step, and
   the full recipe would abort there because `alembic_version` is already stamped ahead of the
   older code's `HEAD_REVISION` (`ensure` raises rather than no-ops on a stamp it does not know).
   The additive schema makes the app-only rollback safe: the new tables and columns are simply
   ignored by the old code. A later *full* deploy on the rolled-back sha needs a hand stamp back
   to that sha's revision first, which is the user's command, never the loop's.
4. `init-db` is not run by the app-only recipe; the old `create_schema` is a subset of the live
   schema anyway.
5. Do **not** run `alembic downgrade`. The baseline's `downgrade()` raises: a rollback here is a
   code rollback, never a schema one. A migration that took an object away would not be
   rollback-safe, which is why writing one is a gate.
6. Verify Layers 1-3 and journal the sha.

## Adding a migration later

- Additive only: `ADD COLUMN IF NOT EXISTS`, `CREATE TABLE IF NOT EXISTS`, `CREATE INDEX IF NOT
  EXISTS`. Add the same statement to `create_schema` and the model in the same commit: the
  models stay the authority and `tests/test_alembic.py` compares the two catalogues.
- An index on `raw_responses`, `orderbook_events`, `venue_trades`, `venue_quotes` or
  `odds_snapshots` goes through `concurrent_index` only.
- Never a view, with one ruled exception. Views live in `create_schema` as
  `CREATE OR REPLACE VIEW`, and that is still where a view's text is changed. The exception
  is a view whose text changes after the baseline: because `0001_baseline` holds the old
  text and `test_a_migrated_database_matches_a_create_schema_database` compares the two
  catalogues, a revision may re-issue the view with one `op.execute` of the same
  `create or replace view ...` statement `create_schema` runs, under a controller ruling and
  with a test that pins the two copies equal — `0008_positions_open_fill` above is the
  precedent, and it is a ruling per fix, not a pattern to reach for. The Alembic op helper
  that replaces a view and `DROP VIEW` stay banned either way (`tests/test_alembic.py`'s
  FORBIDDEN list).
- A `create index concurrently` that aborts mid-build (a lock timeout, a killed deploy) leaves
  an `INVALID` index behind, and `... if not exists` on every later deploy skips it forever
  rather than rebuilding it; verify's `check_results` row for that index goes `skip` instead of
  `pass` when this happens. The remedy is `REINDEX INDEX CONCURRENTLY <name>`.

## Why the baseline looks the way it does

- It creates the partitioned tables as **parents only**. The weekly partitions are runtime
  objects `ensure_partitions` creates, named by date. `tests/test_alembic.py` asserts
  `pg_inherits` is empty after `upgrade_head`.
- It is self-contained: no import of the models or of `create_schema`. Those move on; the
  baseline is the schema of its own commit.
- The migration connection sets `lock_timeout = '5s'` and `statement_timeout = '300s'` before
  anything runs, so a migration that cannot take its lock fails fast instead of queueing the
  WebSocket sink's inserts behind it.
- Postgres 16 cannot build an index CONCURRENTLY on a partitioned parent, so the tape's indexes
  are plain `IF NOT EXISTS` statements in the baseline. That is safe precisely because the
  baseline only ever executes against an empty database: a populated one is stamped instead.
