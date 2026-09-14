# DraftKings settlement rules and the ESPN game-log shape, recorded as evidence

Task 18a (addendum §2.1, §4.1; D19). Worker task ID `impl-46-t18a`, worktree
`phase46-t18a-market-defs`, branch `phase46-t18a-market-defs`, base `87187f0`.
Attempted 2026-09-14.

## What was attempted

The worker sandbox has no general network route (per the omarchy worker notes and the plan's
own MI-7 expectation). Step 3's exact command was run verbatim:

```bash
python3 - <<'PY'
import urllib.request
for url in ("https://sportsbook.draftkings.com/help/rules/football",
            "https://site.api.espn.com/apis/site/v2/sports/football/nfl/athletes/4426348/gamelog"):
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            print(url, r.status, len(r.read()))
    except Exception as exc:
        print(url, "UNAVAILABLE", type(exc).__name__)
PY
```

Output (verbatim, `type(exc).__name__` extended with the message text for the controller's
convenience):

```
https://sportsbook.draftkings.com/help/rules/football UNAVAILABLE URLError <urlopen error [Errno -3] Temporary failure in name resolution>
https://site.api.espn.com/apis/site/v2/sports/football/nfl/athletes/4426348/gamelog UNAVAILABLE URLError <urlopen error [Errno -3] Temporary failure in name resolution>
```

Both hosts are `UNAVAILABLE` (DNS resolution fails inside the sandbox — no route exists to
resolve either name). This is the expected result named in the brief and is **Path B**, not a
failure of this task. `harness/parlay/parlay.yaml`'s `props.market_defs` is left unchanged
(`{}`).

## Settlement rules

Release one's five prop families (`harness/parlay/parlay.yaml` `props.families`, addendum §3.1,
D3): `pass_yds`, `rush_yds`, `rec_yds`, `receptions`, `anytime_td`. None has a rule recorded in
this pass. Per family, "not recorded" and the exact question the controller must answer once it
fetches `https://sportsbook.draftkings.com/help/rules/football` (or the NFL/football player-prop
section that page routes to):

### pass_yds (passing yards, over/under)

Not recorded. Question: does the settled total include or exclude yardage lost to sacks (the
official NFL box score charges sack yardage against rushing, not passing — does DraftKings'
grading follow that convention or its own recorded passing total)? Are overtime passing yards
included? If DraftKings' displayed live stat differs from the final official league box score,
which one is authoritative for settlement?

### rush_yds (rushing yards, over/under)

Not recorded. Question: are kneel-down yards (end-of-half/end-of-game) included in the settled
total? Are yards gained on a backward lateral that a runner then carries included, and are
overtime rushing yards included?

### rec_yds (receiving yards, over/under)

Not recorded. Question: when a reception is followed by a backward lateral to a teammate who
then advances the ball, are the post-lateral yards credited to the original receiver's total or
excluded? Are overtime receiving yards included?

### receptions (receptions, over/under)

Not recorded. Question: does a catch that is overturned on replay review still count toward the
total? Does a reception that is followed by a penalty enforced on the play (but not a replay
overturn) still count?

### anytime_td (anytime touchdown scorer)

Not recorded. Question (as named by the brief itself): does a passing touchdown count for the
passer (as opposed to only the receiver/rusher who crosses the goal line), and does a defensive
or special-teams return or a fumble/interception recovery returned for a touchdown count for the
player who scores it?

**Every family stays `market_unsupported` until a rule is recorded here** — this is the D19
behaviour Task 6 already enforces (per the addendum's Ruling B-C2 and the plan's own text: "a
family stays `market_unsupported` until its rule is recorded"); no code changes were made on
that basis and nothing is built on a guess.

## Game log shape

The addendum (design §4.1, line 131 of the phase 4.6 spec) names the endpoint for the draft
context line: `GET {espn_base_url}/{sport}/athletes/{athlete_id}/gamelog`, fetched once per prop
candidate at build time. The exact JSON path to a per-game stat value (e.g., the path a
`parse_gamelog` function would walk to get a single game's rushing yards or receptions) could
not be measured in this pass: the sandbox cannot reach `site.api.espn.com` (see "What was
attempted" above), and no local fixture exists yet to measure instead.

`tests/fixtures/espn_gamelog_nfl.json` **does not exist in this worktree** (checked directly —
Task 3, which is the task that would add ESPN summary/roster/game-log parsing and its fixtures,
has not landed on this branch; the fixture is neither present nor even a constructed
placeholder here). Until the real shape is fetched (or a Task 3 fixture is constructed and
explicitly flagged as unconfirmed), the draft context line for a prop leg must read
`no season data yet`, per the addendum's own fallback text (line 131: "its shape is measured by
the plan's evidence task (D19) before the context line is trusted; until then, and whenever the
shape is absent, the line reads `no season data yet`").

## Consequences

All five families — `pass_yds`, `rush_yds`, `rec_yds`, `receptions`, `anytime_td` — stay
`market_unsupported` after this task: `props.market_defs` remains `{}` in
`harness/parlay/parlay.yaml`, unchanged by this pass. No prop leg builds for any family until its
rule is recorded here with a verbatim quote, its source URL and the date read.

The one-line follow-up commit that fills the block, once the controller (or a worker with
network access) fetches `https://sportsbook.draftkings.com/help/rules/football` and quotes the
five families' rules verbatim:

`docs(4.6): fill props.market_defs with DraftKings' verbatim settlement rules (closes Task 18a's Path B)`

## What the controller must fetch

- Page: `https://sportsbook.draftkings.com/help/rules/football` (or the specific NFL
  player-props sub-page it links to). Per family, the exact question to answer (repeated from
  "Settlement rules" above for a single list the controller can work from):
  - `pass_yds`: does the total include/exclude sack yardage; are overtime yards included; which
    stat source is authoritative if it disagrees with the box score.
  - `rush_yds`: are kneel-down yards included; are post-lateral yards included; are overtime
    yards included.
  - `rec_yds`: are yards gained after a lateral credited to the original receiver; are overtime
    yards included.
  - `receptions`: does an overturned (replay-reversed) catch still count; does a catch followed
    by an enforced penalty still count.
  - `anytime_td`: does a passing touchdown count for the passer; does a return/recovery
    touchdown count for the player who scores it (the brief's own two sub-questions, verbatim).
- Page: `https://site.api.espn.com/apis/site/v2/sports/football/nfl/athletes/4426348/gamelog`
  (or the gamelog endpoint for any real, currently-rostered NFL athlete id). Question: what is
  the exact JSON path from the response root to a single game's value for each of the tracked
  stats (`passing yards`, `rushing yards`, `receiving yards`, `receptions`), and what does the
  response look like when a player has no games logged yet this season (the shape that drives
  the `no season data yet` fallback)?
