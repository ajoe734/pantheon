# Task Brief: OPS-RTEL-002

This file is task-scoped execution context for the reopened OPS-RTEL-002
dispatch. Treat `ai-status.json` via `scripts/ai-status.sh` as the durable
execution source of truth for lifecycle transitions.

## Task
- Title: Paper runtime fleet reconciler
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Phase: Runtime Telemetry Hardening
- Last update: 2026-06-07T14:50:58Z
- Next: Supervisor auto-started OPS-RTEL-002 after successful dispatch.

## Summary
新增 paper runtime fleet reconciler，從 active paper runtime bindings 自動維持一個 worker per binding，取代手動 docker run。

## Dispatch Context
- Previous OPS-RTEL-002 delivery is already merged into `dev`:
  - PR #1053: initial paper runtime fleet reconciler.
  - PR #1056: fetch-failure safety, restart backoff, Dockerfile/requirements.
  - PR #1076: closeout doc/task-brief sync and binding-scoped signal queue isolation.
- The active status root reopened/reassigned this task to Codex2 after a
  worker dispatch issue. This dispatch revalidates the delivered behavior and
  refreshes this task-scoped brief; it does not broaden runtime code scope.

## Dependencies
- OPS-RTEL-001: done · Telemetry durability bootstrap

## Acceptance
- stack restart starts workers for all active paper bindings
- killing one worker causes automatic restart
- retired binding stops its worker
- no worker consumes shared Redis signals before isolation

## Artifacts
- `services/execution/`
- `services/execution/lean_runtime/`
- `services/execution/runtime-manager/`
- `docker-compose.yml`
- `docs/deployment/runtime-telemetry-hardening-2026-06-06.md`

## Current Implementation Snapshot
- `services/execution/runtime-manager/paper_fleet_reconciler.py` maintains one
  subprocess per active paper `RuntimeBinding`, preserves workers when
  runtime-manager desired-state fetch fails, and applies capped restart backoff.
- Spawned workers receive binding identity and
  `PANTHEON_SIGNAL_QUEUE_KEY=pantheon:signals:pending:<binding_id>` so Redis
  signal consumption is binding-scoped.
- `docker-compose.yml` exposes `paper-fleet-reconciler` under the `paper-fleet`
  profile.
- Later runtime telemetry hardening added monitoring-session coverage to the
  same focused test file, so the current suite has 25 tests.

## Verification
- 2026-06-07 Codex2:
  `python3 -m pytest services/execution/runtime-manager/test_paper_fleet_reconciler.py -q`
  -> 25 passed.

## Working Rules
- Use `AI_NAME=Codex2 ./scripts/ai-status.sh ...` for status changes.
- Keep execution updates short and structured.
- Do not scan `current-work.md` or full `ai-activity-log.jsonl` unless a task
  brief or reviewer explicitly requires it.
