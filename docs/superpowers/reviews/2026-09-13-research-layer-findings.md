# Research layer: first two live days, findings for the design session

**Date:** 2026-09-13, read at 10:30 CT on the runtime database (Omarchy, build ebf0953).
**Scope:** the phase 5 shadow veto (Opus primary, Sonnet shadow), the weekly report annotator and the U4 spend caps. Read-only: no code, schema, setting, cap or prompt was changed for this document.
**Evidence:** `2026-09-13-research-layer-findings/queries.sql` (every query, read-only) and `evidence-2026-09-13-1030ct.txt` (its output). Re-run the script to refresh the numbers.
**Status:** a review record and the input to a user-led design session. Nothing here is a decision. Every change it points at is a spec amendment (addendum 1.4), a U4 cap change or a prompt change, and each of those is a dated user decision under the roadmap's rules (R1, U4; the prompt module's own header says a prompt change "starts a new population for H9").

---

## 1. Summary

The research layer captured everything it did. It has not yet produced anything that measures H9.

- **Spend:** $24.60 on Friday 9/11 and $24.42 on Sunday 9/13, each day's cap reached and the veto dormant for the rest of the Chicago day by design. Today the cap tripped at **01:42 CT** after 85 minutes and 134 signals. The NFL slate kicks off at 12:00 CT today and the veto is asleep for all of it.
- **Decisions:** 705 decided signals, **every one `proceed`**; 9,361 labelled `veto_skipped_budget`. Confidence sits between 0.55 and 0.76 with twelve distinct values.
- **The model never knows which game it is judging.** The feature block carries no team, matchup, ticker or game identifier. In 432 of 451 Opus outputs and 411 of 451 Sonnet outputs the stated reason is a variant of "no team identifiers, so no targeted news check is possible". The web searches then land on league-wide injury pages that cannot be tied to the signal.
- **No outcome to score against.** 302 of the 705 decided signals became orders; 0 filled. Even a discriminating veto could not be scored on this sample.
- **Cost per call tripled** from $0.037 to $0.121 (Opus) between the two live days because the six-hour fair history filled in at five-minute buckets once pricing ticked regularly (fix 48): 7 history points became 72, the features payload grew from 1.7 KB to 9.6 KB, and the cap now buys 134 signals instead of 317.
- **What is good:** the record is complete and auditable (913 notes with features, snippets, tool calls, output, usage, cost, latency, prompt hash); the reservation gate held the cap exactly; the shadow ran on every call; the two annotator calls that succeeded produced accurate, cell-cited bullets (nine of eleven failed, see 4.5).

## 2. What ran

| Chicago day | Kind | Model | Calls | Searches | USD |
|---|---|---|---|---|---|
| Fri 9/11 | veto | claude-opus-5 | 317 | 196 | 11.66 |
| Fri 9/11 | veto | claude-sonnet-5 | 317 | 389 | 11.39 |
| Fri 9/11 | annotate | claude-opus-5 | 10 | 0 | 1.54 |
| Sat 9/12 | annotate | claude-opus-5 | 1 | 0 | 0.13 |
| Sun 9/13 | veto | claude-opus-5 | 134 | 140 | 16.19 |
| Sun 9/13 | veto | claude-sonnet-5 | 134 | 168 | 8.23 |

Saturday shows no veto calls: the pricing stage was budget-exhausted and produced no signals until fix 48 deployed (journal 136 finding (b), 153). `usd_reserved` is 0 on every row, so no reservation was left hanging.

Today's window: first call 00:17 CT, last call 01:42 CT, 268 notes (134 pairs). The first NFL kickoff today is 12:00 CT. The veto judged overnight signals on a flat book and is dormant for the whole slate.

## 3. What was captured

| Table | Rows | What it holds |
|---|---|---|
| `research_notes` | 913 (902 veto, 11 annotate) | per call: features sent, search snippets, tool calls with the pinned tool type, JSON output, usage, cost, latency, request id, prompt hash |
| `veto_decisions` | 10,066 | 705 decided (`proceed`), 9,361 `veto_skipped_budget` with reason `daily` |
| `veto_h9` (view) | decided rows joined to their primary note | the H9 population as the spec defines it |
| `research_spend` | 6 | one row per day, kind and model; the cap ledger |
| `veto_queue` | 11,297 (1,231 unclaimed) | the executor's enqueue of every signal with an intent |
| `report_annotations` | 2 | report run 2: zero bullets stored (its note has three); report run 4: two bullets |
| `rfq_quotes` | 0 | listener switched off since 9/12 00:27 CT (journal 136 (g)) |

Every veto `call_id` has its pair of notes (primary and shadow), as the spec's conformance row asks; five shadow notes carry no parsed output. The capture side of the design works.

## 4. Why the output is not useful yet

### 4.1 The prompt omits the matchup

Addendum 1.4 lists the features as fair history, venue price history, disagreement, fair staleness, time to kickoff, the newest weather snapshot and the ESPN status enum. Team ids, the game id, the event ticker and the teams' names are not on the list, and `harness/research/features.py` builds exactly the list. The `games` row is read only for `sport`, `kickoff_utc` and `status`.

The instruction text asks the model to "search for news that would change the numbers: an injury, a suspension, a weather change, a lineup change, a venue change." Without a matchup the model cannot form a targeted query. Its own reasons say so in 96 % of Opus calls and 91 % of Sonnet calls. The searches it does make (about 1.3 to 3.3 per call) return league-wide injury round-ups, inactives pages and IR trackers, none of which can be cited against a specific signal. `evidence_ids` is empty on every Opus output, and `veto_decisions` holds the primary's decision, so the H9 table has no cited evidence at all.

This is a spec-level gap, not an implementation bug: the implementer built the list as written. It is also the reason the prompt's safe default ("`proceed` unless cited evidence") produces `proceed` every time: there can be no cited evidence.

### 4.2 No variance, nothing to score

- 705 of 705 decided rows are `proceed`. The `veto_rate` Pulse rule at 25 % of decided signals never moves.
- Confidence: min 0.55, max 0.76, standard deviation 0.05, twelve distinct values. It does not vary with anything the features could drive because the model says it has nothing to go on.
- The Sonnet shadow agreed with Opus on 444 of 446 paired calls and returned `reduce` twice. Those two rows are the most interesting in the table: Sonnet **inferred the matchup** from kickoff time, stadium weather and roof (Bills at Texans, thunderstorms and a retractable roof against an open-roof, 1 % precipitation feature; Commanders at Eagles, a left tackle out with a torn triceps) and cited real pages. Its `evidence_ids` are URLs rather than the snippet ids the worker resolves, so the worker would downgrade them to `proceed` anyway. Sonnet cited evidence on 18 of 451 calls; Opus on 0 of 451. Opus followed the "no identifiers, so no targeted check" reasoning more faithfully and was therefore less useful.
- 302 decided signals produced orders; none filled (journal 160: two fills all week, both ncaaf). H9 scores decision labels against realised outcomes. There are no realised outcomes.

### 4.3 The cost per call tripled, and the cap now ends before kickoff

| Chicago day | Model | Calls | features (chars) | fair history points | input tok | cache write tok | cache read tok | output tok | USD per call |
|---|---|---|---|---|---|---|---|---|---|
| 9/11 | opus | 317 | 1,704 | 7 | 1,399 | 1,378 | 10,198 | 396 | 0.037 |
| 9/11 | sonnet | 317 | 1,704 | 7 | 1,394 | 3,155 | 23,719 | 823 | 0.036 |
| 9/13 | opus | 134 | 9,613 | 72 | 7,649 | 8,154 | 14,487 | 557 | 0.121 |
| 9/13 | sonnet | 134 | 9,613 | 72 | 7,479 | 7,471 | 30,241 | 920 | 0.061 |

The features block is the same shape on both days; what changed is how full it is. Addendum 1.4 specifies fair history "over the 6 h before the signal (5-minute buckets)". On 9/11 the pricing stage was ticking sparsely, so the window held about 7 points. After fix 48 the stage ticks every five minutes, the window holds 72 fair points and 59 venue points, and the same six hours cost five times the tokens. The payload is mostly repetition: the sampled row has `fair_p: 0.5` in every one of its 72 buckets.

At $0.18 per signal pair, the $25 day buys about 135 signals. On a game day the executor produces thousands of candidates (4,319 skipped on 9/11, 5,042 today). Two consequences:

- the cap is reached in the quiet hours before the first kickoff, so the veto sees no in-window signal on any busy day;
- the bucket claim order decides which 135 signals get judged, and it is not a sample anyone chose.

Cache write is now the largest cost line on an Opus call (8,154 tokens at the 1.25x write rate, about $0.05 of the $0.12). The only breakpoint is on the system block, so these writes are not the frozen prompt; they track the per-call content. Whether that is the server-tool round re-billing context into cache, or a second breakpoint the client sets, is worth reading off the `usage` JSON before the design session settles the cache layout.

### 4.4 The worst-case reservation is far above the realised cost

`reserve_spend` books 60,000 input, 4,096 output and 3 searches per model per call, $0.62 for the pair, against a realised $0.07 to $0.18. Ruling A-C3 says the constants are re-fitted after the first live day and journaled. They have not been re-fitted; the ledger above is the data to do it with. The over-reservation does not lose money (the release is exact), but it decides when the last call of the day is refused and it sizes any future per-kind budget split.

### 4.5 The annotator

Eleven calls for two reports, $1.68 in total, and nine of the eleven failed: four came back `BadRequestError` (HTTP 400, $0 each), five hit `max_tokens` at 1,024 output tokens and failed the schema parse (about 47,000 input tokens each, the bulk of the annotator's spend). The two calls that succeeded produced accurate, cell-cited bullets: three for report run 2 (zero fills across 99 orders, queue depth, CLV untestable) and four for report run 4 (two fills from 256 ncaaf orders, greyed CLV contrasts, the mispricing map). Two anomalies for the session: `report_annotations` stores run 2 with **zero** bullets although its successful note carries three, so something between the parse and the row dropped them; and the 1,024-token ceiling the failed calls hit is the pre-amendment literal the prompt module's own comment warns about, so the annotator may still pass it. The content, when it lands, is useful; the path to it is not yet reliable.

## 5. What is salvageable from the spend

- The 913 notes are a clean record of the failure mode, with the model's own explanation on almost every row. They are the argument for the design change.
- The realised usage per call, per model, with and without the full history, is exactly the data A-C3 asked for to re-fit the worst case.
- The annotator output for report run 4 stands as the first t10 content, and Sonnet's two `reduce` rows show that a model given a matchup can find signal-specific news.
- The `veto_queue` still holds 1,231 unclaimed signals, and the cache share (161 of 478 decided on 9/11, 93 of 227 today) shows the 30-minute bucket cache is doing real work once a game is judged.

## 6. Questions for the design session

These are the choices the data raises. They are listed, not answered.

1. **Identity.** Should the features carry the matchup (team names or ids, the event ticker, the kickoff) so the model can search for the game? F60 treats venue free text as untrusted, but a team name from the harness's own `teams` table is harness data, not venue text. What is the F60 position on a harness-owned name inside the trusted block? Sonnet's two `reduce` rows show the model will infer the game from kickoff and weather when it can, so the identity is already leaking in the weakest possible form; giving it outright is the honest version.
2. **What question is the model actually good at?** Today's instruction asks for news that invalidates a number. A different framing (for example: "given this matchup, list the facts a sharp book would already have priced that our fair might not") changes the prompt hash and starts a new H9 population. Is that acceptable now, two live days in, before a meaningful population exists?
3. **Payload.** Keep the six-hour window but summarise it (first, last, min, max, count, last change) instead of 72 identical points? Or keep the points but only where the value changed? Either cuts the per-call input by most of its size without losing information the model could use.
4. **Budget shape.** $25 a day judged 134 signals in 85 minutes. Options include: a per-hour or per-game-window allocation so the cap lasts into kickoff; a sampling rule over candidates so the judged set is a chosen sample rather than the first N claims; judging intents that became orders rather than every candidate; or raising the cap. Each is a U4 decision.
5. **The shadow.** Sonnet costs $0.06 of every $0.18 pair and has agreed with Opus on 99.5 % of calls. Keep it for the comparison H9 wanted, drop it, or move it to a sample?
6. **Search.** With no matchup, each search is wasted money. With a matchup, is three the right `max_uses`, and should `allowed_domains` stay unset (D22)?
7. **Scoring.** With zero fills, what outcome does H9 score against in the meantime: markouts at fixed horizons, closing line, or settlement of the underlying game regardless of fill?
8. **Worst case.** Re-fit the reservation constants from the ledger (A-C3) now, and at what margin over the observed p95?
9. **Cache layout.** A sampled Opus call today reads `input 7,580, cache_write 8,500, cache_read 11,980, output 466, searches 1, code_execution_calls 1`. The frozen system prompt is a few hundred tokens, so the 8,500 written per call is per-call content (the tool round re-billing the features and results into cache, or a breakpoint the client sets). Establish which before deciding where the breakpoints go.
10. **Evidence id format.** The worker resolves snippet ids (`s1`, `s2`); Sonnet returned URLs. Either the schema description should say which, or the worker should accept both. Otherwise a correct citation is discarded as uncited.

## 7. What this document does not do

It does not change the prompt, the features, the caps, the constants, the schema or any setting. It does not start or resume the loop. It records that the veto's first two days produced a complete record and no measurable H9 result, and why.
