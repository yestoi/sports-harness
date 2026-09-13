# Independent pilot contract review

Reviewed by the independent workflow-review agent on 2026-09-13. Scope: all six packets, starters, public smoke checks, the two routing packets, eval_manifest.json and hidden_evaluator.py. No candidate inference, project tests, credential reads or repository edits were performed.

Disposition: suitable for a limited synthetic worker feasibility screen. The two requested pre-freeze improvements below are resolved and verified. This review approves the task contracts, not the runner's isolation, model access or provider configuration.

## Contract findings

No blocking contradiction between the six task contracts and hidden expected values was found. Duration arithmetic (183845006 ms), UTF-8 suffix behavior, ready-job ordering and the large-integer half-up average were independently derived. Error cases are described in the packets rather than introduced as undisclosed requirements. Starter code contains signatures only; public smoke cases disclose examples without supplying a reference solution.

Two pre-freeze improvements were requested from the packet author and implemented:

- Clarify TSV header escape notation as literal tab/newline output; the original packet displayed doubled escape notation.
- Require an actual integer duration result in hidden validation. Equality alone admits equal-valued floats even though the packet requires an integer. Representative zero, compound and maximum-boundary cases suffice.

The UTF-8 streaming and atomic-file contracts deliberately require independent source review in addition to passing output checks. The evaluator does not establish bounded retained memory, same-directory unique temporary-file creation, flushing/closing, atomic replacement or cleanup exception preservation by itself. Count these reviews in worker-stack comparison time.

## Frozen baseline and exposure

After the clarifications, freeze hashes for packet.md, starter.py, smoke.py, hidden_evaluator.py and eval_manifest.json before either worker sees a task. Preserve the hash manifest outside worker mounts. Expose only the selected packet/starter/smoke triple, with solution.py as the candidate artifact. Keep hidden checks, this review, other candidates, prior solutions and result summaries outside implementation mounts. Hash candidates before independent review and hidden validation; record every repair as a new candidate.

The evaluator imports candidate Python code and is not a sandbox. The trusted runner must execute it in the disposable no-network/no-credential execution environment and bound runtime/output. Import errors, timeouts and truncated output must count as failures or incomplete attempts, not disappear from totals.

## Interpretation limits

These six tasks can reveal basic adapter/tool-use, explicit-contract following, validation and repair costs. They are fresh synthetic exercises with complete contracts, no source discovery, no multi-file consumers, no existing project tests and no PostgreSQL workload. Passing them cannot establish real milestone coverage, scientific correctness, release acceptance speed or equal quality. The toy receipt and job-order helpers must not be promoted into production authority merely because their tests pass.

Both routing challenges prominently state their forbidden boundary and required ESCALATE behavior. They are useful elementary instruction-compliance checks, but weaker than deceptively small sensitive requests in realistic mixed context. A pass provides no strong evidence of general task-admission reliability. Score them separately from the six implementation tasks, verify unchanged files and inspect tool activity as the manifest requires.

Keep the same frozen packets, acceptance checks and independent review policy across both worker stacks. Report all attempts, repair/fallback effort and scoped validation time. Subsequent prospective bounded repository tasks are needed before enabling unattended Cerebras routing.
