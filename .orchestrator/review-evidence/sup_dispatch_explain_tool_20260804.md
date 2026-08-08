# Task Brief: SUP-DISPATCH-EXPLAIN-TOOL-20260804

Reviewer-signed review-evidence manifest, written for `reconcile_merged_done`
(`scripts/ai_status.py::validate_merged_done_evidence`).

This is a dedicated, stable file rather than
`.orchestrator/task-briefs/sup_dispatch_explain_tool_20260804.md`, because the
supervisor regenerates that mirror from the canonical row on every dispatch. A
regenerated mirror would re-render `- Status: review` and break the byte-identity
check between the command-root working tree and the evidence commit.

## Canonical Metadata

- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Delivery repository: ajoe734/pantheon
- Delivery commit: 5423be86354df64e6d1fd43746af192d9cdfc742
- Delivery PR: https://github.com/ajoe734/pantheon/pull/4532 (MERGED into `dev`
  at `5d2b70fac`, 2026-08-05T00:10:51Z)

## Reviewer Verdict

APPROVED. All six acceptance criteria verified independently against the merged
implementation on 2026-08-05 (thirteenth reviewer pass).

1. Proposal context (`docs/04/supervisor_dispatch_refactor_proposal_2026-08-04.md`,
   "Problem 2") -- implementation shape matches: a read-only composition of
   existing gate functions, no new or duplicated gate logic.
2. `scripts/explain_dispatch.py <task-id> [--agent <agent-id>]` composes all
   eight named pure gate functions; imported at `scripts/explain_dispatch.py:20-46`,
   evaluation order verified below.
3. Per-agent structured trace naming the first blocking gate and its exact reason
   string; live run emits `first_blocking_gate` plus the verbatim reason, exits 0.
4. Read-only, no canonical task-state lock: the module has no write call sites
   (only `json.dumps` to stdout at `:449`) and loads through the shared
   `load_status` read path at `:116-118`. `md5sum -c` proves `ai-status.json` and
   `.orchestrator/config.json` byte-unchanged across a live run.
5. Unit tests: 14/14 pass. All seven required cases are covered by name
   (capability-blocked, quota-exhausted, capacity-exhausted, catalog-locked,
   unmet-dependency, cooldown-suppressed, all-clear), plus seven more.
6. `poll_workers`, `process_queue`, `reconcile_queue_records` untouched: the
   PR 4532 diff is exactly three files and does not include
   `.orchestrator/supervisor.py`.

### Gate-order verification

`dispatch_ready_tasks` (`.orchestrator/supervisor.py:18054`) evaluates, in order:
`agent_auto_dispatch_block_reason` -> `quota_group_concurrency_limit` ->
`agent_dispatch_capacity` -> `task_execution_dispatch_candidate` ->
`agent_can_take_task`, then `dispatch_event_is_in_unchanged_cooldown`.
`task_assignment_is_catalog_locked` and `dependencies_satisfied` are reached only
inside the helper-claim branch.

`explain_dispatch.py` reproduces this exactly: gates 1-5 at `:279-329`, cooldown
at `:371-404`, and the helper-claim-only pair correctly demoted to non-blocking
notes at `:415-417` rather than reported as blocking gates.

### Commands run this pass

    PYTHONPATH=.orchestrator pytest .orchestrator/test_explain_dispatch.py   # 14 passed
    PYTHONPATH=.orchestrator python scripts/explain_dispatch.py SUP-DISPATCH-EXPLAIN-TOOL-20260804
    md5sum -c                                                                # all OK
    gh pr view 4532 --json state,mergedAt,files                              # MERGED, 3 files
    git merge-base --is-ancestor 5423be86... origin/dev                      # ancestor

## Why This Manifest Exists

The governed `approve` transition is permanently unavailable for this task.
`bridge_github_review_decision` requires the bound PR to be in state OPEN, and
PR 4532 auto-merged before the reviewer verdict was recorded. Re-confirmed live
this pass: `approve` with an exact-head binding (`REVIEW_PR=4532`,
`REVIEW_HEAD_SHA=5423be86...`) is rejected with `GitHub PR #4532 is not open`.
`reopen` routes through the same bridge helper (`scripts/ai_status.py:5956-5962`)
and is equally unavailable -- and would be the wrong verdict anyway, since the
implementation passes.

`reconcile_merged_done` is the sanctioned recovery for exactly this shape: an
already-delivered task whose canonical row lost the `review_approved`
transition. It never calls the review bridge, so the OPEN-PR guard does not
apply. It is Human/Ops-only, so the reviewer cannot run it.

This task does NOT require a protected Human/Ops closeout verdict:
`requires_protected_closeout_verdict` is false -- the row carries no
`requires_human_ops_signoff` flag, the id is not `L12-CLOSE-001`, and
`required_human_ops_signoff_task_ids` in
`docs/bff/execution-tasks/2026-07-26-twelve-loop-gap/tasks.json` is
`["L12-CLOSE-001"]`. So no `PANTHEON_PRODUCT_CLOSEOUT_VERDICT_ID` is needed.

## Human/Ops Closeout Recipe

1. Merge this file to `dev` (any route; it does not need a review-bridge PR).
2. Refresh `PANTHEON_COMMAND_ROOT` to a HEAD containing that merge, so this file
   is tracked there and byte-identical to its copy at the evidence commit. The
   command root at `4361a26a` predates PR 4532 and does not track it yet.
3. Run, as Human/Ops:

    AI_NAME=Human/Ops \
    RECONCILE_EVIDENCE_FILE=.orchestrator/review-evidence/sup_dispatch_explain_tool_20260804.md \
    RECONCILE_EVIDENCE_COMMIT=<sha of the commit that merged this file to dev> \
    RECONCILE_DELIVERY_REPOSITORY=ajoe734/pantheon \
    RECONCILE_DELIVERY_ROOT=<a pantheon checkout whose origin is ajoe734/pantheon> \
    RECONCILE_DELIVERY_COMMIT=5423be86354df64e6d1fd43746af192d9cdfc742 \
    "$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh" reconcile_merged_done \
      SUP-DISPATCH-EXPLAIN-TOOL-20260804 \
      "Reconciled to done from merged review evidence; PR 4532 auto-merged before the reviewer verdict could be recorded."

`RECONCILE_EVIDENCE_TARGET_REF` and `RECONCILE_DELIVERY_TARGET_REF` both default
to `origin/dev` and do not need to be set.

This supersedes the action list carried in passes 1-12, which held that a fresh
OPEN PR bound to the review gate was mandatory. It is not: no OPEN PR and no
review-bridge interaction is required on this path.
