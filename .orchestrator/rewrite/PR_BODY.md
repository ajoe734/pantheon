# Supervisor structural rewrite — cutover of P1/P2/P3/P5, activity integrity off the hot path, sidecar deleted

Continues `docs/02-architecture/SUPERVISOR_REWRITE_PLAN.md` (P0/P1A/P3A were already merged as shadow-only modules that nothing imported). This wires the shadow-proven rewrite modules in as the **live** path (legacy always one flag away), completes Phase 2, and physically removes the sidecar make-work engine.

## What changed (10 commits)

| Phase | Change |
|---|---|
| **1** concurrency | `agent_dispatch_capacity` + `quota_group_concurrency_limit` route through `rewrite.concurrency` (`max_parallel` + `account_limit`) |
| **3** task machine | `dispatch_priority_for_task` routes through `rewrite.task_machine` (configured status sets translated to canonical lifecycle states first) |
| **5** provider health | `should_pause_dispatch_for_failure_kind` routes through `rewrite.provider_health` (`decide_failure_response` + `classify_health`) |
| **2** activity integrity | offline `rewrite/verify_activity_integrity.py` **+** `write_activity_log` degrades lineage-drift faults to warn+append (never crash-loops the cycle); security/correctness faults stay fail-closed |
| **4** worker lifecycle | `terminate_worker_pid` → confirm-kill (SIGTERM→wait→SIGKILL→verify); lease renewal can bind to observed work progress (`lease_requires_work_progress`) |
| **7** sidecar | make-work synthesis engine **physically deleted** (658 lines + 653 test lines); utilization policy = reprioritize, never synthesize |
| **6** state | event-log → `project_board` projection model built in `rewrite/state_projection.py` |

## Reversal flags (legacy one flag away)
`ready_dispatcher.use_rewrite_concurrency`, `ready_dispatcher.use_rewrite_dispatch_reason`, `PANTHEON_LEGACY_FAILURE_RESPONSE`, `PANTHEON_LEGACY_TERMINATE`, `PANTHEON_ACTIVITY_LOG_STRICT` / `config.activity_log_strict_hot_path`, `supervisor.lease_requires_work_progress` (default off).

## Verification
- Shadow validator **0 mismatch** on all 4 comparisons (max_parallel, account_limit, failure_pause, dispatch_reason) against live config/board.
- `rewrite/test_cutover.py` pins rewrite==legacy across a config matrix (custom status sets, overrides, ghost slots, unmet deps, full failure-kind vocabulary).
- **91 rewrite tests + 387 tests (supervisor 272 / common 90 / dispatch_policy 25) + 52 subtests green**; end-to-end via 233 run_once/dispatch/poll/failure tests.

## Still pending (fleet-gated, per the plan's non-big-bang mandate)
Physical `poll_workers` decomposition; event-queue indirection removal (`process_queue`/`reconcile_queue_records` — dispatch backbone); live state storage out of git (`ai_status.py`/`planning_state.py`); discussion→task-kind fold.

🤖 Generated with [Claude Code](https://claude.com/claude-code)
