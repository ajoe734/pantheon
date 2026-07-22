# Task Brief: AG-WS-OPS-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Governed Workshop research consultation and conclusion
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Review changes required: preserve and resume partial downstream effects. Research adapter can create/read a task before run POST/readback fails, but CanonicalOperationError carries no task/run ID; router stores a terminal failed receipt with empty canonical refs, same-key replay is blocked, and new-key retry derives new downstream idempotency keys, allowing orphan/duplicate tasks. Consultation has the same gap after request creation when submit/readback fails: no consultation_request_id or compensation is recorded before the failed key is sealed. Add durable partial-effect lineage/resume-or-compensation semantics for both adapters/routes and focused tests that inject failures after research task creation, after research run acceptance/readback, and after consultation creation/submit; prove restart-safe retry does not create duplicate downstream resources. Existing conclude selection/digest/atomicity work and 98 focused tests passed review.

## Summary
實作 research-runs、consultations、conclude 三條 deferred API，綁定 durable workshop version、真實 downstream lineage、idempotency 與 atomic terminal transition。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
