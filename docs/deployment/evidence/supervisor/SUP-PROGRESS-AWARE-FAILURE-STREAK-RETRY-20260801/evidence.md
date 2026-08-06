# Evidence Summary: SUP-PROGRESS-AWARE-FAILURE-STREAK-RETRY-20260801

## 1. Incident and Remediation Summary

1. **Rejection & Reopen Rationale**:
   - Commit trailer length error on commit `04c56e068` (subject exceeded 72 chars).
   - Recovery: Reset branch to `1fef315cb`, merged `origin/dev` tip cleanly, and created a single commit with subject length <= 72 characters.
   - PR #4385 rejection rationale provided below (Item 10).

2. **Supersession Thesis (Acceptance Item 10)**:
   - PR #4385 addressed an L12-only missing process behavior and stale approval.
   - The generic progress-aware failure streak recovery feature was subsequently implemented on `dev` via `SUP-TASK-FAILURE-STREAK-SCHEMA-20260804` (PR #4564 / #4533), introducing `decide_failure_streak_recovery`, `failure_streak_recovery_progress_generation`, and `failure_streak_recovery_decision_for_task` in `.orchestrator/supervisor.py`.
   - Thus, PR #4385 is fully superseded by the generic infrastructure on `origin/dev`.

## 2. Task Acceptance Mapping Table

| Acceptance Item | Description | Code / Test Citation in `.orchestrator/` | Behavior Verification / Audit Surface |
|---|---|---|---|
| Item 1 | Same-owner reviewer retry condition | `supervisor.py:7838` (`decide_failure_streak_recovery`) | Target agent matching owner is verified without restricting owner != reviewer tasks from progress-aware retry when owner is unchanged. |
| Item 2 | Recoverable failure kinds boundary | `supervisor.py:7151` (`FAILURE_RECOVERY_ALLOWED_KINDS`), `supervisor.py:7865` | Restricts retry to `generic_exit` and `missing_process`; fails closed on non-recoverable kinds. |
| Item 3 | Exact-head progress generation binding | `supervisor.py:7672` (`_failure_recovery_consumption_token`), `supervisor.py:7953` | Token generation binds task identity, failure generation ID, and progress generation ID. |
| Item 4 | One-shot consumption token replay prevention | `test_supervisor.py:17746` (`test_exact_progress_generation_is_one_shot`), `L18423`, `L18535` | Token consumption prevents duplicate retries; replay returns `progress_generation_already_consumed`. |
| Item 5 | Task occupancy deny matrix | `test_supervisor.py:17756` (`test_exact_task_occupancy_deny_matrix_and_unrelated_preservation`), `test_supervisor.py:17810` (`test_provider_ready_and_pause_deny_matrix`) | Asserts active worker, pending queue, delivery reservation, or provider pause correctly deny recovery retry. |
| Item 6 | Durable activity audit | `supervisor.py:8393` (`bind_failure_streak_recovery_event_key`), `supervisor.py:20222`, `supervisor.py:21089` (`failure_streak_recovery_activity_snapshot`) | Audit trail records `failure_streak_recovery_queued`, `failure_streak_recovery_consumed`, and worker start events. |
| Item 7 | Operator recovery interface | `supervisor.py:8046` (`failure_streak_recovery_activity_snapshot`), `supervisor.py:20222` | Exposes recovery activity snapshots and event histories for operator inspection. |
| Item 8 | Pure decision function without side-effects | `supervisor.py:7838` (`decide_failure_streak_recovery`) | Decision calculation is purely declarative and produces no state or log side-effects during evaluation. |
| Item 9 | Zero-net code deletion & dev tip alignment | `evidence.json`, Git branch HEAD | Cleanly merged with `origin/dev` tip (`ab5caf7d4`), preserving dev infrastructure without code deletion. |
| Item 10 | PR #4385 supersession evidence | `evidence.md` Section 1 | PR #4385 superseded by generic progress-aware failure streak recovery V2 (PR #4564 / #4533). |
