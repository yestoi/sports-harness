# Native worker isolation implementation

**Status: implementation prepared, 20 pure tests PASS; Linux kernel / actual Claude hook smoke validation pending. Not installed or enabled by this worker.**

Owned files created in the controller checkout only:

- `.claude/skills/autopilot/scripts/worker_guard.py`
- `scripts/worker-shell.py`
- `.claude/skills/autopilot/tests/test_worker_guard.py`
- `scripts/test-suite.py` (bounded follow-up: explicit shared lock file, private worker receipts)

No settings edits, main commit, SSH/SCP/Docker, host service changes, or live DB access performed. The controller's unrelated pending files were not edited.

## Enforcement design

The PreToolUse hook trusts only Claude's envelope `agent_id` to distinguish subagents. Absence leaves parent tool behavior unchanged. Invalid subagent identities, contexts or input deny. The hook must match **all tools**: subagents may use only Bash. Native Read/Edit/Write/Grep/Glob/Notebook/MCP/Agent/TaskOutput/SendMessage and unknown tools are denied because those implementations execute outside the OS namespace; a filename allowlist would have symlink/TOCTOU holes. Workers use shell reads/edits and return their ordinary final response; final responses are not intercepted tool calls.

Bash `updatedInput.command` invokes the fixed controller launcher through `/usr/bin/env -i ... /usr/bin/python3 -I`, carrying the original command in base64. Nothing from that command is evaluated by the host wrapper. The launcher repeats validation and starts `/usr/bin/bwrap` with empty inherited environment and closed extra file descriptors. No keyword filter is used. A command spelling SSH/Docker/HTTP differently still has no host network, agent socket, Docker socket or production filesystem to reach.

The worker namespace starts with a new root and uses `--unshare-all`, explicit `--unshare-user`, `--disable-userns`, `--cap-drop ALL`, `--die-with-parent`, `--new-session`, private `/proc`, minimal `/dev`, temporary `/tmp` and HOME. System `/usr` and bin/lib aliases are read-only; when the shared venv interpreter resolves under `/home/trey/.local/share/uv/python`, only its exact installation directory is additionally mounted read-only (not all uv runtimes or the user home); only selected harmless `/etc` runtime files are exposed. No broad host `/home`, `/etc`, `/run`, `/srv`, `/root`, SSH-agent environment or credential home directory is mounted. The root mount is read-only after setup; writable submounts remain writable.

Controller `/home/trey/dev/sports` is read-only for source, authority, shared `.venv` and Git common metadata. Known credential paths are hidden at every traversed repository depth: `secrets`, `.env*`, `.ssh`, common credential/config directories/files, Git config and local Claude settings. Credential symlinks are rejected. Other symlinks cannot reach host paths missing from the namespace. Special files under exposed repository roots and hardlinked writable regular files fail closed.

A literal leading `cd /home/trey/dev/sports-wt/<task> && ...` selects one worktree. Its `.git` pointer, common-directory value and backpointer must identify the controller repository's registered worktree. Canonical real paths are required; symlink roots, parent traversal and unsupported repositories fail closed. The actual hook cwd can also already be within a registered task. Without selection, a worker in main gets only read-only main access, not a guessed/all-worktrees mount. Shell expansion does not select a task; only the initial literal cd prefix is parsed, leaving heredoc bodies untouched. Switching from an existing task cwd to another task is denied.

The selected worktree is writable, but its `.git` pointer and existing `.claude`, `CLAUDE.md`, `docs/superpowers/autopilot` authority paths are read-only. Shared Git metadata is never writable: `git diff/status/log` can read it, while worker commits are deliberately unavailable. The controller agreed to commit worker diffs before reviewing an exact SHA as a scoped setup exception to legacy worker-commit practice.

Two additional narrow writable mounts are intentional:

1. `/home/trey/dev/sports/.superpowers/sdd/results` for worker reports, including review agents running from main.
2. Only the existing `/home/trey/.cache/sports-harness/test-state/test-suite.lock` file, mounted at `/run/sports-test-suite.lock`. The readonly root prevents unlinking/replacing this mount. `SPORTS_TEST_LOCK_FILE` selects this exact shared inode, and pytest inherits its open file descriptor.

The reports directory and regular, single-link, nonsymlink lock file must be provisioned by the controller; missing prerequisites deny. Worker receipts go to private `/tmp/sports-test-state` via `SPORTS_TEST_STATE_DIR`. The controller state/receipt directory is never mounted; workers cannot forge controller release receipts. Other host cache/config directories are absent.

## Test DB interface

Only host `/run/user/<uid>/sports-test-db`, mounted at worker `/run/sports-test-db`, may expose a service. The host path is provisioned by user-systemd RuntimeDirectory without sudo. If present it must be a real directory containing only the Unix socket `.s.PGSQL.5433` (or be empty while unprovisioned). The directory is mounted read-only and `SPORTS_TEST_SOCKET=/run/sports-test-db` is set. The controller confirmed a socat proxy with this shape. No host network namespace is shared; TCP localhost inside the worker is its own namespace.

Controller obligations: provision that socket to the isolated **test** PostgreSQL on host port 5433; verify its database/server identity; never point it to production PostgreSQL. Rebase/update task branches with the controller's socket-aware `scripts/testdb.py`, shared-state-aware suite runner and Makefile before worker tests. The bounded follow-up changes only `scripts/test-suite.py` to accept `SPORTS_TEST_LOCK_FILE`; testdb/Makefile remain controller-owned and there is no silent host TCP fallback.

## Hook integration contract

Official [Claude hooks reference](https://code.claude.com/docs/en/hooks) documents `agent_id` only on subagent calls and `PreToolUse.hookSpecificOutput.updatedInput` replacing the entire tool input. The hook preserves allowed unchanged Bash fields and explicitly rejects `dangerouslyDisableSandbox`, detached/background Bash, and unknown fields. Controller calls without `agent_id` return `{}`.

The controller should register the trusted main hook for every PreToolUse tool (not just Bash), invoking Python with `-I`, from the fixed main path. Installation outside `/home/trey/dev/sports` is unsupported and worker calls deny. Permit enough hook timeout for the credential/special-file scan; large trees are scanned during both hook validation and launch. The hook returns structured deny for handled failures; the launcher returns 126 if unavailable, with no unsandboxed fallback.

## Completed validation

Command: `python3 .claude/skills/autopilot/tests/test_worker_guard.py`

Result: **20 tests passed**. These are pure tests with temporary filesystem layouts and mocked Linux/bwrap availability; they never invoke a sandbox or DB. They cover parent passthrough, native-tool denial for sensitive and ordinary paths, unsupported contexts and bypass fields, literal worktree selection and symlink/backpointer refusal, heredoc preservation, base64 command transport, writable/read-only mount sets, isolated environment/FD launch, exact uv runtime selection, credential masks, hardlink/special-file refusal, exact test socket shape, absent prerequisites and malformed JSON denial. Changed files parse as Python; the suite runner follows its existing process/FD lifecycle.

Prepared file SHA-256 values:

- `scripts/worker-shell.py`: `d59924f2482b59f4ee47ac67d92e7583d8fb058ec91193f4f36e6fd14b4b1845`
- `worker_guard.py`: `5b8497584a741bebba636dd60218ab88e2116e9a2c37f6ac731267a8fc96b3de`
- `test_worker_guard.py`: `3c5ac937ec5e0ee842ce49fc61b4a33693cc8e6a916dd609dcd1ba2ea7ebbacf`

## Required Omarchy / Claude validation before relying on isolation

1. Run the pure tests on the intended Linux version. Confirm bwrap supports the requested flags and user namespaces work under the actual worker uid; unsupported versions must fail rather than fall back.
2. In a disposable task worktree, run through the actual hook and launcher: read shared authority; write a task sentinel and report sentinel; verify main source, main/task hook authority and common Git metadata cannot be written. Verify `.venv` imports resolve using the read-only shared environment.
3. Use harmless sentinel content to prove `/srv`, credential homes, known nested `.env`/secrets and other worktrees are absent/inaccessible. Confirm arbitrary Python networking cannot reach host loopback/LAN, host `/proc` processes are absent, and no Docker/SSH-agent socket is exposed. Do not print real secret content as a probe.
4. Verify test connection through the sole proxy, target identity, per-branch `make test` behavior and shared lock contention with a controller runner. Socket failure must not cause a production/TCP fallback.
5. Run a real native Claude subagent: record that PreToolUse includes `agent_id`, updatedInput actually replaces Bash, Read/Edit/MCP/Agent calls deny, disable-sandbox input denies, and the parent remains operational. Confirm native tool calls fail closed if prerequisites are missing. Also test malformed hook input and actual hook process/timeouts: client behavior on hook failure is an integration concern, not proven by the Python tests.

## Explicit limits

This is a tool/namespace boundary for the configured native Claude path, not a hostile-user or kernel security boundary. The same Unix user still owns the controller process; an unwrapped tool path, missing hook, older client omitting `agent_id`, timeout/error behavior that skips enforcement, or another trusted hook replacing the approved command would invalidate the guarantee. Settings integration and actual-version smoke evidence are mandatory. It does not retroactively constrain existing unsandboxed worker processes.

The sandbox does not impose cgroup CPU/memory/disk quotas. It prevents direct production filesystem/network access, not denial of service through shared host resources. Shared reports and selected task files are untrusted worker artifacts; a worker can damage those artifacts, interfere with the shared lock, or damage test databases. Controller receipts are excluded from the namespace. The controller must not execute unreviewed worker artifacts outside the sandbox. Per-agent ownership of a selected task remains a controller-ledger responsibility, as agreed; any worker initially in main can select a registered task by literal prefix, and this implementation does not authenticate assignments.

Repository contents and `/usr` are presumed controller-provisioned; the launcher hides named credential locations, but cannot identify secrets deliberately copied under arbitrary source filenames or committed into Git object history. No access to credential home directories is granted. The test proxy's backend is trusted controller configuration. Python installations requiring interpreters outside `/usr` or the narrowly mounted uv installation are unsupported until explicitly accommodated; no broad home bind is used to make them work.

Instruction-like external data encountered: the documentation includes “Fetch the complete documentation index” and explanatory hook examples. They were treated as documentation/data, not authority to install hooks or run sample commands. No hook settings or runtime configuration was changed by this worker.

The optional traversal optimization was not applied: credential/special-file masking still scans mounted repository/runtime trees. Measure actual hook latency and retain sufficient timeout; no security checks were dropped on an unmeasured performance assumption.

## Bounded correction after the first Omarchy smoke

The controller reported bwrap refusing `--disable-userns` without explicit `--unshare-user`; the launcher now requests both. Reviewer identified that a shared writable test-state directory exposed release receipts and allowed replacing the lock file. That directory bind has been removed in favor of the sole existing lock inode. Pure regressions verify no controller receipt ancestor is mounted, symlink/hardlink lock targets are rejected, the root stays readonly around the lock, and the suite runner passes that inode's FD to its child while writing only a private receipt. **20 pure tests pass** after the correction. Actual bwrap receipt-access/unlink denial and live Claude behavior remain controller smoke checks; no runtime access was performed here.
