# Independent controller preparation review

**Verdict: source repairs acceptable; runtime launch acceptance remains pending.** The reviewed fixed MCP design addresses the command-hook bypass and the shared-state escape found during this review. Do not treat these pure/source checks as permission to launch workers before the actual custom-agent, kernel and database checks below pass.

Repository HEAD: `073732ffa30c15ea07eb2a6c441a9dc34cbca3de`. The reviewed preparation was uncommitted and edited concurrently; final SHA-256 fingerprints below identify the inspected source. Scope: worker launcher, MCP server/config/custom agent, hook and pure tests, session helper, test runner/database helper, Linux controller reference and changed routing. Release implementation was excluded. This report is the only repository artifact this reviewer wrote. No Omarchy, production, NAS, SSH, deployment/status target, Docker or database access occurred.

## Concrete findings and dispositions

### C5 — P1 originally: command-hook timeout could execute unwrapped host Bash — repaired by changing the capability boundary

The initial PreToolUse command hook performed complete filesystem scans before returning an updated Bash command. Official Claude documentation says timed-out command hooks do not block: normal tool permissions continue. With bypass permissions, the original raw Bash call could proceed. An allowed worker could inflate its writable tree to make the next validation exceed the external timeout; Python exception handling cannot catch that framework decision. [Official hook timeout semantics](https://code.claude.com/docs/en/hooks#timeouts).

The final design makes every supported worker the custom `sports-worker`, whose configured tools are exactly `mcp__sports_worker__shell` and `mcp__sports_worker__screenshot`. It has no native Bash/Read/Write/Agent/browser capability. The fixed controller-owned stdio server invokes only `/usr/bin/python3 -I /home/trey/dev/sports/scripts/worker-shell.py`, with cleared child environment, fixed host cwd, closed inherited FDs, and no shell=True or alternate execution route. The launcher executes commands only after bwrap construction; failures return errors. Session startup uses the committed MCP config with strict config selection. Hook timeouts therefore cannot transform an MCP request into native Bash. The old hook is explicitly defense in depth, not the boundary.

This is an acceptable source-level repair **conditional on actual Claude enforcement of the custom-agent allowlist**. The controller must capture the worker's real tool inventory and negative native-tool attempts; a file asserting an allowlist and a tools/list unit test do not prove the CLI applies it. General-purpose/plugin/Explore worker dispatch remains prohibited by the routed procedure. A trusted controller choosing an unsupported agent is outside this worker boundary, and must be caught by the kickoff drill/dispatch contract.

### C4 — P1 originally: shared receipt symlink redirected controller writes; lock replacement split suite ownership — repaired

The initial writable controller TEST_STATE bind let workers plant a receipt symlink targeting a host-only authority file. A later controller Path.write_text would follow it. Unlinking/recreating test-suite.lock also let two processes hold exclusive locks on different inodes. I reproduced both primitives using temporary stand-ins, without touching real controller state.

The final launcher binds only the preexisting regular single-link lock inode at `/run/sports-test-suite.lock`; its parent is read-only. Worker receipts use private `/tmp/sports-test-state`, and the controller receipt cache is absent. `SPORTS_TEST_LOCK_FILE` is separate from receipt storage. This removes the reviewed redirect/replacement route. Runtime checks should prove lock mount unlink/replacement fails and contention uses the same inode. The lock contents themselves carry no authority.

### C3 — P1 launch acceptance dependency: test SQL must not export privileged server execution — pending controller evidence

The network namespace constrains worker processes, not the PostgreSQL server behind the permitted socket. A bootstrap-superuser harness role would expose COPY PROGRAM, server files and potentially server-network access beyond the sandbox. I have no live role or container authority evidence and did not execute that route.

The controller reports provisioning a separate local administrator and replacing the bootstrap login with a NOSUPERUSER/NOCREATEROLE/CREATEDB/NOREPLICATION/NOBYPASSRLS harness role, without server-file/program memberships. It reports only a development data volume and default container bridge. These are plans/reported facts, not independently verified acceptance. Before any worker launch, record actual role attributes and memberships; negative COPY PROGRAM/server-read/server-write attempts; rejection of login as the privileged bootstrap/admin identity through the allowed socket; and successful isolated test database creation/schema/targeted test execution. Ensure there is no alternate privileged login reachable through the same proxy.

### C1 — P2 originally: assigned worktree isolation claim exceeded enforcement — wording repaired

A MAIN-cwd worker can choose any registered worktree with its literal leading cd, and the same agent can choose another on a later call. I reproduced this with mocked policy fixtures. The namespace grants one **selected** writable worktree per command; it does not bind assignment to an agent ID. Current docs state this accurately and retain ledger assignment as a procedural controller responsibility. Cross-worker task ownership is not a kernel-enforced guarantee.

### C2 — P2 originally: mandatory Chrome walker had no usable visual tools — routing/capability repaired

Current verify routing has the controller perform browser actions/capture, then an independent sports-worker visually review supplied images through the screenshot MCP tool. Unshown interactions remain pending. The path policy allows only exact file_path-only requests for direct children of the canonical fixed MAIN `.superpowers/sdd/screenshots` directory. It rejects symlink components, nonregular or multiply-linked files, unsupported extensions/signatures, and server responses larger than 10 MiB. O_NOFOLLOW/O_NONBLOCK and a second file/header check are used. The directory is outside writable results and remains read-only to workers, so they cannot exploit the pathname re-open through their own write access.

This is a defensible narrow image capability. The controller must keep that directory trusted and demonstrate an actual image response/visual review. An arbitrary file copied from worker-controlled results is not automatically a trusted capture. Updated Linux/phase/verify routing supersedes historical Chrome-only worker recipes and names the fixed MCP capabilities.

### C6 — P2 originally: unlimited host output spool — repaired

The first MCP implementation sent child stdout to an unlimited host TemporaryFile, retaining a 64 KiB tail only after completion. A sandboxed verbose loop could therefore fill host temporary storage via its inherited stdout. The final OutputTail drains a pipe in 16 KiB chunks and retains at most 64 KiB between reads, with no disk spool. The new pure test drains 16 MiB and checks bounded retention. This resolves the specific host-spool route; it is not a general CPU/disk/memory quota for writable task commands.

## Remaining limitations and restart checks

- MCP `notifications/cancelled` is ignored. A cancelled request can continue until its specified timeout (at most 1800 seconds), occupying one of four execution slots and possibly the shared test lock. EOF and orderly server termination kill tracked child groups; they do not make individual cancellation work. Treat this as an operational limitation and implement request-ID cancellation if prompt cancellation is required. No immediate raw-host escape follows from it.
- Current `test-suite.py` intentionally requires a named branch; its earlier misleading explicit-TEST_DB alternative wording has been corrected. Exact-SHA review must use a named branch/worktree. Historical task branches need current Makefile/runner/socket helpers before contained tests, because the old helper connects to localhost inside the private network namespace.
- The suite lock is cooperative: arbitrary worker SQL or a changed test script can ignore it. Current docs distinguish supported-runner serialization from SQL access control. Private worker receipts are not controller release receipts.
- The session helper's fixed tmux name and flock prevent duplicate starts through that entrypoint and preserve a running controller across terminal loss. It does not restart a model after reboot. Systemd reminders are transient and must be reconstructed from checkpoints; delivery receipts do not prove a model woke up. Current docs accurately describe these limits.
- The controller reports a successful actual bwrap venv/SELECT 1 smoke after explicit --unshare-user and narrow uv alias fixes. I inspected these source changes but did not observe that kernel/runtime test. Actual negative filesystem/network access, immutable authority/lock mount checks, and worker tool inventory/image delivery remain separate acceptance evidence.

## Verification performed

- `python3 .claude/skills/autopilot/tests/test_worker_guard.py`: **24 passed**, pure mocked policy/argv and temporary-file tests.
- `python3 .claude/skills/autopilot/tests/test_worker_tools.py`: **11 passed**, pure protocol, fixed invocation/error, bounded output, timeout, screenshot and concurrency/config tests. Child process execution is mocked.
- Additional temporary-directory symlink/lock primitives and same-agent/two-task policy reproduction passed earlier in review. An independent patched-Popen protocol probe confirmed initialization, exactly two server tools, unknown-tool denial and failure without fallback.

No application suites or database tests ran. No instruction-like external/provider payload was encountered. Historical deployment/tool directions in reviewed files were treated as review data and were not executed.

## Final inspected source fingerprints

```text
d88b8618a834d694e5bb3f31e99d10668badb40d58508e5d769cbb9a5f69cd63  scripts/worker-shell.py
2beca209696938d9da743b73a51254fdc2cfb574e4b787bb8ad5cebae8414bd7  scripts/worker-tools.py
02d6df68efe78e4af27335982354c112ac68369dd1a06fc0fb9dda536641b329  .mcp.json
f47a568a375e2eacf3ac92d92438adcf215caff0624097793073d973f8477faf  .claude/agents/sports-worker.md
0fc64cf036e9317732501fca88ceb4e128ae27e19c125b4ae26c3e202ee88a02  .claude/skills/autopilot/scripts/worker_guard.py
2a5501a5b44ab266b1a974e651c5bd27c806c89ae2b86ffe6512640a52d51be1  .claude/skills/autopilot/tests/test_worker_guard.py
57f15f38bc16dccd1d82d84dd17468f981c38406ed6655d6fcf5404e0725fbb1  .claude/skills/autopilot/tests/test_worker_tools.py
415ec20e26aeffb320b64e4e77af0b0da2f0e9adbd72a28070bd689aa844fdc7  scripts/autopilot-session.sh
814fb7444449b85ee006c74af2f7eea6aef198a5328700984656d9aa86db5ffe  scripts/test-suite.py
c8d2aa22489382b4b74d6fec81a712a7cc3ad05dd6b6e33daa8f74c68f7e92a2  scripts/testdb.py
63761c5838f0a1ea5775ecc93da18ae241e478c2ebe6a0c0c048b71a56ad8fc1  .claude/skills/autopilot/references/linux-controller.md
ae23a2d3adb6d6bc370b1214b5e3d01df13567aebc55c46ffccceb89aeb9c66e  .claude/skills/autopilot/references/phase.md
fb0e1679eb52e5416316f127510fa509e698b43d1e6ad7fa40b5939ef44ed4b7  .claude/skills/autopilot/references/verify.md
6f761aa9e66443b03e5d0323195f0aefc43e606eb50277bfa05d72735421f4a4  .claude/skills/autopilot/SKILL.md
ee1ae0bb908b9b2884cf0edd182c1967b4045efa1f6162373a88ee9144ec5e49  .claude/settings.json
```
