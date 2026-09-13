# Task atomic_json: atomic local JSON snapshot

Implement `write_snapshot(path, value)` in `solution.py` using only the Python standard library. This is a disposable-file utility for a synthetic screen. Do not edit the smoke.

`path` is a str or pathlib.Path whose parent already exists. Serialize `value` exactly as `json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + '\n'`, encoded as UTF-8. Write a uniquely named temporary regular file in the destination's parent and atomically replace the destination with os.replace after the complete contents are written and flushed. Close all file handles. Return None. Do not create missing parent directories.

On serialization or filesystem failure, propagate the original exception, preserve an existing destination's contents, and remove any temporary file created by this call. Do not remove unrelated files. Concurrent writers must not share one fixed temporary pathname. Atomicity means same-directory temporary file plus replacement; crash durability/fsync, permission preservation, symlink handling and unsupported input path types are outside this contract. Do not implement them speculatively.

Tests use ordinary files and, for a replacement-failure case, an existing nonempty directory as the destination. Source review checks use of atomic replacement and safe cleanup, beyond output-only tests.
