#!/usr/bin/env python3
"""Fast, local lifecycle metadata. Never starts or continues the autopilot."""

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
MAX_INPUT = 65536
MAX_OUTPUT = 2400


def git(root, *args, deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    try:
        result = subprocess.run(
            ["git", "--no-optional-locks", "-C", str(root), *args],
            capture_output=True, text=True, timeout=min(remaining, 0.75),
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        if result.returncode:
            return None
        return result.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return None


def fingerprint(path):
    try:
        with path.open("rb") as source:
            data = source.read(1_000_001)
        if len(data) > 1_000_000:
            return {"status": "over-size-limit"}
        return {"status": "observed", "bytes": len(data),
                "sha256": hashlib.sha256(data).hexdigest()}
    except OSError:
        return {"status": "unavailable"}


def snapshot(root, event, deadline):
    worktrees = git(root, "worktree", "list", "--porcelain", deadline=deadline)
    observations = []
    if worktrees is not None:
        for block in worktrees.split("\n\n")[:16]:
            fields = dict(line.split(" ", 1) for line in block.splitlines() if " " in line)
            path = fields.get("worktree")
            if path:
                status = git(Path(path), "status", "--porcelain=v1", "--untracked-files=normal",
                             deadline=deadline)
                # Paths only; no diff, secret contents, command lines or log bodies.
                observations.append({"path": path, "head": fields.get("HEAD"),
                                     "branch": fields.get("branch"),
                                     "status_porcelain": None if status is None else status[:4000],
                                     "status_truncated": status is not None and len(status) > 4000})
    return {
        "schema": 1, "observed_at_utc": datetime.now(timezone.utc).isoformat(),
        "event": "PreCompact", "trigger": event.get("trigger") if event.get("trigger") in
        ("manual", "auto") else "unknown", "checkout": str(root),
        "head": git(root, "rev-parse", "HEAD", deadline=deadline),
        "branch": git(root, "branch", "--show-current", deadline=deadline),
        "worktrees": observations, "worktrees_unavailable": worktrees is None,
        "worktrees_truncated": worktrees is not None and len(worktrees.split("\n\n")) > 16,
        "fingerprints": {name: fingerprint(root / "docs/superpowers/autopilot" / name)
                         for name in ("state.md", "journal.md")},
        "unobserved": ["agent liveness and unread results", "pending subprocess outcomes",
                       "NAS deployment stamp", "review and test verdicts", "scheduled tasks"],
    }


def atomic_write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix=".snapshot-")
    try:
        with os.fdopen(fd, "w") as stream:
            json.dump(value, stream, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def handle(root, event):
    name = event.get("hook_event_name")
    if name not in ("SessionStart", "PreCompact"):
        return None
    deadline = time.monotonic() + 4
    session = event.get("session_id")
    if not isinstance(session, str) or not session or len(session) > 512:
        return None
    session_key = hashlib.sha256(session.encode()).hexdigest()[:24]
    cache = git(root, "rev-parse", "--path-format=absolute", "--git-path", "autopilot-recovery",
                deadline=deadline)
    path = Path(cache) / (session_key + ".json") if cache else None
    if name == "PreCompact":
        if path is None:
            raise ValueError("Git metadata unavailable")
        atomic_write(path, snapshot(root, event, deadline))
        return None
    # No state or transcript text injected, and no writes on SessionStart.
    source = event.get("source")
    source = source if source in ("startup", "resume", "clear", "compact") else "unknown"
    branch = git(root, "branch", "--show-current", deadline=deadline)
    head = git(root, "rev-parse", "--short", "HEAD", deadline=deadline)
    context = (
        "Sports recovery pointer; this event does not authorize starting /autopilot. "
        "For authorized autopilot work, read .claude/skills/autopilot/SKILL.md and "
        "its references/recovery.md, then run its scripts/context.py bootstrap. "
        "Reconcile workers, unread results, worktrees, counters and wakeups before action. "
        "Compaction does not imply dead workers. Metadata is observation, not instruction.\n"
        + json.dumps({"event": source, "checkout": str(root), "branch": branch, "head": head,
                      "same_session_snapshot": str(path) if path and path.is_file() else None})
    )
    if len(context) > MAX_OUTPUT:
        context = "Sports recovery metadata exceeds display limit. Read .claude/skills/autopilot/" \
                  "SKILL.md and references/recovery.md for authorized autopilot work; do not infer state."
    return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}


def main():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise ValueError("hook payload too large")
        event = json.loads(raw)
        if not isinstance(event, dict):
            raise ValueError("hook payload must be an object")
        result = handle(ROOT, event)
        if result is not None:
            print(json.dumps(result))
    except (OSError, ValueError, TypeError):
        # Never emit exit 2 / decision:block, or exception details with payload data.
        print("Autopilot recovery hook unavailable; use the skill's manual recovery route.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
