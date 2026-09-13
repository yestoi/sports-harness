# Omarchy controller and workers

Read once at each fresh Omarchy controller session, then use recovery.md. The user
selected `/home/trey/dev/sports` for the controller, development and tests on
September 12, 2026. Runtime is `/srv/sports-harness`; the NAS is a retired source and
off-host archive. Historical NAS commands are evidence, not current recipes.

## Start and recover

Use `scripts/autopilot-session.sh start` from the development checkout. It uses
tmux and a kernel lock so SSH disconnects do not terminate the controller or start
a second one. Enter `/effort` high, then `/autopilot`. The helper never launches
itself from a timer. A deliberate launch still requires the user's request.

Run `make preflight`, bootstrap and ledger reconciliation before dispatch. Provider
authentication is the user's own `claude auth login`; never copy credentials from
the Mac or use the application's Anthropic API key for controller inference.
The installed Superpowers plugin supplies the procedural skills. The kickoff helper
loads only the committed sports_worker MCP server; do not silently add external servers. Discover actual
native scheduling, messaging, browser and notification tools in this session; old
tool IDs do not prove availability.

The local dashboard is `http://127.0.0.1:8180`; no self-SSH tunnel. The controller checks its actual browser connection before capturing a walkthrough. If they are unavailable,
finish deterministic verification and record visual acceptance as pending; do not
invent a walker pass. A controller-owned headless Chromium capture can supply
screenshots for an independent read-only visual review, with the same verification
contract and no injected dashboard token.

`scripts/autopilot-session.sh notify 'message'` uses the Linux desktop notification
bus. Native session wakeups remain the active-loop mechanism. For a deadline that
must be recorded through session loss, additionally use
`scripts/autopilot-session.sh reminder SECONDS NUMERIC_ID 'reason'`. The user-systemd
timer writes a durable reminder and notifies; it **does not resume a model**. These
transient timers survive SSH disconnects, not reboot. Reconstruct them from the
checkpoint after reboot; console LUKS unlock is still required. A reminder receipt
is not proof that a native wakeup or operator duty ran.

## Worker boundary

Every implementation, review and visual worker uses the project custom agent
`sports-worker`, with the model allocated by phase.md. Its tool allowlist contains
only `mcp__sports_worker__shell` and `mcp__sports_worker__screenshot`; native Bash,
Read, Write, browser, Agent and other tools are absent. Do not substitute a native
`general-purpose`, Explore or plugin worker. This setup overrides older briefs that
name those worker types, while preserving role, model and independent-review rules.

The controller-owned MCP server always runs shell commands through Linux bubblewrap:
private process/network namespaces, empty home/runtime directories, read-only code
and dependencies, one selected writable worktree per command, and shared results.
`/srv`, SSH/login credentials and Docker are absent. Tool failure cannot fall back to
host execution. Project hooks are extra checking only: Claude command-hook timeouts
can fail open, so hooks alone are never claimed as the isolation boundary.

Call the shell tool with the assigned worktree cwd and command. Task selection is
not bound to an agent ID; the controller enforces assignment through the ledger.
Reviewers use main read-only, returning findings or writing `.superpowers/sdd/results/`.
Shared Git metadata stays read-only. The controller inspects/commits worker patches
and produces the exact-SHA review package; historical worker-commit instructions
are superseded. Worker screenshots come only through the screenshot tool from the
fixed read-only `.superpowers/sdd/screenshots/` directory, populated by the controller.

Tests use independent PostgreSQL 16.15 on host loopback 5433 through a Unix socket
proxy. The host user service owns `/run/user/1000/sports-test-db`; the sandbox sees
`/run/sports-test-db` and a non-superuser test account. No general network route or
server-file/program privilege is provided. Only the shared lock inode is mounted at
`SPORTS_TEST_LOCK_FILE`; worker receipts stay in private `/tmp/sports-test-state`,
with controller release receipts inaccessible. The supported runner serializes
suites; this is cooperative scheduling, not SQL access control.

Use `make test` for full evidence or `make test TEST_ARGS='tests/test_x.py'` for scoped
evidence. Full acceptance requires no filters, clean source and the exact candidate.
The controller may run lengthy suites on the worker's branch under the same lock.
Historical branches must first integrate the current runner/socket helpers.
Before dispatch, demonstrate the actual custom-agent tool inventory, sandbox/DB
negative checks, screenshot delivery and the positive targeted test path. Any missing
capability is a setup failure, never permission to give a worker raw host tools.

## Runtime operations

Use `scripts/omarchy.sh {health,status,sql,preflight,logs}` or the corresponding
Omarchy Make targets. From Omarchy these run locally; Mac observation uses SSH.
Every runtime Compose operation selects `/srv/sports-harness/sports-compose`.
The old `.env.nas` profile and deployment targets remain retired-source records.

Release from clean main through `make deploy-omarchy-app` or `make deploy-omarchy`.
Read deploy.md first. The runtime `.env`, credentials, pinned PostgreSQL image,
600 GB capacity alert budget and RFQ-off posture are preserved. No blind environment
regeneration, no direct Compose build from the old runtime source directory, and no
automatic retry after a failed release. Inspect its durable release receipt first.

The NAS-era recorder restart thresholds are retired. Observe RSS, host available
memory, cadence and tape/executor health together; a high recorder RSS on an otherwise
healthy 32 GB host is evidence for profiling, not a scheduled restart. Keep the
original memory acceptance and findings explicit until their checks are satisfied.

## Provisioning record

Install `deploy/omarchy/sports-test-db-proxy.service` under
`~/.config/systemd/user/`, then `systemctl --user daemon-reload` and
`systemctl --user enable --now sports-test-db-proxy.service`. Its RuntimeDirectory
requires no sudo. Provision the existing host test-suite.lock and the main checkout's
`.superpowers/sdd/results` and `screenshots` directories before the sandbox smoke.
Test PostgreSQL's bootstrap account must not be reachable through the socket: the
application test role has CREATEDB, NOSUPERUSER, NOCREATEROLE, NOREPLICATION and
NOBYPASSRLS, with no server-file/program roles. Controller administration stays
through the test container's local Docker exec connection. Never copy production
credentials or mount production volumes into this service.

Hook envelope assumptions follow the official [Claude hooks reference](https://code.claude.com/docs/en/hooks):
subagent tool hooks include `agent_id`, and project hooks run inside subagents.
The actual bounded Claude drill must confirm this installation before loop dispatch.
