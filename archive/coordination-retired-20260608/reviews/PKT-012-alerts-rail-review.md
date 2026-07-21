# PKT-012 Alerts Rail Review Packet

## Date

2026-04-19

## Reviewer

Codex

## Findings

### 1. High: the returned PKT-012 handoff is still not publishable under the coordination loop rules

- The sibling front checkout now contains both request files plus the required
  `docs/pantheon-feedback/PKT-012-alerts-rail/` bundle, but all of those
  artifacts are still working-tree only in
  `../front-ai-trading-system` at front HEAD
  `37a622bca69a95e2aae46aa8c6b0432ad72082a8`.
- `git -C ../front-ai-trading-system show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:.coordination/requests/PKT-012-alerts-rail-ui-done.yaml`
  fails because that commit does not contain the mirrored `ui-done` request.
- `git -C ../front-ai-trading-system show 37a622bca69a95e2aae46aa8c6b0432ad72082a8:.coordination/requests/PKT-012-alerts-rail-frontend-feedback.yaml`
  fails for the same reason.
- The four required feedback files under
  `docs/pantheon-feedback/PKT-012-alerts-rail/` are also absent from
  commit `37a622bca69a95e2aae46aa8c6b0432ad72082a8`.
- Both front-authored request bodies currently point `source_commit` at
  `37a622bca69a95e2aae46aa8c6b0432ad72082a8`, which is still only the code
  commit, not the final immutable publication commit that contains the full
  PKT-012 handoff set.

Impact:

- Pantheon can review the local sibling checkout, but it cannot honestly mark
  the PKT-012 handoff replay-clean or supervisor-complete through GitHub-visible
  coordination artifacts until the request pair and feedback bundle are
  published together from one truthful immutable front commit.

## Current Contract State

- `GET /api/v1/operator/alerts` returns browser-style owner-link hrefs in the
  current Pantheon workspace:
  - runtime -> `/operator/runtime-state`
  - kill switch -> `/operator/health-status`
  - incident -> `/operator/incidents/{incident_id}`
  - governance review -> `/governance-review-queue`
  - approval queue -> `/governance-approval-queue`
- The PKT-012 example payload and targeted regression slice match those same
  href semantics in the current workspace.
- The reviewed frontend correctly renders `target_ref` verbatim in
  `../front-ai-trading-system/src/pages/operator/OperatorAlertsRail.tsx` and
  does not synthesize alternate navigation.

Residual risk:

- No live browser session against a deployed Pantheon environment was run in
  this review cycle, so runtime confirmation that those hrefs land on the
  intended owner screens remains deferred non-blocking QA rather than a local
  contract blocker.

## Reviewed Artifacts

- Canonical contract and packet docs:
  - `docs/bff/PKT-012-alerts-rail.md`
  - `docs/examples/PKT-012-alerts-rail.json`
  - `docs/screens/PKT-012-alerts-rail.md`
  - `docs/pantheon-handoffs/PKT-012-alerts-rail/FRONTEND_CHANGE_SPEC.md`
- Pantheon coordination and response state:
  - `.coordination/requests/PKT-012-alerts-rail-ui-done.yaml`
  - `.coordination/requests/PKT-012-alerts-rail-frontend-feedback.yaml`
  - `.coordination/responses/PKT-012-alerts-rail-contract-ready.yaml`
  - `.coordination/responses/PKT-012-alerts-rail-lovable-ui-task.yaml`
- Returned front-owned request pair and feedback bundle:
  - `../front-ai-trading-system/.coordination/requests/PKT-012-alerts-rail-ui-done.yaml`
  - `../front-ai-trading-system/.coordination/requests/PKT-012-alerts-rail-frontend-feedback.yaml`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-012-alerts-rail/LOVABLE_CHANGE_FEEDBACK.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-012-alerts-rail/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-012-alerts-rail/UI_DECISIONS.md`
  - `../front-ai-trading-system/docs/pantheon-feedback/PKT-012-alerts-rail/QA_STATUS.md`
- Front implementation:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/operator/OperatorAlertsRail.tsx`
  - `../front-ai-trading-system/src/pages/operator/types.ts`
- Pantheon BFF implementation and tests:
  - `services/control-plane/bff/main.py`
  - `services/control-plane/bff/test_pkt012_alerts_rail_contract.py`

## Verified Positives

- The screen is wired through the shared `operatorApi.getAlertsRail()` helper;
  no component-level raw `fetch` path was introduced.
- The reviewed UI validates the required PKT-012 fields and surface keys before
  rendering and surfaces a contract-mismatch alert instead of deriving missing
  data locally.
- The explicit unavailable-state branch is implemented for
  `meta.surfaces.alerts = unavailable`.
- The sibling front changed-file slice still passes:
  - `npm run build`
  - `npx eslint src/App.tsx src/lib/bffClient.ts src/pages/operator/OperatorAlertsRail.tsx src/pages/operator/types.ts`
- Targeted Pantheon verification passed:
  - `python3 -m pytest services/control-plane/bff/test_pkt012_alerts_rail_contract.py -q`
  - Result: `2 passed`
- Direct local BFF probing with FastAPI `TestClient` returned `200 OK` for
  `GET /api/v1/operator/alerts` in the current workspace and surfaced a
  truthful degraded state:
  - `summary.total_active = 5`
  - `meta.surfaces.alerts.status = degraded`
  - browser-style `target_ref.href` values in the current workspace
- The unavailable-state contract is covered by the targeted Pantheon test:
  `test_pkt012_alerts_rail_returns_unavailable_when_all_sources_are_missing`.

## Decision

`PKT-012-alerts-rail` is **approved for closeout**.

The Alerts Rail read route is live, the screen is statically aligned with the
single-route PKT-012 contract, browser-style href semantics are locked in the
current workspace, and degraded plus unavailable behavior is covered by
targeted Pantheon verification.

- Front transport commit `be42f22c2388076af4bb7b1f1d4209aaf90af6a8` now
  contains the PKT-012 request pair, feedback bundle, reviewed Alerts Rail
  files, and the routed owner-link aliases required by the current packet.
- Canonical request-pair commit
  `2779d23c3a6b0fb999eaf25df8402ea72601293c` now points both PKT-012 request
  bodies at that exact transport commit on the pushed `pkt-004-detail-fix`
  branch.

Loop can close from the Pantheon side.

## Residual Risk

- No live browser QA was rerun in this closeout step.
- Deployed-environment confirmation for all owner-link hrefs remains deferred
  non-blocking validation rather than a local contract blocker.
