# Task Brief: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare AG-FE-ID-001 BFF and frontend handoff packet
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude
- Next: Review approved; owner closeout commit/PR required before `done`.

## Summary
平行支援 AG-FE-ID-001，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。

## Scope Guard
- Helper parent: `AG-FE-ID-001`
- Helper kind: `bff_handoff_packet`
- Mutates canonical truth: `false`
- Allowed artifacts:
  - `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17.md`
  - `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17-REVIEW.md`
- Do not edit L1 canonical truth, OpenAPI, capability manifests, BFF runtime,
  registry/governance implementation, or execute-plans source.

## Review And Closeout Evidence
- `AI_NAME=Codex2 ./scripts/ai-status.sh show AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17`
  reports this task as active `review_approved`, owner `Codex2`, reviewer
  `Claude`, with review file
  `support/sidecars/AG-FE-ID-001/AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-17-REVIEW.md`.
- Claude approved the support-only packet and returned it to Codex2 for
  owner closeout. The approval keeps the AG-BE-ID-003 session contract blocker
  unchanged, parent `AG-FE-ID-001` as `todo`, and the three frontend target
  files as missing.
- Closeout remains support-only and must go through a task-scoped commit and
  PR before `AI_NAME=Codex2 ./scripts/ai-status.sh done ...`.
- `current-work.md` and the full `ai-activity-log.jsonl` were not read for this
  brief refresh.
