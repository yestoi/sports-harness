#: Bumped whenever the fair-value or fee computation changes in a way that would move
#: numbers already on disk (spec: pre-registration Amendments). Introduced at 2.1 by F11
#: (the staleness allowance and centicent fee rounding); 2.2 snaps the target price to the
#: venue's published tick grid and prices the NO side (amendment 3, U2); 2.3 records
#: `SignalRow.as_measured` on `harness/strategy/run.py`, which the plan treats as part of the
#: pricing surface (Task 9, D12) -- it is recorded only, never read back into a decision.
PRICING_VERSION = "2.3"
