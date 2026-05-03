# Current Supervisor, Auto Worker, and Dashboard Architecture

Status date: 2026-04-28

## 2026-05-03 Local Runtime Addendum

The local `/home/lupin/code/pantheon` supervisor is temporarily running with an
execution-only guard while blueprint execution tasks drain. The guard lives in
`.orchestrator/config.local.json` and sets `coordination.enabled=false`,
`github_bus.enabled=false`, and `chair_review.enabled=false`.

This is not a permanent architecture change. It prevents the old coordination
queue backup from being re-enqueued while the execution board is active. The
runbook is `docs/operations/orchestrator-execution-queue-isolation.md`.

Auto-worker readiness is tracked separately from queue isolation. See
`docs/operations/auto-worker-readiness.md` for the current provider matrix and
`docs/operations/orchestrator-coordination-replay-policy.md` for the safe replay
policy for the isolated coordination queue.

This document records the current local orchestrator architecture before the
planned supervisor redesign. It is a baseline for the existing
`supervisor + auto worker + dashboard` system.

## Scope

The current system is centered on these runtime files:

- `.orchestrator/supervisor.py`: main supervisor loop, dispatcher, worker
  runtime reconciliation, retry/reassignment policy, and dashboard refresh.
- `.orchestrator/config.json`: runtime paths, agent/provider adapters,
  ready-dispatch settings, retry settings, provider guardrails, and GitHub bus
  settings.
- `.orchestrator/state.json`: supervisor heartbeat, worker records, queue
  records, provider guardrails, underutilization tracking, and mode occupancy.
- `.orchestrator/event-queue.jsonl`: append-only queue of wake-up events to
  be delivered to local LLM workers.
- `.orchestrator/approval-queue.json`: approval broker pending/history state.
- `ai-status.json`: canonical task board.
- `ai-activity-log.jsonl`: canonical collaboration activity log.
- `current-work.md`: generated narrative status view.
- `dashboard-bundle.json`: generated dashboard projection.
- `docs-site/`: static dashboard application.

## Supervisor Loop

The supervisor is launched with:

```bash
bash scripts/run-supervisor.sh --verbose
```

That script execs `.orchestrator/supervisor.py`. On startup, the supervisor:

1. Terminates older supervisor processes in the same repository.
2. Writes `.orchestrator/supervisor.pid`.
3. Bootstraps `.orchestrator/state.json` with lifecycle, pid, heartbeat, and
   mode state.
4. Runs `run_once()`, then repeats on the configured interval.

The current config uses a short poll interval:

```json
"supervisor": {
  "poll_interval_seconds": 2.0
}
```

Each loop performs these major steps:

1. Expire provider dispatch pauses.
2. Prune stale approvals.
3. Refresh provider capability detection.
4. Optionally scan `ai-status.json` through the legacy watcher path.
5. Sync coordination files.
6. Poll active workers and reconcile logs, pids, approvals, failures, retries,
   and reassignment.
7. Reconcile queue records and prune stale queue events.
8. Auto-materialize accepted discussion planning tasks.
9. Dispatch either discussion-planning work or execution work.
10. Dispatch underutilization sidecars when enabled and eligible.
11. Process queued wake-up events into concrete worker processes.
12. Poll workers again, reconcile queue again, sync GitHub bus, trim runtime
    history, refresh dashboard artifacts, and write heartbeat state.

## Ready Dispatch Model

The current primary dispatch path is not the legacy watcher. The config has:

```json
"events": {
  "enqueue_runtime_events": false
}
```

Therefore `.orchestrator/watch_events.py` mostly maintains snapshots unless
replay/runtime events are explicitly enabled. The active execution dispatcher is
`dispatch_ready_tasks()` inside `.orchestrator/supervisor.py`.

Ready dispatch scans `ai-status.json` and assigns at most one active unit of
work per agent by default. It prioritizes work as follows:

1. `review` tasks go to the reviewer with reason `review_ready_dispatch`.
2. `review_approved` tasks go back to the owner for finalization with reason
   `owned_finalize_dispatch`.
3. `in_progress` tasks go to the owner with reason
   `owned_in_progress_dispatch`.
4. dependency-ready `todo` tasks go to the owner with reason
   `owned_ready_dispatch`.

The dispatcher respects dependency completion using `done` as the required
dependency terminal status. It also avoids agents with active workers, pending
queue events, provider guardrail pauses, or disallowed lane assignments.

`Qwen` is currently configured as a sidecar-only agent:

```json
"ready_dispatcher": {
  "sidecar_only_agents": ["Qwen"]
}
```

Mainline tasks assigned to sidecar-only agents can be automatically reassigned
away from those lanes.

## Worker Delivery

Dispatch is a two-step process:

1. `queue_delivery_event()` writes a wake-up event into
   `.orchestrator/event-queue.jsonl`.
2. `process_queue()` turns that event into an adapter delivery request and
   starts the appropriate worker runtime.

Current provider adapters include:

- `Claude` and `Claude2`: `claude_cli`
- `Codex` and `Codex2`: `codex`
- `Gemini`: `gemini`
- `Copilot`: `copilot_local`
- `Qwen`: `qwen`

When an adapter starts successfully, the supervisor records a worker under
`.orchestrator/state.json` with:

- `run_id`
- `provider`
- `agent_id`
- `task_id`
- `queue_event_id`
- `mode`
- `status`
- `pid`
- `log_path`
- `request_snapshot`
- retry and fallback metadata

For `owned_ready_dispatch`, a successful dispatch also calls
`scripts/ai_status.py start`, so the task moves from `todo` to `in_progress`
with a message like:

```text
Supervisor auto-started <task-id> after successful dispatch.
```

## Failure, Retry, and Reassignment

The supervisor reads worker logs and detects common failure patterns such as:

- authentication failures
- quota exhaustion
- 429/capacity failures
- timeouts and connection resets
- provider critical errors
- early worker exit before terminal task status

Failure handling includes:

- retry with provider-specific or global backoff;
- provider dispatch pause for auth, quota, or capacity failures;
- queue retry scheduling for retryable dispatch failures;
- worker retry/fallback handling for active worker failures;
- automatic owner or reviewer reassignment after repeated terminal failure;
- stale queue pruning;
- stale manual-pending worker requeue when auto redelivery becomes possible.

Provider dispatch pauses are stored in:

```text
.orchestrator/state.json -> provider_guardrails.dispatch_pauses
```

Task/provider failure streaks are stored in:

```text
.orchestrator/state.json -> provider_guardrails.task_failure_streaks
```

## Underutilization Sidecars

The current supervisor can create new sidecar tasks when worker utilization
stays below the configured threshold for a continuous window.

The sidecar path:

1. Computes productive worker lane ratio.
2. Finds idle eligible agents.
3. Finds sidecar candidates from `.orchestrator/sidecar_catalog.json` or a
   dynamic fallback.
4. Builds sidecar tasks with metadata:
   - `task_class: sidecar`
   - `auto_generated: true`
   - `helper_parent`
   - `helper_kind`
   - `mutates_canonical`
   - `auto_created_by: supervisor-underutilization`
5. Creates the task through `scripts/ai_status.py assign`.
6. Queues it for immediate `owned_ready_dispatch`.

Dynamic sidecar kinds currently include:

- `bff_handoff_packet`
- `review_packet`
- `acceptance_packet`

Sidecar wake-up prompts include explicit guardrails: support artifacts only,
no canonical truth mutation, and handoff to the assigned reviewer.

## Discussion Planning Mode

When `.orchestrator/planning-state.json` is active in
`discussion_planning` mode, the supervisor changes focus from execution to
planning. In that mode it dispatches readout/baton work through
`dispatch_discussion_planning()` instead of normal execution tasks.

Planning dispatch has separate metadata in queue events, and dashboard mode
occupancy separates planning, execution, and coordination work.

## Dashboard Projection

The dashboard is launched with:

```bash
bash scripts/run-dashboard.sh
```

This runs `scripts/dashboard_keepalive.sh`, which starts
`scripts/dashboard_server.py`. The default local URL is:

```text
http://127.0.0.1:4173/index.html
```

The dashboard server serves `docs-site/` and maps live repository files to HTTP
paths:

- `/ai-status.json`
- `/ai-activity-log.jsonl`
- `/current-work.md`
- `/dashboard-bundle.json`
- `/orchestrator-state.json`
- `/approval-queue.json`
- `/planning-state.json`

It also exposes `/__refresh`, which runs:

```bash
bash scripts/sync-state.sh
```

`scripts/ai_status.py sync` regenerates:

- `current-work.md`
- `dashboard-bundle.json`
- mirror files under `docs-site/`

The supervisor also refreshes dashboard runtime artifacts at the end of each
successful loop.

## Dashboard Runtime Views

The dashboard currently renders:

- supervisor pid, heartbeat, start time, and scan time;
- dispatch queue depth and queued task details;
- approval queue;
- provider guardrail pauses;
- per-agent worker lane counts;
- live worker history grouped by agent;
- truth mismatches between task board, queue, approval queue, and runtime
  workers;
- planning/execution focus;
- planning bridge state;
- task board, handoffs, blockers, activity log, and coordination summaries.

Runtime mismatch detection exists both server-side in `scripts/ai_status.py`
and client-side in `docs-site/js/dashboard-core.js`, with
`dashboard-bundle.json` preferred when available.

## Current Architectural Shape

The current version works as a single large local control loop. Its main
strength is that it can drive the whole collaboration system without another
service: it sees tasks, dispatches workers, retries failures, reassigns work,
creates sidecars, and updates the dashboard.

Its main design pressure is responsibility concentration. The supervisor
currently acts as:

- heartbeat process;
- watcher;
- scheduler;
- worker runtime manager;
- retry engine;
- reassignment engine;
- sidecar generator;
- planning dispatcher;
- coordination sync runner;
- dashboard projection refresher.

The next redesign should preserve the successful data model and runtime
evidence, while reducing the supervisor's active decision surface.
