# PKT-001 Deployment Review Review Packet

## Date

2026-04-24

## Reviewer

Codex3

## Findings

### 1. Closeout: the returned PKT-001 request pair is now replay-clean and pins an immutable reviewed snapshot

- `git ls-remote --heads origin pkt-004-detail-fix` resolves to publish commit `139081f0e4d516494819003bd95968ecb9b86c99`.
- Both published request files,
  `.coordination/requests/PKT-001-deployment-review-ui-done.yaml` and
  `.coordination/requests/PKT-001-deployment-review-frontend-feedback.yaml`,
  now pin `source_commit` to reviewed UI transport commit
  `c94f63082eae1667ed919353d62c85180d7bafba`.
- The targeted diff from
  `c94f63082eae1667ed919353d62c85180d7bafba` to
  `139081f0e4d516494819003bd95968ecb9b86c99` over the PKT-001 paths touches
  only the two request files plus
  `docs/pantheon-feedback/PKT-001-deployment-review/API_GAP_REQUESTS.json`
  and `LOVABLE_CHANGE_FEEDBACK.md`, so the reviewed UI transport snapshot
  itself remains unchanged.

Impact:

- Pantheon can re-review this cycle against a truthful, immutable UI snapshot
  instead of a workspace-only refresh.
- The earlier publication-truth blocker is resolved.

### 2. Closeout: the list and detail readers now fail closed on the required PKT-001 `meta.surfaces` key sets

- `DeploymentReviewConsole.tsx:95-142` now calls
  `findMissingSurfaceFields()` with required list keys
  `deployment_plans` and `allowedActions`.
- `DeploymentPlanDetail.tsx:58-66` defines the required detail surface keys,
  and `DeploymentPlanDetail.tsx:140-223` now routes `response.meta` through
  `findMissingSurfaceFields()` for `deployment_plan`, `approval_decision`,
  `allowedActions`, `latestRun`, `review`, and `runtime_binding`.
- `docs/pantheon-feedback/PKT-001-deployment-review/UI_DECISIONS.md` now
  documents the mounted `/operator/deployment-review` route instead of the
  stale `/deployment-review` wording.

Impact:

- Partial `meta.surfaces` payloads now fall back to the explicit contract-gap
  state instead of being treated as complete.
- The remaining front-owned PKT-001 blocker identified in the prior review is
  resolved.

## Confirmed Positives

- `python3 -m pytest -q services/control-plane/bff/test_pkt001_deployment_review_console_contract.py`
  returned `3 passed in 2.38s` on 2026-04-24.
- `./node_modules/.bin/tsc --noEmit --pretty false` passed in the sibling
  `front-ai-trading-system` workspace.
- Targeted eslint passed for
  `src/pages/operator/DeploymentReviewConsole.tsx` and
  `src/pages/operator/DeploymentPlanDetail.tsx`.
- The reviewed UI still uses `operatorApi.listDeploymentPlans()`,
  `operatorApi.getDeploymentReview()`, and `operatorApi.sendCommand()` for
  snapshot and command traffic; no raw fetch path or invented PKT-001
  endpoint was introduced.
- `DeploymentReviewConsole.tsx:311-367` still opens the PKT-005 runtime SSE
  stream only after `runtime_binding.id` is known and uses accepted events
  only to refresh snapshots, not to replace snapshot truth or widen the
  PKT-001 route family.

## Decision

Close.

The returned PKT-001 follow-up bundle is now replay-clean, fail-closed on the
required list/detail `meta.surfaces` key sets, and still aligned to the
published Pantheon contract and PKT-005 SSE boundary. No front-owned PKT-001
closeout blocker remains for this packet.
