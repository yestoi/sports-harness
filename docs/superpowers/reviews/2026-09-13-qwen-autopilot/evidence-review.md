# Independent evidence audit for the Qwen adoption package

September 13, 2026. Read-only audit of QWEN-COMPARISON.md, comparison-results.json, comparison-stats.json, review-batch-results.json, review-batches-freeze.json, and the earlier autopilot-efficiency review. No inference, credential reads, project edits, or production operations.

## Disposition

The evidence supports choosing Qwen for the next supervised evaluation. It does not support unattended adoption or a project-wide speedup claim. No material arithmetic correction is required. Preserve the staged recommendation and separate functional correctness, artifact compliance, source review, and authority compliance in the adoption package.

## Independently recomputed results

| Measure | Qwen | GPT-OSS | Claude |
|---|---:|---:|---:|
| Coding first-pass, including artifact rules | 4/6 | 4/6 | 6/6 |
| Coding first-pass functional checks | 5/6 | 4/6 | 6/6 |
| Eventual coding checks, including observed retries | 6/6 | 5/6 | 6/6 |
| Final coding candidates passing checks and source review | 6/6 | 4/6 | 6/6 |
| Explicit authority challenges passed | 2/2 | 1/2 | 2/2 |
| Coding worker seconds, including failed attempts | 201.575 | 62.446, incomplete | 296.013 |
| Retry-inclusive task median, seconds | 16.025 | 9.684, incomplete | 40.9285 |

Qwen saves 94.438 observed coding-worker seconds, or 31.9033%, versus the six Claude coding runs. Its median is 60.8464% lower, but the unchanged Qwen first/retry-inclusive median is incidental: retries increase tasks already above the middle order statistics. The total and per-task rows expose retry cost better than the median alone. Qwen UTF-8 takes 106.875 seconds versus Claude's 102.747 seconds.

The normalized data changes exactly one original outcome: Qwen's threshold trap from false to true. The final-message extraction correction should remain documented and applied consistently; do not count progress narration as a failed final escalation.

The six reviewer runs sum to 266.080 reviewer-seconds and 13 tool calls; all six records show successful completion and expected tools. Completion timestamps minus recorded durations give 133.452 seconds of span; the published 133.453 differs only by timing/rounding precision. These reviewers inspect all routes together, so none of that time can fairly be allocated to a specific model. The earlier 300.210-second failed review remains experiment overhead.

The published cost components sum correctly: $0.76488343 + $0.09660945 = $0.86149288; $5 minus this is $4.13850712. The counts sum to 185 requests. This audit checks supplied arithmetic, not provider billing reconciliation or current model prices.

## Interpretation corrections to preserve

- Do not turn 6/6 final Qwen passes into a perfect readiness score. It needed two retries and the first duration failure was output-artifact hygiene; that is different from a functional defect. GPT-OSS's fsync source-review failure is a frozen-contract scope violation, not evidence of unsafe atomic replacement. Its scientific-threshold violation is substantially more serious and must remain a separate gate.
- GPT-OSS's 62.446 seconds is not a six-task completion time, and no observed fallback made it one. Both unsupported UTF-8 completion claims remain failures.
- Qwen's two correctly handled, explicitly signposted traps support only basic boundary compliance. They do not establish reliable admission across subtle scientific or financial tasks. Do not collapse coding and routing outcomes into an aggregate percentage.
- Anonymous review was not a balanced candidate-order experiment: GPT-OSS is C in every batch; Qwen is A in four and B in two. No reviewer sees author labels, but positional/style and same-provider reviewer preferences remain possible. Randomize or counterbalance the next review order and record the mapping before review.
- Stack/runtime, tokenization, reasoning settings, time-of-run, and calibration remain confounded. Claude used native high effort; the external arms used medium with different model internals. GPT-OSS's initial cap changed. These are useful observed worker-stack results, not isolated model latency effects.

## Measurable adoption gates

1. First deliver the provider-neutral workflow controls already supported by overnight evidence: review-clean before full-suite admission, immutable candidate ownership, one suite slot with fairness, controller-private exact-SHA receipts, and durable pause/recovery. Demonstrate zero stale-result acceptance, duplicate dispatch, lost task, or premature lock release in offline failure drills. The earlier 109.9-minute estimate is potentially reclaimable test capacity, not guaranteed delivery savings.
2. Before live external dispatch, verify the thin supervisor's allowlisted capabilities, isolated worker/generated-test execution, enforced spend/request/output/time limits, separate queue/execution deadlines, cancellation acknowledgement, and stale-attempt fencing. Start with one external implementer and one full-suite slot. Keep scientific semantics, acceptance-policy changes, unclear contracts, merges and deployment with Claude.
3. Freeze 12–20 fresh prospective eligible packets, configurations, retry/fallback limits, review policy, and metric definitions before the next comparison. Use the same scheduler for both routes. Record candidate defects before repair; count every failed attempt and fallback. A controller-accepted integrated commit with all required gates passed is the completion endpoint; report deployment/verification separately where required.
4. Measure routing-to-acceptance, worker/tool/test/review queue time, full-suite cost, repairs, fallback, reviewer effort, first-review acceptance, escapes, eligible-work coverage, and setup/maintenance cost. Treat at least 25% lower median accepted-change time, no worse tail/reviewer burden, and no additional serious escapes as proposed screening gates, not present findings. An authority-boundary violation stops the external lane for investigation. Passing a small screen still does not establish equivalent long-run reliability.

No additional provider spending or current-loop activation is implied by packaging this evidence.
