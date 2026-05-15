# Review: APP-003-PKT001-BFF-ALIGN-001

**Reviewer:** Claude2
**Date:** 2026-04-22
**Outcome:** Approved

## Acceptance Check

1. **`GET /api/v1/operator/deployment-plans` matches the published PKT-001 list contract** ✅
   `services/control-plane/bff/main.py:6574-6642` defines the route. The
   per-item projection at `_pkt001_plan_list_item`
   (`services/control-plane/bff/main.py:6492-6508`) emits exactly the contract
   fields `plan_id`, `artifact_id`, `target_stage`, `risk_level`,
   `governance_outcome`, and `submitted_at`. The list-level envelope returns
   `page_info.next_page_token`, `meta.snapshot_at`, and
   `meta.surfaces.{deployment_plans, allowedActions}`, with
   `meta.degradation` populated whenever either surface is not `ok`. Status
   filter accepts the comma-separated `pending_review,approved,rejected`
   tokens defined by the contract.
   Contract tests
   `services/control-plane/bff/test_pkt001_deployment_review_console_contract.py`
   pin the happy-path projection, paging + status filter, the unavailable-
   surfaces honest-mode response (`disable_ctas: true`), and the detail
   `allowedActions` shape.

2. **PKT-001 contract and closeout records agree on the SSE boundary** ✅
   - `docs/bff/PKT-001-deployment-review-console.md:93-94` records
     `/api/v1/runtime/{runtime_id}/events/stream` as a PKT-005 SSE substrate,
     not a new PKT-001 snapshot route.
   - `.coordination/responses/PKT-001-deployment-review-frontend-feedback.yaml`
     captures `decision: approved_pkt005_cross_cut`, `blocking: false`.
   - `.coordination/responses/PKT-001-deployment-review-backend-delivery.yaml`
     and `docs/pantheon-delivery/PKT-001-deployment-review/DELIVERY_NOTE.md`
     state Pantheon now serves the published list route and records runtime
     SSE truthfully as the approved PKT-005 cross-cut.
   - `docs/pantheon-feedback/PKT-001-deployment-review/API_GAP_REQUESTS.json`
     declares `status: no_open_gaps` with the PKT-005 cross-cut
     acknowledgement.
   - `docs/pantheon-feedback/PKT-001-deployment-review/LOVABLE_CHANGE_FEEDBACK.md`
     and `docs/pantheon-handoffs/PKT-001-deployment-review/FRONTEND_CHANGE_SPEC.md:107-119`
     repeat the same boundary statement.

3. **PKT-001 deployment-review is no longer an unmaterialized follow-up** ✅
   The Pantheon-owned BFF/contract gap is the work of this task. The remaining
   front-owned publication-replay residual is materialized in `ai-status.json`
   as `APP-003-PKT001-PUBLICATION-REPLAY-001` (status `todo`, `depends_on:
   APP-003-PKT001-BFF-ALIGN-001`). It is supervisor-tracked rather than a
   floating `current-work` follow-up note.

## Review Notes

Static review only — local environment lacks `fastapi` / `pytest`, so I did
not re-execute the contract test suite. The committed test file already
asserts the per-item field set, paging, status filter, and degradation
surfaces, and the route implementation reads cleanly against the contract.
The frontend-feedback yaml and delivery note both retain the truthful
`follow_up` framing on the front-owned replay residual rather than overstating
the closure, which keeps `current-work` honest while letting this aligns task
close.

## Follow-up

- `APP-003-PKT001-PUBLICATION-REPLAY-001` remains the residual closure path
  for the front-owned replay-clean republish.
