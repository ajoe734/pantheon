# PKT-010 Runtime State Board Review Packet

## Date

2026-04-19

## Reviewer

Codex

## Findings

### 1. High: the current front request pair is still not replay-clean from one immutable commit

- The front repo currently publishes the PKT-010 request pair at
  `../front-ai-trading-system/.coordination/requests/PKT-010-runtime-state-board-ui-done.yaml:1`
  and
  `../front-ai-trading-system/.coordination/requests/PKT-010-runtime-state-board-frontend-feedback.yaml:1`
  from front HEAD `2779d237736b6a1d02ef0e4a4c4f54a7983bb70c`.
- Both payloads now advertise
  `source_commit: be42f22c2388076af4bb7b1f1d4209aaf90af6a8`.
- That advertised commit exists and its tree contains the reviewed PKT-010 UI
  files plus the feedback bundle, but it does not contain the current request
  bodies that point at `be42f22...`; those request-body edits landed later in
  `2779d237736b6a1d02ef0e4a4c4f54a7983bb70c`.
- Under Pantheon replay rules, the request pair must be reproducible from the
  exact immutable commit named in the payload. The current pair therefore
  remains self-inconsistent: replaying `be42f22...` reconstructs older request
  YAML that still points at a different transport SHA.
- Impact: Pantheon can review the current front checkout honestly, but it still
  cannot close the coordination loop through a single Git-replayable request
  pair.

### 2. Medium: `rollback_summary.href` still does not have a locked owner-screen contract

- The PKT-010 contract still says the payload cross-links are owner-screen
  navigation targets:
  `docs/bff/PKT-010-runtime-state-board.md:74-76`.
- Pantheon now emits a browser-ready deployment-review link, but
  `rollback_summary.href` is still shaped as the API-looking path
  `/api/v1/runtimes/{runtime_id}/rollbacks` in the current BFF row projector:
  `services/control-plane/bff/main.py:1265-1268`.
- The targeted contract test also locks that rollback href value today:
  `services/control-plane/bff/test_pkt010_runtime_state_board_contract.py:101-105`.
- The reviewed UI correctly renders payload refs verbatim for both deployment
  and rollback links:
  `../front-ai-trading-system/src/pages/operator/OperatorRuntimeStateBoard.tsx:430-438`
  and `../front-ai-trading-system/src/pages/operator/OperatorRuntimeStateBoard.tsx:836-843`.
- Front HEAD `2779d237736b6a1d02ef0e4a4c4f54a7983bb70c` added a React route alias
  for `/api/v1/runtimes/:runtimeId/rollbacks`, but that alias is not part of
  the advertised `source_commit` and does not by itself prove the deployed
  environment will treat an `/api/...` href as an operator-owned browser
  destination.
- Impact: the frontend is still correct to render the rollback link verbatim,
  but Pantheon must either publish an unambiguously browser-owned rollback
  screen href or revise the packet wording to describe the current rollback
  link semantics truthfully.

## Reviewed Artifacts

- Canonical contract and packet docs:
  - `docs/bff/PKT-010-runtime-state-board.md`
  - `docs/examples/PKT-010-runtime-state-board.json`
  - `docs/screens/PKT-010-runtime-state-board.md`
  - `docs/pantheon-handoffs/PKT-010-runtime-state-board/FRONTEND_CHANGE_SPEC.md`
- Returned front-owned request pair:
  - `../front-ai-trading-system/.coordination/requests/PKT-010-runtime-state-board-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-010-runtime-state-board-frontend-feedback.yaml`
- Front feedback bundle:
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-010-runtime-state-board/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-010-runtime-state-board/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-010-runtime-state-board/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-010-runtime-state-board/QA_STATUS.md`
- Front implementation:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
  - `../front-ai-trading-system/src/components/WorkbenchBreadcrumb.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/operator/OperatorRuntimeStateBoard.tsx`
  - `../front-ai-trading-system/src/pages/operator/types.ts`
- Pantheon BFF implementation and tests:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_pkt010_runtime_state_board_contract.py`

## Verified Positives

- The screen still reads through `operatorApi.getRuntimeStateBoard()` and does
  not add component-level raw fetches.
- The UI keeps deployment-stage and status filtering, sort order, and
  `next_page_token` pagination server-backed.
- The unavailable-board branch is explicit and suppresses the roster when
  `meta.surfaces.runtime_state = unavailable`.
- The current sibling front production build passed:
  - `npm run build`
- Targeted Pantheon verification passed:
  - `python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q`
  - Result: `3 passed`
- Direct probing of the current Pantheon workspace route returned `200 OK` for
  `GET /api/v1/operator/runtime-state` and showed truthful degraded behavior:
  - `meta.surfaces.runtime_state.status = degraded`
  - `meta.surfaces.rollback_history.status = degraded`
- The explicit unavailable runtime-state branch remains covered by the targeted
  PKT-010 contract test even though the current workspace dataset did not
  surface that branch during direct probing.

## Decision

`PKT-010-runtime-state-board` is **follow-up required**.

The primary read model is live and the reviewed UI remains aligned with the
single-roster PKT-010 contract. The loop stays open for two reasons only:

- the front-owned request pair still is not replayable from the exact
  immutable commit it advertises
- Pantheon still has not locked truthful rollback-link semantics for
  `rollback_summary.href`

## Required Follow-up

1. Front repo: republish the canonical `ui-done` and `frontend-feedback` pair
   from one immutable Git commit whose tree contains:
   - the PKT-010 request pair exactly as published
   - the PKT-010 feedback bundle
   - the reviewed PKT-010 UI files
   - any route-alias changes Pantheon expects to rely on for rollback
     navigation
2. Front repo: set both request payloads' `source_commit` to that exact final
   publication commit SHA, not to an earlier transport ancestor.
3. Pantheon: either publish an unambiguously browser-owned
   `rollback_summary.href` target or revise the PKT-010 contract and example
   payload so the rollback link is described truthfully as an API-looking alias
   with deployment guarantees.
4. Front repo: continue rendering payload-owned hrefs verbatim; do not invent
   alternate rollback routes in the screen itself.

## 2026-04-19 Closeout Addendum

Pantheon re-verified the PKT-010 packet after the route-alias bundle landed in
the front repo.

- The front repo publishes the canonical request pair from
  `be42f22c2388076af4bb7b1f1d4209aaf90af6a8`.
- The current router now exposes the owner-link destinations Pantheon expects
  to rely on, including `/api/v1/runtimes/:runtimeId/rollbacks`,
  `/governance-review-queue`, and `/governance-approval-queue`.
- Pantheon's PKT-010 contract remains green:
  `python3 -m pytest services/control-plane/bff/test_pkt010_runtime_state_board_contract.py -q`
  -> `3 passed`.

## Final Decision

**APPROVED.**

Live degraded-surface browser QA remains a non-blocking residual risk only.
