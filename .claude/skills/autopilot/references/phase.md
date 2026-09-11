## Unit: phase

Branch `phaseN-<slug>` from `main` in the main checkout; task branches `phaseN-t<k>-<slug>` from it, one worktree each.
If the SDD workspace is missing, the SDD skill creates it from the committed plan and runs its pre-flight scan.

1. Journal a `phase start` entry (plan path, base commit, expected task count, the wave map). Ledger the standing rulings
   once: the plan's deploy steps run from `main` (step 6, R15); the trailer ruling in step 4.
1a. **Wave map.** From the plan's `Files:` and `Depends on:` lines, list the tasks in waves: a task is ready when every
    task it depends on has merged into the phase branch, and it joins the current wave when its Files are disjoint from
    every task already running. Tasks sharing a file wait, in plan order. Re-derive the map after every merge.
2. **REQUIRED SUB-SKILL:** `superpowers:subagent-driven-development` on the committed plan, with every ready task dispatched
   at once (Parallel work). Its stop conditions map to the roadmap: a merge is pre-authorized; an irreversible or
   destructive operation, a security-sensitive action, or a plan so broken that every path is a guess is a gate.
   Everything else is a ledger ruling: `Ruling: <decision> - <why> - <cost if wrong>`.
2a. **Resume**: first follow [recovery.md](recovery.md). Compaction does not imply a dead worker.
    Reconcile live agents and pending results before re-dispatching anything. For an unreachable agent, the ledger's
    last line per task and the actual branch/worktree decide: committed work without review is packaged for review;
    uncommitted work is retained and handed to a fresh implementer with instructions to inspect and keep what passes.
    A single commit may implement multiple findings: review that diff once against all its finding IDs.
    Never infer completion from a worker's DONE message without consuming its report and checking the commit/evidence.
3. Model allocation. Always pass `model` explicitly; every dispatch carries the containment paragraph.
   - implementer `sonnet`; `haiku` when the brief contains the complete code; `opus` only for tasks the plan or the
     plan-next entry marks judgment-heavy (phase 3: Tasks 5, 6, 10, 13 unless the plan says otherwise).
   - task reviewer `opus` for anything under `harness/recorder/`, `harness/execution/`, `harness/pricing/`,
     `harness/settlement/`, `harness/venues/`; `sonnet` elsewhere. The reviewer fixes Minors itself in the task's worktree
     (comment, name, assertion, docstring: no behaviour change), commits them with the trailers, lists each with its sha,
     and runs `make test`; those need no re-review. Importants and Criticals go back to the implementer (SendMessage).
   - scoped re-review only after an Important or Critical fix round: `haiku` for a diff under 60 lines, else `sonnet`.
     Fix rounds 1-3 resume the same implementer; rounds 4-5 a fresh implementer one tier up; final whole-branch review
     `opus`; fix wave `sonnet` (`opus` if a finding is architectural); its re-review `sonnet`.
   - NEEDS_CONTEXT from the same task twice: the brief is defective; rule, rewrite it, ledger, re-dispatch. Three times: gate.
     A Critical at the task breaker is never parked: rule on the smallest unblocking change, or gate.
4. Commit trailers on every commit use **this** session's values from the harness instructions (`Co-Authored-By` plus
   `Claude-Session`); a plan that hard-codes an older session id is stale on that point.
5. Full suite before every merge and deploy: `make test` on the branch being merged (its own database), pristine
   output (no warnings, no tracebacks). `make test` on `main` after the merge, before the deploy.
6. A plan step that says "controller: deploy this task now" is honoured mid-phase, but the NAS only ever runs `main` (R15):
   after the task's review is clean, `git checkout main && git merge --ff-only phaseN-<slug> && git checkout phaseN-<slug>`,
   then the deploy unit from `main`, verify per verify.md's task-specific rows, journal, continue the branch. A plan whose last
   task contains `make deploy-nas` runs it as the phase's deploy unit after the merge in step 8, never on the branch.
7. When the final review is clean (or residuals handled per 7a): load [plan-next.md](plan-next.md) and run its 3a audit on the branch; archive the ledger, final
   review and fix report as `docs/superpowers/reviews/<date>-phaseN-{sdd-ledger,final-review,final-fixes}.md`; commit
   `docs: archive phase N ...` on the branch. The archived ledger on `main` is the durable proof of completion (Orient rule 0).
   A U8 deadline-slice checkpoint retains the active milestone ledger and lists outstanding tasks/acceptance instead;
   archive phase completion only after the entire milestone is accepted. Keep those remaining tasks in the phase plan.
7a. Final-review residuals: a Critical that survives the first fix wave is never parked: one more wave (fresh `opus`
    implementer, the Criticals only, one scoped re-review), then a still-open Critical is a gate. An Important may be parked
    only as a carried fix with a hotfix unit right after this phase's deploy, before any plan-next. Minors are ledgered.
8. Merge: full suite, then `git checkout main && git merge --ff-only phaseN-<slug>` (**REQUIRED SUB-SKILL**
   `superpowers:finishing-a-development-branch`, pre-answered: merge locally, no PR, no `git pull`, no remote). In the same
   commit on `main`: roadmap status `done` and the `phase done` journal entry (commit range, test count, the exhaustive
   rulings roll-up). Only then `git branch -d` (a missing branch is not an error), remove the worktrees, and `rm -rf` the SDD workspace.
   U8's partial milestone delivery uses step 6's mid-phase merge/deploy path; it does not mark the milestone done or
   delete its branch/ledger. In particular, the 6C deadline slice cannot trigger this completion step by itself.
9. Continue to the deploy unit (Orient rule 2 selects it too), then verify, the phase report, and the repo bundle (Unit: operate).
