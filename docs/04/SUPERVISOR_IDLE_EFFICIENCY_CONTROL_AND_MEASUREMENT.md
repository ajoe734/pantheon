# Supervisor Idle Efficiency Control And Measurement

Date: 2026-05-18
Status: proposed orchestrator control design
Tier: L2 Planning & Execution
Scope: supervisor and auto-worker dispatch control, chair-review gating, recommendation materialization, token/resource fuse behavior, and efficiency measurement
Conflict rule: L0 state files (`ai-status.json`, `.orchestrator/state.json`, `.orchestrator/approval-queue.json`) remain the operational source of truth; this document defines intended supervisor control behavior and measurement semantics. Telemetry storage decisions defer to `TELEMETRY_INGEST_AND_STORAGE_ARCHITECTURE.md`.

## 1. Problem Statement

The supervisor must not treat idle capacity as a work source. Idle agents are capacity, not demand.

The 2026-05-18 operating incident showed the failure mode clearly:

- The task board had no runnable work: no `todo`, `in_progress`, `review`, or `review_approved` task.
- The approval queue was empty.
- There were no open PRs targeting `dev` or `master` at the time of later checks.
- The supervisor continued launching chair-review workers because utilization was low.
- Recent chair-review runs produced useful observations, but repeated them without turning `recommended_focus` into a task, PR, or planning state transition.
- This consumed large token budgets and grew runtime logs while delivery throughput stayed at zero.

The root cause is a control-loop error, not a hardware saturation issue: the supervisor currently allows an underutilization signal to produce LLM work even when deterministic checks can prove that no actionable work exists.

## 2. Goals

1. Do not dispatch an LLM worker solely because workers are idle.
2. Run deterministic gates before chair review or sidecar dispatch.
3. Make chair-review output either become a state transition or enter cooldown.
4. Add budget fuses so repeated no-op LLM work stops automatically.
5. Measure whether changes reduce token/resource burn per useful state transition.
6. Preserve real repair ability when there is runnable work, stale review, blocked PR, approval work, provider failure, or resource pressure.

## 3. Non-Goals

- This design does not reduce the configured worker concurrency for real runnable work.
- This design does not remove chair review; it restricts chair review to cases where deterministic gates show that judgment is needed.
- This design does not replace repository PR/check/merge discipline.
- This design does not define permanent telemetry storage tables; it defines the measurement events that can later be routed into the canonical telemetry ingest path.

## 4. Control Principles

### 4.1 Work Sources

The supervisor may dispatch LLM work only when at least one work source exists:

| Work source | Examples |
|---|---|
| Runnable task | `todo` task whose dependencies, owner lane, and provider guardrails allow execution |
| Review dwell | `review` task waiting longer than configured threshold |
| Finalization dwell | `review_approved` task waiting longer than configured threshold |
| Approval work | pending approval that is low-risk enough for chair decision or requires escalation |
| PR repair | open PR behind base, failing required checks, missing auto-merge, or stuck past SLA |
| Provider repair | dispatch pause, repeated provider failure, bad owner/reviewer lane, or stale worker lease |
| Planning materialization | accepted planning seed or approved sprint topic list that is not yet in `ai-status.json` |
| Recommendation materialization | previous chair review produced actionable `recommended_focus` that has not been converted into state |
| Resource pressure | disk/memory/process pressure that requires degraded mode, cleanup, or pause |

Idle capacity is not on this list.

### 4.2 No-Op Idle Behavior

If all deterministic gates say there is no work source, the supervisor must:

1. update heartbeat and loop metrics;
2. record a cheap `supervisor_gate_decision` event with decision `no_op_idle`;
3. skip LLM dispatch;
4. keep existing workers undisturbed;
5. avoid sidecar generation.

This is the healthy state after sprint closeout and before the next planning/materialization step.

## 5. Deterministic Dispatch Gate

Every supervisor loop must compute a gate result before dispatch.

### 5.1 Inputs

The gate reads only bounded summaries:

- `ai-status.json`: task counts by status, owner, reviewer, dependencies, and task class.
- `.orchestrator/state.json`: worker leases, queue events, provider guardrails, underutilization state, last chair-review metadata, and runtime metrics.
- `.orchestrator/approval-queue.json`: pending approval count, age, type, and risk summary.
- GitHub PR summary cache or fresh query: open PRs by base, merge state, check state, age, and head branch.
- Chair-review outputs: last decision, `recommended_focus`, output hash, materialization status, and expiry.
- Resource summary: disk free, log growth, memory available, load average, active worker count, and supervisor RSS.

The gate must not read large logs or full state blobs unless a specific repair path requires it.

### 5.2 Outputs

The gate returns one of:

| Decision | Meaning |
|---|---|
| `dispatch_execution` | Runnable implementation/finalization work exists |
| `dispatch_review` | Review dwell or review assignment requires worker dispatch |
| `dispatch_chair_review` | Human-like operational judgment is required and no fresher chair result covers it |
| `materialize_recommendation` | Previous chair output must become task/PR/planning state before another review |
| `resource_pressure` | Pause dispatch or enter degraded mode until resource preflight passes |
| `budget_fuse_open` | Skip LLM dispatch because repeated no-op or token/cost budget was exceeded |
| `no_op_idle` | Nothing actionable exists; heartbeat only |

The supervisor should store the decision and the decisive input counts in `.orchestrator/state.json` and append a measurement record.

### 5.3 Gate Order

The gate must evaluate in this order:

1. **Resource preflight.** If disk, memory, load, or runaway process thresholds are exceeded, emit `resource_pressure`; pause new dispatch except bounded cleanup/diagnostic work.
2. **Budget fuse.** If token/cost/no-op budgets are exhausted for the current window, emit `budget_fuse_open`.
3. **Queue and worker reconciliation.** If a started queue event has no live process, or a worker has an expired lease, repair deterministic state before launching new LLM work.
4. **Runnable task scan.** If executable or finalizable task work exists, dispatch that work before chair review.
5. **Review/finalization dwell scan.** If review or closeout is stale, dispatch the correct reviewer/finalizer or create a repair task.
6. **Approval scan.** If pending approval exists, handle deterministic low-risk decisions or dispatch chair review only for approvals that need judgment.
7. **PR repair scan.** If open PRs are behind, failing, or stuck past SLA, materialize an OPS repair task or dispatch a bounded repair worker.
8. **Planning/materialization scan.** If accepted planning output exists but no task board entries were created, materialize tasks or enter planning mode.
9. **Prior recommendation scan.** If the latest chair review recommended actions that are not materialized, run materialization before another chair review.
10. **Chair freshness check.** If the last chair review is still fresh and its input fingerprint has not changed, skip chair review.
11. **No-op idle.** If no work source remains, write heartbeat and stop.

## 6. Chair Review Materialization

Chair review is useful only when it changes the next supervisor action.

### 6.1 Structured Recommendation Schema

The existing `recommended_focus` string list should migrate to structured items:

```json
{
  "id": "OPS-PROMOTE-OPEN-PRS-001",
  "kind": "ops_task | planning_task | pr_repair | branch_hygiene | resource_cleanup | observe_only",
  "urgency": "now | next_cycle | backlog",
  "materialization": "create_task | dispatch_worker | open_pr | record_exception | none",
  "acceptance": "short objective completion condition",
  "reason": "why this is actionable now"
}
```

Legacy string recommendations remain accepted during migration, but the supervisor must classify them before the next chair cycle.

### 6.2 Materialization Rules

After a chair review writes a decision file:

1. If `approval_actions` or `reassignment_actions` are present, apply the allowed deterministic state transition.
2. If `recommended_focus` contains actionable items, create or dispatch the corresponding `OPS-*`, `SUP-*`, planning, or PR-repair unit.
3. If all recommendations are `observe_only`, record that explicitly and start cooldown.
4. If the recommendation cannot be materialized safely, record `materialization_blocked` with the blocker.
5. Do not run another chair review while the previous actionable recommendation is unmaterialized unless the input fingerprint changes materially.

### 6.3 Duplicate Review Detection

Each chair review should compute an input fingerprint from:

- task counts by status;
- queue depth and pending approval count;
- open PR numbers and merge states;
- provider pause summary;
- stale review/finalization IDs;
- last materialized recommendation IDs.

If the fingerprint is unchanged and the last review is within TTL, the next loop must skip chair review and record `chair_review_skipped_fresh`.

## 7. Token, Cost, And Resource Fuses

Fuses are safety belts, not the primary control. The deterministic gate is the primary fix.

### 7.1 Budget Dimensions

Budgets should be configurable by `run_kind` and rolling window:

| Dimension | Example window |
|---|---|
| `llm_runs` | chair reviews per 6h |
| `noop_llm_runs` | no-state-change LLM runs per 24h |
| `total_tokens` | input + cache creation + output, with cache-read tracked separately |
| `cost_usd` | provider-reported cost when available |
| `wall_time_seconds` | long-running no-op reviews |
| `log_bytes_written` | growth of `.orchestrator/logs` |
| `disk_free_gb` | resource pressure threshold |

### 7.2 Initial Policy

Initial policy should be conservative:

- Empty backlog + empty approval queue + no open PRs: zero chair reviews until inputs change.
- Empty backlog with open OPS recommendations: one materialization attempt, then cooldown if blocked.
- Chair review with unchanged fingerprint: skip for at least 6 hours.
- No-op LLM run budget: maximum one per 24 hours per unique input fingerprint during rollout, then zero once confidence is established.
- Disk usage above warning threshold: do not start new non-repair LLM workers.
- Disk usage above critical threshold: enter `resource_pressure` and dispatch only bounded cleanup or human-visible diagnostic work.

## 8. Measurement Design

The measurement system must prove whether the fix worked. It should answer:

1. Did empty-backlog loops stop launching LLM workers?
2. Did token/cost per useful state transition fall?
3. Did chair recommendations turn into tasks, PRs, planning state, or explicit exceptions?
4. Did resource pressure decrease or at least stop worsening during idle periods?
5. Did the fix preserve delivery throughput when real work exists?

### 8.1 Event Streams

Append JSONL records for these event types:

| Event | When |
|---|---|
| `supervisor_gate_decision` | every supervisor loop after deterministic gate |
| `worker_run_started` | when an auto worker process is launched |
| `worker_run_finished` | when a worker exits or reaches terminal state |
| `chair_review_result` | after chair review decision JSON is parsed |
| `recommendation_materialized` | when a recommendation becomes a task, PR, planning state, dispatch, or exception |
| `budget_fuse_opened` | when LLM dispatch is suppressed by budget |
| `resource_pressure_detected` | when dispatch is paused/degraded due to resource preflight |

Initial storage can be `.orchestrator/metrics/supervisor-efficiency.jsonl`, with later routing into the canonical telemetry ingest path.

### 8.2 Required Fields

Every measurement record must include:

```json
{
  "version": 1,
  "event_type": "supervisor_gate_decision",
  "event_id": "evt-...",
  "at": "2026-05-18T13:06:20Z",
  "supervisor_pid": 3265758,
  "loop_id": "loop-...",
  "run_kind": "supervisor_loop | chair_review | execution | review | finalization | materialization",
  "decision": "no_op_idle",
  "trigger_reason": "empty_backlog_no_approvals_no_open_prs",
  "task_counts": {"todo": 0, "in_progress": 0, "review": 0, "review_approved": 0, "done_visible": 1},
  "queue_depth": 0,
  "pending_approvals": 0,
  "open_pr_count": 0,
  "stale_review_count": 0,
  "recommended_focus_unmaterialized_count": 0,
  "chair_fingerprint": "sha256:...",
  "budget_window": "6h",
  "budget_remaining": {"chair_reviews": 0, "noop_llm_runs": 0},
  "resource": {"disk_free_gb": 10.7, "memory_available_gb": 10.0, "load_1m": 1.87},
  "state_changed": false
}
```

Worker finish records must also include:

- `provider`, `agent_id`, `task_id`, `queue_event_id`;
- `duration_seconds`;
- `exit_status` and `terminal_reason`;
- token usage: `input_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `output_tokens`, `total_tokens`;
- `cost_usd` when available;
- `log_bytes_written`;
- `files_changed_count`, `commits_created`, `prs_opened`, `prs_merged`;
- `task_status_changes`;
- `recommendations_count` and `recommendations_materialized_count`.

### 8.3 Useful State Transition

A worker run counts as useful only if at least one of these occurs within the run or its immediate follow-up window:

- task status changes in `ai-status.json`;
- approval is allowed/denied with durable evidence;
- owner/reviewer is reassigned to break a failure loop;
- PR is opened, updated, merged, or explicitly recorded as blocked;
- planning output is materialized into tasks;
- recommendation is recorded as `observe_only` or `materialization_blocked` with a concrete blocker;
- resource pressure is reduced or dispatch is safely paused.

Writing a chair report alone is not a useful state transition.

### 8.4 KPIs

| KPI | Definition | Desired direction |
|---|---|---|
| Empty-idle LLM runs | LLM runs when backlog, approvals, stale review, and open PR counts are all zero | 0 |
| No-op token burn | tokens used by runs with `state_changed=false` | down to near 0 |
| Cost per useful transition | LLM cost divided by useful state transitions | down |
| Chair materialization rate | materialized recommendations / actionable recommendations | up to 100% |
| Duplicate chair rate | chair reviews with unchanged input fingerprint within TTL | 0 |
| Dispatch yield | useful worker runs / total LLM worker runs | up |
| Idle log growth | `.orchestrator/logs` bytes written during no-op idle windows | down |
| Resource pressure minutes | minutes in `resource_pressure` or disk-critical state | down |
| Real-work latency | time from runnable task to worker start | not worse |

### 8.5 Baseline And Verification Windows

Use the 2026-05-18 incident as the initial baseline:

- Since 08:00 UTC, recent logs showed multiple chair/operational review runs during or near the Sprint 8 closeout boundary.
- Later empty-backlog chair reviews consumed significant tokens while producing runtime artifacts but no immediate PR/task transition.
- `.orchestrator/logs` had grown to several gigabytes, and root disk usage was near the warning range.

After implementation, compare:

| Window | Required proof |
|---|---|
| 1 hour after deploy | Empty backlog produces only `no_op_idle` gate events; no LLM chair review starts |
| 6 hours after deploy | Duplicate chair rate is zero for unchanged fingerprints |
| 24 hours after deploy | No-op token burn is near zero; real runnable tasks still dispatch |
| 72 hours after deploy | Recommendation materialization rate and cost per useful transition are visible in metrics |

## 9. Rollout Plan

### P1: Measurement First

- Add gate-decision and worker-run metrics without changing dispatch behavior.
- Parse provider token/cost usage from worker logs when available.
- Record state-change outcomes.
- Establish dashboards or scripts for the KPIs above.

### P2: Deterministic No-Op Gate

- Add the gate order from Section 5.
- Suppress chair review when no work source exists.
- Record `no_op_idle` heartbeat events.
- Add unit tests for empty backlog, runnable task, approval, stale review, stuck PR, prior recommendation, and resource pressure scenarios.

### P3: Recommendation Materialization

- Classify legacy `recommended_focus` strings.
- Add structured recommendation support.
- Materialize actionable recommendations into tasks, PR repair work, planning state, or explicit exceptions.
- Block duplicate chair review until materialization completes or inputs change.

### P4: Budget And Resource Fuses

- Enforce rolling budgets for no-op LLM runs and chair reviews.
- Add disk/log growth preflight.
- Enter degraded/resource-pressure mode instead of restart or dispatch loops when resources are tight.

## 10. Test Plan

Required local tests:

1. Empty board + no approvals + no open PRs -> `no_op_idle`, no worker launch.
2. Runnable `todo` task -> `dispatch_execution`.
3. Stale `review` task -> `dispatch_review`.
4. Stale `review_approved` task -> finalization dispatch or closeout repair task.
5. Pending approval requiring judgment -> `dispatch_chair_review`.
6. Open PR behind base -> `materialize_recommendation` or PR repair task.
7. Previous actionable recommendation unmaterialized -> no new chair review.
8. Unchanged chair fingerprint within TTL -> chair review skipped.
9. No-op LLM budget exceeded -> `budget_fuse_open`.
10. Disk critical threshold -> `resource_pressure`, no non-repair dispatch.
11. Real work present while budget fuse is open for chair review -> execution dispatch still allowed.

Replay tests should use captured 2026-05-18 state summaries to prove the new gate would have emitted `no_op_idle` instead of launching repeated chair reviews after the backlog emptied.

## 11. Operator Readout

The dashboard or status command should expose:

- current gate decision and reason;
- last chair-review fingerprint and expiry;
- unmaterialized recommendations;
- no-op LLM budget remaining;
- token/cost burn by run kind over 1h, 6h, 24h;
- useful state transitions by run kind;
- disk/log growth and resource-pressure state.

The operator-facing question should be answerable without reading raw logs:

```text
Are supervisor and auto workers spending tokens or hardware resources without producing useful state transitions?
```

The expected answer after this design is implemented is visible from metrics, not inferred from anecdotes.
