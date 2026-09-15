# Transport calibration before interpretation

The initial Cerebras duration attempt ended with finish_reason=length at exactly 4096 completion tokens, including 2564 reasoning tokens. It emitted no candidate and is retained as an unsuccessful initial configuration attempt. Cost/time are not discarded. Claude's corresponding duration attempt passed all ten checks.

Before the next Cerebras task, the per-call completion cap was raised from 4096 to 16384; the budget broker reserves the larger bound before each call. The total authorized $5 ceiling is unchanged. No hidden failure details or solution were supplied to Cerebras. The original duration task will be retried fresh at the new configuration, with the original recorded beside it. Subsequent per-call receipts record the actual cap.

The initial screen-freeze records the original 4096 cap and original runner hash; this note is the explicit amendment, not an edit to that historical record. Evaluation packets and tests remain frozen. The result is an adaptively calibrated synthetic feasibility experiment, not a preregistered quality or performance study. Runtime/model stacks differ; Claude retains high effort and native output defaults, Cerebras medium reasoning and the explicit 16384 cap.
