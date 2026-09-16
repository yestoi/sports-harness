#!/usr/bin/env python3
"""Token and compaction figures per controller session and per CT day, from this project's
Claude Code transcripts. Read-only; prints numbers and ids, never message text."""

import argparse
import collections
import json
import re
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[4]
PROJECT = Path.home() / ".claude/projects/-home-trey-dev-sports"
CT = ZoneInfo("America/Chicago")
MIN_TURNS = 20
INPUT_KEYS = ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")


def context_of(usage):
    return sum(usage.get(key, 0) for key in INPUT_KEYS)


def session(path):
    turns = 0
    totals = collections.Counter()
    models = collections.Counter()
    compactions = []
    last = 0
    context_sum = 0
    first = None
    with open(path) as stream:
        for line in stream:
            try:
                item = json.loads(line)
            except ValueError:
                continue
            kind = item.get("type")
            if kind == "user" and item.get("isCompactSummary"):
                compactions.append(last)
                continue
            if kind != "assistant":
                continue
            message = item.get("message") or {}
            usage = message.get("usage")
            if not usage:
                continue
            first = first or item.get("timestamp")
            turns += 1
            last = context_of(usage)
            context_sum += last
            models[message.get("model", "?")] += 1
            for key in INPUT_KEYS + ("output_tokens",):
                totals[key] += usage.get(key, 0)
    if turns < MIN_TURNS or not first:
        return None
    start = datetime.fromisoformat(first.replace("Z", "+00:00")).astimezone(CT)
    return {"start": start, "id": Path(path).stem[:8], "turns": turns, "compactions": compactions,
            "totals": totals, "context_sum": context_sum, "model": models.most_common(1)[0][0]}


def journal_entries_by_day(root):
    """Entries per CT day from the journal headings. A heading without a parseable date is
    attributed to the previous dated heading's day; the count of such headings is returned
    under the key "unparsed" so the denominator is honest."""
    counts = collections.Counter()
    path = Path(root) / "docs/superpowers/autopilot/journal.md"
    if not path.exists():
        return counts
    current = None
    for line in path.read_text().splitlines():
        if not re.match(r"^## \d+\. ", line):
            continue
        match = re.search(r"(\d{4}-\d{2}-\d{2}) \d{2}:\d", line)
        if match:
            current = match.group(1)
        else:
            counts["unparsed"] += 1
        if current:
            counts[current] += 1
    return counts


def table(sessions, entries_by_day):
    lines = [f"{'start CT':16} {'session':8} {'turns':>5} {'compactions (context at each)':32} "
             f"{'cache-read':>13} {'cache-new':>11} {'uncached':>9} {'output':>10} {'avg ctx':>8} model"]
    days = collections.defaultdict(collections.Counter)
    for info in sessions:
        totals = info["totals"]
        at = ", ".join(f"{value // 1000}k" for value in info["compactions"]) or "-"
        compactions = f"{len(info['compactions'])} ({at})"
        lines.append(f"{info['start']:%Y-%m-%d %H:%M} {info['id']:8} {info['turns']:5d} {compactions:32} "
                     f"{totals['cache_read_input_tokens']:13,d} {totals['cache_creation_input_tokens']:11,d} "
                     f"{totals['input_tokens']:9,d} {totals['output_tokens']:10,d} "
                     f"{info['context_sum'] // info['turns'] // 1000:7d}k {info['model']}")
        day = days[info["start"].strftime("%Y-%m-%d")]
        day["turns"] += info["turns"]
        day["compactions"] += len(info["compactions"])
        day["context_sum"] += info["context_sum"]
        day["cache_read"] += totals["cache_read_input_tokens"]
        day["output"] += totals["output_tokens"]
    lines.append("")
    if entries_by_day.get("unparsed"):
        lines.append(f"unparsed journal headings (attributed to the previous dated entry's day): {entries_by_day['unparsed']}")
    for date in sorted(days):
        day = days[date]
        entries = entries_by_day.get(date, 0)
        per_entry = day["cache_read"] // entries if entries else 0
        lines.append(f"{date} day: turns {day['turns']}, compactions {day['compactions']} "
                     f"({1000 * day['compactions'] / day['turns']:.1f} per 1,000 turns), avg context/turn "
                     f"{day['context_sum'] // day['turns']:,d}, cache-read {day['cache_read']:,d}, "
                     f"output {day['output']:,d}, journal entries {entries}, cache-read per entry {per_entry:,d}")
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", default=None, help="YYYY-MM-DD (CT); sessions starting earlier are dropped")
    parser.add_argument("--project", type=Path, default=PROJECT)
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    sessions = [info for info in (session(path) for path in sorted(args.project.glob("*.jsonl"))) if info]
    if args.since:
        sessions = [info for info in sessions if info["start"].strftime("%Y-%m-%d") >= args.since]
    sessions.sort(key=lambda info: info["start"])
    if not sessions:
        parser.exit(1, f"no sessions with at least {MIN_TURNS} assistant turns under {args.project}\n")
    print(table(sessions, journal_entries_by_day(args.root)))


if __name__ == "__main__":
    main()
