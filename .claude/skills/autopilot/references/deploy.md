## Unit: deploy on Omarchy

Only the controller deploys, from `/home/trey/dev/sports` on clean `main`. Before
choosing this unit, reconcile the live stamp and the latest `/srv/sports-harness/releases/*/receipt.json`.
A completed deployment awaiting verification goes to verify; never retry merely
because a notification was lost. Preserve the daily failure ceilings and pending gates.

1. Compare deployed source with main, excluding docs/Markdown/.claude controller
   tooling. Batch all reviewed, ready independent fixes into one release. Keep the
   declared prerequisites and service ownership in the ledger.
2. The release needs a clean full-suite receipt for main's exact commit or exact tree
   (`git rev-parse HEAD^{tree}`): the merged branch's pre-merge `make test` on its own
   database counts when the branch was rebased onto main before the fast-forward, so
   run `make test` on main only when no such receipt exists. Either receipt must show
   the same before/after SHA, no dirty files (worker report files moved out first),
   exit 0, a full scope and every shard exit 0, with no filtering environment/TEST_ARGS.
   Review the log for warnings/tracebacks and expected xfails; a worker's
   “passed” statement is insufficient.
3. `make plan-release-omarchy MODE=app` checks stamp, configuration and game window.
   App-only is rejected for WebSocket/RFQ, database/model/migration, matching/alias,
   variant, Compose, Dockerfile, dependency or backup-script changes. Select full
   when required, never bypass classification to meet a deadline.
4. R4 applies to full releases and NFL windows. Journal 128 permits app-only in
   Thursday–Saturday college windows when the full-trigger diff is empty and no NFL
   window is active. The migration-only game-window waiver expired at cutover.
   The script fails closed on a game window; the three existing emergency exceptions
   require a separate controller-reviewed action and affected-game journal entry,
   never a generic bypass flag. If time blocks deployment, checkpoint and arm the
   appropriate native wakeup, then do independent ready work.
5. Run `make deploy-omarchy-app` or `make deploy-omarchy` in the foreground. The
   script builds an immutable candidate image from the exact Git archive, validates
   preserved settings, requires a fresh successful backup, and rechecks the window
   after building. App-only leaves app-ws untouched. Full application/schema release
   includes app-ws, migrations, init-db, variant registration and team seeding.
   PostgreSQL/backup service configuration changes use a separate infrastructure plan.
6. A failure restores the prior Compose definitions and application image selection;
   it re-registers the previous image's variants before restarting writers if registration was attempted; it never rolls back additive schema by deleting data. Read the receipt's original
   and rollback outcome and check actual old-service health. A failed rollback is a
   stop, not a completed recovery. Existing image tags are never overwritten/rebuilt.
7. Success means every changed service has its expected immutable image and build
   environment, health is good, and a completed recorder tick carries the new SHA.
   Then run `make verify-summary-omarchy DEPLOY_SHA=<sha>` and the full verification
   contract, including code-specific rows. Full releases record the actual tape gap
   and first recovered snapshot. No forced tick in quiet hours.
8. Journal SHA, receipt path, time/window ruling, services, backup evidence, stamp,
   tape continuity, test/review evidence and all deferred judge-after rows. Keep
   implemented/reviewed/merged/deployed/verified states distinct. Continue to verify.
