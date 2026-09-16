"""evidence_image.py: JPEG under the cap, cp -n semantics. Needs ImageMagick's magick."""

import importlib.util
import re
import shutil
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


evidence_image = module("evidence_image")


@unittest.skipIf(shutil.which("magick") is None, "ImageMagick magick not installed")
class EvidenceImageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.src = self.root / "capture.png"
        subprocess.run(["magick", "-size", "1440x900", "gradient:blue-white", str(self.src)], check=True)

    def test_converts_under_the_cap_and_reports_size_quality_and_dimensions(self):
        dest = self.root / "2026-09-16-verify-0900-01-gate.jpg"
        message, code = evidence_image.archive(self.src, dest)
        self.assertEqual(code, 0, message)
        self.assertTrue(dest.exists())
        self.assertLessEqual(dest.stat().st_size, evidence_image.CAP)
        self.assertRegex(message, rf"^{re.escape(str(dest))} \d+ q85 1440x900$")
        self.assertEqual(dest.read_bytes()[:3], b"\xff\xd8\xff")

    def test_existing_destination_is_kept_with_exit_zero(self):
        dest = self.root / "kept.jpg"
        dest.write_bytes(b"original")
        message, code = evidence_image.archive(self.src, dest)
        self.assertEqual((message, code), (f"{dest} exists, kept", 0))
        self.assertEqual(dest.read_bytes(), b"original")

    def test_unreadable_source_fails_and_leaves_no_destination(self):
        bad = self.root / "not-an-image.png"
        bad.write_text("nope")
        dest = self.root / "out.jpg"
        message, code = evidence_image.archive(bad, dest)
        self.assertEqual(code, 1)
        self.assertIn("conversion failed", message)
        self.assertFalse(dest.exists())

    def test_cli(self):
        dest = self.root / "cli.jpg"
        result = subprocess.run(["python3", str(SKILL / "scripts/evidence_image.py"), str(self.src), str(dest)],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(result.stdout.startswith(str(dest)))
