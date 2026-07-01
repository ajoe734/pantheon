# MGMT-GAP-003 - BFF Management DTO Contract Hardening

Owner: Claude2
Reviewer: Codex
Batch: 2
Fleet lane: BFF/control-plane contract
Status: in progress

## Problem

The former missing endpoints now appear in OpenAPI, but production FE wiring
requires stable DTOs, explicit degraded envelopes, and contract tests.

## Scope

Harden the BFF contracts for:

- `/bff/management/data-sources`
- `/bff/management/permissions`
- `/bff/management/memory-governance`
- `/bff/management/consult-rules`
- `/bff/lineage`
- `/bff/workflows`
- `/bff/hooks`
- `/bff/knowledge`

For each endpoint:

- confirm auth and CORS behavior;
- document response envelope;
- ensure empty source is explicit degraded/unavailable, not ambiguous `[]`;
- add contract tests;
- keep OpenAPI current.

## Non-Scope

- Do not add FE fallback rows to satisfy tests.
- Do not remove old compatibility routes unless a caller audit proves they are
  unused.

## Acceptance

- BFF tests pass for all endpoint success/degraded shapes.
- OpenAPI includes the documented schemas.
- Hosted curl with dev operator auth returns 200 for each endpoint.
- FE integration can consume the DTO without custom synthetic projections.

## Implementation Progress

Branch `task/mgmt-gap-003-bff-contracts` hardens the local BFF contract layer by:

- adding shared management console DTO schema components:
  `SurfaceState`, `PageInfo`, `ManagementListMeta`,
  `ManagementRecordsEnvelope`, `DataSourcesEnvelope`, and `LineageEnvelope`;
- binding the in-scope endpoints to FastAPI `response_model` declarations;
- normalizing `page_info.returned`, `page_info.has_more`, and
  `meta.status/source` across the BFF management read family;
- making `meta.surfaces.data_sources` a typed surface object rather than a
  string-only status;
- adding `test_bff_management_gap_003_contract_schema.py` to prove OpenAPI
  publishes the expected schemas for all eight endpoints.

Local contract validation passed:

```sh
python3 -m pytest \
  services/control-plane/bff/tests/test_bff_management_gap_003_contract_schema.py \
  services/control-plane/bff/tests/test_bff_management_data_sources_contract.py \
  services/control-plane/bff/tests/test_bff_governance_subrules_contract.py \
  services/control-plane/bff/tests/test_bff_lineage_contract.py \
  services/control-plane/bff/tests/test_bff_workflows_hooks.py \
  services/control-plane/bff/tests/test_bff_knowledge_inbox.py
```

Result: `34 passed`.

Closeout is still pending until this branch is merged, deployed to dev BFF, and
hosted curl evidence proves 200 responses for every in-scope endpoint.
