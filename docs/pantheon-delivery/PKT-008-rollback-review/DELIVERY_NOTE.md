# PKT-008 Governance Rollback Review Backend Delivery Note

## Status

`loop-complete`

## Summary

Pantheon reviewed the returned Governance Rollback Review UI cycle from
`ajoe734/front-ai-trading-system` against the published PKT-008 contract,
example payload, mirrored request pair, sibling front checkout, and the local
Pantheon BFF app.

The previous PKT-008 blockers are now closed:

- Pantheon serves `GET /api/v1/operator/rollback-review/{rollback_id}`
- Pantheon accepts the published `ApproveRollback` and `RejectRollback`
  envelopes on `POST /api/v1/operator/commands`
- the front request pair is replayable from transport commit
  `73d2b83549564e22cdd1b462a3fe5601db675071`
- the front metadata follow-up commit
  `bfb87a995d3360aef5ab42d32d25fb6d2ae0328a` truthfully points
  `frontend-feedback.source_commit` back to that transport commit

Pantheon therefore closes the current Lovable loop for
`PKT-008-rollback-review`.

## Verified Contract Alignment

- `GovernanceRollbackReview.tsx` reads the screen through the shared BFF client
  and does not add raw component-level network calls.
- The page renders rollback identity, scope summary, `position_impact[]`,
  `affected_bindings[]`, and `trigger_evidence` directly from backend-shaped
  fields.
- Approval and rejection remain gated by `allowedActions` only.
- The Approve CTA remains disabled whenever
  `meta.surfaces.position_data` is `degraded` or `unavailable`, regardless of
  `allowedActions.canApproveRollback`.
- Stale position rows render the published unknown-impact copy when
  `position_data_stale` is `true`.
- Approval and rejection submit only the published `ApproveRollback` and
  `RejectRollback` envelopes on the existing operator command surface.

## Replayable Transport

The sibling front repo now publishes the reviewed request pair through two
consecutive commits:

- `73d2b83549564e22cdd1b462a3fe5601db675071`
  - first Git-visible commit that contains the PKT-008 request pair, feedback
    bundle, and integrated rollback review UI files
- `bfb87a995d3360aef5ab42d32d25fb6d2ae0328a`
  - metadata follow-up commit that updates
    `.coordination/requests/PKT-008-rollback-review-frontend-feedback.yaml`
    to advertise `source_commit: 73d2b83549564e22cdd1b462a3fe5601db675071`

Pantheon verified that transport commit
`73d2b83549564e22cdd1b462a3fe5601db675071` contains:

- `.coordination/requests/PKT-008-rollback-review-ui-done.yaml`
- `.coordination/requests/PKT-008-rollback-review-frontend-feedback.yaml`
- `docs/pantheon-feedback/PKT-008-rollback-review/`
- `src/App.tsx`
- `src/components/AppSidebar.tsx`
- `src/lib/bffClient.ts`
- `src/pages/governance/GovernanceRollbackReview.tsx`
- `src/pages/governance/types.ts`

## Verification Performed

- Reviewed the mirrored Pantheon request artifacts:
  - `.coordination/requests/PKT-008-rollback-review-ui-done.yaml`
  - `.coordination/requests/PKT-008-rollback-review-frontend-feedback.yaml`
- Re-checked the canonical packet:
  - `docs/bff/PKT-008-rollback-review.md`
  - `docs/screens/PKT-008-rollback-review.md`
  - `docs/examples/PKT-008-rollback-review.json`
  - `docs/pantheon-handoffs/PKT-008-rollback-review/FRONTEND_CHANGE_SPEC.md`
- Verified replayability with Git object lookup against:
  - transport commit `73d2b83549564e22cdd1b462a3fe5601db675071`
  - metadata commit `bfb87a995d3360aef5ab42d32d25fb6d2ae0328a`
- Re-reviewed the sibling front implementation:
  - `../front-ai-trading-system/src/App.tsx`
  - `../front-ai-trading-system/src/components/AppSidebar.tsx`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
  - `../front-ai-trading-system/src/pages/governance/GovernanceRollbackReview.tsx`
  - `../front-ai-trading-system/src/pages/governance/types.ts`
- Ran targeted Pantheon verification:
  - `pytest services/control-plane/bff/test_command_executor.py services/control-plane/bff/test_pkt008_rollback_review_contract.py -q`
  - Result: `14 passed`
  - `python3 services/control-plane/bff/smoke_test.py`
  - Result: `23` smoke tests passed, including rollback review read plus
    approve/reject command acceptance
- Ran sibling front repo validation:
  - `npm run build`
  - Result: passed

## Residual Risk

- No live browser QA against a deployed Pantheon environment was performed in
  this closure step.
- `services/control-plane/bff/smoke_test.py` still emits existing Pydantic v2
  deprecation warnings for `.dict()`.
- The front production build still reports a large Vite chunk-size warning.
  Neither warning blocks PKT-008 contract acceptance.
