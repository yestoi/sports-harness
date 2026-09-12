# Evidence for the Cerebras/autopilot investigation

The [investigation](../2026-09-12-cerebras-autopilot-investigation.md) contains the synthesis and vendor-source links. The [adversarial disposition](../2026-09-12-cerebras-autopilot-adversarial-review.md) records how two reviewers changed the recommendation.

`session-summary.json` contains aggregate metadata, not copied prompts or tool outputs. `audit_sessions.py` reads only the named Claude project directory and writes the specified JSON file. It does not invoke models, alter sessions or contact the running application.

Reproduce with the same timestamp cutoff, writing outside the repository:

```sh
python3 docs/superpowers/reviews/2026-09-12-cerebras-autopilot-evidence/audit_sessions.py \
  /Users/trey/.claude/projects/-Users-trey-dev-sports \
  --before 2026-09-12T08:49:21.057224Z \
  --output /tmp/sports-cerebras-session-summary.json
```

Method: enumerate root and subagent JSONL logs; cap each read at the file's size when enumeration began; ignore records later than the cutoff; deduplicate assistant records by message ID, retaining the largest observed input-plus-output usage; deduplicate Agent calls by tool-call ID. Input context includes uncached, cache-read and cache-created input. The 90th percentile uses the lower nearest order statistic. Synthetic records are retained as their own group and omitted from the report's model table. Source sizes and collection time may differ on rerun because a current Claude session is still appending.

Limits: counts include research, setup, monitoring and resumed sessions as well as implementation. They are not a count of completed tasks. Request-weighted context percentiles emphasize longer workers. Token counts do not separate inference, tools, queueing or review time. Most data precedes the newly adopted context-recovery procedure. Local historical findings are reported as dated records, not a fresh NAS audit. No Cerebras quality or speed benchmark was performed.

The two `adversarial-*.md` files preserve reviewers' original critiques of the provisional proposal. Their recommendations were adjudicated in the disposition document; they do not authorize operational actions.
