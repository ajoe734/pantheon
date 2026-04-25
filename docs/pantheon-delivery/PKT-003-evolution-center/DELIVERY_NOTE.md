# PKT-003 Evolution Center — Pantheon Delivery Note

## Status

`delivered`

## Summary

Pantheon re-reviewed the completed `ui-done` and `frontend-feedback` handoffs
for `PKT-003-evolution-center` against the current PKT-003 screen spec, BFF
contract, example payload, the sibling `front-ai-trading-system` source commit
`8314ef67016a15ced808e4aded16cc0686de25a1`, and the live route behavior
exercised locally through FastAPI `TestClient`.

The earlier 2026-04-16 pre-block is no longer current. The Pantheon BFF now
matches the published EV-01 through EV-04 packet contract:

- `GET /api/v1/evolution-decisions` returns `items`, `page_info.next_page_token`,
  and `meta.snapshot_at`
- `GET /api/v1/evolution-decisions/{decision_id}` returns the decision fields at
  the response root with `updated_at`, `notes`, and `meta.snapshot_at`
- `GET /api/v1/freeze-orders` returns `items` with `freeze_order_id`,
  `status`, `scope`, `issued_at`, and `meta.snapshot_at`
- `GET /api/v1/rollbacks` returns `items` with `rollback_id`, `action_type`,
  `runtime_id`, `executed_at`, and `meta.snapshot_at`

No additional Pantheon API gap remains in this loop. The current front-end
implementation stays inside the published contract boundary and does not need a
new Pantheon-owned implementation pass for the current packet scope.

## Verified UI Alignment

- `src/pages/evolution/EvolutionCenter.tsx` loads the three PKT-003 list
  surfaces independently through `operatorApi`, so a degraded or failed panel
  does not block the others.
- `src/pages/evolution/EvolutionDecisionDetail.tsx` uses the canonical EV-02
  detail route and renders the required `updated_at` and `notes` fields.
- The touched evolution files do not add raw `fetch` or `axios` calls; the
  shared BFF client remains the only network boundary.
- The rollback UI intentionally omits the `time_range` control, matching the
  documented v1 no-op contract boundary.
- Missing required response fields degrade into explicit contract-gap states
  instead of inferred or mocked content.

## Verified Pantheon Behavior

- `operator` reads against `GET /api/v1/evolution-decisions` returned `200`
  with the canonical top-level keys `items`, `page_info`, and `meta`
- `viewer` reads against `GET /api/v1/evolution-decisions` returned
  `403 INSUFFICIENT_ROLE`
- `GET /api/v1/freeze-orders` returned `meta.staleness` when
  `BFF_READ_SURFACE_STATE=degraded`, which is the signal the current UI uses to
  raise the non-dismissable stale-read banner

## Verification Performed

- Reviewed the Pantheon-visible handoff requests:
  - `.coordination/requests/PKT-003-evolution-center-ui-done.yaml`
  - `.coordination/requests/PKT-003-evolution-center-frontend-feedback.yaml`
- Reviewed the mirrored review bundle:
  - `docs/pantheon-feedback/PKT-003-evolution-center/LOVABLE_CHANGE_FEEDBACK.md`
  - `docs/pantheon-feedback/PKT-003-evolution-center/API_GAP_REQUESTS.json`
  - `docs/pantheon-feedback/PKT-003-evolution-center/UI_DECISIONS.md`
  - `docs/pantheon-feedback/PKT-003-evolution-center/QA_STATUS.md`
- Re-checked the canonical packet artifacts:
  - `docs/screens/PKT-003-evolution-center.md`
  - `docs/bff/PKT-003-evolution-center.md`
  - `docs/examples/PKT-003-evolution-center.json`
  - `docs/pantheon-handoffs/PKT-003-evolution-center/FRONTEND_CHANGE_SPEC.md`
- Re-reviewed the sibling front repo implementation:
  - `../front-ai-trading-system/src/pages/evolution/Center.tsx`
  - `../front-ai-trading-system/src/pages/evolution/EvolutionCenter.tsx`
  - `../front-ai-trading-system/src/pages/evolution/EvolutionDecisionDetail.tsx`
  - `../front-ai-trading-system/src/pages/evolution/types.ts`
  - `../front-ai-trading-system/src/lib/bffClient.ts`
- Ran Pantheon-side contract acceptance:
  - `pytest -q services/control-plane/bff/test_evolution_center_contract.py`
  - Result: passed
- Ran a targeted Pantheon runtime probe with seeded read-store data:
  - operator request returned `200`
  - viewer request returned `403 INSUFFICIENT_ROLE`
  - degraded read-state returned `meta.staleness`
- Rebuilt the sibling front repo:
  - `npm run build`
  - Result: passed

## Residual Risk

- Remaining risk is runtime-only. This follow-up did not include live browser QA
  against a running Pantheon deployment, so deployed RBAC, real pagination
  volume, and production-shaped stale metadata were not exercised end-to-end in
  a browser session.

---
{
  "feature_id": "PKT-003-evolution-center",
  "status": "delivered",
  "backend_commit": "f2ad42f3fb993a488526bf71059141b9a7bd933e",
  "bff_contract_version": "pantheon-bff@f2ad42f3fb993a488526bf71059141b9a7bd933e",
  "source_payload": ".coordination/requests/PKT-003-evolution-center-frontend-feedback.yaml",
  "reviewed_front_source_commit": "8314ef67016a15ced808e4aded16cc0686de25a1",
  "endpoints": [
    "GET /api/v1/evolution-decisions",
    "GET /api/v1/evolution-decisions/{decision_id}",
    "GET /api/v1/freeze-orders",
    "GET /api/v1/rollbacks"
  ],
  "contract_paths": [
    "docs/bff/PKT-003-evolution-center.md",
    "docs/examples/PKT-003-evolution-center.json",
    "docs/screens/PKT-003-evolution-center.md"
  ],
  "followup_scope": [
    "no additional Pantheon API or contract work is required for the current PKT-003 Evolution Center packet",
    "non-blocking live QA against a running Pantheon deployment may proceed without changing the published contract",
    "if a new live divergence is discovered later, publish a fresh bff-gap instead of inventing local state"
  ]
}
