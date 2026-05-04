# Retired Coordination Queue 2026-05-04

Status: archived and invalidated
Archived at: 2026-05-04
Disposition: stale runtime dispatch snapshot; do not replay

## Archive Location

Invalidated queue:

```text
.orchestrator/backups/retired-queues/20260504-stale-coordination-dispatch/event-queue.invalidated.jsonl
```

Empty replay-candidate marker:

```text
.orchestrator/backups/retired-queues/20260504-stale-coordination-dispatch/replay-candidates.empty.jsonl
```

Original paths removed from generic backup root:

```text
.orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl
.orchestrator/backups/event-queue.replay-candidates-20260503.jsonl
```

## Checksums

```text
7f4cd14e00e18191a912898b2fbe6c3deb7fe0564fb9b9d1359eccaaaeedae07  event-queue.invalidated.jsonl
e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855  replay-candidates.empty.jsonl
```

## Counts

```text
115 archived queue events
0 replay candidates
```

Reason counts:

```text
67 coordination:ui-done
44 coordination:contract-ready
2  coordination:needs-runtime
1  owned_ready_dispatch
1  chair_review:operational_review
```

Classification counts from `scripts/orchestrator_queue_triage.py`:

```text
99 manual_review_required_coordination_artifact
12 do_not_replay_payload_terminal
2  manual_review_required_open_payload
1  do_not_replay_non_coordination
1  do_not_replay_chair_review
```

## Operational Rule

This archive is not an execution queue and not a replay queue. It exists only
so the removed runtime events remain auditable.

If an old feature still needs work, publish a fresh current coordination packet
or create a new execution task from current repo state. Do not restore, append,
or replay a JSONL line from this retired archive.
