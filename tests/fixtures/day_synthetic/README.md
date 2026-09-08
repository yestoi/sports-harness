# `day_synthetic` — one synthesised 30-minute NFL game day

**This day is synthesised, not recorded.** No recorded game day of 2026-09-13 exists (the
date is in the future), so `day.json` was generated from the fixtures already in this
directory rather than exported from the NAS: `odds_featured_nfl.json` (the featured odds body,
re-timed onto this day's kickoff and quoted by three books instead of one, so a consensus has
something to disagree about), `odds_alternates_event.json` (the alternates body, re-timed),
and `kalshi_markets_typed.json` (the market shapes — a `KXNFLGAME` moneyline pair with
`custom_strike`, a `KXNFLSPREAD` and a `KXNFLTOTAL`), plus one WebSocket-shaped orderbook
snapshot, ten deltas and five prints written by hand under whatever price the strategy asked
for. Prices, sizes and volumes are invented. Nothing here came from a venue.

The file itself is exactly what `harness export-fixture --kind day --from-run 1 --to-run 2
--out ...` produces, so a real recorded day drops into the same loader unchanged.

## What is in it

| | |
|---|---|
| Game | New England Patriots at Seattle Seahawks, kickoff 2026-09-13T21:00Z |
| Tape window | 2026-09-13T18:55Z to 19:09:40Z (the two runs, padded by five minutes) |
| Runs | 2, three minutes apart, both finished — their pricing clocks are 19:01:40Z and 19:04:40Z |
| `raw_responses` | 8: featured odds, alternates, Kalshi `/events` and `/markets`, twice |
| `orderbook_events` | 11: one snapshot and ten deltas on the total market |
| `venue_trades` | 5 prints, the last two of which reach the resting order |
| Markets | 5 — four the matcher ties to the game confidently, and one (`KXNFLGAME-26SEP13ZZZYYY-ZZZ`) it declines, which is what pins "zero orders on an unmatched market" |

## Running it

`tests/fixture_day.py` loads it and walks the chain: insert the three tables, seed teams,
normalize, price and signal each run, `replay --execute` over the run range, benchmark the
game, drain the gap outcomes. `tests/test_replay_execute.py::test_fixture_day_end_to_end` is
that walk with its counts asserted.

Every benchmark the day can produce is marked `stale`: the window sits two hours before
kickoff, and a `t5` benchmark drawn from a line that old is stale by definition (spec F42).
That is the honest label for a 30-minute slice, not a defect in the fixture.

Regenerate it only by exporting a real day once one exists. Editing `day.json` by hand will
change what the strategy asks for and silently invalidate the tape written under it.
