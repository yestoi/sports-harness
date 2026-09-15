# Task utf8_tail: bounded complete-character suffix

Implement `utf8_tail(chunks, max_bytes)` in `solution.py`, using only Python's standard library. This is a synthetic stream utility. Do not edit the smoke.

`chunks` is an iterable of bytes objects, potentially a one-shot generator. Concatenated input is a UTF-8 byte stream. Return the longest suffix of its decoded string whose UTF-8 encoding is at most `max_bytes` bytes; omit a leading partial character entirely. Never insert a replacement character, and never truncate a character. Example: for `A€B`, limits 3, 4, and 5 produce `B`, `€B`, and `A€B` respectively. Chunk boundaries may occur inside a character; empty chunks are allowed.

Validate the complete input as strict UTF-8, even bytes discarded from the suffix and even with a zero limit. Raise UnicodeDecodeError for invalid or unfinished encoding. Every chunk must be bytes (not str, bytearray, or memoryview); raise TypeError otherwise. `max_bytes` must be an integer other than bool; otherwise TypeError. A negative limit raises ValueError. Validate the limit before consuming chunks. Empty input returns an empty string.

Process incrementally: do not collect/join the entire stream. Retained state should scale with the byte limit plus the largest current chunk, not total stream size. The caller guarantees each incoming chunk is finite. Behavior and source review both judge this constraint.
