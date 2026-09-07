import subprocess
import sys
import zipfile
from pathlib import Path


def test_wheel_includes_manual_aliases_yaml(tmp_path):
    """pip install . must ship harness/matching/aliases_manual.yaml or `harness seed-teams`
    dies with FileNotFoundError inside a container (no source checkout to fall back on)."""
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    repo_root = Path(__file__).parent.parent

    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(out_dir)],
        cwd=repo_root, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0 and "No module named pip" in result.stderr:
        result = subprocess.run(
            ["uv", "build", "--wheel", "-o", str(out_dir)],
            cwd=repo_root, capture_output=True, text=True, timeout=120,
        )
    assert result.returncode == 0, result.stdout + result.stderr

    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels

    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    assert "harness/matching/aliases_manual.yaml" in names, names


def test_wheel_includes_shipped_variant_yaml(tmp_path):
    """pip install . must ship harness/variants/*.yaml or `harness variants register` (run from
    the packaged CLI with no --variants-dir override) finds nothing to register."""
    out_dir = tmp_path / "dist"
    out_dir.mkdir()
    repo_root = Path(__file__).parent.parent

    result = subprocess.run(
        [sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(out_dir)],
        cwd=repo_root, capture_output=True, text=True, timeout=120,
    )
    if result.returncode != 0 and "No module named pip" in result.stderr:
        result = subprocess.run(
            ["uv", "build", "--wheel", "-o", str(out_dir)],
            cwd=repo_root, capture_output=True, text=True, timeout=120,
        )
    assert result.returncode == 0, result.stdout + result.stderr

    wheels = list(out_dir.glob("*.whl"))
    assert len(wheels) == 1, wheels

    with zipfile.ZipFile(wheels[0]) as zf:
        names = zf.namelist()
    assert "harness/variants/sharp_direct.yaml" in names, names
