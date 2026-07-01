# MGMT-GAP-003 - BFF Management DTO Contract Hardening

Owner: Claude2
Reviewer: Codex
Batch: 2
Fleet lane: BFF/control-plane contract
Status: done

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

## Closeout Evidence

Closed by `ajoe734/pantheon` PR #2649:
`https://github.com/ajoe734/pantheon/pull/2649`.

| Item | Evidence |
|---|---|
| Implementation commit | `8600ccc3f3a4b1d926e99223d92fbe207ca9c4b0` |
| Merge commit | `0f3fc3ff60ad408d390f36244d3f9f465372457c` |
| Dev Branch CI Gate | `https://github.com/ajoe734/pantheon/actions/runs/28485525251` |
| Dev BFF deploy | `https://github.com/ajoe734/pantheon/actions/runs/28485593169` |
| Hosted BFF health | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/healthz` |
| Archive | `docs/04/pantheon_management_console_gap_2026-06-30/archive/mgmt-gap-003-closeout-2026-07-01.md` |

Hosted `/openapi.json` exposes `SurfaceState`, `PageInfo`,
`ManagementListMeta`, `ManagementRecordsEnvelope`, `DataSourcesEnvelope`, and
`LineageEnvelope`.

Hosted authenticated curl returned `200` for all eight in-scope endpoints:

- `/bff/management/data-sources`
- `/bff/management/permissions`
- `/bff/management/memory-governance`
- `/bff/management/consult-rules`
- `/bff/lineage`
- `/bff/workflows`
- `/bff/hooks`
- `/bff/knowledge`
