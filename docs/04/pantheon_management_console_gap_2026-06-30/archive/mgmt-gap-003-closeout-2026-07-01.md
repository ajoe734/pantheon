# MGMT-GAP-003 Closeout - BFF Management DTO Contracts - 2026-07-01

Status: done

This closeout records the implementation, merge, dev BFF deploy, and hosted
contract evidence for `MGMT-GAP-003`.

## Implementation

| Item | Evidence |
|---|---|
| Repo | `ajoe734/pantheon` |
| PR | `https://github.com/ajoe734/pantheon/pull/2649` |
| Implementation commit | `8600ccc3f3a4b1d926e99223d92fbe207ca9c4b0` |
| Merge commit on `dev` | `0f3fc3ff60ad408d390f36244d3f9f465372457c` |
| Dev Branch CI Gate | `https://github.com/ajoe734/pantheon/actions/runs/28485525251` |
| Dev Orchestrator Sync | `https://github.com/ajoe734/pantheon/actions/runs/28485525242` |
| Dev BFF deploy | `https://github.com/ajoe734/pantheon/actions/runs/28485593169` |

Implemented contract hardening:

- Added shared management console DTO schema components:
  `SurfaceState`, `PageInfo`, `ManagementListMeta`,
  `ManagementRecordsEnvelope`, `DataSourcesEnvelope`, and `LineageEnvelope`.
- Bound all in-scope BFF management console read endpoints to FastAPI
  `response_model` declarations.
- Normalized typed surface metadata for success, degraded, and unavailable
  states.
- Normalized `page_info.returned`, `page_info.has_more`, and `meta.status/source`
  for the in-scope read family.
- Converted `meta.surfaces.data_sources` from a string status to a typed surface
  object.
- Added OpenAPI regression coverage proving all eight endpoints publish typed
  response schemas.

## Local Validation

Targeted contract suite:

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

Additional local validation:

- `python3 -m py_compile` for changed `console_gap` modules and the new schema
  test.
- `python3 -m json.tool ai-status.json >/tmp/ai-status.mgmt-gap-003.json`
- `git diff --check`

## Hosted Dev BFF Evidence

Dev BFF deploy run `28485593169` completed successfully. Public BFF smoke passed
inside the workflow.

Hosted health:

| Field | Value |
|---|---|
| URL | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io/healthz` |
| HTTP | `200` |
| status | `ok` |
| live | `true` |
| ready | `true` |
| version | `0.2.0` |

Hosted `/openapi.json` exposes these components:

- `SurfaceState`
- `PageInfo`
- `ManagementListMeta`
- `ManagementRecordsEnvelope`
- `DataSourcesEnvelope`
- `LineageEnvelope`

Authenticated hosted curl with `Authorization: Bearer op-mgmt-gap-003:operator,reviewer`
returned:

| Endpoint | Result |
|---|---|
| `/bff/management/data-sources` | `200`, `meta.status=ok`, `surface.data_sources=ok/service_client`, `returned=13`, `total=13` |
| `/bff/management/permissions` | `200`, `meta.status=unavailable`, `surface.governance_permissions=unavailable/missing`, `returned=0`, `total=0` |
| `/bff/management/memory-governance` | `200`, `meta.status=unavailable`, `surface.memory_governance_rules=unavailable/missing`, `returned=0`, `total=0` |
| `/bff/management/consult-rules` | `200`, `meta.status=unavailable`, `surface.consult_rules=unavailable/missing`, `returned=0`, `total=0` |
| `/bff/lineage` | `200`, `meta.status=degraded`, `surface.lineage=degraded/service_client`, `returned=0`, `total=0` |
| `/bff/workflows` | `200`, `meta.status=unavailable`, `surface.workflow_templates=unavailable/missing`, `returned=0`, `total=0` |
| `/bff/hooks` | `200`, `meta.status=unavailable`, `surface.hook_registry=unavailable/missing`, `returned=0`, `total=0` |
| `/bff/knowledge` | `200`, `meta.status=ok`, `surface.knowledge_inbox=ok/bff_composed`, `returned=8`, `total=8` |

## Residual Notes

`MGMT-GAP-003` closes the BFF DTO/OpenAPI/contract dependency for Batch 2. It
does not wire the frontend to these endpoints. `MGMT-GAP-002` is now unblocked
and must consume these typed envelopes without adding synthetic FE fallback rows.
