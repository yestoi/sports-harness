# Omarchy app release and restart verification

Observed 2026-09-12 20:59–21:23 CT (2026-09-13 01:59–02:23Z).
**Release transport healthy; overall verification FAIL.** Summary agreement is not
research acceptance. This is one verification unit, not a sequence of new passes.
The controller retains one failed deployment acceptance in the CT-day counter because
fix48's positive-gap/candidate row failed after this release.

## Exact source and release

Main `93dfb95773f5649d582ff39832d94241f1a8cdf3` passed clean, unfiltered Omarchy
`make test`: **3209 passed, 6 expected xfails, 1775.15s**. Private receipt has empty
scope/filtering variables, identical before/after HEAD, no dirty files, exit0.
`main-full.log` SHA256 is
`fe71ee096b5a2afc02c03087400dce2f0bee56c7770b4b6fe128b512a92e3925`.
The independently reviewed ten-line Pulse wrapping repair is in this exact build.

`make deploy-omarchy-app` ran 20:59:31–21:05:30 CT
(01:59:31.803970–02:05:30.610154Z), previous build `b0a3991`.
Receipt: `/srv/sports-harness/releases/20260913T015931Z-93dfb95/receipt.json`.
Its archived copy is `app-release-93dfb95-receipt.json`. All four changed services
(app-run, app-serve, app-exec, app-research) have expected image
`sha256:e7e8428a27cee32c1ec21e1f9bfa6cbfec10777472a2af3e36c04fea41be0e23` and build93dfb95.
Read-only planning returned an empty full-trigger diff, college window active, no NFL
block; journal128's Saturday app-only exception applied, with in-recipe rechecks.
Backup23 was fresh and validated. Natural tick13135 completed on the new SHA, statusok,
02:02:59.762176–02:05:25.799360Z. No forced duplicate fetch was run.

The unchanged WS container is
`0d04dc4baeb3ffca8bbe927ae953a9848f620d4aa334c583b92b1ce420afb2be`;
PostgreSQL is `38fbd8d9904fa053167ec1da9468fb3d49e077a608d683094705ce3855769ef3`.
WS retains b0a3991. Backup container also unchanged. Schema remains
`0006_quotes_run_index`: the index fix has NOT been deployed.
Paper, LIVE0, RFQ0, SNAPSHOTS1, user1000:1000 and600GB budget remain.
No thresholds, definitions, eligibility, retention or provider cadence values changed.

## Deterministic observations

| Check | Evidence and disposition |
|---|---|
| Summary | 9/9 PASS: build, sections, latency, run13136 identity, WS age, candidates (all0), killfalse, numeric credits, data-quality shape. `post-summary.txt`. |
| Fresh integrity sweep | Existing deployed CHECKS, unchanged2s bound, additive job165 at02:06:25Z:23pass,3fail,1skip. Negative staleness/feed-lag PASS0; duplicate_trades SKIP timeout. FAIL late fills154, markout timing154, score decreases11. `fresh-check-sweep.json`. |
| Broad SQL contract | 92 independent read-only statements with unchanged2s bounds: one 2h tape-count timeout. Literal legacy intent39534 and build-history995 matches differ from accepted narrower CHECKS semantics. No ruler silently changed. `verify-contract-after.log`, `contract-after-parsed.json`. |
| Pricing order | Runs13135/13136 execute gate then primary, all seven variants and direct before derived; not exhausted. Run13135 directfair71, derived1032, no_sharp1378, gaps0 and candidates0. Stage-order repair works; end-to-end acceptance FAIL. |
| Upstream coverage | Current run13135 has zero venue_quotes. At02:11Z markets watermark391392 refers to run12900/raw fetched Sep12 14:05Z; newest raw markets504098/run13137 fetched Sep13 02:08Z. Later normalizer families get one old row after earlier families exhaust the shared deadline. About12h backlog, not demonstrated new API shape. No watermark reset/reprocess. Route to6D coverage/throughput and fix48 acceptance. `normalization-backlog-after.log`. |
| Settle47 | Existing authorized settle command, job166,02:08:18–02:09:42Z,84.286s,statusok/not exhausted,105markouts and numeric elapsed_s on all11stages. reportWTD not due. `post-settle.log`, `post-settle-and-release-checks.log`. |
| WTD due-case | Old-build job164 refreshed week37 report10 at01:10Z; report_wtd_last01:10:38Z means next due no earlier07:10:38Z (Sun02:10:38CT). Inspect next scheduled settle at/after due, not an early forced refresh. Original within6h-of-release row remains open. |
| Memory49 | Five new-build samples span121.8–1935.4MiB by02:13Z. More capacity has not established bounded retention. Original20ticks/<5% plus6h/500MiB acceptance OPEN. |
| Tape50 | Four post-release sequence_gap markers on sid2, each got=expected+2, no ws_connect/disconnect event in this release interval and unchanged WS ID. This is an anomaly, not proof of actual transport loss. QueryCanceled24h acceptance still OPEN. `ws-release-continuity.log`. |
| Weather52 | Cadence120 game-window ticks correctly skip. Quiet-hour zero-fetch and daytime successful-fetch/freshness evidence remain due. No cadence change. |
| Host6E | Runtime02:06Z:854.976GiB free (~89.8%),23273MiB available; sample si/so0, IOwait11–13%. Executor9samples2125–2472ms,p95~3097ms; recorder4ticks139914–146031ms. These small samples do not satisfy two corrected-workload windows. |
| Growth | Display62.3972GB/600GB (10.4%),10.663956GB/day,181.696days marked partial. The days projection is not arithmetically reconciled to600GB; do not treat it as accepted6E capacity evidence. |
| Gate | Existing paper `harness gate` appended3not-passing reports at02:17:06.106713Z, exactly one gate_variant. Hash5643698204d0e1882f9443fdc371e00351afa6697f13e1041a2e74c1deda53f5, eligibility key count0. Criteria unchanged; not a live authorization. `post-gate.log`, `post-gate-checks.log`. |

All affected research measurements remain under audit. The full index release, ordinary
scheduled reports,6h/24h/25h observations, cold start and corrected6B workload are follow-ups;
this setup run does not wait overnight or start a new correctness patch wave.

## Controller rescore of visual evidence

The actual bounded Opus controller dispatched one Sonnet sports-worker using only the
fixed shell/screenshot MCP. Archived advisory report `restart-visual-review-93dfb95.md`
judged the initial deployed captures:23PASS/3FAIL/3PENDING. It is retained unchanged.
The controller personally inspected Pulse's full desktop/operator-events image, Pulse
and Gate mobile tops, and fresh populated desktop Gate. Deterministic geometry and SQL
supersede advisory claims; not every advisory pixel judgment was independently repeated.

- Item17 is now FAIL: after a stored gate evaluation,390px Gate has764px body scroll width.
  The viewport/client width remains390; `body_width` and overflowing card bounds
  measure the off-screen content, rather than the separately recorded CSS width.
  All five desktop pages fit1440; Pulse/Floor/Study/Ticket fit390. Earlier all-five-mobile
  evidence covered an EMPTY Gate only. No source change occurred between captures.
- Item18 remains FAIL: BROKEN reflects tape_gap16, check_fail3, check_skipped1 and
  book_dirty_in_game37. Do not recolor or relax rules to make this pass.
- Item19 remains FAIL: Pulse visibly exposes two historical
  `WebSocketConnectionClosedException(Connection to remote host was lost.)` messages.
  Those are historical operator events; this does not establish a current app exception.
- Item23's missing-evaluation failure is resolved on desktop by the stored paper
  evaluation: NOT PASSING,2/12met, date/hash and all12stored definitions present.
  Mobile criteria remain clipped, so the combined-width item is not accepted.
- Items16/22/29 remain pending: zero skip-reason rows, no current-week final markdown,
  no final annotation fence. Week selection control was not clicked. Study full images
  stop at16000px, so pixels below that cutoff are unreviewed even though DOM text exists.
- Item4's seven active plus one replay row differs from literal old six-variant wording;
  the walker accepted rendering only. No variants or verification definition were changed.
- Item14's partial days-to-ceiling figure is unverified as above. Item11's empty order
  tables render; that does not pass productive-pricing acceptance.
- Item25 has actual keyboard evidence: Tab expands glossary; ArrowRight from a focused
  tab selects #floor at both widths. No forms, Kill/Unkill or tokens were used.
- Two annotations created this week can refer to earlier final reports; the worker's
  claim that this necessarily contradicts the current-week empty report is not adopted.

`restart-deployed-fresh-93dfb95-browser.json` uses a new about:blank navigation for EVERY
route after resizing, correcting the first capture utility's inherited SPA navigation
FCP values. Fresh FCP44–108ms for UI,708ms legacy, zero recorded JavaScript exceptions.
The first system-python capture failed before screenshots (missing websocket-client);
the existing project .venv worked, with no dependency change.

Screenshots and JSON are mirrored under `.superpowers/sdd/screenshots/` on both hosts;
raw logs/reports under `.superpowers/sdd/omarchy-restart-2026-09-12/`. Source CSS preview
and failed attempts remain separate. A final artifact manifest records hashes.
