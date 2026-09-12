#!/usr/bin/env python3
"""Read local Claude project logs and emit aggregate observations, never prompts.

Usage: python3 audit_sessions.py PROJECT_LOG_DIR --output summary.json
An optional --before ISO_TIMESTAMP restricts messages to a historical cutoff.
Files are read only through their initial byte length, so active appends do not
make a scan unbounded. This measures requests/context, not inference latency,
task acceptance, comparative model quality, or invoiced cost.
"""

import argparse
import collections
import datetime
import json
from pathlib import Path
import statistics


def audit(project_dir, before=None):
    roots = sorted(project_dir.glob("*.jsonl"))
    workers = sorted(project_dir.glob("*/subagents/*.jsonl"))
    sources = [(path, path.stat().st_size) for path in roots + workers]
    messages = {}
    dispatches = {}
    malformed = 0
    included_roots = set()
    included_workers = set()
    for path, size in sources:
        is_root = path.parent == project_dir
        with path.open("rb") as handle:
            lines = handle.read(size).splitlines()
        for line in lines:
            try:
                record = json.loads(line)
            except (ValueError, UnicodeError):
                malformed += 1
                continue
            if before and record.get("timestamp", "") > before:
                continue
            (included_roots if is_root else included_workers).add(str(path))
            message = record.get("message", {})
            if not isinstance(message, dict):
                continue
            if record.get("type") == "assistant" and message.get("id"):
                usage = message.get("usage", {})
                context = sum(usage.get(key, 0) or 0 for key in (
                    "input_tokens", "cache_read_input_tokens",
                    "cache_creation_input_tokens",
                ))
                output = usage.get("output_tokens", 0) or 0
                candidate = {
                    "model": message.get("model", "unknown"),
                    "root": is_root, "context": context, "output": output,
                }
                old = messages.get(message["id"])
                if old is None or context + output > old["context"] + old["output"]:
                    messages[message["id"]] = candidate
            content = message.get("content", [])
            if not isinstance(content, list):
                continue
            for block in content:
                if not isinstance(block, dict):
                    continue
                if block.get("type") == "tool_use" and block.get("name") == "Agent":
                    params = block.get("input", {})
                    dispatches[block["id"]] = {
                        "model": params.get("model", "inherited"), "root": is_root,
                    }
    groups = collections.defaultdict(list)
    for message in messages.values():
        role = "controller" if message["root"] else "worker"
        groups[f"{role}:{message['model']}"].append(message)
    stats = {}
    for group, values in sorted(groups.items()):
        contexts = sorted(value["context"] for value in values)
        stats[group] = {
            "messages": len(values),
            "context_p50": int(statistics.median(contexts)),
            "context_p90": contexts[int((len(contexts) - 1) * 0.9)],
            "context_max": max(contexts),
            "messages_context_over_128000": sum(value > 128000 for value in contexts),
            "output_tokens": sum(value["output"] for value in values),
        }
    return {
        "collected_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "before": before,
        "root_files": len(included_roots),
        "worker_files": len(included_workers),
        "malformed_lines": malformed,
        "unique_assistant_messages": len(messages),
        "dispatches": len(dispatches),
        "root_dispatches": sum(value["root"] for value in dispatches.values()),
        "dispatch_models": dict(collections.Counter(
            value["model"] for value in dispatches.values()
        )),
        "model_stats": stats,
        "source_file_sizes": {str(path.relative_to(project_dir)): size for path, size in sources},
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project_dir", type=Path)
    parser.add_argument("--before")
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    result = audit(args.project_dir, args.before)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "source_file_sizes"}, indent=2))
