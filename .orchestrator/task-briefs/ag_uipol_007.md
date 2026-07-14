# Task Brief: AG-UIPOL-007

This task-scoped brief records the approved delivery and owner closeout. The
terminal orchestrator status is written only after the Pantheon closeout PR is
merged into `dev`.

## Task

- Title: Trading Room multi-lens monitoring and candidate parity
- Status: review_approved (owner closeout in progress)
- Owner: Codex
- Reviewer: Claude
- Review artifact: `support/reviews/AG-UIPOL-007-review-claude.md`
- Matrix coverage: TR-01–TR-09

## Delivery

Frontend source remains in `ajoe734/execute-plans`; no frontend source is
materialized in Pantheon.

- PR #319, merge `26cef514493d8d1cbb7137d240a66fda1b02a8b1`: initial
  Strategy Lens switcher, five dashboard recipes, candidate board, and review
  drawer.
- PR #320, merge `2fb8b36e17b9d0de80c036045c841dcbdb02cc9b`: i18n,
  Candidate Pool read integration, honest sample fallback, dynamic workspace
  handoff, and drawer keyboard/focus behavior.
- PR #322, merge `3ce439d8713dcb437673bf7b81df78cb917d8082`: reviewer
  findings, recipe sample disclosure, delayed-data/lens-column coverage, and
  composition with the current AG-UIPOL-008 Winner Branch workspace.
- PR #341, merge `36a2f9292eadccd32b1fd79db2e7820ce750a984`: canonical
  `full | partial | missing` availability normalization at the frontend BFF
  response boundary, preventing proposal/workspace rendering failures without
  changing the Pantheon contract.

## Acceptance evidence

- Claude's round-4 verdict is **Approved**.
- Focused component, i18n, typecheck, targeted lint, and safe live/strict
  production-build results are recorded in the hosted evidence artifact.
- The final hosted deployment identity and desktop/narrow browser captures will
  be recorded in
  `docs/bff/execution-tasks/2026-07-13-agora-ui-polish/evidence/AG-UIPOL-007-hosted-evidence.md`.

## Truth and authority boundary

- Dashboard recipes are explicitly labelled sample-only; they are not claimed
  as live market telemetry.
- Candidate Pool failure/empty responses remain visible as an explicit sample
  fallback, never as live data.
- Drawer lifecycle controls update the visible review state only. This task
  does not claim a persisted governed Candidate Pool mutation.
- No direct order, broker, capital-binding, or runtime-binding action is added.
- AG-UIPOL-008 continues to own Winner Branch workspace information and
  AG-UIPOL-011 owns the final cross-surface narrow gate.
