# Orchestrator Coordination Queue Replay Policy

Status: retired queue policy for the 2026-05-03 isolated queue
Recorded: 2026-05-03
Retired: 2026-05-04
Task: ORCH-COORDINATION-QUEUE-TRIAGE-REPLAY-POLICY

## Scope

This policy covers the old coordination events isolated from
`.orchestrator/event-queue.jsonl` before the blueprint execution wave.

Final archive path:

```text
.orchestrator/backups/retired-queues/20260504-stale-coordination-dispatch/event-queue.invalidated.jsonl
```

Replay candidate file:

```text
.orchestrator/backups/retired-queues/20260504-stale-coordination-dispatch/replay-candidates.empty.jsonl
```

The original backup path,
`.orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl`,
has been removed from the active backup root so it is not mistaken for a
candidate restore source.

## Final Disposition

On 2026-05-04 the operator instructed this repo to assume the isolated
coordination events are stale/invalid if they have already expired and to move
them to the appropriate archive. The queue was therefore retired as an
invalidated runtime dispatch snapshot.

This is an archive, not a deletion. The file remains available for audit, but
it must not be copied, appended, or replayed into
`.orchestrator/event-queue.jsonl`.

Tracked manifest:

```text
docs/operations/retired-coordination-queue-20260504.md
```

## Triage Result

Run:

```bash
python3 scripts/orchestrator_queue_triage.py --format markdown
```

Observed before retirement and preserved by checksum:

```text
Backup events: 115
Replay candidates: 0
```

Classification counts:

```text
1  do_not_replay_chair_review
1  do_not_replay_non_coordination
12 do_not_replay_payload_terminal
99 manual_review_required_coordination_artifact
2  manual_review_required_open_payload
```

Reason counts:

```text
1  chair_review:operational_review
44 coordination:contract-ready
2  coordination:needs-runtime
67 coordination:ui-done
1  owned_ready_dispatch
```

The generated replay candidate file is intentionally empty.

## Policy

Do not bulk append the retired archive into the active queue.

Do not replay individual lines from the retired archive. The queue backup
contains supervisor delivery attempts, not canonical product truth. The
operator-level disposition is now that these old events are invalidated as a
runtime queue source.

If any old feature or coordination packet still matters, create a fresh
coordination publication or execution task from current source truth. Do not use
the old JSONL line as the event source.

## Hard Stops

- Do not copy the retired archive over `.orchestrator/event-queue.jsonl`.
- Do not append all 115 archived lines.
- Do not replay stale `coordination:ui-done`, `coordination:contract-ready`,
  `coordination:needs-runtime`, `owned_ready_dispatch`, or
  `chair_review:operational_review` lines from this archive.
- Do not enable GitHub bus and retired coordination replay in the same step;
  future coordination work must be republished as fresh current events.
