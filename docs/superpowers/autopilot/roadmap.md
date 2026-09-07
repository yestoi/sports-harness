# Roadmap — sportsbook harness autopilot

**End goal (spec §1):** a self-hosted system whose product for the first three weeks is
a dataset and, if the data supports it, a paper-validated straight-bet strategy on
CFTC-regulated exchanges; live trading only after an explicit gate and the user's
separate legal decision.

Spec: `docs/superpowers/specs/2026-09-06-sportsbook-harness-design.md` (v2) plus the
phase addenda under `docs/superpowers/specs/`. Where an addendum and the v2 spec differ,
the addendum wins for its phase and lists every difference in its §0.

## Phases (spec §15)

| Phase | Status | Plan | Gate before execution |
|---|---|---|---|
| 0 Recorder | done 2026-09-06, deployed | `docs/superpowers/plans/2026-09-06-phase0-recorder.md` | — |
| 1 Normalize and match | done 2026-09-06, deployed | `docs/superpowers/plans/2026-09-06-phase1-normalize-match.md` | — |
| 2 Pricing and signals | done 2026-09-07, deployed (hotfix `3224d0a`) | `docs/superpowers/plans/2026-09-07-phase2-pricing-signals.md` | — |
| 3 Paper execution, settlement, benchmarks, CLV | **planned** | `docs/superpowers/plans/2026-09-07-phase3-paper-execution.md` | none |
| 4 Kalshi authenticated adapter (still paper), risk gate, backups, Alembic | not planned | — (plan-next) | none; the demo smoke runs only when `secrets/kalshi_demo_*` exist |
| 5 Research layer and hypotheses: futures snapshots, NWS, parlay CLI, shadow veto, report annotator, RFQ listener, overview page | not planned | — (plan-next) | none; veto and annotator run only when `secrets/anthropic_api_key` exists |
| 6 Deferred items from the phase 2–3 reviews | not planned | — (plan-next) | none |
| Operator mode | after phase 6, and calendar duties throughout | — | — |
| Go-live gate | — | — | user's legal decision + a stored passing gate report; never autonomous |

## Standing authorizations (user, 2026-09-07; second round ~07:40 CT)

| Action | Authorized |
|---|---|
| Fast-forward merge to `main` after a pristine full suite | **yes** |
| `make deploy-nas` (restarts the NAS containers) | **yes** |
| Exercise the kill switch during verification | **no** — observe the badge only |
| Brainstorm, plan, and execute phases 4, 5, 6 without waiting | **yes** — decisions from the tables below or the model's judgment, each recorded in the addendum's "Decisions taken on the user's behalf" |
| Operator mode after the last phase (weekly report, alias passes, post-game verification, daily watch, hotfixes) | **yes** |
| Anything touching live trading, bankroll, the legal decision, real money, or destructive NAS actions | **never** — gate |

Mid-phase deploys that a committed plan instructs are covered by the deploy authorization.

## Secrets (provision when convenient; the loop never blocks on them)

Same handling as the existing files: `secrets/`, mode 600, no trailing newline
(`printf '%s' "<value>" > secrets/<name>`), never pasted in chat or committed. Each
feature is coded and tested against recorded shapes; its live path switches on when the
file exists at deploy time (the Kalshi WebSocket recorder pattern).

| File | For | How |
|---|---|---|
| `secrets/kalshi_demo_key_id`, `secrets/kalshi_demo_private_key.pem` | phase 4 demo smoke (`harness kalshi-smoke --env demo`): place, amend, cancel, group cancel, expiry, fills, positions, balance on play money | separate demo account at Kalshi's demo environment → settings → API keys; download the PEM once |
| `secrets/anthropic_api_key` | phase 5 shadow veto and the five report bullets | Anthropic console → API keys |
| `secrets/backup_age_key` | phase 4 encrypted backups — generated **by the loop** on the Mac; copy it somewhere safe (a backup no one can decrypt is not a backup) | nothing to do until the loop tells you it exists |
| Novig credentials | **dropped** (user 2026-09-07: not usable in Louisiana) | — |

No key is needed for NWS forecasts, futures snapshots, the parlay CLI, or the RFQ
listener (production key; it idles on 403).

A Claude subscription OAuth token (`claude setup-token`, `CLAUDE_CODE_OAUTH_TOKEN`)
authenticates only Claude Code and its wrappers (the CLI in `-p` mode, the Agent SDK,
GitHub Actions). It is not a credential for the harness's own API client, so the research
layer needs a Console API key with pay-as-you-go billing (verified against the Claude Code
authentication and Agent SDK docs on 2026-09-07). Do not route the per-candidate veto
through `claude -p`: it would draw on the same subscription rate limits the autopilot runs on.

## Pre-loaded decisions

The brainstorm for each phase treats these as the user's answers. Anything not listed is
the model's call, written into the addendum with rationale, cost if wrong, and how to
reverse it.

### Phase 4 — Kalshi authenticated adapter (paper), risk gate, backups, Alembic (spec §15.4, §5.2, §9.1–9.4, §14)

1. `Settings.kalshi_env ∈ {demo, prod}` with per-environment secret files; demo host and
   any demo-specific behaviour verified against current Kalshi docs (ctx7) at plan time.
2. `KalshiAuthed` implements the §5.2 protocol on top of the existing RSA-PSS signing:
   `post_only=true`, `order_group_id` on every order, `expiration_time`,
   `cancel_order_on_pause=true`, `self_trade_prevention_type=maker`; encode/decode
   round-trip tests for YES and NO; fee model read from `GET /series` and asserted.
3. Live guard: constructing the adapter in `prod` with write methods enabled requires all
   of `LIVE_TRADING=1`, `mode: live`, a stored passing gate report, and
   `secrets/legal_decision` (the user's dated statement). None exists; tests assert the
   refusal. Nothing in phase 4 sends an order to production.
4. Demo smoke: `harness kalshi-smoke --env demo` (tiny post-only order far from the
   market → amend → cancel → group cancel → expiry → read fills/positions/balance) is a
   runbook step; the verify unit runs it itself when the demo secrets exist (play money).
5. Startup reconciliation, 60 messages/min budget, three-reject freeze (15 min), echo
   check, `venue_status` and the outage rules: per spec, unchanged.
6. Drawdown stop on equity (−20 % over 7 days): paper equity = paper bankroll + ledger;
   paper mode raises a dashboard alert and labels signals; live mode trips the kill switch.
7. Backups: nightly 03:30 CT dump of every table except the five bulk tables
   (`raw_responses`, `orderbook_events`, `venue_trades`, `venue_quotes`,
   `odds_snapshots`); weekly full dump Sunday 04:00 CT; encrypted with `age` to the
   committed public key `deploy/backup_age.pub`; keypair generated once on the Mac into
   `secrets/backup_age_key` (never pushed); written to
   `/volume1/docker/sports-harness/backups/`; retention 30 nightly, 8 weekly; `ledger` and
   `gate_reports` CSV exports kept forever. Mechanism (a compose job on the postgres image
   vs. an in-app job) decided at plan time; no secret ever enters a dump path.
8. Alembic baseline generated from the current models; `init-db` becomes
   `create_all` for empty databases plus `alembic upgrade head`; the NAS database is
   stamped at the baseline during the phase 4 deploy.

### Phase 5 — research layer and hypotheses (spec §7, §8, §15.5; Novig removed)

Order, each independent: (a) futures and ladder weekly snapshots for H7, Tuesdays
09:00 CT, discovered by Kalshi football series prefix, raw plus normalized; (b) NWS
forecast snapshots for H6: a stadium YAML (NFL and FBS, lat/lon, roof) built by the model
from public sources, `api.weather.gov` gridpoint forecasts for outdoor games inside 72 h,
hourly, User-Agent `sports-harness/1 (self-hosted research harness)`; (c) parlay CLI per
§8.1 with `parlay.yaml`: weekly budget **$50** (user), smart card $25, lottery card $5, at
most three lottery cards, legs from moneyline/spread/total at DraftKings prices already in
the feed, LSU or Saints anchor, rationale from a template unless the Anthropic key exists;
(d) shadow veto per §7.1 — **week 1 on `claude-opus-5`** at default effort with adaptive thinking, structured output (decision ∈ {proceed, reduce, veto}, confidence, reason, evidence ids), web search capped at three uses per call, the stable system prompt cached; a **paired `claude-sonnet-5` shadow** runs on the identical frozen prompt for every call and is recorded, never used. The harness precomputes the numeric features (line moves, disagreement, staleness, time to kickoff) and passes explicit timestamps; the prompt defaults to `proceed` and requires quoted evidence ids for any `reduce` or `veto`. `research_notes` stores, per call and per model: the frozen inputs, every retrieved snippet with URL and timestamp, tool calls, the output JSON, usage tokens, cost, latency, request id, and the joins to the candidate's later CLV and markouts. 30-minute cache, veto-rate alert, dormant without the key. The model swap is decided after week 1 by the study in the operator calendar, never by cost alone;
(e) weekly report annotator, `claude-opus-5`, five bullets that cite table cells only,
dormant without the key; (f) RFQ listener per §8.2 on the production key, paper quotes
only, idles with a `venue_status` note on 403; (g) overview page: dashboard page 2,
server-rendered SVG, no JS, bounded queries (equity, CLV by week, fills).

Variants: when the veto ships, register `no_veto` as a secondary with a dated
pre-registration amendment (spec §6.7 makes it mandatory; the shadow veto never changes
decisions, so `no_veto` equals the primary until enforcement).

Novig: no adapter, no credentials, no live path (user, 2026-09-07). The `novig` bookmaker
column from the Odds API stays as a read-only benchmark feed already being recorded; H8
is measured from that feed or reported "not collected".

### Phase 6 — deferred items (from the phase 2 and 3 reviews)

1. NO-side signals: evaluate buying NO (selling YES) symmetrically; executor places NO
   bids; pre-registration amendment.
2. Key-number adjustment at 3 and 7 in the NFL margin model: point masses from published
   margin frequencies, sources cited in the addendum; CFB unchanged unless the data says
   otherwise.
3. Edge priced at the order's actual contract count instead of the 100-contract fee
   reference.
4. Duplicate quote rows per market per run: deterministic latest-`fetched_at` pick plus
   a run note.
5. Taker-imbalance label from the last five minutes of prints by size bucket.
6. Dedicated normalizer process only if `budget_exhausted` appears on ≥ 10 % of game-day
   ticks (measure first).
7. I9 bare-city aliases and the alias-pass automation (also an operator duty).
8. Orderbook compaction only if the database passes 800 GB.

## Operator calendar (America/Chicago)

| When | Duty |
|---|---|
| Monday 09:00 | `harness report --week N` and `harness gate` on the NAS; commit `docs/reports/2026-wNN.md`; one-line push |
| Monday 09:30, and the morning after a Thursday or Friday game | alias pass: `harness match-report` on the NAS → additions to `harness/matching/aliases_manual.yaml` on a `fix-aliases-<date>` branch (implementer + reviewer) → merge → deploy → confirm the match rate rose |
| Morning after every game day | verify unit (full contract) → hotfix loop |
| Daily 09:00 | credits remaining, database size vs budget, executor heartbeat, error lines, kill-switch state → one journal line; anomalies → carried fixes |
| Tuesday 09:30 (once phase 5a ships) | confirm the futures snapshot job ran |
| Seven days after the veto goes live | veto model study on the frozen week-1 cases (stored inputs and snippets, no live search): Opus 5 at `medium` and `low`, Sonnet 5 at `high` and `medium`, three to five reps each; programmatic checks (valid JSON, every claim cites a stored snippet, veto rate in band, trap cases), a pairwise blind judge on `claude-fable-5-1` against the frozen Opus outputs, and the outcome table (CLV and 30-minute markout by decision, with CIs, reported not gated); present the score-versus-cost table and the disagreement cases for the user's spot-check; **the swap is the user's call** |
| Mid-October (user) | go-live gate review with the legal decision — the loop prepares the gate report and the numbers, never the decision |

## User-side TODOs

- Provision the secrets above when convenient.
- Odds API tier decision (100k credits/month by choice; ~1,000/day observed).
- The legal decision before any live trading.
- Copy `secrets/backup_age_key` somewhere safe once the loop creates it.

## Carried fixes

(none)
