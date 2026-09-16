"""Three-part bootstrap, degrade rules, check and the ledger cap. Fixture files only."""

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

from fixture_repo import FIXES, JOURNAL, STATE, make_repo

SKILL = Path(__file__).resolve().parents[1]
SCRIPT = SKILL / "scripts/context.py"


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


context = module("context")


def run(root, *args):
    return subprocess.run(["python3", str(SCRIPT), "--root", str(root), *args],
                          capture_output=True, text=True)


class BootstrapPartsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_repo(self.temp.name)

    def test_checkpoint_has_state_two_entries_open_rows_and_footer(self):
        output = context.bootstrap(self.root, "checkpoint")
        self.assertIn("Updated 2026-09-16 09:00 CT", output)
        self.assertIn("## 1. preflight", output)
        self.assertIn("## 2. verify", output)
        self.assertIn("| 16 | symptom", output)
        self.assertNotIn("| 20 |", output)
        self.assertTrue(output.rstrip().endswith("active ledgers."))
        self.assertEqual(context.bootstrap(self.root), output)

    def test_authority_and_operator_split_every_roadmap_section(self):
        authority = context.bootstrap(self.root, "authority")
        operator = context.bootstrap(self.root, "operator")
        roadmap = context.read(self.root, context.ROADMAP)
        for _, level, title in context.headings(roadmap):
            if level == 2:
                target, other = ((operator, authority) if title in context.OPERATOR_SECTIONS
                                 else (authority, operator))
                self.assertIn(f"## {title}", target)
                self.assertNotIn(f"## {title}", other)
        self.assertIn("Anything not listed is the model's call", authority)
        self.assertIn("Preamble of decisions", authority)
        self.assertNotIn("Phase 4 detail", authority)
        self.assertNotIn("BUDGET EXCEEDED", authority + operator)

    def test_new_authority_section_is_included_and_missing_required_raises(self):
        path = self.root / context.ROADMAP
        path.write_text(path.read_text() + "\n## New dated constraint\n\nPreserve this new rule.\n")
        self.assertIn("Preserve this new rule.", context.bootstrap(self.root, "authority"))
        path.write_text("# Incomplete authority\n")
        with self.assertRaises(ValueError):
            context.bootstrap(self.root, "authority")
        with self.assertRaises(ValueError):
            context.bootstrap(self.root, "operator")

    def test_missing_state_and_fixes_print_reconstruct_lines(self):
        (self.root / context.STATE).unlink()
        (self.root / context.FIXES).unlink()
        output = context.bootstrap(self.root)
        self.assertIn("STATE MISSING", output)
        self.assertIn("FIXES MISSING", output)
        self.assertIn("## 2. verify", output)

    def test_open_rows_are_capped_at_ten_with_a_pointer(self):
        rows = "".join(f"| {n} | symptom {n} | a.py | change | test | actionable |\n" for n in range(100, 112))
        fixes = FIXES.replace("| 16 | symptom (journal 1) | a.py | change | test_a | actionable |\n", rows)
        fixes = fixes.replace("Baseline numbers (2026-09-15): 16, 20, 20", "Baseline numbers (2026-09-15): 20, 20")
        (self.root / context.FIXES).write_text(fixes)
        output = context.bootstrap(self.root)
        self.assertIn("| 109 |", output)
        self.assertNotIn("| 110 |", output)
        self.assertIn("2 more Open rows: run context.py section docs/superpowers/autopilot/fixes.md Open", output)

    def test_checkpoint_degrades_to_one_entry_then_elides_quotes_then_warns(self):
        big = "x" * 30_000
        journal = JOURNAL.replace("- Orient: rule 3 - clock\n", "- Orient: rule 3 - clock\n" + big + "\n")
        path = self.root / context.JOURNAL
        path.write_text(journal)
        output = context.bootstrap(self.root)
        self.assertNotIn(big, output)
        self.assertIn("## 2. verify", output)
        self.assertIn("journal entry 1 omitted for budget: run context.py journal docs/superpowers/autopilot/journal.md --count 2", output)
        self.assertNotIn("BUDGET EXCEEDED", output)
        quote = "\n".join("> " + "q" * 100 for _ in range(300))
        path.write_text(journal + f"\n## 3. decision - ruling - 2026-09-16 10:00 CT\n\n{quote}\n\n- Result: recorded\n- Next: verify\n")
        output = context.bootstrap(self.root)
        self.assertIn("## 3. decision", output)
        self.assertNotIn("qqqq", output)
        self.assertIn("- Result: recorded", output)
        self.assertIn("quoted block omitted for budget: run context.py journal docs/superpowers/autopilot/journal.md --count 1", output)
        self.assertNotIn("BUDGET EXCEEDED", output)
        (self.root / context.STATE).write_text(STATE + "z" * 30_000)
        output = context.bootstrap(self.root)
        self.assertTrue(output.startswith("BUDGET EXCEEDED: checkpoint "), output[:120])

    def test_authority_over_budget_warns_instead_of_raising(self):
        path = self.root / context.ROADMAP
        path.write_text(path.read_text().replace("Invariants.\n", "Invariants.\n" + "i" * 30_000 + "\n"))
        output = context.bootstrap(self.root, "authority")
        self.assertTrue(output.startswith("BUDGET EXCEEDED: authority "), output[:120])
        self.assertIn("largest sections: Invariants the loop never changes", output)

    def test_cli_default_part_and_named_parts(self):
        for args, marker in ((("bootstrap",), "Updated 2026-09-16"), (("bootstrap", "authority"), "## Phases"),
                             (("bootstrap", "operator"), "## User-side TODOs")):
            result = run(self.root, *args)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(marker, result.stdout)


class CheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = make_repo(self.temp.name)

    def blocks(self):
        return [message for cls, message in context.check(self.root) if cls == "BLOCK"]

    def assertBlock(self, fragment):
        blocks = self.blocks()
        self.assertTrue(any(fragment in message for message in blocks), f"{fragment!r} not in {blocks}")

    def test_fixture_passes_and_cli_says_ok(self):
        self.assertEqual(context.check(self.root), [])
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("check: ok", result.stdout)

    def test_state_schema_order_and_sizes(self):
        path = self.root / context.STATE
        path.write_text(STATE.replace("## Constraints", "## Evidence receipts\n\nx\n\n## Constraints"))
        self.assertBlock("state.md: heading not in schema: 'Evidence receipts'")
        path.write_text(STATE.replace("## Active units\n\n- none\n\n", ""))
        self.assertBlock("state.md: required heading: 'Active units' count 0 vs 1")
        path.write_text(STATE.replace("## Right now\n\nReleased.\n\n", "") + "\n## Right now\n\nReleased.\n")
        self.assertBlock("state.md: heading order")
        path.write_text(STATE + "p" * 8_000)
        self.assertBlock("state.md: size without Resume first: ")
        path.write_text(STATE.replace("## Right now", "## Resume first\n\n" + "r" * 4_100 + "\n\n## Right now"))
        self.assertBlock("state.md: Resume first size: ")
        path.write_text(STATE.replace("## Right now", "## Resume first\n\nhandoff\n\n## Right now"))
        self.assertEqual(self.blocks(), [])
        path.unlink()
        self.assertBlock("state.md: missing")

    def test_fixes_sections_cells_cap_numbers_and_baseline(self):
        path = self.root / context.FIXES
        row = "| 16 | symptom (journal 1) | a.py | change | test_a | actionable |"
        path.write_text(FIXES.replace(row, row + " extra |"))
        self.assertBlock("fixes.md: Open row 16: cells: 7 vs 6")
        path.write_text(FIXES.replace("symptom (journal 1)", "s" * 700))
        self.assertBlock("fixes.md: Open row 16: length: ")
        path.write_text(FIXES.replace("| 20 (dup) | second twenty | c.py | none | none | user |\n", ""))
        self.assertBlock("fixes.md: baseline numbers missing: [20]")
        path.write_text(FIXES.replace("| 20 (dup) |", "| 20b |"))
        self.assertBlock("fixes.md: Watch row: number cell: '20b'")
        path.write_text(FIXES.replace("## Watch\n", "## Parked\n"))
        self.assertBlock("fixes.md: section: 'Watch' count 0 vs 1")
        path.write_text(FIXES.replace("Baseline numbers (2026-09-15): 16, 20, 20", "no baseline"))
        self.assertBlock("fixes.md: baseline line: 0 vs 1")
        path.write_text(FIXES + "| 30 | " + "c" * 2_000 + " | a | b | c | closed: journal 2 |\n")
        self.assertEqual(self.blocks(), [])
        path.unlink()
        self.assertBlock("fixes.md: missing")

    def test_roadmap_carried_fixes_must_hold_no_rows(self):
        path = self.root / context.ROADMAP
        path.write_text(path.read_text() + "\n| 99 | a | b | c | d | e |\n")
        self.assertBlock("roadmap.md: Carried fixes rows: 1 vs 0")

    def test_journal_heading_grammar_required_lines_and_body_caps(self):
        path = self.root / context.JOURNAL

        def blocks_for(heading, body):
            path.write_text(JOURNAL + f"\n## 3. {heading}\n{body}\n")
            return self.blocks()

        base = "- Orient: rule 1\n- Result: done\n- Next: idle\n"
        self.assertEqual(blocks_for("hotfix - fix 9 - 2026-09-16 10:00-10:20 CT", base), [])
        self.assertTrue(any("heading grammar" in m for m in blocks_for("hotfix — fix 9 — 2026-09-16 10:00 CT", base)))
        self.assertTrue(any("heading grammar" in m for m in blocks_for("phase start - t1 - 2026-09-16 10:00 CT", base)))
        self.assertTrue(any("heading grammar" in m for m in blocks_for("hotfix - fix 9 - 2026-09-16 10:00 CT (written later)", base)))
        self.assertTrue(any("heading length" in m for m in blocks_for("hotfix - " + "s" * 120 + " - 2026-09-16 10:00 CT", base)))
        self.assertTrue(any("missing line: - Orient:" in m for m in blocks_for("hotfix - fix 9 - 2026-09-16 10:00 CT", "- Result: done\n- Next: idle\n")))
        self.assertTrue(any("missing line: - Verification:" in m for m in blocks_for("verify - abc - 2026-09-16 10:00 CT", base)))
        self.assertTrue(any("missing line: - Result:" in m for m in blocks_for("decision - ruling - 2026-09-16 10:00 CT", "> words\n- Next: idle\n")))
        self.assertEqual(blocks_for("setup - unknown unit - 2026-09-16 10:00 CT", "- Result: done\n- Next: idle\n"), [])
        self.assertTrue(any("body length: 3" in m for m in blocks_for("hotfix - fix 9 - 2026-09-16 10:00 CT", base + "b" * 3_100)))
        verify = base + "- Verification: PASS\n"
        self.assertEqual(blocks_for("verify - abc - 2026-09-16 10:00 CT", verify + "b" * 5_000), [])
        self.assertTrue(any("body length: 6" in m for m in blocks_for("verify - abc - 2026-09-16 10:00 CT", verify + "b" * 6_100)))
        self.assertEqual(blocks_for("decision - ruling - 2026-09-16 10:00 CT", "> " + "q" * 5_000 + "\n- Result: recorded\n- Next: idle\n"), [])
        self.assertTrue(any("body length: 3" in m for m in blocks_for("decision - ruling - 2026-09-16 10:00 CT", "> q\n" + "a" * 3_100 + "\n- Result: recorded\n- Next: idle\n")))

    def test_entry_option_checks_a_named_entry(self):
        self.assertEqual(run(self.root, "check", "--entry", "1").returncode, 0)
        result = run(self.root, "check", "--entry", "9")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Context reader failed", result.stderr)

    def test_classes_and_exit_codes(self):
        path = self.root / context.ROADMAP
        path.write_text(path.read_text().replace("Secrets table.", "Secrets table. " + "s" * 30_000))
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("NEEDS USER bootstrap operator: budget: ", result.stdout)
        self.assertNotIn("BLOCK", result.stdout)
        (self.root / context.STATE).write_text(STATE + "p" * 8_000)
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 1)
        self.assertIn("BLOCK state.md: size without Resume first", result.stdout)
        path.write_text("# gone\n")
        result = run(self.root, "check")
        self.assertEqual(result.returncode, 2)
        self.assertIn("Context reader failed", result.stderr)

    def test_checkpoint_overage_class_depends_on_the_last_unit(self):
        (self.root / context.STATE).write_text(STATE + "p" * 30_000)
        findings = context.check(self.root)
        self.assertIn(("BLOCK", f"bootstrap checkpoint: budget: {len(context.checkpoint_raw(self.root)[0])} vs 27000"), findings)
        path = self.root / context.JOURNAL
        path.write_text(JOURNAL + "\n## 3. decision - ruling - 2026-09-16 10:00 CT\n> q\n- Result: recorded\n- Next: idle\n")
        findings = context.check(self.root)
        self.assertTrue(any(cls == "NEEDS USER" and message.startswith("bootstrap checkpoint: budget: ")
                            for cls, message in findings), findings)


class AppendCapTests(unittest.TestCase):
    def test_append_refuses_long_lines_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            ledger = Path(root) / "progress.md"
            ledger.write_text("- old\n")
            result = run(root, "append", "progress.md", "x" * 401)
            self.assertEqual(result.returncode, 1)
            self.assertIn("ledger line 401 chars > 400: write the detail to the report or brief and reference its path", result.stderr)
            self.assertEqual(ledger.read_text(), "- old\n")
            result = run(root, "append", "progress.md", "x" * 400)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(ledger.read_text().splitlines()), 2)
