# Adversarial review and disposition

Date: 2026-09-12. Two separate reviewer agents challenged the provisional [investigation](2026-09-12-cerebras-autopilot-investigation.md): one on performance/integration, one on quality/evaluation. Both inspected local evidence; the performance reviewer also checked official vendor documentation. Neither ran inference, tests or operational commands. Their reports are independent critiques, not empirical validation of Cerebras quality or throughput.

Preserved reports:

- [Performance adversary](2026-09-12-cerebras-autopilot-evidence/adversarial-performance.md): seven findings, three High and four Medium.
- [Quality adversary](2026-09-12-cerebras-autopilot-evidence/adversarial-quality.md): six findings, five High and one Medium.

Their line references identify the provisional draft or inspected historical files; the final investigation has moved as findings were incorporated. These are research-design findings, not newly discovered production defects.

| Challenge | Disposition in the revised recommendation |
|---|---|
| Native Claude versus an external runner confounds runtime and model | Accepted. Call it worker-stack substitution; use a same-runner Claude arm only for model attribution. |
| Old worktrees expose future Git history; this Astra session already knows later failures | Accepted. Sanitize repositories and planner context; label hindsight-assisted calibration; prefer fresh prospective tasks. |
| Existing shared environment and DB-creation role are not isolation | Accepted. Small first batch avoids Postgres; later DB work needs trusted provisioning and scoped access. Include isolation cost. |
| Planner, worker, tests and reviewer can share one wrong premise | Accepted. Assess contract validity separately from compliance, using independently derived hidden cases and actual consumers. |
| Reviewer fixes can conceal worker defects; successful-only timing hides failed routes | Accepted. Score the pre-repair candidate, record all reviewer edits, and count every dispatch/repair/escalation. |
| Tiny task classes may be misrouted; eligible work may cover little of the critical path | Accepted. Positive admission evidence, two offline routing traps, eligible-work coverage and milestone lead-time metrics. |
| Controller rulings previously exceeded ceilings or relaxed gates | Accepted. Enforce limits and transitions outside the controller; fence stale attempts and bind evidence to an immutable candidate/environment. |
| Fast generation can still hit prompt throughput or review bottlenecks | Accepted. Include quota arithmetic, throttle/queue telemetry and review backlog limits. |
| Four arms × 12–20 tasks plus infrastructure is too large for the first check | Accepted. Six paired offline tasks against one Cerebras candidate first; second candidate and larger evaluation only if warranted. |
| Setup and maintenance can consume the savings | Accepted. Add break-even criterion and three historical task timelines; defer a durable dispatcher/MCP service. |

The reviewers' strongest contrary case survives: after sensitive work, tests, strong review and operational waits are excluded, the remaining eligible inference may be too small a share of the critical path to justify the new stack. The files do not settle that question.

Final decision: pursue a limited feasibility experiment, starting with GPT OSS as the public text-worker candidate, while keeping Fable 5.1 as controller and current review allocations. Do not conclude that most work can move, that quality is equivalent, or that a specific project-wide speedup is achievable. Those are measured promotion criteria for future work, not outcomes of this investigation.

The quality reviewer reread the revised recommendation and found no material unresolved contradiction after separating the first screen's scoped-validation metric from complete accepted-change timing. Two sequencing clarifications were also incorporated. This closes the document critique; it does not validate the unrun experiment.

No current loop, credentials, routing, spend cap, deployment, or application code was changed. The proposed experiment has not been run.
