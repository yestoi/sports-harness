#!/usr/bin/env python3
"""Read canonical Markdown sections without summaries or silent truncation."""

import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
AUTOPILOT = Path("docs/superpowers/autopilot")


def headings(text):
    """Return (zero-based line, level, title), ignoring fenced code examples."""
    result = []
    fence = None
    for number, line in enumerate(text.splitlines(keepends=True)):
        marker = re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line)
        if marker:
            run, rest = marker.groups()
            if fence is None:
                fence = run
            elif run[0] == fence[0] and len(run) >= len(fence) and not rest.strip():
                fence = None
            continue
        if fence is not None:
            continue
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            result.append((number, len(match[1]), match[2]))
    return result


def section(text, title):
    entries = headings(text)
    matches = [item for item in entries if item[2] == title]
    if len(matches) != 1:
        raise ValueError(f"expected one heading {title!r}; found {len(matches)}")
    start, level, _ = matches[0]
    end = next((line for line, depth, _ in entries if line > start and depth <= level),
               len(text.splitlines(keepends=True)))
    return start, end, "".join(text.splitlines(keepends=True)[start:end])


def journal_tail(text, count=2):
    starts = [line for line, level, title in headings(text)
              if level == 2 and re.match(r"\d+\.\s", title)]
    if not starts:
        raise ValueError("no numbered journal entries found")
    lines = text.splitlines(keepends=True)
    start = starts[-count] if len(starts) >= count else starts[0]
    return start, len(lines), "".join(lines[start:])


def read(root, path):
    target = (root / path).resolve()
    target.relative_to(root.resolve())
    return target.read_text()


def render(path, selected):
    start, end, content = selected
    return f"Source: {path}:{start + 1}-{end}\n{content.rstrip()}\n"


def bootstrap(root):
    """Keep authority intact; only phase-specific decisions load on demand."""
    roadmap_path = AUTOPILOT / "roadmap.md"
    roadmap = read(root, roadmap_path)
    entries = headings(roadmap)
    required = {"Phases (spec §15)", "Decisions (2026-09-07)",
                "Standing authorizations (user, 2026-09-07)",
                "Files and sections the loop may edit",
                "Invariants the loop never changes (hard-forbidden; always a gate, never a ruling)",
                "Operator calendar (America/Chicago)", "Carried fixes", "Pre-loaded decisions"}
    missing = required - {title for _, _, title in entries}
    if missing:
        raise ValueError(f"roadmap headings changed; read full roadmap: {sorted(missing)}")
    first_section = next(line for line, level, _ in entries if level == 2)
    output = [render(roadmap_path, (0, first_section,
              "".join(roadmap.splitlines(keepends=True)[:first_section])))]
    # Include future top-level authority sections automatically. Never filter by
    # phase status or guess whether a dated decision is still relevant.
    for _, level, title in entries:
        if level == 2 and title != "Pre-loaded decisions":
            output.append(render(roadmap_path, section(roadmap, title)))
        elif level == 2:
            start, end, _ = section(roadmap, title)
            first_child = next((line for line, depth, _ in entries
                                if start < line < end and depth == 3), end)
            output.append(render(roadmap_path, (start, first_child,
                          "".join(roadmap.splitlines(keepends=True)[start:first_child]))))
    state_path = AUTOPILOT / "state.md"
    try:
        state = read(root, state_path)
    except FileNotFoundError:
        output.append("STATE MISSING: reconstruct from journal and active ledgers; do not reset counters.\n")
    else:
        output.append(render(state_path, (0, len(state.splitlines()), state)))
    journal_path = AUTOPILOT / "journal.md"
    output.append(render(journal_path, journal_tail(read(root, journal_path))))
    output.append("Required next: read the selected unit procedure, applicable Pre-loaded decisions, "
                  "and active ledgers. This bootstrap is not the full verification contract.\n")
    return "\n".join(output)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("bootstrap")
    for command in ("headings", "section", "journal"):
        child = sub.add_parser(command)
        child.add_argument("path", type=Path)
        if command == "section":
            child.add_argument("title")
        if command == "journal":
            child.add_argument("--count", type=int, default=2)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            output = bootstrap(args.root)
        else:
            text = read(args.root, args.path)
            if args.command == "headings":
                output = "\n".join(f"{args.path}:{line + 1} {'#' * level} {title}"
                                   for line, level, title in headings(text))
            elif args.command == "section":
                output = render(args.path, section(text, args.title))
            else:
                if args.count < 1:
                    raise ValueError("journal count must be positive")
                output = render(args.path, journal_tail(text, args.count))
        print(output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Context reader failed: {error}. Read the canonical file directly.\n")


if __name__ == "__main__":
    main()
