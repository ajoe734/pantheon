# BP6-LUV-019 Review

## Date

2026-04-17

## Reviewer

Codex

## Findings

No blocking findings.

## Verified Closure Evidence

- Pantheon now serves the published PKT-008 read surface at
  `GET /api/v1/operator/rollback-review/{rollback_id}` in
  `services/control-plane/bff/main.py`.
- Pantheon now accepts the published rollback command envelopes on
  `POST /api/v1/operator/commands`; `ApproveRollback` and
  `RejectRollback` are registered in both `services/control-plane/bff/models.py`
  and `services/control-plane/bff/command_executor.py`.
- The mirrored `frontend-feedback` request is present in Pantheon at
  `.coordination/requests/PKT-008-rollback-review-frontend-feedback.yaml` and
  points `source_commit` to the replayable front transport commit
  `73d2b83549564e22cdd1b462a3fe5601db675071`.
- The sibling front repo transport commit
  `73d2b83549564e22cdd1b462a3fe5601db675071` contains the canonical request
  pair, the PKT-008 feedback bundle, and the integrated UI files:
  - `.coordination/requests/PKT-008-rollback-review-frontend-feedback.yaml`
  - `.coordination/requests/PKT-008-rollback-review-ui-done.yaml`
  - `docs/pantheon-feedback/PKT-008-rollback-review/`
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/governance/GovernanceRollbackReview.tsx`
  - `src/pages/governance/types.ts`
- The sibling front metadata follow-up commit
  `bfb87a995d3360aef5ab42d32d25fb6d2ae0328a` truthfully points the
  `frontend-feedback.source_commit` field back to transport commit
  `73d2b83549564e22cdd1b462a3fe5601db675071`.
- The reviewed UI remains aligned to the published PKT-008 contract and example
  payload:
  - the page reads only `position_impact[]`, `affected_bindings[]`,
    `trigger_evidence`, and backend-shaped `allowedActions`
  - the page disables Approve whenever `meta.surfaces.position_data` is
    `degraded` or `unavailable`
  - the page renders stale row messaging when `position_data_stale = true`
  - approval and rejection submit the published `ApproveRollback` and
    `RejectRollback` envelopes through the shared BFF client

## Verification Performed

- Reviewed the published Pantheon packet:
  - `docs/bff/PKT-008-rollback-review.md`
  - `docs/screens/PKT-008-rollback-review.md`
  - `docs/examples/PKT-008-rollback-review.json`
  - `docs/pantheon-handoffs/PKT-008-rollback-review/FRONTEND_CHANGE_SPEC.md`
- Reviewed the Pantheon-mirrored request artifacts:
  - `.coordination/requests/PKT-008-rollback-review-ui-done.yaml`
  - `.coordination/requests/PKT-008-rollback-review-frontend-feedback.yaml`
- Verified front replayability with Git object lookup against:
  - transport commit `73d2b83549564e22cdd1b462a3fe5601db675071`
  - metadata commit `bfb87a995d3360aef5ab42d32d25fb6d2ae0328a`
- Re-reviewed the sibling front implementation under
  `../front-ai-trading-system`:
  - `src/App.tsx`
  - `src/components/AppSidebar.tsx`
  - `src/lib/bffClient.ts`
  - `src/pages/governance/GovernanceRollbackReview.tsx`
  - `src/pages/governance/types.ts`
- Ran targeted Pantheon verification:
  - `pytest services/control-plane/bff/test_command_executor.py services/control-plane/bff/test_pkt008_rollback_review_contract.py -q`
  - Result: `14 passed`
  - `python3 services/control-plane/bff/smoke_test.py`
  - Result: `23` smoke tests passed, including rollback review read and
    approve/reject command acceptance
- Ran sibling front validation:
  - `npm run build`
  - Result: passed

## Decision

`PKT-008-rollback-review` is loop-complete for the current packet scope.

Close `BP6-LUV-019`.

## Residual Risk

- No live browser QA against a deployed Pantheon environment was performed in
  this closure step.
- `services/control-plane/bff/smoke_test.py` still emits existing Pydantic v2
  deprecation warnings for `.dict()`. They do not block PKT-008 contract
  acceptance.
- The front production build still reports a large Vite bundle-size warning.
  That is unrelated to the PKT-008 rollback-review contract and does not block
  this loop closure.
