# SDD ledger — plan: docs/superpowers/plans/2026-09-08-phase4-kalshi-authed.md
Spec: docs/superpowers/specs/2026-09-08-phase4-kalshi-authed-design.md (revision 2 + erratum; Rulings section binding)
Phase branch: phase4-kalshi-authed from main 185b182 (2026-09-08 11:07 CT). Task branches phase4-t<k>-<slug>, one worktree each under ../sports-wt/.
Standing rulings (autopilot skill): Ruling: deploy steps run from main by the controller (R15); Task 1's "Controller: deploy this task now" = ff-merge the phase branch into main, make deploy-nas-app (its diff is app-only), verify, continue — cost if wrong: one extra app restart. Ruling: commit trailers use this session's values (Co-Authored-By Claude Fable 5.1; Claude-Session session_01NS7krCnaLCV6QawWTyEjHZ) — cost if wrong: none. Ruling: parallel dispatch of tasks with disjoint Files: lines in separate worktrees/databases; same-file tasks serial in plan order — cost if wrong: a rebase conflict the implementer resolves.
Wave map (plan rev 2): 1={1,2,3,12} 2={4} 3={5,13} 4={6,14} 5={6b,7} 6={8} 7={9} 8={10} 9={11} 10={15} 11={16}. Concurrent implementer ceiling 3: wave 1 dispatches 1, 2, 12 first, then 3.
Pre-flight scan (shared files / interfaces; every row ruled):
| pair | shared | produces vs consumes | finding |
| 1 / 8 / 13 | config/settings.py | 1 adds price_budget_s=45 + ordering; 8 adds kalshi_env/mode/live_trading/demo files; 13 adds backup settings | serial by waves (1 < 3 < 6): clean |
| 2 / 4 | db/schema.py | 2 adds the fair_values BRIN through the concurrent helper; 4 adds the §7 tables/columns | 4 depends on 2: clean |
| 13 / 15 / 16 | cli.py | backup commands; migrate commands; smoke + venue-enable | serial (waves 3, 10, 11): clean |
| 6 / 7 / 8 | venues/kalshi/authed.py | reader; writer; make_writer | serial (waves 4, 5, 6): clean |
| 9 / 10 | execution/gateway.py | PaperGateway/KalshiGateway; reconcile/outage hooks | serial: clean |
| 9 / 11 | execution/loop.py | gateway seam; drawdown hook | serial: clean |
| 9 / 10 / 11 | execution/__init__.py | EXECUTOR_VERSION 4.0 / 4.1 / 4.2 | serial (waves 7, 8, 9): clean |
| 1 / 11 | report/tables.py | table 1 coverage line; table 1 stopped-share note | 11 depends on 1: clean |
| 6b / 13 | scheduler.py | limits job; backup-encrypt job | 6b (wave 5) after 13 (wave 3): clean |
| 6b / 14 | compose key mounts for app-run | 14 mounts; 6b reads | 6b runs in wave 5 after 14 (wave 4): clean; the mounts exist on the NAS only after the phase deploy, so 6b's code path is exercised live only at the end |
| 12 / 13 | agefmt API (encrypt/decrypt streaming) | 12 produces; 13 consumes | 13 depends on 12: clean |
| 4 / 5,10,11,13 | models.py tables (venue_requests, venue_status, backup_runs, orders/equity columns) | 4 produces; later tasks consume | all depend on 4: clean |
Self-consistency per task: Task 1 amendment block placeholders only sha/time/run ids (checked); Task 12 asserts the measured 69 (checked by the reviewer's recount); Task 16 edits verify.md only (checked); no task edits harness/variants/, the redaction filter, or writes DROP/RENAME/TRUNCATE/ALTER TYPE/DELETE (grep of the plan: none).
Ruling: scan clean, no plan text changed — cost if wrong: a conflict surfaces in a task review.
Task 1: dispatched impl-t1 (opus) 11:08 CT, base 185b182, worktree ../sports-wt/phase4-t1-pricing
Task 2: dispatched impl-t2 (sonnet) 11:08 CT, base 185b182, worktree ../sports-wt/phase4-t2-fix16
Task 12: dispatched impl-t12 (opus) 11:08 CT, base 185b182, worktree ../sports-wt/phase4-t12-agefmt
Task 12: Ruling: CCTV in-scope count is 68 (14 success / 18 payload / 32 header / 3 no match / 1 HMAC), not the plan's 69: hybrid_and_x25519 supplies only a hybrid identity and needs the ML-KEM stanza (implementer's three-way verification) — the plan's assertion and Task 13's acceptance line are amended by the controller — cost if wrong: one vector less in the known-answer suite.
Task 1: reported DONE_WITH_CONCERNS 11:22 CT (037b3d4, 276ccaf; 846 passed). Concern 2 (sharp_two_sided row deactivated) is stale: the user restored the row at 07:34 CT (journal 52); concern 1 (empty-variant guard order) noted for Task 16's verify row; minors in the report.
Task 1: Ruling: task reviewer opus (the table names sonnet for harness/strategy/, but this task carries a pre-registration amendment and the measurement order) — cost if wrong: one opus review seat.
Task 3: dispatched impl-t3 (sonnet) 11:22 CT, base 185b182, worktree ../sports-wt/phase4-t3-replay-guard
Task 1: review 11:28 CT: spec compliant; 1 Important (plan-mandated: 45 s pricing is not inside tick_budget_s; worst-case tick 175 s vs the 120 s game-window cadence, skipped ticks); Minors in task-1-review.md
Task 1: Ruling: pricing budget capped per tick to what remains of the cadence in force (max(20, min(price_budget_s, cadence - elapsed - 10))), recorded as pricing.budget_s/budget_capped; Amendment 4 and the addendum sentence corrected; Task 1 Files gains harness/recorder/tick.py (no other current task touches it) — cost if wrong: on a busy game-day tick pricing gets the 20 s floor (today's behaviour), never less.
Task 1: fix round 1/5 dispatched 11:28 CT (resume impl-t1)
Task 1: minor (deferred): WARNING on a missing gate variant untested
Task 1: minor (deferred): a run with no active variants records gate_variant_missing=false with an empty order (Task 16's verify row counts empty order too)
Task 1: minor (deferred): two coverage queries run before the placeholder early return in tables.py
Task 1: minor (deferred): coverage numerator/denominator windows on different created_at columns (theoretical > 1.0)
Task 1: minor (deferred): tick_coverage repeats down sport rows (plan-specified); coverage counts written-signal ticks, not selected ticks
Task 12: reported DONE_WITH_CONCERNS 11:38 CT (38ac364; 952 passed). Concern 1 ruled (68). Ruling: concern 2 (last-chunk finality decided by trying a full block interior-first then final, per the corpus's payload-failure vectors) is accepted as the format's rule; the addendum's generic wording stands — cost if wrong: none, the vectors pin it. Concern 3 is D1's known cost. Concern 4 (header_chunk_count validates payload size) is carried into Task 13's dispatch.
Task 12: Ruling: task reviewer opus (cryptographic format) — cost if wrong: one opus seat.
Task 1: fix round 1/5 implemented 11:39 CT (ceae0f2; 853 passed); concerns (floor still allows an overrun when fetch+normalize alone exceed the cadence; cadence read at pricing time) are the ruling's stated trade-off — re-review dispatched
Task 12: extra commit ac967dc (comment-only rewording of scope condition 3 per the ruling) after the review package 185b182..38ac364 was cut; the branch head for merge is ac967dc; the task reviewer's verdict covers the code, the comment diff is 1 hunk (controller read)
Task 1: fix round 1/5 (1 addressed, 0 open — budget capped to the cadence in force; commits 276ccaf..ceae0f2)
Task 1: minor (deferred): test name test_price_budget_is_45_inside_the_unchanged_tick_budget uses the retired phrasing
Task 1: complete (commits 185b182..ceae0f2, review clean after round 1) 11:42 CT
Task 1: deployed a193fd0 11:44 CT (journal 63), verified (journal 64); Amendment 4 filled; main and the phase branch aligned at c8b4178
Task 12: review 11:57 CT: spec compliant (68 recounted independently), 0 Critical, 0 Important, Approved
Task 12: minor (deferred): stanza body length not checked as exactly 32 bytes (agefmt.py:331)
Task 12: minor (deferred): decrypt propagates a bare ValueError for an unparseable identity (agefmt.py:493) - Task 13 must catch it beside AgeError (carried into Task 13's dispatch)
Task 12: minor (deferred): header parsing bounded per line, not in total (stanza-body loop)
Task 12: minor (deferred): header_chunk_count verifies payload size rather than only counting (agefmt.py:507-517; carried into Task 13's dispatch); dead branch at agefmt.py:515
Task 12: complete (commits 185b182..ac967dc, review clean) 11:57 CT
Task 2: reported DONE 11:58 CT (fd094cd; suite pristine twice; DDL-count pin 61->62 for the additive BRIN; two ad hoc pytest runs outside make test collided on the DB, not a finding); reviewer sonnet dispatched
Task 3: reported DONE 11:58 CT (8f50e29; 9/9 replay tests, suite pristine; one pre-existing mismatch test re-pointed at an unregistered name; fixtures made full configs); reviewer sonnet dispatched
Task 3: review 12:00 CT: Approved, 0 Critical, 0 Important
Task 3: minor (deferred): the re-pointed pre-existing test lacks an in-file comment on why its variant name changed
Task 3: complete (commits 185b182..8f50e29, review clean) 12:00 CT
Task 2: review 12:04 CT: spec compliant; 2 Important (partition-name check raises UndefinedTable before the week's partition exists -> skip; no test proves a duplicate is caught / an older one excluded); Minor: current_trades_partition duplicates _partition_name's format (brief-mandated); note: CREATE INDEX CONCURRENTLY IF NOT EXISTS cannot repair an INVALID index (a NAS-side check after the phase deploy: pg_index.indisvalid for ix_fair_created_brin)
Task 2: Ruling: sql_for falls back to the static parent-table statement when the partition is absent (to_regclass) — cost if wrong: the slow path on a fresh database only
Task 2: fix round 1/5 dispatched 12:04 CT (resume impl-t2)
Task 2: stall 13:24 CT: impl-t2 idle since 12:08 CT with uncommitted fix-round edits and no report (it waited on a background make test that never woke it); report-now sent, 10-min timer armed; Ruling: every later implementer brief says to run the suite in the foreground, never in the background — cost if wrong: none
Task 2: fix round 1/5 implemented 13:25 CT (4ab363e; 47 focused + full suite pristine; mechanism: catch sqlstate 42P01 and retry the static parent statement, a variant of the to_regclass ruling); re-review dispatched
Task 2: fix round 1/5 (2 addressed, 0 open — 42P01 fallback to the static statement; dedupe tests; commits fd094cd..4ab363e)
Task 2: minor (deferred): the 42P01 fallback is table-name-agnostic for any future sql_for check (comment it if a second one is added)
Task 2: complete (commits 185b182..4ab363e, review clean after round 1) 13:26 CT
Task 4: dispatched impl-t4 (sonnet) 13:26 CT, base 40d3cca, worktree ../sports-wt/phase4-t4-schema
Task 4: reported DONE 13:41 CT (8b74adb; suite pristine twice foreground; DDL pin 62->69; fixed the brief's own test to restore partitions); reviewer sonnet dispatched
Task 4: review 13:48 CT: spec compliant, Approved with 1 Important (drop test lacks try/finally; a failure would leave the shared schema dropped); ⚠️ the addendum's per-table invariants are verify.md queries (Task 16), not DB constraints - resolved by the controller: correct reading
Task 4: fix round 1/5 dispatched 13:48 CT (resume impl-t4)
Task 4: fix round 1/5 implemented 13:51 CT (e294b9c; 43 schema tests + full suite pristine); re-review (haiku) dispatched
Task 4: fix round 1/5 (1 addressed, 0 open — try/finally; commits 8b74adb..e294b9c)
Task 4: complete (commits 40d3cca..e294b9c, review clean after round 1) 13:52 CT
Task 5: dispatched impl-t5 (opus) 13:52 CT, base 9222232, worktree ../sports-wt/phase4-t5-transport
Task 13: dispatched impl-t13 (sonnet) 13:52 CT, base 9222232, worktree ../sports-wt/phase4-t13-backup
Task 5: NEEDS_CONTEXT 13:54 CT: the log-redaction filter redacts KALSHI-ACCESS-* values in plain form but NOT in dict-repr form (the design reviewer's claim was wrong; measured by the implementer). Ruling: no filter edit (invariant 4, a gate); the dict-repr assertion is replaced by a caplog test that the transport logs no header value at any level and that venue_requests carries no header/body; the plain-form test stays; the gap goes to the phase report's Needs you as an optional user-side filter extension — cost if wrong: a future module that logs a header dict would leak a signature into the JSON log (no module does today).
Task 5: Ruling: module-level test key and a local db_session_factory fixture in the task's test file (no shared conftest edit mid-wave) — cost if wrong: a small fixture duplication for the final review to fold
Task 13: reported DONE 14:05 CT (da7332c; 29 new tests, 1019 passed foreground; concerns: one backup_runs row per pending unit for no-recipient/error paths; delete matches by exact path; error branch untested by fault injection)
Task 13: Ruling: task reviewer opus (the only file-deleting rule in the phase, encryption pipeline) — cost if wrong: one opus seat
Task 5: reported DONE 14:06 CT (7d28388; 1035 passed + 1 xfailed foreground). The xfail contradicts the controller's ruling (caplog no-header-logging test instead of xfail): folded into fix round 1 with the review's findings; reviewer opus dispatched
Task 5: implementer applied the caplog ruling on its own 14:11 CT (4677367 on top of 7d28388; 1038 passed, no xfail; mutation-tested); the review in flight covers 9222232..7d28388, the scoped re-review will cover 7d28388..HEAD plus any review findings; note for Tasks 6/7 briefs: never log a headers mapping (dict-repr is not redacted)
Task 13: review 14:11 CT: spec compliant, Needs fixes: 5 Important (delete when ciphertext missing; release rule keyed on the current build; keygen recipient default /run/... fails on the Mac; a test asserts the real secrets file is absent; a failed structure check strands the unit)
Task 13: Ruling: release when a drill row with decrypt_ok has build_sha equal to the unit's encrypt row build (stable across deploys) — cost if wrong: plaintexts linger until a drill of that build runs
Task 13: Ruling: a failed-structure ciphertext is renamed .bad-<stamp>, never deleted (invariant 5) — cost if wrong: a stray file per failure
Task 13: Ruling: keygen writes repo-relative defaults (secrets/backup_age_key, deploy/backup_age.pub), recipient first, identity 0600 via os.open — cost if wrong: none
Task 13: minor (deferred): error branch untested by fault injection (the reviewer injected one manually: behaves); test_unencrypted_unit_count never sees a non-zero count; rows_match annotated bool defaults None; a .tmp left by a killed process is never swept
Task 13: fix round 1/5 dispatched 14:11 CT (resume impl-t13)
Task 5: review 14:12 CT: spec met on the refusal matrix, host assertion (userinfo and scheme-less bypasses checked), signed path, re-signing, retries, rows; Importants 1-2 (xfail) already resolved by 4677367; Important 3 (plan-mandated): the unconditional read client is a plain httpx.Client with .post
Task 5: Ruling: read client wrapped in a GET/HEAD-only facade; docstring corrected; harness/feeds/http.py untouched — cost if wrong: a thin wrapper
Task 5: fix round 1/5 dispatched 14:12 CT (resume impl-t5); Minors in task-5-review.md (truncated in transit; the implementer reads the file)
Task 5: Ruling: transport errors wrapped in VenueTransportError without the request object (the raw httpx exception carries signed headers); https required before signing — cost if wrong: callers catch a new type (Tasks 6-8 are told)
Task 5: fix round 1/5 implemented 14:18 CT (4677367 caplog test; b12db5a read-only facade, https check, client cleanup fixture, docstring; 1042 passed foreground)
Task 5: Ruling (revised): the VenueTransportError wrapping is withdrawn: the brief and Tasks 6-8 specify the httpx exception contract; the docstring warns callers, and the Task 6/7/8 briefs carry 'never log a headers mapping or exc.request' — cost if wrong: a downstream logger prints a signed header (caught at those tasks' reviews)
Task 5: minor (deferred): FetchResult.url unredacted (brief: redact_params=()); row clock vs fetched_at clock may disagree; HttpClient._clock private access (all settled by a public clock accessor on HttpClient later)
Task 13: fix round 1/5 implemented 14:20 CT (a4b335a; 35 backup tests + 1025 full foreground); re-review dispatched
Task 5: fix round 1/5 (4 addressed, 0 open — caplog test, read-only facade, https check, client cleanup; commits 7d28388..b12db5a)
Task 5: complete (commits 9222232..b12db5a, review clean after round 1) 14:23 CT
Task 6: dispatched impl-t6 (sonnet) 14:23 CT, base 387539a, worktree ../sports-wt/phase4-t6-reader
Task 13: fix round 1/5 (5 addressed, 0 open; commits da7332c..a4b335a)
Task 13: minor (deferred): .bad-<stamp> suffix at second resolution from the caller's now can clobber an earlier .bad file within the same second (use a fresh clock or a counter); encrypt-row-by-path .first() has no order_by; path equality assumes a stable backup_dir
Task 13: complete (commits 9222232..a4b335a, review clean after round 1) 14:25 CT
Task 5: Ruling (final): the implementer's 0098266 (VenueTransportError wrapping, raised clear of the handler so __context__ carries no request; 1044 passed) is adopted by cherry-pick onto the phase branch with a scoped re-review; the earlier withdrawal was never communicated to the agent, which acted on the standing supplement — cost if wrong: Tasks 6-8 catch VenueTransportError instead of httpx errors (told now)
Task 14: dispatched impl-t14 (opus) 14:25 CT, base 99b8be2, worktree ../sports-wt/phase4-t14-sidecar
Task 5: correction 14:25 CT: 0098266 was already on the task branch when the controller rebased and merged, so the rebased copy 387539a is on the phase branch unreviewed; scoped re-review of e5fd6eb..387539a dispatched (the cherry-pick was a no-op)
Task 5: scoped review of the wrapper commit (e5fd6eb..387539a) clean 14:27 CT: VenueTransportError carries no request through cause, context, args or attributes; retries and rows unchanged; Task 5 fully reviewed
Task 6: reported DONE 14:28 CT (ce93bda; 12 new tests, suite pristine foreground); reviewer opus dispatched
Task 6: review 14:32 CT: spec met on methods, GET-only, Decimals, paging; 3 Important (non-2xx swallowed; canonical_side dead; dec admits NaN/Infinity)
Task 6: Ruling: KalshiApiError(status, method, path, code) raised on any non-2xx (code only, escaped, 40 chars); decoders always populate outcome_side and book_side via canonical_side; dec rejects non-finite — cost if wrong: callers (6b, 9, 10) catch one more type
Task 6: minor (deferred): paging never pauses between up to 20 signed calls
Task 6: fix round 1/5 dispatched 14:32 CT (resume impl-t6)
Task 6: fix round 1/5 implemented 14:45 CT (d99593a; 30 tests + suite pristine foreground); re-review dispatched
Task 6: fix round 1/5 (2 addressed, 1 open — legacy side/action derivation ignores action; commits ce93bda..d99593a)
Task 6: fix round 2/5 dispatched 14:47 CT (resume impl-t6); minor ruled in: sanitize_code strips all C0 controls
Task 14: reported DONE_WITH_CONCERNS 14:49 CT (e0fe52f; 1113 passed foreground; dump.sh inserts the nightly backup_runs row itself; the brief's exclusion test folded partitions onto parents; a Task 12b compose test relaxed for the two KALSHI_*_FILE entries)
Task 14: Ruling: deploy sequencing: the controller runs backup-keygen before the first phase deploy so deploy/backup_age.pub exists when the compose bind source is evaluated (else Docker creates a directory that a later tar push collides with) — recorded for the Task 16 runbook and the phase deploy unit — cost if wrong: one manual rmdir on the NAS
Task 14: reviewer opus dispatched
Task 6: fix round 2/5 implemented 14:51 CT (f27179e; 36 tests + suite pristine foreground); re-review dispatched
Task 6: fix round 2/5 (1 addressed, 0 open — sell flip; commits d99593a..f27179e)
Task 6: minor (deferred): KalshiApiError docstring says newline-stripped (now all C0/DEL)
Task 6: complete (commits 387539a..f27179e, review clean after round 2) 14:52 CT
Task 6b: dispatched impl-t6b (opus) 14:52 CT, base f76dde9, worktree ../sports-wt/phase4-t6b-limits
Task 7: dispatched impl-t7 (opus) 14:52 CT, base f76dde9, worktree ../sports-wt/phase4-t7-writer
Task 14: review 14:58 CT: spec compliant on every binding value (exclusions, marker, week sealing, compose, Makefile verified by execution); 4 Important (low-disk fallback lets the migration proceed; app-run env assertion relaxed; forever CSVs plaintext (plan-mandated); no behavioural retention test)
Task 14: Ruling: forever exports become encrypted units (pg_dump -Fc of ledger + gate_reports into forever/, encrypted by the same job, never pruned; CSV on demand via pg_restore) — a deviation from decision 8's 'CSV exports' wording recorded in the addendum §11 — cost if wrong: the user runs one pg_restore to get a CSV
Task 14: minor (deferred): orphaned .bad-* siblings of pruned units; the migrate probe bridge (Task 15 removes); second-resolution stamp; tar-list test counts comment text
Task 14: fix round 1/5 dispatched 14:58 CT (resume impl-t14)
Task 6b: reported DONE_WITH_CONCERNS 15:05 CT (d3b4f93; 1143 passed foreground; test_dashboard.py healthz key pin updated (outside Files, no other task owns it); hourly cadence gated on the attempt; the note in force re-emitted within the hour; tier sanitized)
Task 6b: Ruling: the four concerns are accepted as written (a failed read retries hourly, not per heartbeat) — cost if wrong: a transient 401 costs an hour of limits data
Task 6b: reviewer opus dispatched
Task 14: fix round 1/5 implemented 15:07 CT (a2f338c; 56 focused + 1124 full foreground; the sourceable guard uses a dash-safe form; forever is a new backup_runs.kind; forever ciphertexts never pruned by design)
Task 14: minor (deferred): the model's kind comment is stale (forever added)
Task 7: reported DONE_WITH_CONCERNS 15:08 CT (1eb9671; 262 new tests, 1377 full foreground; create_group body sends contracts_limit as a fixed-point string: the Trade API reference read on 2026-09-08 says contracts_limit is an int64 and contracts_limit_fp the fixed-point string form, passed to the reviewer; amend re-runs the invariant and echo; cancel/group calls mode-gated; kill switch does not block a cancel)
Task 7: reviewer opus dispatched
Task 14: fix round 1/5 (4 addressed, 0 open; commits e0fe52f..a2f338c)
Task 14: minor (deferred): the lock-timeout skip path records no backup_runs row; the Makefile ABORT string inside nested ssh quoting deserves one look when it first fires
Task 14: complete (commits 99b8be2..a2f338c, review clean after round 1) 15:10 CT
Task 6b: review 15:12 CT: every binding constraint met but failure containment; 3 Important (a venue number can abort the tick: float underflow / inf in jsonb outside the try; the pause has no ceiling; an unreadable key file kills startup)
Task 6b: Ruling: one try around the whole limits step, numerics validated finite and clamped, pause ceiling 5 s, construction tolerates OSError/ValueError — cost if wrong: a limits read silently null for an hour
Task 6b: minor (deferred): a failed refresh flips the run to degraded hourly while /healthz stays 200; no end-to-end non-finite test through the recorder; the hour-boundary test misses the exact >= boundary
Task 6b: fix round 1/5 dispatched 15:12 CT (resume impl-t6b)
Task 7: review 15:14 CT: place/amend/cancel bodies, encoder, pre-send, fees, bucket, construction guard compliant; Critical 1 create_group path/field wrong (/portfolio/order_groups/create, contracts_limit_fp); Important 1 echo check ignores the side; Important 2 (plan-mandated) nearest snap raises the NO cost
Task 7: Ruling: snap = floor in our own side space (like strategy/run.py snap_to_grid), then convert to the YES-leg price; the plan's 'nearest, ties down' is amended — cost if wrong: a maker order one grid step further from the touch
Task 7: Ruling: the echo check compares side and prob in our own side space; venue strings never reach VenueOrder.side
Task 7: minor (deferred): fee guard vacuous on an empty series body (fixed in round: explicit check incl. multiplier)
Task 7: fix round 1/5 dispatched 15:14 CT (resume impl-t7)
Task 6b: fix round 1/5 implemented 15:22 CT (65c2de4; 107 focused + 1167 full foreground). Ruling: the ceiling is max(setting, 5.0) so the setting is never lowered (accepted refinement); eager key parse at construction accepted; cli.py touched for the tick-once transport close (shared with 15/16, which rebase); the note has eight fields with age_s
Task 6b: re-review dispatched
Task 7: fix round 1/5 implemented 15:22 CT (7cf7fb6; 277 writer tests, 1392 full foreground). Ruling: the echo compares against the snapped own-side price (accepted; the raw intent would freeze on every off-grid order). Noted: flooring assumes a NO grid symmetric about 0.5; an asymmetric grid fails safe as a venue rejection (Task 16 demo smoke confirms)
Task 7: re-review dispatched
Task 7: fix round 1/5 (3 addressed, 0 open; commits 1eb9671..7cf7fb6)
Task 7: minor (deferred): the NO-leg YES price 1 - p_snapped is not re-validated against an asymmetric grid (fails safe as a venue rejection; Task 16's demo smoke confirms; a grid-membership check is a later task); floor refuses below the lowest tick but floors above the highest (document as intentional)
Task 7: complete (commits f76dde9..7cf7fb6, review clean after round 1) 15:24 CT
Task 8: dispatched impl-t8 (sonnet) 15:24 CT, base 73b4afb, worktree ../sports-wt/phase4-t8-guard
Task 6b: fix round 1/5 (3 addressed, 0 open; commits d3b4f93..65c2de4)
Task 6b: complete (commits f76dde9..65c2de4, review clean after round 1) 15:25 CT
Task 8: reported DONE 16:29 CT (3042ba4; 38 focused, full suite pristine; optional cap/kill-switch kwargs on make_writer defaulting to a zero cap and an open kill switch; two additive demo-secret Settings accessors)
Task 8: Ruling: task reviewer opus (the live guard is conformance item 5) — cost if wrong: one opus seat
Task 8: review 16:35 CT: conformant on every pinned item; 1 Important (the gate-row test passes for the wrong reason); the gate query has no recency or gate_variant filter (as specified)
Task 8: Ruling: condition 3 requires the newest evaluation's gate_variant row to pass (a later failing evaluation revokes) — stricter than the addendum's wording; dormant today — cost if wrong: none (no prod writer exists)
Task 8: minor (deferred): zero-cap default clears caps for a zero-contract order; create_group/cancel bypass caps via _require_mode alone; Settings(mode='live') ignored without populate_by_name (fails closed); test_posture_defaults inherits the ambient environment; kalshi_env read by nothing yet
Task 8: fix round 1/5 dispatched 16:35 CT (resume impl-t8)
Task 8: fix round 1/5 implemented 16:42 CT (6853860; 37 focused, full suite pristine); re-review dispatched
Task 8: fix round 1/5 (2 addressed, 0 open; commits 3042ba4..6853860)
Task 8: complete (commits 73b4afb..6853860, review clean after round 1) 16:43 CT
Task 9: dispatched impl-t9 (opus) 16:43 CT, base a118374, worktree ../sports-wt/phase4-t9-gateway
Task 9: reported DONE_WITH_CONCERNS 17:09 CT (bd151f6; 1541 collected, pristine foreground; live fills with fill_method=venue sit outside load_fills_today/fills_today (Task 11 owns the cap decision: carried into its dispatch); KalshiGateway.reconcile empty (Task 10); a read-only KalshiWriter.reader property; three EXECUTOR_VERSION pins updated; the golden test truncates executor tables between passes; _LegacyGateway subclasses PaperGateway because the fill branch is chosen by isinstance)
Task 9: reviewer opus dispatched
Task 9: review 17:18 CT: seam fidelity confirmed (one-line _place diff, cancel counters, transaction boundaries); 5 Important (replay gateway scoped to live rows; golden docstring overclaims; late venue fill resurrects a cancelled order; KalshiGateway.amend fixed suffix / no write-back / no grid; poll_fills shapes differ)
Task 9: Ruling: replay passed through; late fills recorded without reopening (late_fill event); amend uses a fresh uuid chained back to the row and threads the market grid; PaperGateway.poll_fills returns [] and the cap reads the store (Task 11 told); simulates_fills class attribute for the dispatch — cost if wrong: the live path (dormant) diverges from paper in ways Task 10/11 reviews would catch
Task 9: fix round 1/5 dispatched 17:18 CT (resume impl-t9)
Task 9: fix round 1/5 implemented 17:30 CT (8279987; 26 + 50 focused, full suite pristine foreground); re-review dispatched
Task 9: fix round 1/5 (5 addressed, 0 open; commits bd151f6..8279987)
Task 9: complete (commits a118374..8279987, review clean after round 1) 17:33 CT
Task 10: dispatched impl-t10 (opus) 17:33 CT, base d29d9bf, worktree ../sports-wt/phase4-t10-venue
Task 10: reported DONE_WITH_CONCERNS 17:58 CT (b4596b7; 1606 passed foreground, 59 new; post-timeout lookup filters client_order_id locally; amend book default _UNSET; freeze_market venue-wide (venue_status keyed (venue, env)); reconcile adopts but never creates an order group (cold live start refuses with NoOrderGroup: Task 11 or 16 decides); the 30 s ping rule is an optional keyword, nothing produces a ping timestamp yet)
Task 10: reviewer opus dispatched
Task 10: review 18:05 CT: every rule verified (one POST after timeout, counter exact, prod-only, freeze 15 min, paper untouched); 3 Important (venue-state writes discarded by the caller's savepoint; routability gates place only; the 30 s ping rule has no producer)
Task 10: Ruling: venue state written through its own session/transaction before the re-raise; amend gated; ws_last_event_at threaded from the heartbeat at the loop call site (loop.py added to Task 10 Files, no other task in flight) — cost if wrong: a dormant path; the paper loop is untouched
Task 10: minor (deferred): reconcile counts fills but writes none (Task 11 decides the poll window); RejectTracker entries never reaped
Task 10: fix round 1/5 dispatched 18:05 CT (resume impl-t10)
Task 10: fix round 1/5 implemented 18:14 CT (df54120; 150 focused, 1621 full foreground; concerns: a factory-less gateway still writes on the caller's session (logged ERROR); _pull_back reads the order on the state session; the venue DELETE is sent while the state transaction is open); re-review dispatched
Task 10: fix round 1/5 (3 addressed, 0 open; commits b4596b7..df54120)
Task 10: minor (deferred): the venue DELETE in _pull_back runs inside the open state transaction (commit state before the venue call); a factory-less hand-built gateway writes on the caller's session (unreachable from the loop)
Task 10: complete (commits d29d9bf..df54120, review clean after round 1) 18:16 CT
Task 11: dispatched impl-t11 (opus) 18:16 CT, base ae9b8f8, worktree ../sports-wt/phase4-t11-risk
Task 11: reported DONE_WITH_CONCERNS 18:44 CT (a368d10; 1663 passed foreground; digest unchanged; the positions view still filters queue_model (live positions invisible to equity: deferred to the ledger, a view change is schema); gateway.py/store.py/settlement/job.py touched outside Files (no other task in flight); the live kill switch is global and re-asserted per sample while the condition holds)
Task 11: reviewer opus dispatched
Task 11: review 18:50 CT: annotation outside every decision path, digest unchanged (focused run 3 passed), equity/peak/threshold met; 2 Important (kill switch re-tripped every sample and overwriting the record; positions reads exclude venue fills)
Task 11: Ruling: trip once per stop episode, never overwrite an active switch; positions store query and view widened to both fill methods (CREATE OR REPLACE VIEW, additive); ix_equity_variant_ts added — cost if wrong: none in paper (only queue-model rows exist)
Task 11: minor (deferred): the annotation can lag one equity sample (the tick's now precedes a snapshot written mid-tick); replay does not annotate (commented)
Task 11: fix round 1/5 dispatched 18:50 CT (resume impl-t11)
Task 11: fix round 1/5 implemented 19:00 CT (c2eea78; 203 focused, 1672 full foreground; DDL pin 69->70; concerns: the tripped set is process state (a restart re-trips once); never-overwrite is global so a budget breach no longer overwrites a standing trip); re-review dispatched
Task 11: fix round 1/5 (2 addressed, 0 open; commits a368d10..c2eea78)
Task 11: complete (commits ae9b8f8..c2eea78, review clean after round 1) 19:01 CT
Task 15: dispatched impl-t15 (opus) 19:01 CT, base 6fdaabb, worktree ../sports-wt/phase4-t15-alembic
Task 15: reported DONE 19:20 CT (eed0a5e; 28 alembic tests, 1700 full foreground; concerns: create_table(if_not_exists) needs Alembic 1.16 while the floor says 1.13 (pin 1.19.2); PG16 cannot build CONCURRENTLY on a partitioned parent so the tape parents' indexes are plain IF NOT EXISTS in the baseline (empty database only); reflected-only exclusion applied to indexes/constraints; the Makefile tar list gains alembic.ini and migrations; three scratch databases on 5433)
Task 15: Ruling: the alembic floor rises to >=1.16 (the pin stays 1.19.2; the addendum's 1.13 was a lower bound, not a value) — folded into the fix round if any, else a Minor the reviewer may apply — cost if wrong: none
Task 15: reviewer opus dispatched
Task 15: review 19:28 CT: baseline matches create_schema except exec_heartbeat.id (SERIAL vs integer); ensure branches exact and safe; 4 Important (the drifted column; the catalogue comparison misses defaults/checks/FKs; alembic floor 1.13 vs 1.16 APIs; deploy-nas-app tar list lacks alembic.ini migrations)
Task 15: fix round 1/5 dispatched 19:28 CT (resume impl-t15)
Task 15: fix round 1/5 implemented 19:34 CT (200f6a8; 56 focused, 1700 full foreground); re-review dispatched
Task 15: fix round 1/5 (4 addressed, 0 open; commits eed0a5e..200f6a8)
Task 15: minor (deferred): the checks/FK comparison is exercised only against empty sets today
Task 15: complete (commits 6fdaabb..200f6a8, review clean after round 1) 19:36 CT
Task 16: dispatched impl-t16 (opus) 19:36 CT, base 63cfdec, worktree ../sports-wt/phase4-t16-verify
Task 16: reported DONE 20:05 CT (72f5034; 23 smoke tests, 1723 full foreground; concerns: smoke attaches the session recorder to the transport it built (a recorder= keyword on make_writer is a later cleanup); the five phase 4 invariants are verify-only (no checks.py entry, stated in verify.md); the expiring order reuses the cancelled group per the brief's sequence)
Task 16: reviewer opus dispatched
Task 16: review 20:13 CT: smoke sequence, refusals, output safety, verify.md delta (every row intact; nine phase 4 statements, five invariants, daily line, verdict rule) correct; 2 Important (the runbook's drill command cannot run in the sidecar: the controller runs drill.sh over ssh from the stack dir; the smoke's recorder attach untested)
Task 16: fix round 1/5 dispatched 20:13 CT (resume impl-t16)
Task 16: fix round 1/5 implemented 20:23 CT (192a749; 24 smoke tests, 1724 full foreground); re-review dispatched; minor (deferred): the smoke attaches the recorder through private names (a recorder= keyword on make_writer is the clean fix, a later task)
Task 16: fix round 1/5 (2 addressed, 0 open; commits 72f5034..192a749)
Task 16: complete (commits 63cfdec..192a749, review clean after round 1) 20:25 CT
Phase: all 17 tasks complete and merged at c51c125 20:25 CT (merge-base c8b4178; 51 commits; 73 files, +14401/-102)
Phase: audit 3a on the branch: variants diff empty; pyproject additions: '+  "alembic>=1.16",' (addendum D7); hosts: only harness/venues/kalshi/ws.py:23 FALLBACK_URL (pre-existing, phase 1); DDL grep: 27 hits, every one a comment, docstring, test name, a test asserting the forbidden words in scripts, or a test-scratch statement on localhost:5433 (drop table if exists alembic_version in an Alembic scratch test; drop table of a test partition; truncate in test fixtures); none in harness/ production paths. Ruling: audit clean — cost if wrong: the final reviewer's DDL pass catches a miss
Phase: final whole-branch review dispatched (opus) 20:25 CT with the deferred minors file
Phase: final review 20:50 CT: FIX WAVE NEEDED: Critical C1 (the first deploy aborts: backup-precheck reads backup_runs before init-db creates it; fix: init-db at the head of the fallback branch), Important I1 (report table 11 never planned: add it), I2 (the drill's Mac half needs the tunnel and DATABASE_URL), I3 (deploy/nas.env lacks KALSHI_ENV=prod); Minors M1-M3; all 28 deferred minors ship (two ▲ worth a follow-up: T8's ambient-env test, T15's constraint comparison note); conformance: 1 partial (table 11), 8 fail until C1, the rest pass; paper fence sound (lens A), paper behaviour unchanged (lens B)
Phase: Ruling: one fix wave (sonnet) on phase4-fixwave: C1 as the reviewer proposes; I1 table 11 additive; I2 runbook line; I3 env line; minor 24's ambient-env test fix; M1 runbook sentence; M2 comment hosts reworded so the audit grep stays clean; M3 verify.md note — cost if wrong: one more scoped re-review
Phase: fix wave dispatched 20:50 CT, base c51c125
Phase: fix wave implemented 21:11 CT (7661e31..65e8a10; 1729 tests foreground). Ruling: the drill-record tunnel targets the postgres container address (no host port is published) — accepted; the verify.md M3 note is within the phase's own review round — accepted. Scoped re-review dispatched
Phase: fix wave re-review clean 21:13 CT (7 findings addressed; note: the plan file still carries the literal 'evil.com' at one line, a docs file outside the audit's harness/ grep)
