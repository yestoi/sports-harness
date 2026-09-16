#!/usr/bin/env python3
"""Read canonical Markdown sections without summaries or silent truncation; check recording budgets."""

import argparse
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]
AUTOPILOT = Path("docs/superpowers/autopilot")
ROADMAP = AUTOPILOT / "roadmap.md"
STATE = AUTOPILOT / "state.md"
JOURNAL = AUTOPILOT / "journal.md"
FIXES = AUTOPILOT / "fixes.md"

BUDGET = 27_000          # characters per bootstrap part; the harness persists results over about 30,000
OPEN_ROWS_SHOWN = 10
STATE_LIMIT = 8_000      # state.md minus its Resume first section
RESUME_LIMIT = 4_000
ROW_LIMIT = 600          # Open and Watch rows
LEDGER_LIMIT = 400
HEADING_LIMIT = 120      # journal heading text after "## "
BODY_LIMIT = 3_000
BODY_LIMITS = {"verify": 6_000, "deploy": 6_000, "repair": 6_000}
ORIENT_UNITS = {"preflight", "hotfix", "deploy", "verify", "operate", "phase", "plan-next", "idle", "repair"}
VERIFICATION_UNITS = {"verify", "deploy"}
QUOTE_UNITS = {"decision", "gate"}

OPERATOR_SECTIONS = (
    "Current host and restart setup (user-directed, 2026-09-12)",
    "Secrets (provision when convenient; the loop never blocks on them)",
    "Operator calendar (America/Chicago)",
    "User-side TODOs",
)
AUTHORITY_REQUIRED = {
    "Phases (spec §15)", "Decisions (2026-09-07)", "Standing authorizations (user, 2026-09-07)",
    "Files and sections the loop may edit",
    "Invariants the loop never changes (hard-forbidden; always a gate, never a ruling)",
    "Carried fixes", "Pre-loaded decisions",
}
STATE_SECTIONS = ("Resume first", "Right now", "Order of work", "Active units",
                  "Pending results", "Counters and deadlines", "Constraints")
STATE_REQUIRED = STATE_SECTIONS[1:]
FIX_SECTIONS = ("Open", "Watch", "Closed")
HEADING_RE = re.compile(r"^(\d+)\. ([a-z][a-z0-9-]*) - (.+) - "
                        r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}(?:-\d{2}:\d{2})? CT)$")
ROW_RE = re.compile(r"^\|\s*\d+")
NUMBER_RE = re.compile(r"^(\d+)( \(dup\))?$")
BASELINE_RE = re.compile(r"^Baseline numbers \([\d-]+\): (.+)$", re.M)
FOOTER = ("Next: run context.py bootstrap authority, then context.py bootstrap operator; then the selected "
          "procedure, applicable Pre-loaded decisions and active ledgers.\n")
STATE_MISSING = "STATE MISSING: reconstruct from journal and active ledgers; do not reset counters.\n"
FIXES_MISSING = ("FIXES MISSING: reconstruct fixes.md from roadmap.md history and the journal before Orient; "
                 "do not select idle\n")


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
    entries_ = headings(text)
    matches = [item for item in entries_ if item[2] == title]
    if len(matches) != 1:
        raise ValueError(f"expected one heading {title!r}; found {len(matches)}")
    start, level, _ = matches[0]
    end = next((line for line, depth, _ in entries_ if line > start and depth <= level),
               len(text.splitlines(keepends=True)))
    return start, end, "".join(text.splitlines(keepends=True)[start:end])


def entries(text):
    """Every numbered journal entry as (number, start line, end line, heading text, body)."""
    lines = text.splitlines(keepends=True)
    starts = [(line, title) for line, level, title in headings(text)
              if level == 2 and re.match(r"\d+\.\s", title)]
    result = []
    for index, (start, title) in enumerate(starts):
        end = starts[index + 1][0] if index + 1 < len(starts) else len(lines)
        body = "".join(lines[start + 1:end]).rstrip()
        result.append((int(title.split(".", 1)[0]), start, end, title, body))
    return result


def entry(text, number):
    matches = [item for item in entries(text) if item[0] == number]
    if len(matches) != 1:
        raise ValueError(f"expected one journal entry {number}; found {len(matches)}")
    return matches[0]


def journal_tail(text, count=2):
    starts = [line for line, level, title in headings(text)
              if level == 2 and re.match(r"\d+\.\s", title)]
    if not starts:
        raise ValueError("no numbered journal entries found")
    lines = text.splitlines(keepends=True)
    start = starts[-count] if len(starts) >= count else starts[0]
    return start, len(lines), "".join(lines[start:])


def cells(row):
    """Table cells of one Markdown row. A literal pipe inside a cell is written as backslash-pipe;
    the cells are returned as written (the backslash is kept, nothing is unescaped)."""
    inner = row.strip()
    if inner.startswith("|"):
        inner = inner[1:]
    if inner.endswith("|"):
        inner = inner[:-1]
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", inner)]


def read(root, path):
    target = (root / path).resolve()
    target.relative_to(root.resolve())
    return target.read_text()


def render(path, selected):
    start, end, content = selected
    return f"Source: {path}:{start + 1}-{end}\n{content.rstrip()}\n"


def assemble(chunks):
    return "\n".join(text for _, text in chunks)


def budget_warning(part, chunks, output):
    largest = sorted(chunks, key=lambda item: -len(item[1]))[:3]
    summary = ", ".join(f"{title} {len(text)}" for title, text in largest)
    return f"BUDGET EXCEEDED: {part} {len(output)} chars > {BUDGET}; largest sections: {summary}\n\n"


def open_rows_chunk(root):
    try:
        fixes = read(root, FIXES)
        start, end, text = section(fixes, "Open")
    except (OSError, ValueError):
        return FIXES_MISSING
    lines = text.splitlines(keepends=True)
    rows = [index for index, line in enumerate(lines) if ROW_RE.match(line)]
    if len(rows) > OPEN_ROWS_SHOWN:
        lines = lines[:rows[OPEN_ROWS_SHOWN]] + [
            f"{len(rows) - OPEN_ROWS_SHOWN} more Open rows: run context.py section {FIXES} Open\n"]
    return render(FIXES, (start, end, "".join(lines)))


def checkpoint_chunks(root, degrade):
    """degrade 0: two entries; 1: last entry only; 2: last entry without its quoted block."""
    chunks = []
    try:
        state = read(root, STATE)
    except OSError:
        chunks.append(("state.md", STATE_MISSING))
    else:
        chunks.append(("state.md", render(STATE, (0, len(state.splitlines()), state))))
    journal = read(root, JOURNAL)
    items = entries(journal)
    if not items:
        raise ValueError("no numbered journal entries found")
    lines = journal.splitlines(keepends=True)
    if degrade == 0 and len(items) >= 2:
        start = items[-2][1]
        chunks.append(("journal tail", render(JOURNAL, (start, len(lines), "".join(lines[start:])))))
    else:
        number, start, end, _, _ = items[-1]
        block = lines[start:end]
        note = "" if len(items) < 2 else (
            f"journal entry {number - 1} omitted for budget: run context.py journal {JOURNAL} --count 2\n")
        if degrade >= 2:
            block = [line for line in block if not line.startswith(">")]
            note += f"quoted block omitted for budget: run context.py journal {JOURNAL} --count 1\n"
        chunks.append(("journal tail", render(JOURNAL, (start, end, "".join(block))) + note))
    chunks.append(("fixes.md Open", open_rows_chunk(root)))
    chunks.append(("footer", FOOTER))
    return chunks


def checkpoint_raw(root):
    """The checkpoint part after the degrade rules, without the warning line, and its chunks."""
    for degrade in (0, 1, 2):
        chunks = checkpoint_chunks(root, degrade)
        output = assemble(chunks)
        if len(output) <= BUDGET:
            break
    return output, chunks


def authority_chunks(root):
    roadmap = read(root, ROADMAP)
    items = headings(roadmap)
    missing = AUTHORITY_REQUIRED - {title for _, _, title in items}
    if missing:
        raise ValueError(f"roadmap headings changed; read full roadmap: {sorted(missing)}")
    lines = roadmap.splitlines(keepends=True)
    first = next(line for line, level, _ in items if level == 2)
    chunks = [("preamble", render(ROADMAP, (0, first, "".join(lines[:first]))))]
    # Include future top-level authority sections automatically. Never filter by
    # phase status or guess whether a dated decision is still relevant.
    for _, level, title in items:
        if level != 2 or title in OPERATOR_SECTIONS:
            continue
        if title == "Pre-loaded decisions":
            start, end, _ = section(roadmap, title)
            child = next((line for line, depth, _ in items if start < line < end and depth == 3), end)
            chunks.append((title, render(ROADMAP, (start, child, "".join(lines[start:child])))))
        else:
            chunks.append((title, render(ROADMAP, section(roadmap, title))))
    return chunks


def operator_chunks(root):
    roadmap = read(root, ROADMAP)
    present = {title for _, level, title in headings(roadmap) if level == 2}
    missing = set(OPERATOR_SECTIONS) - present
    if missing:
        raise ValueError(f"roadmap headings changed; read full roadmap: {sorted(missing)}")
    return [(title, render(ROADMAP, section(roadmap, title))) for title in OPERATOR_SECTIONS]


def bootstrap(root, part="checkpoint"):
    """Keep authority intact; only phase-specific decisions load on demand."""
    if part == "checkpoint":
        output, chunks = checkpoint_raw(root)
    elif part == "authority":
        chunks = authority_chunks(root)
        output = assemble(chunks)
    elif part == "operator":
        chunks = operator_chunks(root)
        output = assemble(chunks)
    else:
        raise ValueError(f"unknown bootstrap part {part!r}")
    if len(output) > BUDGET:
        output = budget_warning(part, chunks, output) + output
    return output


def append_line(path, text, now=None):
    """Append one ledger line stamped from the clock (America/Chicago), never from memory."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if len(text) > LEDGER_LIMIT:
        raise ValueError(f"ledger line {len(text)} chars > {LEDGER_LIMIT}: write the detail to the "
                         "report or brief and reference its path")
    when = (now or datetime.now(ZoneInfo("America/Chicago"))).astimezone(ZoneInfo("America/Chicago"))
    with Path(path).open("a", encoding="utf-8") as handle:
        handle.write(f"- {when:%Y-%m-%d %H:%M} CT: {text}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    sub = parser.add_subparsers(dest="command", required=True)
    boot = sub.add_parser("bootstrap")
    boot.add_argument("part", nargs="?", default="checkpoint", choices=("checkpoint", "authority", "operator"))
    for command in ("headings", "section", "journal", "append"):
        child = sub.add_parser(command)
        child.add_argument("path", type=Path)
        if command == "section":
            child.add_argument("title")
        if command == "append":
            child.add_argument("text")
        if command == "journal":
            child.add_argument("--count", type=int, default=2)
    args = parser.parse_args()
    try:
        if args.command == "bootstrap":
            output = bootstrap(args.root, args.part)
        elif args.command == "append":
            try:
                append_line(args.root / args.path, args.text)
            except ValueError as error:
                parser.exit(1, f"{error}\n")
            output = f"appended to {args.path}"
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
