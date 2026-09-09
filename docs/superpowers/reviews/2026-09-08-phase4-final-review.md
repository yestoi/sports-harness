# Phase 4 final whole-branch review

Branch `phase4-kalshi-authed`, 51 commits `c8b4178..c51c125` (+ `036ee2d`, a state doc only).
Reviewed 2026-09-08 by the final reviewer. Read-only on the checkout; the tree, index, HEAD and
branch state are unchanged (`git status --short` empty at the end).

**Verdict: FIX WAVE NEEDED — one Critical, three Important. The paper fence itself is sound.**

Nothing on this branch can send an order, quote or cancel to the production venue, and the paper
executor's behaviour is unchanged. The Critical is a deploy blocker, not a posture problem: the
documented deploy order cannot complete tonight because `backup-precheck` queries a table that
the same recipe creates two steps later.

## Evidence I ran

- `make test` on this worktree against `localhost:5433`: **1724 passed in 191 s**, no warnings,
  no tracebacks. `pgrep -f pytest` was empty before the run.
- Reproduced the Critical against a scratch database on `localhost:5433`: `harness backup-precheck`
  exits 1 with `psycopg.errors.UndefinedTable: relation "backup_runs" does not exist`.
- Ran every phase 4 statement in `verify.md` (Layer 2 block and the five Layer 2b invariants)
  against a database built by `create_schema`: all parse and execute. `ix_fair_created_brin`,
  `ix_equity_variant_ts` and `ix_venue_requests_ts` all exist after `create_schema`.
- Scratch databases dropped afterwards.

### Areas of the diff read

Makefile, Dockerfile, docker-compose.yml, .dockerignore, .gitignore, pyproject.toml,
constraints.txt, alembic.ini; `deploy/backup/{loop,dump,drill}.sh`, `deploy/nas.env`;
`harness/venues/kalshi/{http,authed,smoke}.py`; `harness/execution/{loop,gateway,venue,risk,store}.py`;
`harness/ops/{backup,checks}.py`, `harness/ops/agefmt.py` (interface and callers, not the
cryptography line by line — Task 12's opus review and the 68 CCTV vectors carry that);
`harness/{cli,scheduler,health,replay}.py`, `harness/recorder/tick.py`,
`harness/strategy/{run,pipeline}.py`, `harness/settlement/job.py`, `harness/report/tables.py`,
`harness/db/{schema,migrate,models}.py`; `migrations/env.py` and the baseline's index/parent
sections; `tests/test_compose.py`, `tests/test_exec_loop.py`, `tests/test_strategy.py`,
`tests/test_gateway.py`, `tests/test_backup_scripts.py`, `tests/test_alembic.py` (names and the
load-bearing bodies); `docs/superpowers/autopilot/verify.md`, `docs/runbooks/{backups,alembic,phase0-deploy}.md`.
All seventeen `task-*-review.md` files and the full ledger.

---

## Findings

### Critical

**C1. The first phase 4 deploy aborts at `backup-precheck`: `backup_runs` does not exist yet.**
`Makefile:59`. The recipe's order is push, build, `up -d postgres app-backup`, `backup-precheck`,
`migrate ensure`, `init-db`, `up -d`. `backup_runs` is a new table on this branch (absent at
`c8b4178`, which is what `main` and therefore the NAS runs). It is created by `create_schema`,
which runs in `init-db` — two steps *after* the precheck. `migrate ensure` stamps and never
executes the baseline on a populated database, so it creates nothing either.

Traced through:

1. `docker compose run --rm app-run backup-precheck` → `newest_nightly_ok` selects from
   `backup_runs` → `UndefinedTable` → typer exits 1. Reproduced empirically.
2. The fallback `docker compose exec -T app-backup /backup/dump.sh nightly` writes the dump file
   but its `record_run` insert fails; `deploy/backup/dump.sh:75` swallows that as
   `WARN could not record a backup_runs row` and exits 0.
3. The second `backup-precheck` fails identically.
4. The recipe reaches its third `||` branch, prints `[DEPLOY] ABORT: ...` and exits 1.

Nothing is left broken (only `postgres` and `app-backup` were started), but the deploy cannot
complete as documented, and the message it prints points the operator at disk space and the dump
lock, neither of which is the cause.

`tests/test_backup_scripts.py:181` (`test_the_deploy_recipe_runs_the_precheck_before_the_schema_step`)
pins exactly this ordering, so the suite confirms the recipe matches the spec rather than that the
recipe runs.

**Smallest fix.** Create the schema inside the fallback branch only, before the dump — the happy
path (every deploy after the first, where a fresh nightly row already exists) is untouched:

```make
@ssh $(NAS_USER)@$(NAS_IP) 'cd $(NAS_STACK) && docker compose run --rm app-run backup-precheck || { docker compose run --rm app-run init-db && docker compose exec -T app-backup /backup/dump.sh nightly && docker compose run --rm app-run backup-precheck; } || { echo "[DEPLOY] ABORT: ..."; exit 1; }'
```

Every phase 4 schema change is `CREATE ... IF NOT EXISTS` / `ADD COLUMN IF NOT EXISTS` with no
DROP, RENAME, TRUNCATE or ALTER TYPE (§7, confirmed by grep over the branch), so running it ahead
of the dump on the bootstrap path costs nothing the dump was protecting. Both existing ordering
tests still pass with this edit: `mk.index("app-run init-db")` still falls after
`mk.index("backup-precheck")`, and the recipe still invokes `backup-precheck` exactly twice.

A larger alternative, if the "dump strictly before any DDL" rule is to be held absolutely: give
`backup-precheck` a distinct exit code for "table absent" and branch the recipe on it. Not worth
it — the bootstrap case exists exactly once.

### Important

**I1. Weekly report table 11 was never implemented and never planned.** Spec §3 Tripwire names
"weekly report table 11 'Venue requests by method and env' asserts non-GET on `env = 'prod'` = 0
and is additive (not a gate input, R1)". `harness/report/tables.py` ships tables 1, 2, 3, 4, 4b,
5, 6 and 8; there is no table 11 and no task brief carries it (the only ledger mention is a
passing reference in `task-4-brief.md:24`). The tripwire's teeth are intact — the Layer 2b
invariant at `verify.md:264` and the companion 2-hour prod-GET row are both there and both run —
so this is a missing report surface, not a missing control. **Fix:** either add the table (about
30 lines beside `_T1_STOPPED`, one query grouped by `env, method` over the report window) or
record a dated deviation in the addendum §11 saying the tripwire lives only in verify.md this
phase. Do not leave it as an unremarked gap in a conformance-cited section.

**I2. The Mac half of the restore drill will fail on first use: no tunnel, no database URL.**
`docs/runbooks/backups.md:86-89`. `harness backup-drill-record` writes to the harness database,
which is on the NAS; the runbook gives the command with no instruction to open `make tunnel-nas`
or to point `DATABASE_URL` at the tunnel. `Settings.database_url` on the Mac resolves to whatever
`.env` holds locally, so the command either errors on connect or, worse, writes the drill row
into a local database where the release rule will never see it. Spec §4.3 says the row is written
"over the tunnel"; the runbook lost that. **Fix:** one line before the command —
`make tunnel-nas` in another shell, then
`DATABASE_URL=postgresql+psycopg://harness:harness@localhost:<tunnel port>/harness harness backup-drill-record ...`.

**I3. `deploy/nas.env` never gained `KALSHI_ENV=prod`.** Spec §8: "`.env` gains `KALSHI_ENV=prod`
(informational; writes stay dormant) and nothing else." The file is unchanged on this branch and
the string appears nowhere in the repo outside the spec. Behaviourally inert today —
`Settings.kalshi_env` defaults to `"prod"` and, per the ledger's own Task 8 minor, is read by
nothing yet — but the posture file is the artifact a later reader audits, and §8 is a conformance
citation. **Fix:** append `KALSHI_ENV=prod` to `deploy/nas.env` beside `LIVE_TRADING=0` and
`HARNESS_MODE=paper`, with the same one-line comment those two carry.

### Minor

**M1. `create index concurrently` inside `create_schema` can leave an invalid index that is never
rebuilt.** `harness/db/schema.py:196,420`. The connection is AUTOCOMMIT (correct for CONCURRENTLY)
but carries `lock_timeout = 5s`. If the build aborts on a lock, Postgres leaves an INVALID
`ix_fair_created_brin` behind, and `create index concurrently **if not exists**` on every later
deploy then skips it forever, so fix 16's staleness check silently falls back to a sequential
scan. Detected rather than silent: `verify.md`'s `check_results` row requires
`fair_values_negative_staleness` to be `pass`, not `skip`. Worth one sentence in
`docs/runbooks/alembic.md` on how to drop and rebuild an invalid index if that row ever goes
`skip`. Ship as is.

**M2. `https://evil.com/?x=api.elections.kalshi.com` appears in `harness/venues/kalshi/http.py:12`
and `:60`.** It is a comment explaining why the host check parses rather than suffix-matches, and
it is the right explanation — but the phase audit's outbound-host grep will list `evil.com`
beside the real hosts. Add "in a comment, not a URL" to the audit note, or reword the example to
`https://not-kalshi.example/...`. Ship as is.

**M3. `verify.md:271`'s drawdown band assumes cash cannot go negative.** The invariant fires when
`drawdown_pct < -1`, justified in the comment by "cash cannot go below zero". `cash` is
`bankroll + ledger cash delta` and a variant that lost more than its bankroll would go negative,
making `drawdown_pct < -1` legitimate rather than an integrity anomaly. Unreachable at today's
sizes. Ship as is; if it ever fires, read it as a solvency event before reading it as corruption.

---

## Lens A — Paper fence, end to end

Every path from the executor loop and the recorder to a Kalshi write is fenced, and the fences are
independent rather than layered on one condition.

The transport refuses first: `KalshiTransport.request` (`http.py:168`) raises `PaperModeViolation`
for any method outside `{GET, HEAD}` when `writes_enabled` is false, above all signing, all
network I/O and all recording, and repeats the check against `_write_client is None` as a guard
that survives `python -O`. A paper transport holds no write-capable object at all: `_write_client`
is `None`, and the read client is wrapped in `_ReadOnlyClient`, a facade with `get`, `head` and
`close` and nothing else, so the process cannot even reach `post` by attribute. The recorder path
is fenced separately by construction — `HttpClient` has no write verb
(`test_http_client_has_no_write_methods`) — and `harness/feeds/http.py` is untouched.
`make_writer` (`authed.py:1005`) is the only constructor; the class refuses direct construction
outside it. For `prod` it checks `LIVE_TRADING == 1`, `Settings.mode == "live"`, the **newest**
`gate_reports` evaluation passing for the gate variant, `secrets/legal_decision`, and — added in
Task 8's fix round — the production key files, raising `LiveGuardRefused` naming the first missing
one. For `demo` it requires both demo key files and a host ending in `demo.kalshi.co`, and the
transport re-checks the parsed hostname against the env's allowlist before signing, so a demo
writer cannot reach `api.elections.kalshi.com` and a prod transport cannot reach anything else.
Non-https is refused before a signature exists.

Could any configuration reachable on the NAS today enable a write? No, and it takes more than one
flip. `deploy/nas.env` commits `LIVE_TRADING=0` and `HARNESS_MODE=paper`; both must change, in a
committed file, which is a gate. Even with both flipped, `app-exec` — the only container that runs
the executor — has no volumes and no credential environment at all (`docker-compose.yml`, pinned
by `test_compose_app_exec_block_unchanged` and `test_compose_app_exec_has_no_volumes_and_no_kalshi_env`),
so `has_kalshi_credentials()` is false there and `make_writer` refuses on the fifth condition;
`secrets/legal_decision` resolves relative to `/app` and is never populated; and no passing
`gate_reports` row for the gate variant exists. The demo pair is mounted by no service
(`test_no_service_mounts_the_demo_secrets`) and reaches a container only inside the controller's
own `docker compose run --rm -v ...`, which is the design the smoke and the verify row both
document. The age private key is mounted nowhere (`test_no_service_mounts_the_age_private_key`).

All seven refusal tests named in §12 item 5 exist and pass.

## Lens B — Paper behaviour unchanged

Unchanged, and the evidence is adequate for what each piece claims.

The gateway seam is proven by `test_golden_replay_is_byte_identical_across_the_seam`
(`tests/test_gateway.py:574`), which drives the shipped `Executor` twice over the committed
fixture day — once through a copy of phase 3's two `store` calls, once through `PaperGateway` —
and diffs `orders`, `order_events`, `fills`, `ledger`, the heartbeat counters and the metric
samples, with non-emptiness assertions on each so two empty lists cannot agree. Its docstring is
honest that it proves the seam and not the loop; the loop's own 50 tests in `test_exec_loop.py`
are unmodified (the only edit to that file is one added spy test and the `EXECUTOR_VERSION`
constant, bumped 3.7 → 4.2 as the global constraint requires). The one line of `_place` that
moved is visible in the diff and is a direct substitution.

The drawdown annotation cannot change a candidate set by construction: `ANNOTATION_LABELS` is
subtracted from `FILTER_LABELS` (`harness/strategy/run.py:56`), `CAP_LABELS` is untouched, and
`plan.py` reads only `CAP_LABELS`. `YES_ONLY_DIGEST` is byte-identical on this branch (the diff
touches only its surrounding comment), and
`test_the_golden_digest_is_identical_with_the_annotation_set` runs every registered yes-only id
with `stopped=True` and `stopped=False` and asserts the decision tuples are equal.

`MONEY_FILL_METHODS = ("queue_model", "venue")` widens four reads, and `fill_method = 'venue'`
rows are written only by `Executor._venue_fills`, which is reachable only when
`uses_the_simulator(gateway)` is false, which is only the live gateway. In paper the widened
filters return exactly what the single-value filters returned. The `positions` view is widened by
`create or replace` with an unchanged column list, so nothing that reads it needs to know.

Task 6b's limits read is the one genuine behaviour change to the paper container, and it is the
spec's: `Recorder._read_limits` sets `self.kalshi._sleep_s = page_pause_s(kalshi_sleep_s,
read_refill_rate)`, which can *raise* the recorder's Kalshi page pause above the configured
0.05 s if the venue's read bucket is slower. That is §1.3's rule verbatim, it can never lower the
setting, and it is bounded at 5 s. It does lengthen the fetch phase, which eats into the tick, so
`verify.md`'s Pricing budget row correctly tells the walker to watch `recorder.tick_ms` beside
`budget_capped`. Worth watching on night one; not a defect.

The Task 1 pricing order and budget change is **not on this branch** — commits `2937a7a` and
`a193fd0` are already on `main`, deployed as journal 63 and verified PASS as journal 64, and the
review base `c8b4178` sits on top of them. Its Amendment 4 record
(`docs/superpowers/reviews/2026-09-07-phase2-preregistration.md:117-126`) carries all four
protocol elements with the deploy sha and time, the run-id range 344–4327, table 2 and family C
named, and the per-variant re-scoring command without `--file`.

## Lens C — Cross-task seams

The seams line up. Exception types are consistent and each is caught where it crosses: the
transport raises `VenueTransportError` (deliberately holding only method, path and the cause's
class name, so no `.request` with live signed headers can reach a log), `PaperModeViolation` and
`HostNotAllowed`; the reader raises `KalshiApiError(status, method, path, code)` on any non-2xx;
the writer raises `PriceOffGrid` and `EchoMismatch`; the factory raises `LiveGuardRefused`. The
CLI's `kalshi-smoke` catches the set that can escape the writer build and prints class names
only, never messages that could quote a response body.

`OrderGateway`'s protocol and both implementations agree on every signature, including the
`market` argument on `amend` and the `(session, since, now=None)` shape of `poll_fills`.
`uses_the_simulator` checks the declared `simulates_fills` first and keeps the `isinstance` check
as a belt, which is the right order for a wrapper. `observe_tape` and `on_drawdown_stop` are
called unconditionally from the loop and are no-ops on `PaperGateway`. `may_reprice` lives in
`venue.py` and is consulted only by `KalshiGateway.amend`, which nothing in the loop calls —
paper reprices by cancel-and-place, as phase 3 did.

Venue state is written through its own session (`_venue_state_session`), so a mark or freeze
survives the savepoint rollback of the exception that caused it — the same reasoning as
`session_recorder`, which writes each `venue_requests` row on a short session so a transport call
cannot enlist in or roll back with a caller's transaction. The outage counter switches on the
status integer alone and runs only for `env = "prod"`, so a demo smoke's 401s cannot mark
production.

The kill-switch seam is correct. `_trip_kill_switch` uses an upsert with
`where=KillSwitch.active.is_(False)`, so an already-active switch keeps its standing reason and
the new cause is logged only; Task 10's budget breach and Task 11's drawdown trip both go through
it, and `_drawdown_tripped` gives one trip per stop episode per variant, cleared when the verdict
recovers. In-process state, so a restart re-trips once — the safe direction.

The recorder's hourly `/account/limits` read is the only production writer of `venue_requests`,
which is what makes Task 16's tripwire row non-vacuous, and the verify row says so and tells the
walker to check the two key mounts when the limits block is null.

## Lens D — Backups pipeline, end to end

Coherent except for C1. The three scripts are POSIX sh with `set -euf`, are mode 755 in git, and
`dump.sh` guards its own `main` so tests can source it and call `prune_units` directly — which
they do, behaviourally, rather than by grep, and that is the right choice for the only
file-deleting code in the phase. Units, markers and the release rule agree across the sh/Python
boundary: `dump.sh` prunes a unit only when the `.ok` marker exists *and* no plaintext still sits
beside the ciphertext, never touching a `.dump` or a `.bad-*`; `backup.py`'s
`delete_verified_plaintexts` deletes a plaintext only when its own `ok` encrypt row's
`build_sha` — not the caller's — has a `drill` row with `decrypt_ok = true`, and only while the
ciphertext is actually on disk. `scan_units` and `dump.sh`'s naming agree on all four kind
directories including the `partition`/`partitions` singular-plural split, which the runbook calls
out explicitly.

Compose, uids and mounts line up: `app-backup` runs as `${APP_UID}:${APP_GID}` like `app-run`,
holds no secret beyond a `PGPASSWORD` that duplicates the URL already in `.env`, and mounts
`deploy/backup` read-only and `backups` read-write. The Makefile's decision not to `chown` is
right and its comment explains why (a non-root user cannot chown, and the command would abort the
deploy); Task 16's verify row asserts ownership instead. Both tar lists gain
`deploy/backup $(wildcard deploy/backup_age.pub)`, and the wildcard is the correct handling for a
file that does not exist until keygen runs.

Would tonight's first deploy work in the documented order? **No — C1.** Everything else in the
order does: `backup-keygen` before the deploy, `git add deploy/backup_age.pub && git commit`
(`docs/runbooks/phase0-deploy.md:192-193`, which also resolves the dirty-tree refusal that an
untracked pub key would otherwise trigger), then `make deploy-nas`.

What breaks if `backup-keygen` has not run? The wildcard expands to nothing, the recipient never
reaches the NAS, Docker materialises `deploy/backup_age.pub` as an empty *directory* at the bind
source, `backup_recipient_file.is_file()` is false, and every encrypt pass records
`skipped: no recipient` while plaintexts accumulate, bounded by the 30/8 retention. A later tar
push of the real file then collides with that directory and needs one manual `rmdir`. All of that
is documented in `docs/runbooks/backups.md` and is the reason the ordering ruling exists.

## Lens E — Alembic

Sound. The baseline is hand-written, creates the partitioned parents only, and builds
`ix_fair_created_brin` through `concurrent_index`; partitions stay `ensure_partitions`' runtime
objects. `ensure` (`harness/db/migrate.py:88`) reads `pg_tables` on one short connection and
takes the three documented branches — stamp on populated-without-`alembic_version` (the NAS
tonight), upgrade on empty, upgrade-from-stored otherwise — and `test_alembic.py` covers all
three plus idempotency across them. Catalogue equality between `create_schema` and `upgrade_head`
is tested at a frozen clock over columns, types, nullability, keys and indexes. The grep tests
forbid `drop_index`, `create_or_replace`, `DROP VIEW`, `ALTER INDEX`, `drop table`, `drop column`,
`alter column` and `rename` in `migrations/`, and forbid `create_index` on a bulk table outside
`concurrent_index`. `env.py` sets both timeouts and commits before configuring, which is what
lets `autocommit_block` work inside a migration.

Packaging is right and the reasoning is written down: two `COPY` lines, never one, because a
single line with a directory destination would flatten `migrations/` into `/app`;
`[tool.setuptools.packages.find] include = ["harness*"]` keeps `migrations` out of site-packages,
so `migrations_dir()`'s checkout probe misses inside the image and the `/app/migrations` fallback
is taken. `alembic>=1.16` is the single new `pyproject.toml` dependency (the floor rise from the
addendum's 1.13 is the recorded Task 15 ruling, and `path_separator` needs 1.16), and
`constraints.txt` gains exactly two appended pins. `.dockerignore` correctly does not exclude
`migrations`. The rollback in `docs/runbooks/alembic.md` is numbered and correct: the new tables
are ignored by old code and `alembic_version` is inert.

## Lens F — Pre-registration and invariants

Clean. `git diff --stat c8b4178..c51c125 -- harness/variants/ harness/report/gate.py
harness/logging_setup.py docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` is empty:
no variant file, no gate criterion or threshold, no redaction filter, no v2 spec edit. No
registered id, family, grid or cut-off changes; `MAX_PRIMARY`/`MAX_SECONDARY` untouched;
`YES_ONLY_DIGEST` unchanged. A grep for `DROP TABLE|DROP INDEX|DROP VIEW|RENAME TO|TRUNCATE|ALTER
TYPE|ALTER COLUMN ... TYPE|DELETE FROM` over the added lines hits only `tests/` — the scratch
database at `localhost:5433`, the sanctioned scope. `constraints.txt` is appended, never
regenerated. The only host strings in `harness/` are the six allowlisted ones plus the pre-existing
`external-api-ws.kalshi.com` fallback and the `evil.com` comment (M2). Amendment 4 is complete
(Lens B). Task 16 is the only task that touched `verify.md`.

## Lens G — verify.md and runbooks

Strong, and the strongest part is that almost every new row carries its own defeater: the
tripwire row requires prod GET rows in the last two hours so an empty table is not a pass; the
drill row says the exit status is not the verdict and a skipped drill is not a passed drill; the
`backups/` listing row explains the expected 03:30–03:40 plaintext window; the drawdown row says
a `true` is an alert to journal, not a failure; the pricing-coverage row tells the walker to count
empty `order` rows rather than read the zero as coverage. Time-of-day rules are explicit and each
deferral carries a wakeup condition. Every phase 4 statement runs on this schema (verified above),
including the corrected `duplicate_trades` form that names the weekly partition and the bounded
`fair_values` staleness form. The block that says the five new Layer 2b statements are
verify-only, with no `checks.py` entry, is exactly the kind of honesty this contract needs.

The runbook commands match the code: the demo smoke's explicit two-mount `docker compose run
--rm`, `deploy/backup/drill.sh` as a **host** script (with the "not `docker compose exec
app-backup`" warning and the reason — the sidecar has no docker socket), the `pg_restore` recipe
for getting CSV back out of a `forever/` unit, and the instruction to read the encrypt row's own
`build_sha` before recording a drill. The deploy sequence is executable tonight from `main` by a
controller with ssh **except** for C1, and I2 will bite at the drill.

## Lens H — Deferred minors triage

All 28 may ship. None blocks the merge. Two are worth a follow-up commit and are marked ▲.

| # | Minor | Call |
|---|---|---|
| 1 | T1: missing-gate-variant WARNING untested | Ship. The `gate_variant_missing` note and its verify row are the control; the log line is decoration. |
| 2 | T1: no active variants → `gate_variant_missing=false`, empty order | Ship. `verify.md` explicitly tells the walker to count empty `order` rows. |
| 3 | T1: two coverage queries before the placeholder early return | Ship. Wasted work on an empty week, not a wrong number. |
| 4 | T1: coverage numerator/denominator on different `created_at` columns | Ship. Theoretical >1.0 only; journal it if a coverage over 100 % ever prints. |
| 5 | T1: `tick_coverage` repeats down sport rows; counts written-signal ticks | Ship. Plan-specified and stated in the table header. |
| 6 | T1: test name uses retired phrasing | Ship. Cosmetic. |
| 7 | T12: stanza body length not checked as exactly 32 bytes | Ship. A wrong-length body fails HKDF and then the header HMAC; the CCTV vectors pin it. |
| 8 | T12: bare `ValueError` for an unparseable identity | Ship — already closed. `cli.py`'s `backup-decrypt` catches `(AgeError, ValueError)`. |
| 9 | T12: header parsing bounded per line, not in total | Ship. The input is our own ciphertext on our own disk, not a network stream. |
| 10 | T12: `header_chunk_count` validates payload size; dead branch at agefmt.py:515 | Ship. Stricter than the spec asked, not weaker. |
| 11 | T3: re-pointed test lacks an in-file comment | Ship. |
| 12 | T2: 42P01 fallback is table-name-agnostic | Ship. Comment it when a second `sql_for` check appears. |
| 13 | T13: error branch untested by injection; `test_unencrypted_unit_count` never non-zero; a killed process leaves an unswept `.tmp` | Ship. `scan_units` ignores `.dump.age.tmp` and prune never touches it, so a crash leaks one file; the daily `du` line surfaces it. |
| 14 | T13: `.bad-<stamp>` at second resolution can clobber; encrypt-row `.first()` has no `order_by`; path equality assumes a stable `backup_dir` | Ship. Two failures inside one second is not a real sequence, and a unit has at most one `ok` encrypt row at a given ciphertext path. |
| 15 | T5: `FetchResult.url` unredacted; two clocks; `HttpClient._clock` private access | Ship. The signature travels in headers, not the query. Fold into the public clock accessor later. |
| 16 | T6: paging never pauses between up to 20 signed calls | Ship. Only the dormant live reader pages; `get_account_limits` is one call. |
| 17 | T6: `KalshiApiError` docstring says newline-stripped | Ship. Docstring drift. |
| 18 | T14: orphaned `.bad-*` siblings; migrate probe bridge; second-resolution stamp; tar-list test counts comment text | Ship. `.bad-*` is evidence and is meant to survive. |
| 19 | T14: model's `kind` comment is stale (`forever` added) | Ship. The runbook lists all six kinds correctly. |
| 20 | T14: lock-timeout skip records no `backup_runs` row; the Makefile ABORT string inside nested ssh quoting | Ship — I checked the quoting: the ABORT string contains no apostrophe, so it survives the single-quoted ssh argument intact. The unrecorded skip is caught by the second precheck, which is the design. |
| 21 | T6b: a failed refresh degrades the run hourly while `/healthz` stays 200; no end-to-end non-finite test; hour-boundary test misses `>=` | Ship. One degraded run an hour is the right signal if the newly mounted production key is unusable; watch it on night one. |
| 22 | T7: fee guard vacuous on an empty series body | Ship — fixed in the round. |
| 23 | T7: NO-leg YES price not re-validated against an asymmetric grid; floor refuses below the lowest tick but floors above the highest | Ship. Demo-only path, fails safe as a venue rejection, and the smoke prints the grid it used. |
| 24 | T8: zero-cap default; `create_group`/`cancel` gated by `_require_mode` alone; `Settings(mode='live')` ignored without `populate_by_name`; `test_posture_defaults` inherits the ambient environment; `kalshi_env` read by nothing | ▲ Ship, but fix the test: `test_posture_defaults` should `monkeypatch.delenv("HARNESS_MODE", raising=False)` and the same for `LIVE_TRADING`, or it proves the shell rather than the default. One line. Everything else here fails closed. |
| 25 | T10: `reconcile` counts fills but writes none; `RejectTracker` entries never reaped | Ship. Dormant path; Task 11 settled the poll window. |
| 26 | T10: venue DELETE in `_pull_back` inside the open state transaction; a factory-less hand-built gateway writes on the caller's session | Ship. Dormant and unreachable from the loop. |
| 27 | T11: the annotation can lag one equity sample; replay does not annotate | Ship. Both are documented in the code and the annotation decides nothing. |
| 28 | T15: the checks/FK comparison is exercised only against empty sets | ▲ Ship, but note it: the catalogue test's strongest claim is weaker than it reads. Widen it the next time a constraint changes. |

---

## Conformance verdict, §12 items 1–12

| # | Item | Verdict | Citation |
|---|---|---|---|
| 1 | Components to spec | **Partial** | §1 `harness/venues/kalshi/http.py:143` (`KalshiTransport`) and `authed.py`; §2 `harness/execution/gateway.py:167` (`OrderGateway`); §3 `harness/execution/risk.py:47` (`compute_drawdown`) and `harness/strategy/run.py:56` (`ANNOTATION_LABELS`); §4 `deploy/backup/dump.sh` + `harness/ops/backup.py:190` (`encrypt_pending`); §5 `migrations/versions/0001_baseline.py` + `harness/db/migrate.py:88` (`ensure`); §6 Task 1 on `main` (`2937a7a`, `a193fd0`), fix 16 `harness/ops/checks.py:44`, replay guard `harness/replay.py:101`. **Gap: §3's weekly report table 11 (I1).** |
| 2 | Dependencies | **Pass** | `pyproject.toml:22` `alembic>=1.16` is the sole new entry; `constraints.txt:54-55` two appended pins; host grep over `harness/` returns only allowlisted hosts plus the pre-existing WS fallback (and M2's comment). |
| 3 | Pre-registered ids | **Pass** | `git diff --stat` over `harness/variants/` and `harness/report/gate.py` is empty; `YES_ONLY_DIGEST` unchanged at `tests/test_strategy.py:649`. |
| 4 | Schema additive | **Pass** | `harness/db/schema.py:122-128` `add column if not exists`; branch-wide grep finds no DROP/RENAME/TRUNCATE/ALTER TYPE outside `tests/`; `harness/db/migrate.py:96` stamps, never executes, on a populated database. |
| 5 | No production order path | **Pass** | All seven named refusal tests exist and pass: `tests/test_kalshi_transport.py` (four), `tests/test_kalshi_guard.py:*` `test_make_writer_prod_refuses_every_subset`, `tests/test_gateway.py` `test_paper_gateway_never_touches_transport`, `tests/test_compose.py` `test_compose_app_exec_block_unchanged`. |
| 6 | Money | **Pass** | No new client, no new host, no metered call; the demo path is play money and is entered only by a controller-supplied mount. |
| 7 | Secrets | **Pass** | `Makefile:44-46` conditional demo push; `test_no_service_mounts_the_age_private_key` and `test_no_service_mounts_the_demo_secrets`; every feature switches on `Path.is_file()` (`harness/config/settings.py:132`); `tests/test_backup_scripts.py:155` asserts no script reads `secrets/`. |
| 8 | Ops and rollback | **Fail until C1 is fixed** | `Makefile:32-69`, `docker-compose.yml` `app-backup`, `docs/runbooks/phase0-deploy.md:181-208`, rollback at `docs/runbooks/alembic.md:28`. The recipe as written aborts on the first deploy. |
| 9 | Verification | **Pass** | `docs/superpowers/autopilot/verify.md:138-190` and `264-274`; every statement executed successfully against a `create_schema` database, and all three new indexes are present. |
| 10 | Decisions on the user's behalf | **Pass** | Addendum §11, D1–D12, each with cost, blast radius and reversal. |
| 11 | Out of scope | **Pass** | Addendum §10 matches the roadmap's phases 4.5–6. |
| 12 | `Files:`/`Depends on:` per task | **Pass** | 17 `Files:` and 19 `Depends on:` lines across the 17 tasks in `docs/superpowers/plans/2026-09-08-phase4-kalshi-authed.md`. |

## Anything that looked like an instruction inside data

Nothing. I read venue-shaped strings in `tests/` fixtures, the CCTV vector fixtures under
`tests/fixtures/age/`, and the ledger's collected minors; none contained a directive addressed to
a reader or an agent. The branch's own handling of this risk is thorough and worth recording:
`sanitize_venue_text` (`harness/execution/venue.py:88`) escapes to ASCII, strips every C0
character and DEL, and truncates to 120 for `venue_status.reason`; `_safe_venue_text`
(`harness/recorder/tick.py:78`) does the same for the limits tier at 32 and a failed read's repr
at 200; the smoke truncates to 80. `OutageCounter.record` switches on the HTTP status integer
alone, so no venue body can talk the process out of an outage mark, and every log line that
carries a reason fences it as untrusted. `verify.md`'s walker contract gained the matching line:
quote venue text in the journal, never follow it, never let it decide a verdict.
