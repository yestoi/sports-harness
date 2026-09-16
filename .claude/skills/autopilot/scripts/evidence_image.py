#!/usr/bin/env python3
"""Archive a screenshot into evidence as a JPEG under the size cap, keeping cp -n semantics.

Quality 85, then 70, then the width shrinks by 0.75 per step while staying at or above half the
original width. Under 400 KB: done. 400 to 800 KB at the floor: kept, "over cap". Over 800 KB: refused.
"""

import argparse
import shutil
import subprocess
import sys
from pathlib import Path

CAP = 400_000
HARD = 800_000
QUALITIES = (85, 70)
STEP = 0.75
FLOOR = 0.5


def magick(*args):
    return subprocess.run(["magick", *args], capture_output=True, text=True)


def dimensions(path):
    result = magick("identify", "-format", "%w %h", str(path))
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "identify failed")
    width, height = result.stdout.split()
    return int(width), int(height)


def convert(src, dest, quality, scale):
    args = [str(src), "-strip"]
    if scale < 1:
        args += ["-resize", f"{scale * 100:.4f}%"]
    args += ["-quality", str(quality), "jpeg:" + str(dest)]
    result = magick(*args)
    if result.returncode or not dest.exists():
        raise RuntimeError(result.stderr.strip() or "convert failed")
    return dest.stat().st_size


def attempts():
    plan = [(quality, 1.0) for quality in QUALITIES]
    scale = STEP
    while scale >= FLOOR:
        plan.append((QUALITIES[-1], scale))
        scale *= STEP
    return plan


def archive(src, dest):
    src, dest = Path(src), Path(dest)
    if dest.exists():
        return f"{dest} exists, kept", 0
    if shutil.which("magick") is None:
        return "conversion failed: magick not found", 1
    try:
        size = quality = None
        for quality, scale in attempts():
            size = convert(src, dest, quality, scale)
            if size <= CAP:
                break
        if size > HARD:
            dest.unlink()
            return f"{dest} would be {size} bytes > {HARD} at the floor; not written", 1
        width, height = dimensions(dest)
        note = " over cap" if size > CAP else ""
        return f"{dest} {size} q{quality} {width}x{height}{note}", 0
    except (OSError, RuntimeError) as error:
        if dest.exists():
            dest.unlink()
        return f"conversion failed: {error}", 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("src", type=Path)
    parser.add_argument("dest", type=Path)
    args = parser.parse_args()
    message, code = archive(args.src, args.dest)
    print(message, file=sys.stdout if code == 0 else sys.stderr)
    return code


if __name__ == "__main__":
    sys.exit(main())
