## Unit: verify

Follow `verify.md` exactly. In brief:

1. Layer 1 freshness; Layer 2 local Omarchy checks by time of day with the "Game window" block; Layer 2 b invariants (every query
   returns 0) and plausibility bands. Save the raw query output to
   `docs/superpowers/autopilot/evidence/<date>-<unit>-<HHMM>-layer 2.txt`; the journal cites the path and states the verdicts.
   A deterministic check outranks any agent verdict; a FAIL from one never needs an agent's agreement.
2. Layer 3 deterministic: `make verify-summary-omarchy DEPLOY_SHA=<sha>` (build stamp, section errors, page time, run id, WebSocket
   age, candidates per variant, kill switch, credits) with `--evidence` to the same directory. Any FAIL is a dashboard FAIL.
3. Visual walkthrough when this is the day's first verify, the diff touches
   `harness/dashboard/`, or step 2 failed. On Omarchy the controller owns browser
   actions and captures the contract's screenshots into
   `/home/trey/dev/sports/.superpowers/sdd/screenshots/`. A `sonnet` sports-worker reviewer receives
   those exact paths and the walker checklist; the screenshot MCP tool returns
   these controller-owned PNG/JPEG images; code access uses the sandboxed shell tool. Do not assign Chrome
   tools to a sandboxed worker. The reviewer returns per-item PASS/FAIL/PENDING;
   interactions not demonstrated by the captures stay pending. The controller copies
   screenshots with `cp -n` into canonical evidence, re-scores every FAIL plus one PASS,
   and fills cross-checks. Browser tools absent or a missing image means pending visual
   evidence, never an invented pass. This Linux procedure supersedes the historical
   Chrome-only tool list; the underlying checklist and required evidence are unchanged.
4. Transients: an ERROR line on the contract's upstream-failure list with the next real tick `ok` is journaled as an anomaly,
   not carried, unless it recurs within 24 h. A non-zero invariant or an out-of-band quantity is an integrity anomaly:
   carried fix, and every number derived from that table is marked "under audit" in reports until it clears.
5. Journal a `verify` entry: `PASS n/m` with evidence paths, anomalies, and any item deferred by the time-of-day rules with
   its judge-after time. A FAIL adds a line to Carried fixes and the next unit is hotfix. Never silently pass a failed item.
6. When the roadmap's optional secrets exist (`test -e`), run the checks that depend on them (the demo smoke, the veto dry run).
