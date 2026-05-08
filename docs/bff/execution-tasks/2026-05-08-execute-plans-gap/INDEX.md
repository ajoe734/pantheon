# Execute-Plans BFF Gap Execution Packet - 2026-05-08

Status: ready for supervisor dispatch

## Source Of Truth

This packet is for the new Lovable repo:

- `/home/lupin/code/execute-plans`

Do not use the legacy `front-ai-trading-system` repo for this gap set.

Contract sources inspected:

- `execute-plans/.lovable/spec/v2/Pantheon_Frontend_Build_Spec_Part_06_Shared_Data_Model_BFF_API_Contract_en-US.md`
- `execute-plans/.lovable/spec/v2/Pantheon_Frontend_Build_Spec_Part_06_Shared_Data_Model_BFF_API_Contract_zh-TW.md`
- `execute-plans/.lovable/spec/v4/pack-d/Pantheon_Pack_D_BFF_API_Contract.md`
- `execute-plans/.lovable/spec/v4/pack-d/Pantheon_Pack_D_SSE_Event_Contract.md`
- `execute-plans/.lovable/spec/v4/pack-d/Pantheon_Pack_D_Session_Auth_Tenant_Contract.md`
- `execute-plans/.lovable/spec/v5/Pantheon_v5_Closed_Loop_Supervisor_OS_SA_2026-05-06.md`
- `execute-plans/.lovable/spec/v5/Pantheon_v5_Closed_Loop_Supervisor_OS_SD_2026-05-06.md`
- `execute-plans/.lovable/feedback/2026-05-07-final/Pantheon_BFF_Contract_Spec_2026-05-07_Final.md`
- `execute-plans/.lovable/feedback/2026-05-07-C/Pantheon_BFF_Contract_Spec_2026-05-07-C_Planner_Disposition.md`
- `execute-plans/src/**`
- `execute-plans/README.md`

## Audit Summary

Restricted active-contract scan:

- 169 unique `/bff` or `/health` endpoint references.
- 15 are currently implemented or covered by dynamic Pantheon BFF routes.
- 154 are missing as exact Pantheon BFF surfaces.

Full `.lovable` historical scan:

- 320 unique endpoint references.
- Most additional endpoints are long-tail FULL spec surfaces, especially Agora and management registry expansion.
- These are covered by `BFF-LUV-GAP-007` plus the route-registry task so supervisor can decide implementation vs supersession instead of losing them.

Pantheon exact BFF routes currently present:

- `GET /health`
- `GET /bff/actions`
- `GET /bff/approvals`
- `PATCH /bff/agora/journal/{id}`
- `POST /bff/mcp-servers/{id}/import-tools`
- `POST /bff/mcp-tools/{id}/{action}`
- `POST /bff/v1/commands`
- `POST /bff/v1/mcp/servers/{id}/import-tools`
- `POST /bff/v1/mcp/servers/{id}/tools/{toolId}/actions/{actionId}`
- `GET /bff/v5/interventions`
- `POST /bff/v5/interventions/{id}/remediate`

The previous `BFF-FINAL-001..010` packet already covers the 2026-05-07 final handoff. This packet adds the broader `execute-plans` route-family gaps needed for Lovable cutover.

## Execution Tasks

| Task | Owner Lane | Purpose |
|---|---|---|
| `BFF-LUV-GAP-001` | Integration | Build the route registry and contract-surface test harness. |
| `BFF-LUV-GAP-002` | Control plane | Implement strategy/persona BFF compatibility surfaces. |
| `BFF-LUV-GAP-003` | Control plane | Implement capital, ranking, and rebalance surfaces. |
| `BFF-LUV-GAP-004` | Control plane | Implement evolution, experiments, jobs, and events surfaces. |
| `BFF-LUV-GAP-005` | Control plane | Implement governance, runtime, risk, incident, audit, and command-confirmation surfaces. |
| `BFF-LUV-GAP-006` | Control plane | Implement active Agora core surfaces used by current source and Part 06. |
| `BFF-LUV-GAP-007` | Control plane | Reconcile extended Agora/FULL-spec long-tail surfaces. |
| `BFF-LUV-GAP-008` | Control plane | Implement tools, MCP, and skills compatibility surfaces. |
| `BFF-LUV-GAP-009` | Integration | Implement `/bff/me` and Pack D session/auth/tenant readiness. |
| `BFF-LUV-GAP-010` | Runtime/SSE | Implement `/bff/events/stream` and `/bff/sse/*` compatibility routes. |
| `BFF-LUV-GAP-011` | Control plane | Decide and implement `/bff/v5/interventions/{id}/two-man-sign` alias or explicit supersession. |
| `BFF-LUV-GAP-012` | Integration | Run execute-plans cutover smoke against the finished BFF surface. |

## Registry And Coverage Harness

`BFF-LUV-GAP-001` publishes the checked-in route matrix at:

- `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`

Run this command to see the current family-level coverage and outstanding task mappings:

```bash
python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py
```
