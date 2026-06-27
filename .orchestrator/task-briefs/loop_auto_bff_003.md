# Task Brief: LOOP-AUTO-BFF-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Label seed snapshot registry scheduled and live truth
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Auto-reassigned LOOP-AUTO-BFF-003 away from unavailable lane Codex2 (disabled, sidecar-only, or auth-down); owner Codex2 -> Codex.

## Summary
在 operator panels 明確標示 seed、fixture、snapshot、registry、scheduled、live truth，避免 demo fixture 被看成真實 loop。

## Implementation Notes
- BFF `/bff/v5/loop-health` now carries operator-facing truth labels and an
  `operator_truth` packet field.
- Management frontend now renders a Loop Truth panel backed by
  `managementClient.loopHealth`.
- Snapshot fallback remains visible as snapshot truth and is not accepted as
  live proof.
