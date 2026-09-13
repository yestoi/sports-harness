# Task duration: duration string to milliseconds

Implement `parse_duration(text)` in `solution.py`, using only Python's standard library. The starter and public smoke are supplied. Do not edit the smoke. This is a synthetic utility.

Return an integer number of milliseconds. Accept one or more ASCII decimal integer tokens immediately followed by `d`, `h`, `m`, `s`, or `ms`. Units must appear in strictly descending magnitude, without repetition. Unit factors are 86400000, 3600000, 60000, 1000, and 1. Amounts may have leading zeroes and may exceed a conventional clock component, e.g. `90m` is valid. Zero amounts are valid.

Zero or more literal ASCII spaces may separate tokens and appear at either end. No other whitespace is accepted. Thus `1h30m`, `1h 30m`, and ` 0s ` are valid; `1 h`, tabs, signs, decimals, and Unicode digits are invalid. Longest unit matching makes `1ms` one token. The entire input must match, including absence of trailing junk.

Reject non-string input with TypeError. Reject empty/all-space input, invalid syntax/order, input longer than 128 characters (before trimming), or a total exceeding 31536000000 milliseconds with ValueError. The maximum itself is valid.
