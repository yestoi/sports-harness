# Fix 52 independent review

**Scoped code verdict: PASS. Final fix-52 integration verdict: NOT READY until the controller-owned verifier adaptation is reviewed and Omarchy tests pass.**

- Reviewed branch/worktree: `recovery/fix52`, `/Users/trey/dev/sports-wt/recovery-fix52`.
- Exact head: `8a3b25bb578393c40be3e7e5fa1244eb40ebe8af`.
- Base: `2a1061c`.
- Scope: full four-file diff; roadmap finding 52; actual weather source, recorder cadence/budget/checkpoint flow, NWS response clock, source-state upsert, historical feature query, and canonical verification row.
- Review was read-only except this requested report. No local test suites, DB access, SSH, SCP, Docker, or deployment. `git diff --check 2a1061c..8a3b25b` passed. No runtime pass is claimed.

## Findings

### Important — outstanding integration dependency: verifier does not yet read the success mark

At this exact head, `docs/superpowers/autopilot/verify.md:227` still judges freshness solely from a `weather_snapshots.fetched_at` inside two hours. Stable forecasts intentionally keep their original evidence timestamp after this patch, so successful hourly refreshes still fail the original verification query once that evidence ages. The comment at `harness/weather/snapshots.py:78-80` says the verifier reads the new key, but that is prospective at this head.

The controller confirmed during review that it owns a separately authorized integration edit using the same greatest(success mark, legacy snapshot timestamp), preserving the existing two-hour threshold. This is therefore an explicit outstanding integration dependency, not an unassigned hidden code change. Review the exact final verifier diff before closing fix 52. The stale two-hour threshold must not be loosened, and the outdoor eligibility / skip explanations must remain intact. If the code ships before that integration, part (b) of finding 52 remains incomplete.

No Critical or additional Important defect found in the scoped implementation. No new destructive behavior or unrelated cadence/pricing changes found.

## Required semantic checks

**Quiet cadence.** `Recorder._weather` computes the minimum non-None interval from the same per-sport planner used by ordinary source due checks. The only change from `cadence_in_force` is its empty-set result: None replaces the pricing helper's 900 fallback. Both sports quiet therefore skip before constructing an NWS client. Existing 20-second pre-kickoff and 120-second game windows still exclude weather; 300/900 still allow it, subject to the unchanged 25-second budget guard. A mixed active/quiet set retains its actual minimum active cadence. Pricing's fallback and forced-tick behavior remain untouched; force cannot bypass the weather gate.

**Successful completion and unchanged data.** `_one_game` advances `nws_hourly:<game_id>` only after a 200 response passes schema parsing, contains at least one period in the game window, and all changed snapshot rows have flushed. The same marker advances when the useful forecast is unchanged and no snapshot is appended. The namespace is distinct from odds/other source state. `games_due` uses the later of the marker and legacy snapshot timestamp, so old installations retain their existing initial freshness and ordering. No mark plus no snapshot stays due.

**Failure/no-data.** Points failure, unsuccessful hourly HTTP status, schema refusal, no relevant periods, and HTTP exceptions all occur before the marker write. Prior success is retained and a stale game remains due. Dome/no-stadium skips are unchanged. An arbitrary future forecast period is not itself a successful game forecast: periods are filtered to kickoff minus one hour through plus four hours before marking.

**Transactions/rollback.** `store.set_source_state` executes a normal upsert on the caller's Session and performs no commit. It is in the same transaction as raw evidence and appended weather snapshots. Recorder checkpoints immediately after `_weather`; its weather exception handler rolls back. A DB flush/upsert failure cannot independently commit a successful marker. Per-game exception handling already catches errors without a savepoint; a poisoned DB transaction can consequently roll back the pass's earlier evidence/marks together, but this is existing pass behavior and does not create false freshness. This conclusion comes from source inspection; the new tests do not inject a DB write failure or explicitly roll back a completed refresh.

**Future/as-of semantics.** `NwsClient.get` stamps `result.fetched_at` from its local clock after receiving/parsing the response, not from venue body dates. A mark a few seconds after recorder tick-start `now` is expected: due-ness clears until one hour after actual completion. Mutable markers are not historical evidence. `harness/research/features.py:104-110` still queries only `weather_snapshots` with `fetched_at <= :as_of`; a later unchanged refresh cannot hide earlier evidence, and changed forecasts append new rows that an earlier as-of cannot see. The patch does not repair pre-existing corrupt/far-future database timestamps or implement historical scheduling; a far-future marker would defer refresh until its time plus one hour. No new upstream timestamp trust was introduced.

## Test discrimination and remaining validation

The real quiet-hour test exercises 00:59:59, 01:00, 03:00, 07:59:59 and 08:00 Chicago boundaries with the actual cadence planner, both force values, and checks that no client is constructed during quiet hours. Pre-fix fallback-to-900 code fails these cases. The enumerated cadence tests also pin None/20/30/120 exclusion and 300/900 allowance; the pricing fallback remains 900 in the real quiet-hour case.

The stable-forecast regression advances the response clock, refreshes unchanged data, and proves an immediate pass does no network work, 59:59 remains not due, and exactly one hour becomes due. It separately verifies the original snapshot timestamp is unchanged and exercises the real historical query before and after the refresh. Pre-fix snapshot-only scheduling fails the immediate-due assertions. The changed-temperature case proves earlier as-of results do not pick later data. Failure parameterization covers HTTP, schema, no relevant periods, and transport exception; existing points-failure coverage now also checks the absent success mark. Ordering tests cover marks, a legacy snapshot, and an unrelated source key.

The controller should run the targeted files via the repository Makefile on Omarchy at this exact head and the full suite on the final integration candidate:

```sh
PYTEST_ADDOPTS='tests/test_recorder_weather.py tests/test_tick.py' make test
make test
```

Additional focused evidence useful for final integration: a successful refresh followed by explicit transaction rollback restores both old success mark and old evidence; a simulated snapshot flush/upsert failure cannot leave an advanced mark; the final verifier query passes an unchanged successful refresh with evidence older than two hours, fails an old marker, and handles legacy/no-marker cases with the same threshold. These are remaining validation requests, not observed runtime defects.

## Instruction-like data

The new source comment states the verification query already uses the marker; this was treated as a claim to check, not authority, and inspection found the integration dependency above. No venue/API fixture text was acted on as an instruction. The existing NWS client documents a venue retry hint; that is existing data-derived policy, and this review neither changed it nor issued any request.
