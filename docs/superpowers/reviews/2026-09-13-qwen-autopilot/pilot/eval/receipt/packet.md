# Task receipt: pure toy receipt predicate

Implement `eligible(receipt, candidate, profile)` in `solution.py` using only Python's standard library. This is a deliberately toy data-validation exercise, not a security boundary or production release implementation. Do not edit the smoke.

Return a bool, never raise for malformed ordinary JSON-like values. `candidate` must be a string of exactly 40 lowercase ASCII hexadecimal characters. `profile` must be a nonempty string. `receipt` must be a dict with all these fields:

- `run_id`: nonempty string.
- `origin`: exactly the string `trusted-runner`.
- `head` and `head_after`: each exactly equal to candidate.
- `profile`: exactly equal to the profile argument.
- `exit_code`: integer 0, excluding bool.
- `scope`: an empty list, not another empty container.
- `dirty_before` and `dirty_after`: each exactly the empty string.
- `environment`: a dict containing `addopts` and `plugins`, each exactly the empty string.

Extra receipt and environment keys are allowed. Missing fields reject. Do not coerce types, trim text, ignore hexadecimal case, or mutate any arguments. Whitespace-only run_id/profile count as nonempty strings. The origin field is a toy label; this function does not authenticate receipt provenance.
