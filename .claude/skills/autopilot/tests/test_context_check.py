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
