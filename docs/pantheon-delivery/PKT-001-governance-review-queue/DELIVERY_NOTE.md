# PKT-001 Governance Review Queue Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon aligned the blocking PKT-001 governance review queue read surface to
the published contract. `GET /api/v1/operator/governance/review-queue` now
serves the canonical `items/page_info/meta` envelope expected by the Governance
Review Queue UI, including the backend-authoritative
`allowedActions.canForwardToApproval`, `allowedActions.canRequestChanges`,
`allowedActions.canEscalate`, and `meta.surfaces` fields that triggered the
original `bff-gap`.

This resolves the Pantheon-owned runtime blocker in
`.coordination/requests/PKT-001-governance-review-queue-needs-runtime.yaml`.
Lovable can resume the Governance Review Queue UI cycle against the published
contract and example payload without inventing a new endpoint or shadow state.

## BFF Changes Delivered

### `GET /api/v1/operator/governance/review-queue`

- Returns top-level `items`
- Returns `page_info.next_page_token`
- Returns `meta.snapshot_at`
- Returns `meta.surfaces.review_queue`
- Returns `meta.surfaces.allowedActions`
- Accepts `item_type`, `risk_level`, `status`, `page_token`, and `page_size`
  query params
- Projects review-queue items to the published contract fields:
  - `item_id`
  - `item_type`
  - `risk_level`
  - `submitted_at`
  - `submitted_by`
  - `governance_outcome`
  - `allowedActions`
  - `review_summary`

### Surface behavior

- When the backend-owned read surface is unavailable and no local fallback is
  allowed, the route stays honest by returning `items: []` with
  `meta.surfaces.review_queue.status = unavailable` and the matching
  `allowedActions` surface marked unavailable.
- When Pantheon serves the seeded local snapshot fallback, the route still
  returns queue rows while marking `review_queue` and `allowedActions` as
  `degraded` with `source: local_snapshot`.
- `POST /api/v1/operator/commands` remains the only routing write surface for
  the queue drawer.

## Files Updated

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/smoke_test.py`
- `services/control-plane/bff/test_pkt001_governance_review_queue_contract.py`

## Verification

Pantheon re-ran targeted governance queue verification after the route landed:

- `pytest -q services/control-plane/bff/test_pkt001_governance_review_queue_contract.py`
- `pytest -q services/control-plane/bff/smoke_test.py -k 'governance_review_queue_composed_view or rollback_review_composed_view or deployment_review_composed_view'`

Both completed successfully.

## Follow-up

- Republish the contract-ready and Lovable task packets for the resumed UI cycle
- Resume the Governance Review Queue implementation in
  `front-ai-trading-system` against the corrected read route
- Republish `frontend-feedback` and `ui-done` from one truthful Git-visible
  front commit after the next real implementation pass
- If a new live divergence is discovered during the resumed UI cycle, emit a
  fresh `bff-gap` handoff instead of inventing local state
