"""The real repository stays inside the recording budgets the loop owns."""

import importlib.util
import unittest
from pathlib import Path

SKILL = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


context = module("context")


class LiveRepositoryTests(unittest.TestCase):
    def setUp(self):
        for name in ("roadmap.md", "journal.md"):
            if not (context.ROOT / context.AUTOPILOT / name).exists():
                self.skipTest(f"{name} absent; not a controller checkout")
        # A missing state.md or fixes.md is a BLOCK the first test must expose, never a skip.

    def test_check_reports_no_block(self):
        blocks = [message for cls, message in context.check(context.ROOT) if cls == "BLOCK"]
        self.assertEqual(blocks, [])

    def test_checkpoint_part_fits_the_harness_budget(self):
        raw, _ = context.checkpoint_raw(context.ROOT)
        self.assertLessEqual(len(raw), context.BUDGET)
