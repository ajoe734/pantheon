# Task Brief: OPS-PR-REVIEW-BEFORE-MERGE-GATE-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Gate task auto-merge on exact independent review when required
- Status: in_progress
- Owner: Claude
- Reviewer: Codex2
- Next: Review rejected at exact PR #4218 head 190fb7fe8c95fa060a33e45edc0e6ac0a0e55a59. Blocking findings: (1) approval is not structurally bound to the reviewed head. command_approve records only actor, timestamp, and free-text message; ApprovalRecord carries no approved head; evaluate_gate merely checks current head commit time <= approval time. Reproduced by approving bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb then replacing the PR head with cccccccccccccccccccccccccccccccccccccccc committed before approval: gate returned allow_merge=True reason=exact_head_approved. Required: record immutable PR/head and expected-base binding in canonical approval evidence, compare exact identities in the gate, and add an older/pre-dated head replacement regression. (2) auto_integrator.py ignores a failed gh pr merge --disable-auto on an approved gated PR and proceeds to direct --match-head-commit merge. Reproduced disable-auto return code 1; result still action=merged and emitted both commands. Required: failed revocation must block or wait and must never emit the direct merge; add a nonzero revocation regression. The existing 64 gate, 9 integrator, 52 workflow-helper, 24 triage, and 17 index-safety tests plus bash -n and py_compile pass, but omit these fail-open cases. Update evidence AC2/AC3 and revalidate. PR remained OPEN, autoMergeRequest=null, mergeStateStatus=BEHIND at review time.

## Summary
讓需要獨立審查的任務在 reviewer 核准且 head 未變前不得 auto-merge；保留明確允許 merge 後審查的既有路徑。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
