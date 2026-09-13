"""The release tree: main's content hash with the loop's own bookkeeping left out.

`docs/superpowers/autopilot/` (journal, state, roadmap, evidence) changes after nearly every
merge, and nothing the suite runs reads it, so a receipt taken on the merged code still
describes main after the journal commit. The guard test here is what keeps that true.
"""
import ast
import hashlib
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from release_tree import EXCLUDED, release_tree  # noqa: E402

LISTING = ("100644 blob 1111111111111111111111111111111111111111\tREADME.md\n"
           "100644 blob 2222222222222222222222222222222222222222\tharness/x.py\n"
           "100644 blob 3333333333333333333333333333333333333333\tdocs/superpowers/autopilot/journal.md\n")


def load_script(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), ROOT / f"scripts/{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_only_the_autopilot_bookkeeping_directory_is_excluded():
    assert EXCLUDED == ("docs/superpowers/autopilot/",)


def test_a_change_under_the_excluded_directory_keeps_the_hash_and_any_other_change_moves_it():
    same = LISTING.replace("3333333333333333333333333333333333333333", "4444444444444444444444444444444444444444")
    other = LISTING.replace("2222222222222222222222222222222222222222", "4444444444444444444444444444444444444444")
    added = LISTING + "100644 blob 5555555555555555555555555555555555555555\tdocs/superpowers/autopilot/state.md\n"
    base = release_tree(run=lambda args, **kw: LISTING)
    assert release_tree(run=lambda args, **kw: same) == base
    assert release_tree(run=lambda args, **kw: added) == base
    assert release_tree(run=lambda args, **kw: other) != base
    assert len(base) == 64 and base != hashlib.sha256(b"").hexdigest()


def test_release_tree_reads_the_named_revision_from_git(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path, text=True,
                                       env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t",
                                            "GIT_COMMITTER_EMAIL": "t@t", "HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}).strip()
    git("init", "-q", "-b", "main")
    (tmp_path / "harness").mkdir()
    (tmp_path / "harness/x.py").write_text("x = 1\n")
    (tmp_path / "docs/superpowers/autopilot").mkdir(parents=True)
    (tmp_path / "docs/superpowers/autopilot/journal.md").write_text("## 1\n")
    git("add", "-A"); git("commit", "-qm", "one")
    first = git("rev-parse", "HEAD")
    (tmp_path / "docs/superpowers/autopilot/journal.md").write_text("## 1\n## 2\n")
    git("add", "-A"); git("commit", "-qm", "journal")
    second = git("rev-parse", "HEAD")
    (tmp_path / "harness/x.py").write_text("x = 2\n")
    git("add", "-A"); git("commit", "-qm", "code")
    third = git("rev-parse", "HEAD")
    run = lambda args, **kw: subprocess.check_output(args, cwd=tmp_path, text=True)
    assert git("rev-parse", f"{first}^{{tree}}") != git("rev-parse", f"{second}^{{tree}}")
    assert release_tree(first, run=run) == release_tree(second, run=run)
    assert release_tree(second, run=run) != release_tree(third, run=run)


def test_both_scripts_use_the_shared_release_tree_function():
    runner, release = load_script("test-suite"), load_script("release-omarchy")
    assert runner.release_tree is release_tree and release.release_tree is release_tree


def _non_docstring_strings(source, name):
    tree = ast.parse(source, filename=name)
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)) and node.body:
            first = node.body[0]
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant) and isinstance(first.value.value, str):
                docstrings.add(id(first.value))
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            yield node.value


@pytest.mark.parametrize("folder", ["tests", "harness", "scripts"])
def test_nothing_the_suite_runs_names_the_excluded_directory_outside_a_docstring(folder):
    """A test or module that read the loop's bookkeeping would make the release tree unsound:
    the excluded directory could change a result the receipt vouches for. Docstrings and
    comments may cite evidence files there; code may not name the path."""
    offenders = []
    for path in sorted((ROOT / folder).rglob("*.py")):
        # release_tree.py defines the exclusion; worker-shell.py mounts the directory read-only
        # into worker sandboxes and never runs inside the suite.
        if path.name in {"release_tree.py", "test_release_tree.py", "worker-shell.py"}:
            continue
        for value in _non_docstring_strings(path.read_text(), str(path)):
            if "superpowers/autopilot" in value:
                offenders.append(f"{path.relative_to(ROOT)}: {value[:60]!r}")
    assert offenders == []
