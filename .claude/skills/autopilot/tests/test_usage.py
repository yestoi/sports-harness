"""usage.py reads a synthetic transcript; never the real one."""

import importlib.util
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


usage = module("usage")


def transcript(path, turns, compact_after, day="2026-09-14"):
    lines = []
    for index in range(turns):
        if index == compact_after:
            lines.append({"type": "user", "isCompactSummary": True, "message": {"role": "user", "content": "summary"}})
        context = 1_000 * (index + 1)
        lines.append({"type": "assistant", "timestamp": f"{day}T12:{index % 60:02d}:00.000Z",
                      "message": {"model": "claude-fable-5-1", "usage": {
                          "input_tokens": 10, "cache_read_input_tokens": context - 110,
                          "cache_creation_input_tokens": 100, "output_tokens": 50}}})
    path.write_text("\n".join(json.dumps(line) for line in lines) + "\n")


class UsageTests(unittest.TestCase):
    def test_session_counts_turns_compactions_and_context(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "abcdef12-session.jsonl"
            transcript(path, turns=30, compact_after=20)
            info = usage.session(path)
            self.assertEqual(info["turns"], 30)
            self.assertEqual(info["compactions"], [20_000])
            self.assertEqual(info["totals"]["output_tokens"], 1_500)
            self.assertEqual(info["context_sum"], sum(1_000 * n for n in range(1, 31)))
            self.assertEqual(info["id"], "abcdef12")
            self.assertEqual(info["start"].strftime("%Y-%m-%d %H:%M"), "2026-09-14 07:00")
            transcript(path, turns=5, compact_after=99)
            self.assertIsNone(usage.session(path))

    def test_cli_prints_session_and_day_rows_and_filters_by_date(self):
        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            transcript(project / "aaaa1111.jsonl", 30, 20, day="2026-09-14")
            transcript(project / "bbbb2222.jsonl", 25, 99, day="2026-09-15")
            root = Path(directory) / "repo"
            (root / "docs/superpowers/autopilot").mkdir(parents=True)
            (root / "docs/superpowers/autopilot/journal.md").write_text(
                "## 1. verify - a - 2026-09-14 10:00 CT\n- Result: done\n## 2. verify - b - 2026-09-14 11:00 CT\n- Result: done\n")
            result = subprocess.run(["python3", str(SKILL / "scripts/usage.py"), "--project", str(project),
                                     "--root", str(root)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("aaaa1111", result.stdout)
            self.assertIn("bbbb2222", result.stdout)
            self.assertIn("2026-09-14 day", result.stdout)
            self.assertIn("entries 2", result.stdout)
            filtered = subprocess.run(["python3", str(SKILL / "scripts/usage.py"), "--project", str(project),
                                       "--root", str(root), "--since", "2026-09-15"], capture_output=True, text=True)
            self.assertNotIn("aaaa1111", filtered.stdout)
            self.assertIn("bbbb2222", filtered.stdout)
            self.assertNotIn("summary", result.stdout)
