"""Backup keygen, streaming age encryption, drill records, and the plaintext release rule
(addendum §4.3, ruling A-C4, ruling A-I9).

A retention *unit* is one stamped backup: a plaintext `.dump`, its `.meta.json` sidecar and,
once encrypted, a `.dump.age` ciphertext and a `.ok` marker, all named
`<kind>/harness-<kind>-<stamp>.<ext>` under `Settings.backup_dir`. `scan_units` is the one
place that groups those files back into a `Unit` by kind and stamp; everything else in this
module works from that grouping rather than globbing paths itself.

Encryption never touches the private key -- it is not on the NAS -- so the most this module
can prove about a fresh ciphertext is structural: the age header parses and the STREAM chunk
count matches the plaintext length (`verify_ciphertext_structure`, over
`harness.ops.agefmt.header_chunk_count`). Proof that the bytes actually decrypt comes only from
a Mac-side drill (`record_drill`), and `delete_verified_plaintexts` is the only thing in the
harness allowed to remove a plaintext, and only once a drill row says so for the same build.
"""

from __future__ import annotations

import hashlib
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import BinaryIO

from sqlalchemy import select
from sqlalchemy.orm import Session

from harness.db.models import BackupRun
from harness.ops.agefmt import AgeError, decrypt, encrypt, generate_identity, header_chunk_count

log = logging.getLogger(__name__)

__all__ = [
    "Unit", "scan_units", "keygen", "encrypt_pending", "verify_ciphertext_structure",
    "delete_verified_plaintexts", "decrypt_file", "record_drill", "marker_path",
    "newest_nightly_ok", "unencrypted_unit_count",
]

_NAME_PREFIX = "harness-"


@dataclass(frozen=True)
class Unit:
    """One retention unit: the plaintext dump, its ciphertext and its sidecar metadata, keyed
    by the stamp in the filename (A-I9). Retention counts units, never files."""
    kind: str            # nightly|weekly|partition
    stamp: str
    dump: Path | None
    age: Path | None
    meta: Path | None


class _HashingReader:
    """Wraps a binary file object, updating a hasher with every byte handed out."""

    def __init__(self, wrapped: BinaryIO, hasher) -> None:
        self._wrapped = wrapped
        self._hasher = hasher

    def read(self, size: int = -1) -> bytes:
        data = self._wrapped.read(size)
        self._hasher.update(data)
        return data


class _HashingWriter:
    """Wraps a binary file object, updating a hasher with every byte written."""

    def __init__(self, wrapped: BinaryIO, hasher) -> None:
        self._wrapped = wrapped
        self._hasher = hasher

    def write(self, data: bytes) -> int:
        self._hasher.update(data)
        return self._wrapped.write(data)

    def flush(self) -> None:
        self._wrapped.flush()

    def fileno(self) -> int:
        return self._wrapped.fileno()


def _unit_name(kind: str, stamp: str) -> str:
    return f"{_NAME_PREFIX}{kind}-{stamp}"


def scan_units(backup_dir: Path) -> list[Unit]:
    """Group every `.dump`, `.dump.age` and `.meta.json` under `backup_dir` into `Unit`s.

    `backup_dir` holds one subdirectory per kind (`nightly/`, `weekly/`, `partition/`); the
    stamp is whatever follows `harness-<kind>-` up to the file's extension. A `.dump.age.tmp`
    (an encryption still in flight) and a `.ok` marker are not unit members and are ignored
    here -- the marker is read directly by `dump.sh`'s retention loop, not through this scan.
    A missing `backup_dir` (the Mac, where the jobs no-op) scans as empty.
    """
    backup_dir = Path(backup_dir)
    if not backup_dir.is_dir():
        return []

    parts: dict[tuple[str, str], dict[str, Path]] = {}
    for kind_dir in sorted(p for p in backup_dir.iterdir() if p.is_dir()):
        kind = kind_dir.name
        prefix = f"{_NAME_PREFIX}{kind}-"
        for entry in sorted(kind_dir.iterdir()):
            name = entry.name
            if not name.startswith(prefix):
                continue
            if name.endswith(".dump.age.tmp") or name.endswith(".ok"):
                continue
            if name.endswith(".dump.age"):
                stamp, ext = name[len(prefix):-len(".dump.age")], "age"
            elif name.endswith(".dump"):
                stamp, ext = name[len(prefix):-len(".dump")], "dump"
            elif name.endswith(".meta.json"):
                stamp, ext = name[len(prefix):-len(".meta.json")], "meta"
            else:
                continue
            parts.setdefault((kind, stamp), {})[ext] = entry

    return [
        Unit(kind=kind, stamp=stamp, dump=found.get("dump"), age=found.get("age"), meta=found.get("meta"))
        for (kind, stamp), found in parts.items()
    ]


def marker_path(unit: Unit) -> Path:
    """The one name for the encrypt marker, used by `encrypt_pending` here and by `dump.sh`'s
    retention loop (Task 14): `<kind>/harness-<kind>-<stamp>.ok`, beside the ciphertext. It
    exists because the POSIX-sh sidecar cannot query `backup_runs` without authenticating to
    Postgres, and A-I9 forbids deleting a `.dump.age` that has no `ok` row behind it.
    """
    base = unit.dump or unit.age or unit.meta
    if base is None:
        raise ValueError("a unit with no files at all has no marker path")
    return base.parent / f"{_unit_name(unit.kind, unit.stamp)}.ok"


def keygen(identity_path: Path, recipient_path: Path) -> str:
    """Write the private identity 0600 and the public recipient 0644. Refuses to overwrite an
    existing identity (a regenerated key makes every existing backup undecryptable). Returns
    the recipient string for the operator notice."""
    identity_path = Path(identity_path)
    recipient_path = Path(recipient_path)
    if identity_path.exists():
        raise FileExistsError(f"{identity_path} already exists; refusing to regenerate the backup key")

    identity, recipient = generate_identity()
    identity_path.parent.mkdir(parents=True, exist_ok=True)
    identity_path.write_text(identity + "\n")
    identity_path.chmod(0o600)

    recipient_path.parent.mkdir(parents=True, exist_ok=True)
    recipient_path.write_text(recipient + "\n")
    recipient_path.chmod(0o644)
    return recipient


def verify_ciphertext_structure(age_path: Path, plaintext_bytes: int) -> bool:
    """What can be proven without the private key: the header parses and the chunk count
    matches the plaintext length (§4.3). The private key is not on the NAS."""
    try:
        header_chunk_count(Path(age_path), plaintext_bytes)
    except AgeError:
        return False
    return True


def encrypt_pending(session: Session, backup_dir: Path, recipient_file: Path, build_sha: str,
                    now: datetime) -> list[BackupRun]:
    """For every unit with a `.dump` and a `.meta.json` and no `.dump.age`: encrypt streaming
    to `<name>.dump.age.tmp`, fsync, rename, verify the ciphertext's structure, record one
    `backup_runs` row (kind='encrypt', build_sha set) and write the unit's `.ok` marker beside
    the ciphertext. Idempotent: a unit that already has a `.dump.age` is skipped. When
    `recipient_file.is_file()` is False -- absent, or the empty directory Compose creates for a
    missing bind source (I6) -- records one row `status='skipped'`,
    `notes={'reason': 'no recipient'}` and touches nothing on disk.
    """
    backup_dir = Path(backup_dir)
    recipient_file = Path(recipient_file)
    has_recipient = recipient_file.is_file()
    recipient = recipient_file.read_text().strip() if has_recipient else None

    rows: list[BackupRun] = []
    for unit in scan_units(backup_dir):
        if unit.dump is None or unit.meta is None or unit.age is not None:
            continue

        if not has_recipient:
            row = BackupRun(
                kind="encrypt", build_sha=build_sha, path=str(unit.dump), status="skipped",
                started_at=now, finished_at=now, notes={"reason": "no recipient"},
            )
            session.add(row)
            session.commit()
            rows.append(row)
            continue

        rows.append(_encrypt_unit(session, unit, recipient, build_sha, now))
    return rows


def _encrypt_unit(session: Session, unit: Unit, recipient: str, build_sha: str, now: datetime) -> BackupRun:
    dump = unit.dump
    tmp_path = dump.with_name(dump.name + ".age.tmp")
    final_path = dump.with_name(dump.name + ".age")
    try:
        plaintext_bytes = dump.stat().st_size
        plaintext_hash = hashlib.sha256()
        ciphertext_hash = hashlib.sha256()
        with dump.open("rb") as src, tmp_path.open("wb") as raw_dst:
            encrypt(_HashingReader(src, plaintext_hash), _HashingWriter(raw_dst, ciphertext_hash), recipient)
            raw_dst.flush()
            os.fsync(raw_dst.fileno())
        os.replace(tmp_path, final_path)

        ok = verify_ciphertext_structure(final_path, plaintext_bytes)
        row = BackupRun(
            kind="encrypt", build_sha=build_sha, path=str(final_path), bytes=plaintext_bytes,
            plaintext_sha256=plaintext_hash.hexdigest(), ciphertext_sha256=ciphertext_hash.hexdigest(),
            status="ok" if ok else "error", started_at=now, finished_at=now,
            notes=None if ok else {"error": "ciphertext structure check failed after encryption"},
        )
        session.add(row)
        session.commit()
        if ok:
            marker_path(unit).write_text("")
        return row
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        row = BackupRun(
            kind="encrypt", build_sha=build_sha, path=str(dump), status="error",
            started_at=now, finished_at=now, notes={"error": repr(exc)},
        )
        session.add(row)
        session.commit()
        log.error("backup encrypt failed for %s: %s", dump, exc)
        return row


def delete_verified_plaintexts(session: Session, backup_dir: Path, build_sha: str,
                               now: datetime) -> list[Path]:
    """Delete a unit's plaintext only when `backup_runs` holds a `drill` row with
    `build_sha = :build_sha` (the column, Task 4) and `notes->>'decrypt_ok' = 'true'`, AND the
    unit's own encrypt row is `ok` (A-C4). Never deletes a `.dump.age`, a `.meta.json` or a
    `.ok` marker.
    """
    del now  # the release rule has no time bound of its own; kept for interface symmetry
    drills = session.execute(
        select(BackupRun).where(BackupRun.kind == "drill", BackupRun.build_sha == build_sha)
    ).scalars().all()
    if not any(d.notes and d.notes.get("decrypt_ok") is True for d in drills):
        return []

    deleted: list[Path] = []
    for unit in scan_units(backup_dir):
        if unit.dump is None:
            continue
        age_path = str(unit.dump.with_name(unit.dump.name + ".age"))
        encrypt_row = session.execute(
            select(BackupRun).where(
                BackupRun.kind == "encrypt", BackupRun.build_sha == build_sha,
                BackupRun.path == age_path, BackupRun.status == "ok",
            )
        ).scalars().first()
        if encrypt_row is None:
            continue
        unit.dump.unlink()
        deleted.append(unit.dump)
    return deleted


def decrypt_file(age_path: Path, out_path: Path, identity_file: Path) -> str:
    """Mac-side. Returns the sha256 of the decrypted plaintext."""
    identity = Path(identity_file).read_text().strip()
    hasher = hashlib.sha256()
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with Path(age_path).open("rb") as src, out_path.open("wb") as raw_dst:
        decrypt(src, _HashingWriter(raw_dst, hasher), identity)
    return hasher.hexdigest()


def record_drill(session: Session, build_sha: str, decrypt_ok: bool, plaintext_sha256: str,
                 rows_match: bool | None, now: datetime, notes: dict) -> BackupRun:
    """Writes `kind='drill'` with `build_sha` in its own column and `decrypt_ok` in `notes`."""
    row = BackupRun(
        kind="drill", build_sha=build_sha, status="ok" if decrypt_ok else "error",
        plaintext_sha256=plaintext_sha256, rows_match=rows_match,
        started_at=now, finished_at=now, notes={**notes, "decrypt_ok": decrypt_ok},
    )
    session.add(row)
    session.commit()
    return row


def newest_nightly_ok(session: Session, now: datetime, max_age_h: int) -> BackupRun | None:
    """The precheck's query half: the newest `kind='nightly'`, `status='ok'` row younger than
    `max_age_h`, or None."""
    cutoff = now - timedelta(hours=max_age_h)
    return session.execute(
        select(BackupRun)
        .where(BackupRun.kind == "nightly", BackupRun.status == "ok", BackupRun.finished_at >= cutoff)
        .order_by(BackupRun.finished_at.desc())
    ).scalars().first()


def unencrypted_unit_count(backup_dir: Path) -> int:
    """The daily line's number: units with a plaintext and no ciphertext."""
    return sum(1 for unit in scan_units(backup_dir) if unit.dump is not None and unit.age is None)
