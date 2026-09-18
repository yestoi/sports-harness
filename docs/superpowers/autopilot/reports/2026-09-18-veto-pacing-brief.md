# Pacing the veto's daily budget across the day (docket item 19, brief for the user)

Written 2026-09-18 by the controller under the user's ruling of 2026-09-18 (journal 272, item 19, point 2). A proposal, not a dispatch: it changes when the cap binds, so the user rules before any code. Caps unchanged: `veto_daily_usd_cap` $25, `veto_weekly_usd_cap` $150 (U4, invariant 7). Numbers: `evidence/2026-09-18-veto-study-finding.txt` (read-only, last 7 days to 07:30 CT).

## What happens today

- The research worker claims the **oldest** claimable bucket first (`harness/research/veto.py` `_OLDEST_BUCKET`: `order by q.bucket_start`). The daily cap resets at midnight CT (`research_spend.day`), so each day's $25 goes to the buckets that were queued overnight and early morning.
- Result over 7 days: 3,149 decided rows out of 178,618 intents (1.8 %). Of the decided rows, 2,267 (72 %) were judged 48-149 h before kickoff and 568 more 24-45 h before; 314 (10 %) inside 18 h; **none inside 5.7 h of kickoff**. The 175,469 `veto_skipped_budget` rows include 14,882 that fell inside 6 h of kickoff, where the veto's designed value (status and injury news) can exist.
- Decided calls cluster at 00-02 CT (2,124 of the sample) and 08-13 CT (1,662); the cap is spent before 09:00 CT every day (spend $24.40-24.56 per day; today's $24.56 by 06:50 CT, 414 calls).
- Labels: `proceed` 3,097 (1,234 from cache, 1,518 with reason `fair_move`), `veto_error` 52 (43 connection errors, 5 search errors, 4 timeouts), `reduce` 0 on the primary, `veto` 0. Primary versus shadow: 1,829 pairs both decided, 3 disagreements (all `proceed` versus `reduce`), 0.16 %. The 0-of-1,863 primary `proceed` rate therefore says nothing yet about whether the veto fires close to kickoff: it has never been asked there.
- Cost: $0.085 per primary-plus-shadow pair; the week is $122.26 through Friday morning, so Saturday reaches about $146.7 and Sunday has about $3.30 under the weekly cap whatever the daily pacing does.

## Options (all under the unchanged caps; no new spend)

1. **Kickoff-proximity claim order** (smallest change). Claim the bucket whose game is **closest to kickoff** first (`order by g.kickoff_utc - now()` over the queue joined to games, ties by `bucket_start`), keeping the per-row stale-claim guard. Cost: signals for games days away are decided only when nothing nearer is queued, so the far-out sample shrinks to what the overnight quiet hours leave; H9's population stays "every decided signal", but its time-to-kickoff mix changes on the amendment date. Reversal: one ORDER BY. Risk: on a Saturday with 30 kickoffs the $25 goes entirely to the 11:00 CT slate and the evening slate is unseen.
2. **Hourly sub-budgets** (the ruling's wording). Split the $25 into 24 hourly allotments, weighted by the expected intent volume per CT hour (the queue's own last-7-day histogram, refreshed daily), with unspent allotments rolling forward inside the day. The worker refuses a call when the current hour's allotment is spent (a new `BudgetRefused("hourly", ...)` reason, labelled `veto_skipped_budget` with `reason_code = 'hourly'`). Cost: about 25 lines in `spend.py` plus a settings-free weight table; the daily and weekly caps stay the outer bound. Reversal: set every weight equal. Risk: the weights are a model of the day; a weekday with one night game gets the same shape as a Saturday unless keyed by day of week.
3. **Reserve a near-kickoff floor** (combinable with 1 or 2). Hold back a fixed share of the day (proposal: 50 %, $12.50) for signals whose kickoff is within 6 h; release it to the general pool at 21:00 CT if unspent. Cost: the far-out sample halves. Reversal: share to 0 %.

Recommendation: **1 plus 3** for this weekend's shape (few games, kickoff-clustered), with 2 as the later refinement once a week of near-kickoff decisions exists to weight it by. Whichever is chosen is a dated measurement amendment (spec §6.7): H9's decided population changes its time-to-kickoff mix from the amendment date, the pre-amendment range is labelled, ids unchanged.

## What the loop does meanwhile

Nothing in code. The daily 09:00 CT line notes from today whether the annotator or the parlay rationale was refused because the veto had spent the shared cap (`research_notes.kind` in `annotate`, `parlay`; refusals appear as `BudgetRefused` in the research worker's log and as `usd_reserved` never settling). The calendar's seven-day veto study is deferred, not skipped (journal 272): its model re-runs are `study` kind under the same $25 and would displace the veto entirely on the day they run.
