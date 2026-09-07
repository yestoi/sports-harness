# Phase 2 pre-registration record

Spec §6.7 requires the primary and at most five secondary strategy variants to be registered in `strategy_variants` before the Week 2 kickoff (Friday 2026-09-12, 00:00 UTC). This record freezes the six variant ids. Each id is the hash of the frozen YAML in `harness/variants/`; changing any field produces a new id, so a later edit cannot be passed off as the pre-registered variant.

## Registered variants

| name | tier | variant_id | what it tests (delta from the primary) |
|---|---|---|---|
| sharp_direct | primary | f259ca109084 | Sharp-consensus (Pinnacle 0.65 / BOL 0.35) direct fair values only; all filters as labels; quarter-Kelly sizing on a $3,000 paper bankroll |
| sharp_plus_derived | secondary | 49af716f8708 | H4: adds margin-model derived fair values for ladder rungs without an exact sharp line |
| no_velocity | secondary | 64ba3ef09642 | Mandatory no-veto control: sharp-line velocity filter disabled (`velocity_max_pts: 1.0`) |
| wide_band | secondary | c2bc45377328 | Price band widened from 0.20–0.80 to 0.15–0.85 to trade the tails |
| nfl_only | secondary | e549e693e117 | Scope control: NFL only |
| constrained | secondary | ff363c8ac08d | Same as primary but bankroll caps enforced (`apply_caps: true`) for sizing realism |

Every other hypothesis is evaluated after the fact with `harness replay --file`, which registers a `replay`-tier variant and never touches the live rows.

## Provenance

- Source commit of the YAML files: 8d5feea (main, 2026-09-07; YAML files unchanged since 225976f)
- Registered on the NAS: 2026-09-07 ~04:00 UTC, `make deploy-nas` output (main @ 8d5feea):

```
name                    tier        variant_id    active
constrained             secondary   ff363c8ac08d  True
nfl_only                secondary   e549e693e117  True
no_velocity             secondary   64ba3ef09642  True
sharp_direct            primary     f259ca109084  True
sharp_plus_derived      secondary   49af716f8708  True
wide_band               secondary   c2bc45377328  True
```

- Ids first computed on the dev database at commit acecbfa on 2026-09-07; the NAS table must match exactly.

## Reporting rules (from spec §6.7)

Weeks 1–2 explore; week 3 confirms only what was selected in week 2. Benjamini–Hochberg at 10% across cells, empirical-Bayes shrinkage of cell means toward the grand mean, cells with n < 30 greyed.

## Amendment 2026-09-07 (measurement fix, variant ids unchanged)

The first hours of live pricing produced zero candidates: every direct fair value was labelled
`not_stale = false` with staleness 138-353 s while the books were a median 12 s old at fetch.
Cause: the tick priced with its start-time clock, and the book-line loader's `fetched_at <= now`
bound excluded the odds fetched seconds later in the same tick, so pricing used the previous
fetch (2-5 min old). Fixed on main the same day by reading the clock at pricing time. No variant
config changed, so the six ids above stand; signals before the fix carry the wrong label and
must be excluded from Week 1 reporting (or re-scored with `harness replay`).
