# Omarchy controller runtime evidence

User-directed setup; no autonomous loop launched and no new 6B task dispatched.
Controller main at e38e6a077b61846d968ade57a45f43f25082bf8d includes the reviewed
release and worker tools. This records actual drills, not application release acceptance.

- Claude 2.1.270 authenticated through the user's claude.ai login. Official Superpowers
  6.3.0 installed. No Mac login credential or application API key was copied for this.
- Fixed sports-worker child tool inventory contained exactly MCP shell and screenshot.
  The second bounded Opus-controller/Sonnet-worker drill completed 31 tests in 0.40 s
  against `harness_test_recovery_worker_smoke` at bb90c7a, plus a real PNG delivery.
  Controller inspected actual child tool_use/tool_result records in session
  `5da60aed-9045-460c-ba85-d8c8172df1d8`; these corroborate the prose report.
- The first drill exposed Git's EACCES reading a /dev/null credential mask in a nodev
  namespace. e38e6a0 uses a validated empty regular file as the read-only mask; the
  second real drill passed. Host worktree remained clean. Namespace-only masked
  example files are documented and cannot serve as release cleanliness evidence.
- Actual kernel/MCP checks deny production paths, Docker socket, SSH/private home,
  host loopback and production TCP routes, main writes, and replacing the host suite
  lock. Assigned task writes, isolated test SQL and the fixed screenshot path work;
  arbitrary screenshot paths are rejected. A controller-held lock contends correctly.
- Test role is NOSUPERUSER, NOCREATEROLE, CREATEDB, NOREPLICATION, NOBYPASSRLS, with
  no role memberships. Server file/program operations and bootstrap/admin password
  login were denied. Development database/application ownership was transferred;
  system catalog ownership and production databases were untouched.
- 96 isolated controller/release/guard/recovery tests passed on Omarchy; after the
  final empty-file correction, 35 guard/MCP tests passed and independent review accepted.
- Reminder delivery was proved by service success and a durable receipt at 00:04:07Z.
  Scheduled systemd reminders do not launch models and do not survive reboot.

## Limits retained

Command-hook timeout can fail open; hooks are defense in depth only. The required
custom-agent allowlist and fixed bubblewrap server form the worker boundary. Task
assignment and test-suite scheduling are cooperative; controller receipts are private.
MCP cancellation notifications do not cancel the child; its bounded deadline applies.
A full test process intercepted SIGINT during the failed pricing run (application signal
handlers can survive in-process tests). The controller verified the exact test PID,
worktree and uncaught SIGHUP disposition before ending only that process. Its receipt
records exit -1; it is not passing acceptance. Reconcile the process/lock after cancellation.
A real controller session must discover its native wakeup/browser/push capabilities.
Omarchy-to-NAS SSH currently fails authentication; archive copying remains pending.

Raw controller evidence remains in `/home/trey/dev/sports/.superpowers/sdd/omarchy-restart-2026-09-12/`
(`claude-drill-r2.json`, its stderr log, and worker smoke logs). Login tokens are not
part of this artifact. Earlier archived reviews are preserved; final source review
is appended separately as controller-final-review.md.
