# PPLG-007 - Frontend Create Paper Persona And Unified Fleet UX

Priority: P0

Area: Management console frontend

Depends on: `PPLG-002`, `PPLG-003`, `PPLG-005`

## Goal

Update the Persona Registry and Persona Fleet/League UX so user-facing creation
means paper runtime setup, row actions use concrete states, paper/canary/live
personas compete in one view, and live/canary/quarterly review is visibly
human-approved.

## Required Work

- Rename primary create CTA to `建立 Paper Persona`.
- Use paper-launch workflow for primary create.
- Remove or hide normal identity-only create path from the operator happy path.
- Show setup progress, setup failure, retry, and repair.
- Replace generic `啟動精靈` with state-specific row actions.
- Rework the global `研究 / 模擬 / 正式` selector so it controls command-safety
  context and allowed actions, not separate hidden persona datasets.
- Add a unified Persona League/Fleet default view that shows paper challengers,
  canary challengers, and live incumbents in the same cohort ranking.
- Add explicit filters for `competition_track`, `capital_scope`, and
  `review_status` instead of making the global mode selector hide tracks.
- Add paper evaluation, promotion review, canary/live, quarterly review, and
  risk incident states to Fleet.
- Show human review evidence before canary/live/quarterly allocation changes.

## Acceptance Criteria

- Creating through the primary UI reaches `paper_running`/`paper_warming_up` or
  visible `setup_failed`.
- Existing paper-running personas do not show startup wizard.
- Switching between `研究`, `模擬`, and `正式` does not remove paper challengers
  from live incumbent comparison; it only changes available commands and
  default filters.
- Fleet/League default view shows paper challengers, canary challengers, and
  live incumbents with cohort rank and competition track.
- Eligible personas show `送交實盤審核`.
- Pending review rows link to review detail.
- Canary/live rows show capital scope and approval evidence.
- Risk-off/frozen rows link to incident review.
- E2E tests cover create happy path, setup failure retry, mode selector
  semantics, promotion pending, and no canary without approval.

## Artifacts

- Frontend management persona files in the active UI repo/worktree
- Persona Fleet DTO/client code
- E2E tests for management persona flows
