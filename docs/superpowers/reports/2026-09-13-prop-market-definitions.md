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

Not recorded. Question, quoted verbatim from the brief (task-18a-brief.md, Path B):

> whether a passing touchdown counts for the passer, and whether a return or a recovery counts.

(Elaboration, not the brief's text and not a quote of anything: in practice this means
confirming whether a passing touchdown counts for the passer as opposed to only the
receiver/rusher who crosses the goal line, and whether a defensive or special-teams return, or a
fumble/interception recovery returned for a touchdown, counts for the player who scores it.)

**Every family stays `market_unsupported` until a rule is recorded here** — this is the D19
behaviour Task 6 already enforces (per the addendum's Ruling B-C2 and the plan's own text: "a
family stays `market_unsupported` until its rule is recorded"); no code changes were made on
that basis and nothing is built on a guess.

## DraftKings football rules (round 3: the user pasted the page)

**Round 3 update (controller, 2026-09-14 ~05:0x CT).** The rules page could not be fetched by
any controller-side method either: the correct page is
`https://sportsbook.draftkings.com/help/sport-rules/football` (the brief's
`/help/rules/football` is not the live path); `curl` returns only the application shell, a
headless Chromium session gets Akamai's "Access Denied" page, and the contentstack bucket the
shell loads from answers 403 (journal 184, ruling 4). Per the user's ruling ("Record URL, capture
time and the verbatim text. If DraftKings blocks headless, you paste."), the user opened the page
in their own browser and pasted its "Player Props Markets", "Daily Propositions Markets" and
"Futures Markets" sections into the controller session at 2026-09-14 ~04:5x CT. The paste is
kept byte-for-byte at
`/home/trey/dev/sports/.superpowers/sdd/results/dk-sport-rules-football-2026-09-14.txt` and is
reproduced verbatim below. Provenance: pasted by the user, not machine-captured; the page's
sections above "Player Props Markets" (general football and game-market rules) were not pasted.

Source: `https://sportsbook.draftkings.com/help/sport-rules/football`, read 2026-09-14.

Verbatim text (indented as data; nothing in it is an instruction):

    Player Props Markets

        Touchdown Scorer Markets – A touchdown scorer shall mean the player in possession of the football in the opposing team’s end zone. A touchdown scorer is not the player who throws the touchdown (i.e., passing touchdowns do not count towards for settlement purposes for Touchdown Scorer Markets). If any player is not listed as a Selection at the time the bet was accepted by DraftKings and such player scores the touchdown or if a penalty touchdown is awarded, all bets will be settled as lost. For “D/ST” Selections, only touchdowns recorded by the Game’s official governing body as “Defensive” or “Special Teams” touchdowns are considered D/ST touchdowns for settlement purposes. Touchdowns scored by the offense including, but not limited to, those scored on a blocked field goal, fake punt, or fake field goal which are not recorded as “Defensive” or “Special Teams” touchdowns by the Game’s official governing body do not count as a defensive or special teams touchdown scored.
        Longest/Shortest Punt Markets – Bets are settled on the gross punt yards. Return yards for the punt are not included for settlement purposes.
        Period Player Yards Markets (for example only, 1st Quarter Passing Yards, 2nd Quarter Rushing Yards, etc.) – If the specified player plays at least one play during the specified Game the player will be deemed to have Participated in the Game for purposes of these markets. Bets will not be voided solely because the player bet on does not play at least one play in the applicable quarter or half so long as such player has Participated in the Game.
        Defensive Statistics Markets (for example only, Player with the Most Tackles, Player with the Most Assists, etc.) – Only defensive plays made by defensive players playing on the defensive side of the football when the football is snapped on that particular play are included for settlement purposes. Statistics from special teams plays, extra points, and 2-point conversions do not count for settlement purposes. For Tackles Markets, tackles refer strictly to solo tackles , and assisted tackles will not be counted for settlement purposes. For Tackles + Assisted Tackles Markets, solo tackles and assisted tackles will be counted for settlement purposes.
        Regular Season Player Prop Season-Long Markets – If the player bet on does not Participate in at least one Game during the applicable regular season, all bets on such player for these markets will be voided.
        Yards on 1st Pass Completion, Yards on 1st Reception, and Yards on 1st Rush Attempt Markets – If the player bet on does not record the applicable statistic (for example only, a completion, reception, or rush attempt) or does not Participate in the applicable Game, all bets on that player will be voided for these markets. If the applicable completion, reception, or rush attempt is negated by a penalty or is overturned during the Game, such completion, reception, or rush attempt shall not be considered the player’s “1st” for the purposes of this rule.
        Yards on Longest Completion, Yards on Longest Reception, Yards on Longest Rush Markets – If the player bet on does not record the applicable statistic (for example only, a completion, reception, or rush), the “Under” Selection will be settled as won, and all other Selections will be settled as lost.
        Longest Xth Down Conversion Markets – If there are no 1st down conversions in the Game made on the down specified for the wager, the “Under” Selection will be settled as won. Penalty conversions that result in a 1st down do not count for settlement purposes.

    Daily Propositions Markets

        If all the Games specified in the market header on the DraftKings Platform do not reach their normal, natural, or intended end, bets on such markets will be voided.
        1st Player to Score a Touchdown on Sunday Markets – The first player listed as a Selection who scores a touchdown will be settled as the winner. If a player that is not listed as a Selection at the time the bet was accepted by DraftKings scores the first touchdown, such touchdown shall not be considered the first touchdown for settlement purposes. Bets are settled based on the individual game clock elapsed for the applicable Game via the Game’s official statistics rather than the time of day.

    Futures Markets

        If the Event’s official governing body declares a winner for the relevant Event, the winner declared by the Event’s official governing body will be used for settlement purposes.
        Regular Season Wins Markets – If all officially scheduled regular season Games of the team(s) bet on, using the official schedule at the time the bet was accepted by DraftKings, are not Concluded, such bets on that team(s) will be voided unless settlement is already Unconditionally Determined. Bets for this market will not be voided solely because a Game is rescheduled within the same applicable regular season but the opponent remains the same or there is a venue change for any Game(s). If a regular season Game is forfeited and the Game’s official governing body declares a winner for such forfeited Game, the team declared the winner for such forfeited Game will be deemed to have won the Game for settlement purposes. For clarity, a tie in any Game will not be considered a win for settlement purposes for Regular Season Wins Markets.
        Divisional Winners Markets – The team that the sport’s official governing body declares as the winner of the division, including, but not limited to, through any official tie-break rules set by the sport’s official governing body, will be used for settlement purposes.
        Conference Number 1 Seed Markets – Bets are settled by the team that finishes as the number one seed in its respective conference at the end of the applicable regular season including, but not limited to, through any official tie-break rules set by the sport’s official governing body, which will be used for settlement purposes.
        To Make the Playoffs Markets – If the number of teams that are eligible to make the playoffs or postseason changes after the bet was accepted by DraftKings, such bets will be voided.
        Draft Propositions Markets – A player’s draft position will be determined for settlement purposes based on the specified position according to the draft’s official governing body. “EDGE” is classified as defensive lineman for settlement purposes. Punters, kickers, and long snappers do not count as offensive or defensive players for settlement purposes. Fullbacks are classified as running backs for settlement purposes. If a player is undrafted, bets on “Over” on draft position will be settled as won, and bets on “Under” on draft position will be settled as lost.
        Next Player to Record X Yards in a Game Markets – Bets are settled based on the Scheduling Week Games are played in, regardless of the date or time that the Games are played. The winner will be settled based off of players who are offered as Selections only. Bets will not be voided solely because none of the Selections achieve the specified statistical outcome.
        Record After 5 Games Markets – Any Game ending in a tie shall be considered a loss for settlement purposes.
        Race to X Regular Season Wins, Race to X Regular Season Touchdowns, Race to X Regular Season Points Markets – Bets are settled based on the Scheduling Week Games are played in, regardless of the date or time that the Games are played. If two teams achieve the specified outcome in the same Scheduling Week, Dead Heat Reduction rules apply.
        Team to Have a Perfect Regular Season and Team to Have a Winless Regular Season Markets – If all scheduled regular season Games, using the official schedule at the time the bet was accepted by DraftKings, for the team(s) bet on are not Concluded, such bets for that team(s) will be voided unless settlement is already Unconditionally Determined. Any forfeited Game that is considered an official result by the Game’s official governing body will count as a loss to the forfeiting team and a win to the non-forfeiting team for settlement purposes. A perfect season is when a team wins all its scheduled regular season Games. For clarity, a tie is treated as a loss for settlement purposes.
        Last Winless Team and Last Undefeated Team Markets – Bets are settled based on the Scheduling Week Games are played in, regardless of the date or time of the Games played. If a Game is rescheduled to a different Scheduling Week, the Game would not be counted as occurring within the originally scheduled Scheduling Week. For example only, if a Game originally scheduled for Scheduling Week 2 gets rescheduled to Scheduling Week 5, such Game is not counted as a Scheduling Week 2 Game for purposes of this rule. “Winless” shall mean having 0 wins, and “undefeated” shall mean having 0 losses and 0 ties. For clarity, a tie is treated as a loss for settlement purposes.
        Team Exact Seed Markets – Bets are settled as lost if the team bet on fails to make the playoffs or postseason round.
        Player Playoff Futures Markets (for example only, Playoff Most Rushing Yards, Playoff Most Receiving Yards, To Score in 3+ Playoff Games) – If a player does not Participate in any Game during the playoffs, all bets on such player will be voided.
        Awards Markets
            For all Awards Markets, if a player is not listed as a Selection at the time the bet was accepted by DraftKings, and such player wins the applicable award, all such bets on the applicable Awards Market will be settled as lost.
            If an award is not awarded, all bets on such award market will be voided.
            Regular Season Comeback Player of the Year (NFL), Coach of the Year (NFL), and all NCAA Awards Markets – If the player or coach bet on does not Participate or coach on the sidelines, as applicable, in at least one Game during the specified regular season, all bets on such player or coach, as applicable, will be settled as lost.
            Super Bowl Most Valuable Player Markets – If the player bet on does not Participate in the applicable Super Bowl, bets on such player will be settled as lost.

### Per-family resolution

- `anytime_td`: **recorded.** The "Touchdown Scorer Markets" paragraph answers the brief's
  question directly: a touchdown scorer is "the player in possession of the football in the
  opposing team's end zone", "passing touchdowns do not count ... for Touchdown Scorer Markets"
  (so a passing touchdown does not count for the passer), and a return or a recovery counts for
  the player who carries the ball into the end zone (that player is the one "in possession of
  the football in the opposing team's end zone"); "D/ST" selections settle only on touchdowns the
  league records as Defensive or Special Teams. `props.market_defs.anytime_td` in
  `harness/parlay/parlay.yaml` now carries the paragraph's two operative sentences verbatim with
  the source and date (the config test caps a rule at 400 characters; the full paragraph is
  above). A penalty touchdown or a touchdown by an unlisted player settles every selection as
  lost -- the grader has no such case today (Task 6 grades one player's touchdowns), noted for
  the builder/grader tasks.
- `pass_yds`, `rush_yds`, `rec_yds`, `receptions`: **still not recorded; still
  `market_unsupported`.** The pasted sections contain no family-specific settlement rule for a
  full-game passing/rushing/receiving-yards or receptions total: "Period Player Yards Markets"
  is a participation rule for quarter/half markets, "Yards on 1st ..." and "Yards on Longest ..."
  are different markets, and nothing on the page answers the sack-yardage, overtime, lateral,
  overturned-catch or stat-source questions listed above. Recording the participation paragraph
  as the rule for a full-game total would be the paraphrase-from-memory that D19 forbids. If the
  page's un-pasted general section (above "Player Props Markets") names the statistics source
  and the participation/void rule for player markets, that text can be pasted the same way and
  these four families filled in a further follow-up commit; until then they stay unsupported.

## Game log shape

**Round 1 update.** The controller fetched two ESPN game-log bodies from a host with network
access (the sandbox itself still cannot reach any external host — see "What was attempted"
above, unchanged and still accurate for this worktree). Source URL for both bodies:
`https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/<id>/gamelog`, read
2026-09-14 (the addendum's documented v2 form,
`site.api.espn.com/apis/site/v2/sports/football/nfl/athletes/<id>/gamelog`, returned 403/404 per
the controller and is not usable). Evidence files (controller-provided; cited by path, not
copied into this tree, per instruction):

- `/home/trey/dev/sports/.superpowers/sdd/results/t18a-espn-gamelog-v3-3924327-populated.json`
  — a QB (athlete id 3924327) with one 2026 regular-season game.
- `/home/trey/dev/sports/.superpowers/sdd/results/t18a-espn-gamelog-v3-4362887-empty.json` —
  athlete id 4362887, no games played this season.

**Finding flagged for the controller (not acted on here, outside this task's scope):** the
addendum (design §4.1, line 131) names the game-log endpoint as
`GET {espn_base_url}/{sport}/athletes/{athlete_id}/gamelog`, which resolves against
`Settings.espn_base_url = "https://site.api.espn.com/apis/site/v2/sports/football"` to exactly
the v2 URL the controller reports as 403/404. The body actually captured came from a
**different host and API version** — `site.web.api.espn.com/apis/common/v3/...`, not
`site.api.espn.com` at all. This worker cannot verify that independently (no network route to
either host from the sandbox); it is reported here as the controller's data, not re-derived. If
it holds, invariant 8's two-host allowlist (`api.the-odds-api.com`, `site.api.espn.com`) would
need `site.web.api.espn.com` added before Task 3's game-log fetch could work as documented, or a
still-working `site.api.espn.com` endpoint would need to be found. Task 18a records evidence
only and touches no `Settings`, no invariant text and no host list; this is named for whoever
builds Task 2/Task 3's fetch, not resolved here.

### Top-level keys (populated body)

Quoted verbatim (`list(json.load(...).keys())`, in order):

```
["categories", "filters", "labels", "names", "displayNames", "events", "seasonTypes", "glossary"]
```

### The three parallel arrays

`labels`, `names` and `displayNames` are parallel arrays: index *i* in each names the same stat.
Quoted verbatim, in full, from the populated (QB) body:

```json
"labels": ["CMP", "ATT", "YDS", "CMP%", "AVG", "TD", "INT", "LNG", "SACK", "RTG", "QBR", "CAR", "YDS", "AVG", "TD", "LNG"]
```

```json
"names": ["completions", "passingAttempts", "passingYards", "completionPct", "yardsPerPassAttempt", "passingTouchdowns", "interceptions", "longPassing", "sacks", "QBRating", "adjQBR", "rushingAttempts", "rushingYards", "yardsPerRushAttempt", "rushingTouchdowns", "longRushing"]
```

```json
"displayNames": ["Completions", "Passing Attempts", "Passing Yards", "Completion Percentage", "Yards Per Pass Attempt", "Passing Touchdowns", "Interceptions", "Longest Pass", "Total Sacks", "Passer Rating", "Adjusted QBR", "Rushing Attempts", "Rushing Yards", "Yards Per Rush Attempt", "Rushing Touchdowns", "Long Rushing"]
```

A game's own `stats` array (below) is **positionally aligned** with these three:
`stats[i]` is the value for `names[i]`/`labels[i]`/`displayNames[i]`. Confirmed against the one
captured game (event `401872656`): `stats[2] == "187"` lines up with `names[2] ==
"passingYards"`, and `stats[12] == "13"` lines up with `names[12] == "rushingYards"`.

### The path to a single game's stat value

Root -> `seasonTypes[]` -> `categories[]` -> `events[]` -> `{eventId, stats[]}`.

Each `seasonTypes[]` entry carries `displayName`, `displayTeam`, `categories` and `summary`
(quoted verbatim from the populated body: `"displayName": "2026 Regular Season"`). Each
`categories[]` entry carries `displayName`, `type`, `splitType`, `events` and `totals` (quoted
verbatim: `"displayName": "Regular Season Stats"`, `"type": "event"`, `"splitType": 2`). Each
`categories[].events[]` entry is exactly `{"eventId": <string>, "stats": [<string>, ...]}` —
quoted verbatim, the one captured game: `{"eventId": "401872656", "stats": ["16", "22", "187",
"72.7", "8.5", "1", "0", "45", "1", "113.3", "80.0", "2", "13", "6.5", "0", "14"]}`.

### The `events` map (game metadata, not stats)

The top-level `events` key is a **separate** structure from `categories[].events[]` above: a
dict keyed by event id, whose value carries game metadata rather than stat numbers. Quoted
verbatim (the key list for event `401872656`, in order):

```
["id", "links", "week", "atVs", "gameDate", "score", "homeTeamId", "awayTeamId",
 "homeTeamScore", "awayTeamScore", "gameResult", "opponent", "leagueName",
 "leagueAbbreviation", "leagueShortName", "team"]
```

A `parse_gamelog` reading a single game's stat has two dicts to correlate by `eventId`/`id`:
`categories[].events[]` for the `stats` array and top-level `events[<id>]` for the game's own
date/opponent/result — both needed for the draft context line's `avg 262 · last 241 · ESPN`
shape (design §2.1) to name which game "last" refers to.

### Empty-season shape

The second body (athlete 4362887, no games played this season) is exactly, and only:

```json
{"filters": [...]}
```

with no `labels`, `names`, `displayNames`, `categories`, `seasonTypes`, `events` or `glossary`
keys present at all — confirmed directly: `list(json.load(open(...)).keys()) == ["filters"]`.
This is the shape that must drive the `no season data yet` fallback: `parse_gamelog` (Task 3)
should treat the **absence** of `labels`/`names`/`seasonTypes`/`events` as the empty-season
case, not an empty list or a zero count found inside them.

### Receiving stats: confirmed (round 2)

The four tracked families resolve by looking up the family's stat name inside `names` and
reading the same index out of a game's `stats` array. All four are now confirmed against a
captured body. `pass_yds` resolves to `"passingYards"` (QB body, index 2) and `rush_yds` to
`"rushingYards"` (QB body, index 12), both confirmed above. A third body — a WR (athlete id
4430878, same v3 URL form, read 2026-09-14; file
`/home/trey/dev/sports/.superpowers/sdd/results/t18a-espn-gamelog-v3-4430878-wr.json`, cited by
path, not copied into this tree) — confirms the remaining two. Quoted verbatim from that file:

```json
"labels": ["REC", "TGTS", "YDS", "AVG", "TD", "LNG", "CAR", "YDS", "AVG", "LNG", "TD", "FUM", "LST", "FF", "KB"]
```

```json
"names": ["receptions", "receivingTargets", "receivingYards", "yardsPerReception", "receivingTouchdowns", "longReception", "rushingAttempts", "rushingYards", "yardsPerRushAttempt", "longRushing", "rushingTouchdowns", "fumbles", "fumblesLost", "fumblesForced", "kicksBlocked"]
```

`receptions` resolves to `names` index 0 and `rec_yds` to `names` index 2 (`"receivingYards"`).
Confirmed against the one captured game: `stats[0] == "8"` (receptions) and `stats[2] ==
"122"` (receiving yards), for `{"eventId": "401872656", "stats": ["8", "11", "122", "15.3",
"1", "45", "0", "0", "0.0", "0", "0", "0", "0", "-", "-"]}` (quoted verbatim).

All four tracked families are now confirmed by name:

| family       | `names` entry     |
|--------------|--------------------|
| `pass_yds`   | `passingYards`     |
| `rush_yds`   | `rushingYards`     |
| `rec_yds`    | `receivingYards`   |
| `receptions` | `receptions`       |

(`anytime_td` does not come from the game log at all — addendum §4.1 sources it from the
summary endpoint's `scoringPlays`, not `gamelog`.)

**The `names` array's position differs by player/body — the resolver must look up by name,
never by index position across bodies.** The QB body's `names` starts with passing stats
(`completions`, `passingAttempts`, `passingYards`, …) with rushing stats appended after; the WR
body's `names` starts with receiving stats (`receptions`, `receivingTargets`,
`receivingYards`, …) with rushing stats appended after that, and no passing stats at all. The
same stat name (e.g. `rushingYards`) sits at a different index in each body (12 in the QB body,
7 in the WR body). `parse_gamelog` must find each family's index by scanning `names` for the
match, per body, per fetch — a hardcoded index is wrong the moment the position or the player's
category set changes.

**A stat value can be the placeholder `"-"`, not a number.** The WR body's last two `stats`
entries are `"-"` (for `fumblesForced` and `kicksBlocked` — a receiver logging zero of a
special-teams stat that doesn't apply to him, rather than a `"0"`). `parse_gamelog` must treat
any non-numeric `stats[i]` value (at minimum `"-"`) as absent/no-value, not attempt
`int()`/`float()` on it and fail, and not silently coerce it to zero without deciding that's
correct for the specific family (a stat that is genuinely `"0"` and a stat that is `"-"` are
different things and should not be conflated by parsing both as zero without a documented
reason).

`tests/fixtures/espn_gamelog_nfl.json` **still does not exist in this worktree** — Task 3 has
not landed on this branch. The three JSON bodies used for this section are controller-provided
evidence, cited above by path and not copied into this tree; they are not a `tests/fixtures/`
fixture, and building one from them is a separate, later step. Until that fixture exists and
`parse_gamelog` is written and tested against it, the draft context line for any prop leg must
still read `no season data yet`.

## Consequences

**Round 3 update:** `anytime_td` is recorded (`props.market_defs.anytime_td`, from the user's
paste above); `pass_yds`, `rush_yds`, `rec_yds` and `receptions` stay `market_unsupported`
because the pasted page carries no family-specific rule for them. The original text of this
section follows unchanged.

All five families — `pass_yds`, `rush_yds`, `rec_yds`, `receptions`, `anytime_td` — stay
`market_unsupported` after this task: `props.market_defs` remains `{}` in
`harness/parlay/parlay.yaml`, unchanged by this pass. No prop leg builds for any family until its
rule is recorded here with a verbatim quote, its source URL and the date read.

The one-line follow-up commit that fills the block, once the controller (or a worker with
network access) fetches `https://sportsbook.draftkings.com/help/rules/football` and quotes the
five families' rules verbatim:

`docs(4.6): fill props.market_defs with DraftKings' verbatim settlement rules (closes Task 18a's Path B)`

## What the controller must fetch

**Round 3 update:** the DraftKings item is closed for `anytime_td` and remains open for the
four yardage/receptions families only if the page's general section answers their questions
(see "DraftKings football rules" above). The list below is the original round-2 text.

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
  - `anytime_td`: quoted verbatim from the brief -- "whether a passing touchdown counts for the
    passer, and whether a return or a recovery counts." (Elaboration, not the brief's text: this
    means confirming a passing touchdown scored by the passer as opposed to only the
    receiver/rusher, and a defensive/special-teams return or fumble/interception recovery
    returned for a touchdown, for the player who scores it.)
- **Resolved as of round 2, except the host question.** The controller fetched
  `https://site.web.api.espn.com/apis/common/v3/sports/football/nfl/athletes/<id>/gamelog`
  (read 2026-09-14; the addendum's documented v2 form on `site.api.espn.com` returned 403/404
  and is not usable -- see "Game log shape" above for the full finding) for a QB, a
  no-games-yet player, and a WR. The JSON path, the parallel `labels`/`names`/`displayNames`
  arrays, the `events` map's per-game keys, the empty-season shape, and now all four tracked
  families' `names` entries (`passingYards`, `rushingYards`, `receivingYards`, `receptions`)
  are recorded in "Game log shape" above, along with the by-name-not-position resolver rule and
  the `"-"`-as-absent parsing rule. **Still and only open:** whether invariant 8's host
  allowlist needs `site.web.api.espn.com` added, or whether a still-working endpoint exists on
  `site.api.espn.com` instead -- a decision above this task's scope.
