"""Local reader/hook checks. No Claude session, NAS, Docker or application DB."""

import importlib.util
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

SKILL = Path(__file__).resolve().parents[1]


def module(name):
    spec = importlib.util.spec_from_file_location(name, SKILL / "scripts" / (name + ".py"))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


context = module("context")
hook = module("recovery_hook")


class ReaderTests(unittest.TestCase):
    def test_last_two_complete_entries_ignore_code_examples_and_variable_length(self):
        old = "## 1. old\nold\n"
        long = "## 2. long review\n" + "finding\n" * 100
        newest = "## 3. partial delivery\n```markdown\n## 999. example\n```\nremaining T7-T11\n"
        self.assertEqual(context.journal_tail(old + long + newest)[2], long + newest)

    def test_section_keeps_subsections_and_code_fences(self):
        desired = "## Contract\n### Child\n~~~~\n## not a section\n~~~\n~~~~\nall checks\n"
        self.assertEqual(context.section("# Root\n" + desired + "## Next\nend\n", "Contract")[2], desired)

    def test_ambiguous_missing_or_empty_sources_fail(self):
        for text in ("# Other\n", "## Same\na\n## Same\nb\n"):
            with self.assertRaises(ValueError):
                context.section(text, "Same")
        with self.assertRaises(ValueError):
            context.journal_tail("# Journal\n")

    def test_bootstrap_preserves_authority_and_live_checkpoints_verbatim(self):
        output = context.bootstrap(context.ROOT)
        roadmap = context.read(context.ROOT, context.AUTOPILOT / "roadmap.md")
        for _, level, title in context.headings(roadmap):
            if level == 2 and title != "Pre-loaded decisions":
                self.assertIn(context.section(roadmap, title)[2].rstrip(), output)
        state = context.read(context.ROOT, context.AUTOPILOT / "state.md")
        journal = context.read(context.ROOT, context.AUTOPILOT / "journal.md")
        self.assertIn(state.rstrip(), output)
        self.assertIn(context.journal_tail(journal)[2].rstrip(), output)
        self.assertIn("Anything not listed is the model's call", output)
        self.assertNotIn("### Phase 4: Kalshi authenticated adapter", output)

    def test_missing_state_does_not_reset_counts_and_new_authority_is_included(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / context.AUTOPILOT
            target.mkdir(parents=True)
            for name in ("roadmap.md", "journal.md"):
                shutil.copyfile(context.ROOT / context.AUTOPILOT / name, target / name)
            with (target / "roadmap.md").open("a") as stream:
                stream.write("\n## New dated constraint\nPreserve this new rule.\n")
            output = context.bootstrap(root)
            self.assertIn("STATE MISSING", output)
            self.assertIn("Preserve this new rule.", output)
            (target / "roadmap.md").write_text("# Incomplete authority\n")
            with self.assertRaises(ValueError):
                context.bootstrap(root)


class HookTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="sports recovery test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "controller checkout"
        self.root.mkdir()
        self.git("init", "-q")
        self.git("config", "user.name", "Recovery Test")
        self.git("config", "user.email", "recovery@example.invalid")
        docs = self.root / context.AUTOPILOT
        docs.mkdir(parents=True)
        (docs / "state.md").write_text("6C partial; reviewer live; unread result; failed deploys 2.\n")
        (docs / "journal.md").write_text("## 1. deploy\nVerified build abc; remaining checks deferred.\n")
        (self.root / "code.py").write_text("original\n")
        self.git("add", ".")
        self.git("commit", "-qm", "fixture")
        self.worker = Path(self.temp.name).resolve() / "dirty implementer"
        self.git("worktree", "add", "-qb", "task-review", str(self.worker))
        (self.worker / "code.py").write_text("uncommitted work\n")
        (self.root / "secrets").mkdir()
        (self.root / "secrets" / "key").write_text("DO-NOT-EXPORT-SECRET")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def event(self, name, **kwargs):
        return {"hook_event_name": name, "session_id": "session/../unsafe-name",
                "transcript_path": "/must/not/read", "cwd": "/ignored/event/cwd", **kwargs}

    def test_compaction_preserves_dirty_work_and_records_unknown_runtime(self):
        before = self.git("status", "--porcelain")
        head = self.git("rev-parse", "HEAD")
        state = (self.root / context.AUTOPILOT / "state.md").read_bytes()
        result = hook.handle(self.root, self.event("PreCompact", trigger="auto"))
        self.assertIsNone(result)
        cache = Path(self.git("rev-parse", "--path-format=absolute", "--git-path", "autopilot-recovery"))
        files = list(cache.glob("*.json"))
        self.assertEqual(len(files), 1)
        saved = json.loads(files[0].read_text())
        worker = next(item for item in saved["worktrees"] if item["path"] == str(self.worker))
        self.assertIn("M code.py", worker["status_porcelain"])
        self.assertEqual(saved["head"], head)
        self.assertIn("agent liveness and unread results", saved["unobserved"])
        self.assertNotIn("DO-NOT-EXPORT-SECRET", files[0].read_text())
        self.assertNotIn("6C partial", files[0].read_text())
        self.assertEqual(before, self.git("status", "--porcelain"))
        self.assertEqual(state, (self.root / context.AUTOPILOT / "state.md").read_bytes())
        self.assertEqual((self.worker / "code.py").read_text(), "uncommitted work\n")
        self.assertEqual(head, self.git("rev-parse", "HEAD"))
        resumed = hook.handle(self.root, self.event("SessionStart", source="compact"))
        output = resumed["hookSpecificOutput"]["additionalContext"]
        self.assertIn(str(files[0]), output)
        self.assertLessEqual(len(output), hook.MAX_OUTPUT)
        # A different session gets no claim that the old session's handles survived.
        fresh = self.event("SessionStart", source="startup")
        fresh["session_id"] = "new-session"
        self.assertIn('"same_session_snapshot": null', hook.handle(self.root, fresh)
                      ["hookSpecificOutput"]["additionalContext"])

    def test_startup_does_not_write_or_launch_work(self):
        for source in ("startup", "resume", "clear", "compact"):
            result = hook.handle(self.root, self.event("SessionStart", source=source))
            self.assertEqual(result["hookSpecificOutput"]["hookEventName"], "SessionStart")
            self.assertNotIn("initialUserMessage", result["hookSpecificOutput"])
        cache = Path(self.git("rev-parse", "--path-format=absolute", "--git-path", "autopilot-recovery"))
        self.assertFalse(cache.exists())

    def test_worktree_snapshots_are_isolated(self):
        event = self.event("PreCompact", trigger="manual")
        hook.handle(self.root, event)
        hook.handle(self.worker, event)
        left = self.git("rev-parse", "--path-format=absolute", "--git-path", "autopilot-recovery")
        right = subprocess.check_output(["git", "-C", str(self.worker), "rev-parse",
                                        "--path-format=absolute", "--git-path", "autopilot-recovery"], text=True).strip()
        self.assertNotEqual(left, right)
        self.assertEqual(len(list(Path(left).glob("*.json"))), 1)
        self.assertEqual(len(list(Path(right).glob("*.json"))), 1)

    def test_git_timeout_is_unknown_not_clean(self):
        with patch.object(hook.subprocess, "run", side_effect=subprocess.TimeoutExpired("git", 0.01)):
            saved = hook.snapshot(self.root, {}, hook.time.monotonic() + 1)
        self.assertIsNone(saved["head"])
        self.assertTrue(saved["worktrees_unavailable"])

    def test_bad_hook_input_never_blocks_compaction_or_echoes_payload(self):
        script = SKILL / "scripts/recovery_hook.py"
        for payload in (b"DO-NOT-EXPORT-SECRET", b"[]", b"x" * (hook.MAX_INPUT + 1)):
            run = subprocess.run(["python3", str(script)], input=payload, capture_output=True)
            self.assertEqual(run.returncode, 0)
            self.assertEqual(run.stdout, b"")
            self.assertNotIn(b"DO-NOT-EXPORT-SECRET", run.stderr)


if __name__ == "__main__":
    unittest.main()
