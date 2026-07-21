# RW-05 Artifact Compare BFF Gap Resolution

Reviewer: `Codex`
Date: `2026-04-24`
Disposition: `approved`

## Findings

No blocking findings.

The returned RW-05 `bff-gap` from `origin/pkt-004-detail-fix` is valid, and
the Pantheon-owned gap was narrow: the live list route was missing
`artifacts[].allowedActions.canCompare` even though the canonical handoff and
the reviewed UI required backend-owned compare authority at the registry row
level. That list contract is now aligned.

## Verification

- `services/control-plane/bff/read_store.py` now emits
  `allowedActions.canCompare` on each artifact summary row returned by
  `GET /api/v1/artifacts`, using the same comparable-status rule already used
  by the detail and compare surfaces.
- `services/control-plane/bff/test_rw05_artifact_compare_contract.py` now
  asserts the list-level `allowedActions.canCompare` field for comparable
  artifact rows and for a `pending` artifact row.
- `python3 -m pytest -q services/control-plane/bff/test_rw05_artifact_compare_contract.py`
  passed (`6 passed`).
- `python3 -m json.tool docs/examples/RW-05-artifact-compare.json` passed
  after republishing the example list payload with `allowedActions.canCompare`.
- Canonical packet truth is aligned in:
  - `.coordination/requests/RW-05-artifact-compare-bff-gap.yaml`
  - `.coordination/responses/RW-05-artifact-compare-contract-ready.yaml`
  - `.coordination/responses/RW-05-artifact-compare-backend-delivery.yaml`
  - `.coordination/responses/RW-05-artifact-compare-lovable-ui-task.yaml`
  - `.coordination/responses/RW-05-artifact-compare-lovable-prompt.md`
  - `docs/bff/RW-05-artifact-compare.md`
  - `docs/examples/RW-05-artifact-compare.json`
  - `docs/pantheon-handoffs/RW-05-artifact-compare/FRONTEND_CHANGE_SPEC.md`
  - `docs/pantheon-delivery/RW-05-artifact-compare/DELIVERY_NOTE.md`
  - `docs/pantheon-delivery/RW-05-artifact-compare/CONTRACT_LOCK.json`

## Decision

`RW-05-artifact-compare-bff-gap` is resolved for the current Pantheon
workspace.

The next truthful step is front-owned: sync the refreshed RW-05 packet, verify
the reviewed registry and compare selectors against list-level
`allowedActions.canCompare`, and republish a truthful `ui-done` /
`frontend-feedback` pair from the reviewed UI commit
`6321613cff3c49b11a7619e0f9170217a27a7b17`.
