# Orchestrator Coordination Queue Replay Policy

Status: active policy for the 2026-05-03 isolated queue
Recorded: 2026-05-03
Task: ORCH-COORDINATION-QUEUE-TRIAGE-REPLAY-POLICY

## Scope

This policy covers the old coordination events isolated from
`.orchestrator/event-queue.jsonl` before the blueprint execution wave. The
backup is:

```text
.orchestrator/backups/event-queue.pre-blueprint-execution-20260503T130840Z.jsonl
```

That file is audit evidence. It is not a replay file.

## Triage Result

Run:

```bash
python3 scripts/orchestrator_queue_triage.py \
  --format markdown \
  --replayable-jsonl .orchestrator/backups/event-queue.replay-candidates-20260503.jsonl
```

Observed on 2026-05-03:

```text
Backup events: 115
Active events: 2
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

The generated replay candidate file is intentionally empty:

```text
.orchestrator/backups/event-queue.replay-candidates-20260503.jsonl
```

## Policy

Do not bulk append the backup into the active queue.

The queue backup contains supervisor delivery attempts, not canonical product
truth. Many events point at coordination artifacts that still exist, but the
presence of a `.coordination` file does not prove that replay is still valid.
Some payloads are terminal, some were already superseded by later review
packets, and some refer to cross-repo frontend states that need fresh source
commit checks before a worker should run.

Only replay an item when all of these are true:

- The packet is still non-terminal in the current coordination truth.
- The target repo/source commit still exists and still matches the payload.
- The intended next worker kind is still useful after the blueprint execution
  wave.
- The target provider is auto-ready according to
  `python3 .orchestrator/doctor.py --json --no-write`.
- The replay is created as a fresh event or fresh coordination publication, not
  by copying the old backup line verbatim.

## Manual Review Buckets

`manual_review_required_coordination_artifact` means the old event still has a
matching `.coordination/requests` or `.coordination/responses` file. Review the
artifact and its latest paired review/response before deciding whether to
republish.

`manual_review_required_open_payload` currently applies to old open-looking
payloads such as `KW-01-institutional-memory` and
`PKT-003-post-incident-review`. These are not automatic replay candidates. They
need a current source/target repo check and a fresh next-step decision.

`do_not_replay_payload_terminal` means the payload itself carries a closed or
complete disposition.

`do_not_replay_chair_review` and `do_not_replay_non_coordination` are runtime
snapshots from past supervisor loops. They are not product coordination work.

## Safe Replay Procedure

1. Run the triage script and confirm `Replay candidates: 0` remains true:

   ```bash
   python3 scripts/orchestrator_queue_triage.py --format markdown
   ```

2. Pick one feature ID and inspect its current coordination truth:

   ```bash
   rg -n "<feature-id>" .coordination docs/pantheon-feedback docs/pantheon-handoffs
   ```

3. If the packet still needs work, republish or touch the specific current
   coordination artifact so the watcher emits a fresh event after the local
   guard is removed. Prefer a new source commit/reference over the old queued
   payload.

4. Re-enable the local modes only after the execution-only guard is no longer
   needed. See `docs/operations/orchestrator-execution-queue-isolation.md`.

5. Run a supervisor dry-run/once pass and confirm only the intended fresh event
   appears:

   ```bash
   python3 .orchestrator/supervisor.py --once --no-watch --verbose
   jq -r '.reason' .orchestrator/event-queue.jsonl | sort | uniq -c
   ```

## Hard Stops

- Do not copy the backup over `.orchestrator/event-queue.jsonl`.
- Do not append all 115 backup lines.
- Do not replay stale `coordination:ui-done` events without checking the target
  frontend repository commit.
- Do not enable GitHub bus and coordination replay in the same step; restore one
  runtime surface at a time so failures are attributable.
