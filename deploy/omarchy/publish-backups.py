#!/usr/bin/env python3
"""Expose completed ciphertext units as hard links for a restricted NAS reader.

Only the export links are pruned. The live backup spool is never modified.
NAS retention is independent: its pull must not use --delete.
"""
import argparse
import json
import os
from pathlib import Path
import stat


def regular(path):
    try:
        return stat.S_ISREG(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def publish(source, destination):
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination or source in destination.parents or destination in source.parents:
        raise ValueError("source and export directories must be separate")
    if not source.is_dir():
        raise FileNotFoundError(source)
    destination.mkdir(mode=0o700, parents=True, exist_ok=True)
    desired = set()
    for kind in ("nightly", "weekly", "partitions", "forever"):
        folder = source / kind
        if folder.is_symlink():
            raise ValueError("backup kind directory must not be a symlink")
        output = destination / kind
        if output.is_symlink():
            raise ValueError("export kind directory must not be a symlink")
        output.mkdir(mode=0o700, exist_ok=True)
        for age in sorted(folder.glob("*.dump.age")):
            base = age.name.removesuffix(".dump.age")
            unit = [age, folder / (base + ".meta.json"), folder / (base + ".ok")]
            if not all(regular(p) for p in unit):
                continue
            for original in unit:
                exported = output / original.name
                try:
                    if not regular(exported) or not os.path.samefile(original, exported):
                        temporary = output / (original.name + ".publish-tmp")
                        temporary.unlink(missing_ok=True)
                        os.link(original, temporary, follow_symlinks=False)
                        if not regular(temporary):
                            temporary.unlink()
                            raise ValueError("backup changed into a symlink")
                        os.replace(temporary, exported)
                    desired.add(exported)
                except FileNotFoundError:
                    # Normal retention may remove a unit while this scan runs.
                    break
    for kind in ("nightly", "weekly", "partitions", "forever"):
        for exported in (destination / kind).iterdir():
            if exported not in desired and (exported.is_file() or exported.is_symlink()):
                exported.unlink()
    print(json.dumps({"published_files": len(desired), "export": str(destination)}))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source")
    parser.add_argument("destination")
    args = parser.parse_args()
    publish(args.source, args.destination)
