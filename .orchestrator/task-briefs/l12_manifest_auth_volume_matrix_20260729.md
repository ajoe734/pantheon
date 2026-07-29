# Task Brief: L12-MANIFEST-AUTH-VOLUME-MATRIX-20260729

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: L12 manifest auth volume applicability matrix workstream
- Status: review
- Owner: Codex2
- Reviewer: Antigravity
- Next: Re-review PR #4336 after the owner refresh onto `origin/dev` `97d9ecd85826296d56d323ad6d8298c05c07ce08`; approve with REVIEW_PR=4336 and REVIEW_HEAD_SHA set to the exact current head so the canonical merge gate is structurally bound.

## Summary
建立每個 worker 的 auth 與 durable-volume applicability matrix，補 readback/validator 缺口。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.

## Owner Closeout Preparation
- Recorded Antigravity's `2026-07-29T03:01:13Z` independent decision and approval event for implementation commit `96fdc27cf7d645dedfe9e62ddbebbce6d472e0d3` in the task evidence manifest.
- Merged current `origin/dev` at `97d9ecd85826296d56d323ad6d8298c05c07ce08` into the task branch without changing the task-owned matrix, validator, or guarded parent surfaces.
- Reverified the provisioned Python distribution, focused pytest (`6 passed`), `py_compile`, audit-mode matrix admission, expected default-mode exit `1`, bare Compose configuration, JSON syntax/summary consistency, guard-path isolation, and `git diff --check`.
- The first approval event did not carry the structured PR/head/base binding required by `task_review_merge_gate.py`. The final PR head therefore returns to the same assigned reviewer for a bound exact-head approval before merge and `done`.

## Owner Finalization Reassignment
- The supervisor reassigned ownership from Codex to Codex2 after the prior owner worker repeatedly disappeared during boot reconciliation; Antigravity remains the assigned reviewer.
- Antigravity's bound approval of closeout head `639cb882ca1ddd208eec2f519a463cc1d67f232d` passed both canonical review and root merge-freeze checks, but PR #4334 advanced strict `dev` before PR #4336 landed.
- Codex2 refreshed only the integration base and this closeout receipt. The reviewed matrix, validator, readback findings, evidence manifest, guarded parent surfaces, and assigned reviewer are unchanged.
