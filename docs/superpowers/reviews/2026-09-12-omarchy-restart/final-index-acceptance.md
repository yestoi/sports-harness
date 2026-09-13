# Final index source acceptance

Exact source `4b2f2cd79c6955067d9af961a1172d17f5d866e4`, base `93dfb95`: **3,230 passed, 6 expected xfails, 1862.90s, clean unfiltered full suite on Omarchy**.
Command `make test`, database `harness_test_recovery_fix45_final`.
Started 2026-09-13T02:31:35.079619+00:00; finished 2026-09-13T03:02:39.255164+00:00.
Receipt has empty filters/scope, clean before/after, identical HEAD and exit0.
Log SHA256: `15b4860b7ce363297dd62c0a2a9c193eccb5d3753bc5c5c45108d916613e2c36`.
Full log: `.superpowers/sdd/omarchy-restart-2026-09-12/fix45-final-full-fixture-grant.log` on both hosts.

The first restricted-role attempt failed eight fault fixtures because the role could
not update `pg_index.indisvalid`; its failed log and receipt remain archived separately.
The controller temporarily granted UPDATE on that column only in this isolated test
database. Whole-table/other-column UPDATE, server-file/program memberships and superuser
remained absent. All85 schema tests passed, then the complete unchanged suite above
passed. The grant was revoked after the run; proof is in `fix45-fixture-grant-revoked.txt`.
No source, fixture assertions, timeout or filtering was changed to obtain this result.

Independent final source/rebase reviews passed; only four runner comments changed
relative to main executable code. Original8c24df2 and failed attempts remain preserved.
Main now contains this source. Production stays93dfb95/schema0006 until a later full
release inside the permitted window, with fresh exact-main full acceptance first.
This does not establish live EXPLAIN/24h index benefit or resolve normalization backlog.
