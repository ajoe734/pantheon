# Orchestrator Execution Queue Isolation

Status: active local runtime guard
Recorded: 2026-05-03
Task: ORCH-EXECUTION-QUEUE-ISOLATION-CLOSEOUT

## Scope

This record documents the temporary queue isolation used for the 2026-05-03
blueprint execution wave. It is an operational guard, not a permanent policy
change: coordination scanning, the GitHub bus, and chair review remain valid
orchestrator modes and are only disabled by local override while this execution
queue is isolated.

## Files

- Local guard: `.orchestrator/config.local.json`
- Active queue: `.orchestrator/event-queue.jsonl`
- Pre-isolation backup:
  `.orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl`
- Dashboard evidence: `docs-site/dashboard-bundle.json`

## Why Isolation Happened

The pre-isolation queue had a large coordination backlog from the Lovable /
frontend coordination bus. Replaying that backlog during the blueprint execution
wave would have consumed worker slots and made it look like coordination/GitHub
bus work was the current priority. The active queue was reset so the supervisor
could dispatch the execution board only.

Do not treat the guard as evidence that coordination or the GitHub bus is
retired. It only prevents this local supervisor loop from re-enqueueing the old
coordination backlog while execution work is being drained.

## Queue Diff

Command:

```bash
wc -l .orchestrator/event-queue.jsonl \
  .orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl
```

Observed after manual closeout on 2026-05-03:

```text
0   .orchestrator/event-queue.jsonl
115 .orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl
```

Active queue reasons:

```text
<empty>
```

Active queue events are allowed to change while the execution board drains.
The invariant is that the active queue contains no `coordination:*` or
`chair_review:*` events while the execution-only guard is active. After this
manual closeout, the active queue was intentionally empty:

```text
0 active queue events
```

Backup queue reasons:

```text
1  chair_review:operational_review
44 coordination:contract-ready
2  coordination:needs-runtime
67 coordination:ui-done
1  owned_ready_dispatch
```

The backup therefore contains 113 coordination dispatches, one old execution
dispatch, and one chair review event. It is an audit artifact and must not be
bulk-appended back into the active queue.

## Local Guard

`.orchestrator/config.local.json` currently overrides the default orchestrator
configuration:

```json
{
  "coordination": {
    "enabled": false
  },
  "github_bus": {
    "enabled": false
  },
  "chair_review": {
    "enabled": false
  }
}
```

Runtime effects:

- `coordination.enabled=false` makes `sync_coordination_files(...)` return
  without scanning `.coordination` payloads or enqueueing coordination events.
- `github_bus.enabled=false` makes `sync_github_bus(...)` return without GitHub
  polling, issue/PR updates, or command processing.
- `chair_review.enabled=false` makes `dispatch_chair_review(...)` return without
  creating operational review events.
- Execution dispatch remains active through the normal ready-task path.

## Dashboard Evidence

`docs-site/dashboard-bundle.json` can be used to verify the isolated runtime.
During closeout it showed zero live coordination occupancy and no active
coordination queue reasons. The exact number of running execution workers
changes as the board drains; after manual cleanup the runtime state was set to
idle with an empty active queue.

```json
jq '{focus_mode, runtime_summary: {queue_depth: .runtime_summary.queue_depth, running_workers: .runtime_summary.running_workers, mode_occupancy: .runtime_summary.mode_occupancy}}' docs-site/dashboard-bundle.json
```

This is the expected signal: the dashboard can still display coordination
summary state, but live supervisor occupancy is execution-only.

## Restore Procedure

Use this only after the execution wave no longer needs isolation.

1. Confirm there is no active decision to keep the local guard:

   ```bash
   python3 -c 'import json, sys; sys.path.insert(0, ".orchestrator"); from common import LOCAL_CONFIG_PATH, load_json; c = load_json(LOCAL_CONFIG_PATH, default={}); print(json.dumps({"coordination": c.get("coordination"), "github_bus_enabled": (c.get("github_bus") or {}).get("enabled"), "chair_review_enabled": (c.get("chair_review") or {}).get("enabled")}, indent=2))'
   ```

2. Remove the three local false overrides, or set them back to true if this
   machine intentionally wants explicit local enablement:

   ```json
   {
     "coordination": {"enabled": true},
     "github_bus": {"enabled": true},
     "chair_review": {"enabled": true}
   }
   ```

   Prefer removing the override blocks when default `.orchestrator/config.json`
   should own the mode settings.

3. Restart the supervisor process or wait for the next supervisor reload cycle
   if it is managed by the local launcher.

4. Verify that new runtime state reflects restored mode availability:

   ```bash
   jq '.runtime_summary.mode_occupancy' docs-site/dashboard-bundle.json
   jq -r '.reason' .orchestrator/event-queue.jsonl | sort | uniq -c
   ```

5. If a specific coordination packet still needs work, create or republish that
   specific payload so the watcher emits a fresh event. Do not restore the 113
   pre-isolation coordination events as a batch.

## Non-Goals

- Do not copy
  `.orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl`
  over `.orchestrator/event-queue.jsonl`.
- Do not append all backup lines into the active queue.
- Do not interpret the current local guard as canonical architecture, product
  policy, or permanent retirement of coordination/GitHub/chair modes.
- Do not erase the backup file; it records exactly what was removed from the
  active queue before the blueprint execution isolation.

## Verification Commands

Focused checks used for this record:

```bash
wc -l .orchestrator/event-queue.jsonl \
  .orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl
jq -r '.reason' .orchestrator/event-queue.jsonl | sort | uniq -c
jq -r '.reason' .orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl | sort | uniq -c
jq -r '[.created_at,.event_id,.task_id,.target_agent,.reason] | @tsv' \
  .orchestrator/event-queue.jsonl
jq '{focus_mode, runtime_summary: {queue_depth: .runtime_summary.queue_depth, running_workers: .runtime_summary.running_workers, mode_occupancy: .runtime_summary.mode_occupancy}}' \
  docs-site/dashboard-bundle.json
```
