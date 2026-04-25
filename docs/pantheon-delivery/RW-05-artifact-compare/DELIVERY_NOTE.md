# RW-05 Artifact Compare Backend Delivery Note

## Status

`delivered`

## Summary

Pantheon consumed the canonical RW-05 `bff-gap` returned from
`origin/pkt-004-detail-fix` and resolved the only remaining Pantheon-owned
contract mismatch for this cycle.

The live RW-05 route family remains:

- `GET /api/v1/artifacts`
- `GET /api/v1/artifacts/{artifact_id}`
- `GET /api/v1/artifacts/compare`

The resolved gap was narrow and specific:

- `GET /api/v1/artifacts` previously returned summary rows without
  `artifacts[].allowedActions.canCompare`
- the canonical RW-05 handoff and the reviewed frontend selector both require
  backend-owned list-level compare authority

Pantheon now publishes that field on the live list route using the same
comparable-status rule already enforced by the detail and compare surfaces:

- `sealed` -> `allowedActions.canCompare: true`
- `superseded` -> `allowedActions.canCompare: true`
- `pending` -> `allowedActions.canCompare: false`
- `failed` -> `allowedActions.canCompare: false`

No new Pantheon endpoint or client-side shadow state was introduced.

## Source Handoff

Source request:

- `/home/edna/code/pantheon/.coordination/requests/RW-05-artifact-compare-bff-gap.yaml`

Verified frontend publication chain:

- source branch: `origin/pkt-004-detail-fix`
- source payload publication commit:
  `1a1a42eebda033a1fbda4696df5b81271f5eed9b`
- reviewed UI commit referenced by the gap:
  `6321613cff3c49b11a7619e0f9170217a27a7b17`

## Verification Performed

- Repaired the RW-05 list projection in:
  - `services/control-plane/bff/read_store.py`
- Re-locked the list contract in:
  - `services/control-plane/bff/test_rw05_artifact_compare_contract.py`
- Republished the canonical contract artifacts:
  - `docs/bff/RW-05-artifact-compare.md`
  - `docs/examples/RW-05-artifact-compare.json`
  - `docs/pantheon-handoffs/RW-05-artifact-compare/FRONTEND_CHANGE_SPEC.md`
- Revalidated the example payload:
  - `python3 -m json.tool docs/examples/RW-05-artifact-compare.json`
  - Result: passed
- Re-ran the targeted RW-05 contract proof:
  - `python3 -m pytest -q services/control-plane/bff/test_rw05_artifact_compare_contract.py`
  - Result: `6 passed`
  - Date: `2026-04-24`

## Contract State After Repair

The current RW-05 contract is now aligned across runtime, tests, and canonical
handoff docs:

- `GET /api/v1/artifacts` publishes list-level
  `artifacts[].allowedActions.canCompare`
- `GET /api/v1/artifacts/{artifact_id}` still publishes detail-level
  `allowedActions.canCompare` and `allowedActions.canViewDetail`
- `GET /api/v1/artifacts/compare` still owns all comparison computation
- `meta.surfaces.artifact_list`, `artifact_detail`, and `artifact_compare`
  remain the canonical degradation signals

## Next Step

Pantheon-side BFF work for this gap is complete.

The next truthful step is front-owned:

- sync the refreshed RW-05 response packet
- verify the reviewed registry and compare selectors against list-level
  `allowedActions.canCompare`
- republish a truthful `ui-done` / `frontend-feedback` pair from reviewed UI
  commit `6321613cff3c49b11a7619e0f9170217a27a7b17`

## Files Updated

- `.coordination/requests/RW-05-artifact-compare-bff-gap.yaml`
- `.coordination/reviews/RW-05-artifact-compare-bff-gap-resolution.md`
- `.coordination/responses/RW-05-artifact-compare-contract-ready.yaml`
- `.coordination/responses/RW-05-artifact-compare-backend-delivery.yaml`
- `.coordination/responses/RW-05-artifact-compare-lovable-ui-task.yaml`
- `.coordination/responses/RW-05-artifact-compare-lovable-prompt.md`
- `docs/pantheon-delivery/RW-05-artifact-compare/CONTRACT_LOCK.json`
- `docs/bff/RW-05-artifact-compare.md`
- `docs/examples/RW-05-artifact-compare.json`
- `docs/pantheon-handoffs/RW-05-artifact-compare/FRONTEND_CHANGE_SPEC.md`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/test_rw05_artifact_compare_contract.py`
