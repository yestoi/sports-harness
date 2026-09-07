# Roadmap — sportsbook harness autopilot

**End goal (spec §1):** a self-hosted system whose product for the first three weeks is
a dataset and, if the data supports it, a paper-validated straight-bet strategy on
CFTC-regulated exchanges; live trading only after an explicit gate and the user's
separate legal decision.

Spec: `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2) plus the
phase addenda under `docs/superpowers/specs/`.

## Phases (spec §15)

| Phase | Status | Plan | Gate before execution |
|---|---|---|---|
| 0 Recorder | done 2026-09-06, deployed | `docs/superpowers/plans/2026-09-06-phase0-recorder.md` | — |
| 1 Normalize and match | done 2026-09-06, deployed | `docs/superpowers/plans/2026-09-06-phase1-normalize-match.md` | — |
| 2 Pricing and signals | done 2026-09-07, deployed (hotfix `3224d0a`) | `docs/superpowers/plans/2026-09-07-phase2-pricing-signals.md` | — |
| 3 Paper execution, settlement, benchmarks, CLV | **planned** | `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md` | none |
| 4 Kalshi authenticated adapter (still paper) | not planned | — | user: Kalshi demo API key; user: brainstorm the addendum (drafting not authorized) |
| 5 Shadow veto, RFQ listener, futures snapshots, parlay CLI, NWS, Novig adapter, overview chart | not planned | — | user: Anthropic API key, Novig credentials, Kalshi RFQ access; drafting not authorized |
| Go-live gate | — | — | user's legal decision + a stored passing gate report; never autonomous |

## Standing authorizations (user, 2026-09-07 00:45 CT)

| Action | Authorized |
|---|---|
| Fast-forward merge to `main` after a pristine full suite | **yes** |
| `make deploy-nas` (restarts the NAS containers) | **yes** |
| Exercise the kill switch during verification | **no** — observe the badge only |
| Draft the phase 4 design addendum and plan | **no** — after phase 3 is verified: report and stop |
| Continue into credential-free phase 5 items | **no** |

Mid-phase deploys that a committed plan explicitly instructs (phase 3 Task 1 Step 5) are
covered by the deploy authorization.

## Deferred items (recorded in earlier reviews; need the user's OK to schedule)

- NO-side signals (only YES bids are evaluated).
- Key-number mass adjustment at 3 and 7 in the margin model (spec §6.3 vs code).
- 100-contract fee basis for gap metrics (per-contract fee overstated on small orders).
- Duplicate quote rows per market per run are dropped arbitrarily.
- Taker-imbalance and shadow-veto labels (need the trade tape and the Claude client).
- Dedicated normalizer process (currently inside the tick).
- Orderbook compaction / partitioning — only if the 1 TB budget line is crossed (addendum §0.6).
- I9 bare-city aliases; RFQ listener (phase 5).

## User-side TODOs (not the loop's)

- Week 1 alias pass after the Sept 13–14 games (`docs/runbooks/phase1-match-report.md`).
- Request Novig API credentials; create a Kalshi demo API key for phase 4.
- Odds API tier decision (100k credits/month by choice; ~1,000/day observed).
- The legal decision before any live trading.

## Carried fixes

(none)
