# Task Brief: AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-24

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare AG-FE-DB-002 acceptance packet and dependency map
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Prepare support-only follow-up 24 packet and hand off to Claude for review.

## Summary
平行支援 AG-FE-DB-002，整理 acceptance checklist、dependency map 與 support packet，不改 canonical truth。

## Scope

- Helper parent: `AG-FE-DB-002`
- Helper kind: `acceptance_packet`
- Artifact: `support/sidecars/AG-FE-DB-002/AG-FE-DB-002-SIDECAR-ACCEPTANCE-FOLLOWUP-24.md`
- Allowed changes: support artifact and this task brief only
- Not allowed: L1 canonical truth, core contract truth, runtime, registry, BFF,
  governance, routing, or parent task implementation

## Current dispatch context

- Supervisor status shows this task active `in_progress`, owner `Codex`,
  reviewer `Claude`.
- Parent `AG-FE-DB-002` remains active `blocked`, owner `Claude`, reviewer
  `Claude2`, `waiting_for` `Codex`.
- Follow-up 23 is archived `done` and approved the refined blocker:
  `AG-FE-DB-002` should wait for cross-repo delivery/sync of the reviewed
  `AG-FE-DB-001` frontend compose surface into the active `execute-plans`
  base rather than waiting for Agora v1.3.
