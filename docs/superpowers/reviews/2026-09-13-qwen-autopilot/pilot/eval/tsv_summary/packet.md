# Task tsv_summary: stable summary of observations

Implement `summarize(records)` in `solution.py` using only Python's standard library. This is a synthetic report-formatting utility. Do not edit the smoke.

`records` is an iterable (including one-shot generators) of dicts. Each must contain `status`, exactly one of the strings queued/running/done/failed, and `duration_ms`, either None or a nonnegative integer excluding bool. Extra dict keys are ignored. Malformed records raise ValueError; a noniterable records argument raises ValueError too.

Return TSV text with header `status\tcount\ttotal_ms\tavg_ms\n`. Emit only statuses present, always in queued, running, done, failed order. Count every observation, including repeated records and None durations. Total sums only numeric durations (0 if none). Average divides total by the number of numeric durations, rounds to nearest integer with exact half-up rounding, and is `-` if there are no numeric durations. Use exact integer arithmetic, so large values remain correct. Every row ends with newline. Empty input returns only the header. Do not mutate records.

The escape notation above means actual tab separators and an actual newline, not literal backslash characters.
