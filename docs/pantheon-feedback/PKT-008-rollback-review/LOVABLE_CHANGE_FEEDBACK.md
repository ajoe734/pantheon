# PKT-008 Governance Rollback Review Lovable Change Feedback

Reviewed the published PKT-008 front-end bundle at commit `73d2b83549564e22cdd1b462a3fe5601db675071` against the mirrored handoff packet.

## Outcome

Pantheon review result: ready for review handoff.

The Governance Rollback Review screen is now implemented as a governance-specific route that reads the BFF-composed rollback review payload through the shared client, renders rollback scope and position impact directly from backend-shaped fields, and preserves backend-owned approval/rejection authority without reconstructing rollback state in the browser.

## Verified Against Pantheon

- `GET /api/v1/operator/rollback-review/{rollback_id}` is consumed through `operatorApi.getRollbackReview()` in the shared BFF client.
- `POST /api/v1/operator/commands` approval and rejection are submitted through `operatorApi.approveRollback()` and `operatorApi.rejectRollback()` with the published `ApproveRollback` and `RejectRollback` envelopes.
- No raw `fetch()` calls were added inside `GovernanceRollbackReview.tsx`.
- The screen validates `allowedActions.canApproveRollback` and `allowedActions.canRejectRollback` before rendering action controls; missing fields surface a contract-gap alert instead of a client fallback.
- The position impact table renders only the backend-supplied `position_impact[]` rows. No client-side impact derivation from bindings or telemetry fields was added.
- The affected bindings panel renders only the backend-supplied `affected_bindings[]` rows.
- When `meta.surfaces.position_data` is `degraded` or `unavailable`, the Approve CTA is disabled and the position impact table stays visible in read-only mode with stale-data messaging.
- The trigger evidence drawer opens from user interaction and renders `trigger_evidence` fields only.
- A screen-level degradation alert appears when any rollback review surface is `degraded` or `unavailable`.

## Notes

- The new route is exposed at `/governance-rollback-review?rollback=<rollback_id>` and is linked from the sidebar for manual governance review flows.
- Approval and rejection both require operator-entered rationale, which is reused for `audit_context.reason` and the command-specific notes/reason fields in the outgoing payload.
- Missing required response fields are surfaced as an explicit contract-gap state that directs the operator to emit a `bff-gap` handoff instead of rendering inferred content.

## Pantheon Follow-up

- Review the new screen against the mirrored PKT-008 contract and example payload.
- Run live BFF verification once a rollback-review endpoint is available in the target environment.
- Confirm the returned command envelopes and degraded-surface behavior with production RBAC and operator workflows.
