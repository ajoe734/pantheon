# LOOP-PROD-AUDIT-ARCHIVE-INCIDENT-001 — Archive claim discrepancy; no repair

Status: **incident only; repair is not authorized**.

The prior draft packet on PR #3677 pinned a `47d…` archive and `b16…` bad row
from a #3652 comment. Independent review did not find that object in the
authoritative current status root. The visible object at the stated path is
instead pinned by this contract and passes strict gzip plus JSONL parsing,
including line 8004.

The machine-authoritative [contract](fixtures/archive-audit-archive-incident.v1.json)
contains the complete digest-bound source evidence and admission boundary.

## Consequence

There is no archive parser repair to perform on this source. In particular, no
one may quarantine/replace/recompress it, recover or clear an outbox, replay
the `LOOP-PROD-RUNTIME-BOOT-001` handoff, or mutate status/task/dependency/actor
records under this packet. The original #3652 failure needs a fresh,
reproducible source pin if it remains real.

The active `ai-activity-log.jsonl` is not an immutable artifact. This contract
therefore does not pin its SHA or line count. Any observation must open a
read-only bounded snapshot and parse it strictly. A valid snapshot produces a
timestamped receipt only; an invalid snapshot fails closed and requires a new,
separately pinned incident. Neither result authorizes repair here.

## Observation-only admission

If another read-only reproduction is needed, it requires two distinct approvals
(Human/Ops and an independent runtime reviewer), supervisor verification, one
clean worktree/run, and a scratch-only `PANTHEON_STATUS_ROOT`. Normal
`ai_status` and normal outbox recovery are prohibited. This packet does not
install that authority and does not launch a worker.
