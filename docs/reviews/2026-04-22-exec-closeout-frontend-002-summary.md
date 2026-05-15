# EXEC-CLOSEOUT-FRONTEND-002 Summary

Date: `2026-04-22`
Owner: `Codex2`
Reviewer: `Codex`
Task: `EXEC-CLOSEOUT-FRONTEND-002`

## Outcome

- `RW-02-search` is now truthfully closed for this cycle. The Pantheon mirror
  request at `.coordination/requests/RW-02-search-ui-done.yaml` already carries
  `status: closed` with `pantheon_disposition: loop_complete`, and the paired
  runtime follow-up at `.coordination/requests/RW-02-search-needs-runtime.yaml`
  is `completed` with fresh live HTTP evidence.
- `TW-01-teaching-dialog` no longer has an open runtime-refresh follow-up. The
  runtime handoff at `.coordination/requests/TW-01-teaching-dialog-needs-runtime.yaml`
  is `completed`, and the Pantheon closeout response at
  `.coordination/responses/TW-01-teaching-dialog-frontend-feedback.yaml`
  records `disposition: approved` plus `can_close: true`.
- `TW-04-teaching-replay` no longer has an open runtime/topology follow-up.
  `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml` is
  `completed`, the route-topology bff-gap is `resolved`, and
  `.coordination/responses/TW-04-teaching-replay-frontend-feedback.yaml`
  records `disposition: approved` plus `can_close: true`.
- `PKT-001-deployment-review` remains a truthful blocker rather than a closed
  loop. `.coordination/responses/PKT-001-deployment-review-frontend-feedback.yaml`
  already records `disposition: follow_up`, while the canonical contract still
  requires `GET /api/v1/operator/deployment-plans` and the active runtime
  evidence still shows that operator-scoped list route missing.

## Evidence

- `python3 -m pytest -q services/control-plane/bff/test_rw02_search_contract.py services/control-plane/bff/test_tw01_teaching_dialog_contract.py services/control-plane/bff/test_tw04_teaching_replay_contract.py`
  -> `43 passed`
- Fresh runtime probe:
  - `GET /api/v1/research/search?q=momentum` -> `200`
  - `GET /api/v1/trainer/sessions?persona_id=persona-alpha&status=active&page_size=2` -> `200`
  - `GET /api/v1/trainer/replay?persona_id=persona-alpha&status=completed` -> `200`
  - `GET /api/v1/trainer/replay/trn-20260418-003` -> `200`
  - `GET /api/v1/operator/deployment-plans` -> `404`
- Closeout records absorbed:
  - `.coordination/requests/RW-02-search-ui-done.yaml`
  - `.coordination/requests/RW-02-search-needs-runtime.yaml`
  - `.coordination/requests/TW-01-teaching-dialog-needs-runtime.yaml`
  - `.coordination/responses/TW-01-teaching-dialog-frontend-feedback.yaml`
  - `.coordination/requests/TW-04-teaching-replay-needs-runtime.yaml`
  - `.coordination/requests/TW-04-teaching-replay-bff-gap.yaml`
  - `.coordination/responses/TW-04-teaching-replay-frontend-feedback.yaml`
  - `.coordination/responses/PKT-001-deployment-review-frontend-feedback.yaml`

## Disposition

- `RW-02-search`: closeout complete for this cycle.
- `TW-01-teaching-dialog`: closeout complete for this cycle.
- `TW-04-teaching-replay`: closeout complete for this cycle.
- `PKT-001-deployment-review`: reopen only as a Pantheon contract/runtime
  blocker until the operator-scoped deployment-plan list route is published and
  the frontend feedback bundle stays truthful about SSE boundary usage.
