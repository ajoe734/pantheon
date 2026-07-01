# Live Audit Archive - Pantheon Management Console - 2026-06-30

This archive preserves the evidence used by
`docs/04/pantheon_management_console_gap_2026-06-30/README.md`.

## Environment

| Item | Evidence |
|---|---|
| FE host | `https://pantheon-lupin-dev-fe.35.201.239.38.sslip.io` |
| BFF host | `https://pantheon-lupin-dev-bff.35.201.239.38.sslip.io` |
| FE deployment | `20260630T134507Z` |
| FE commit | `f53176db20a477331e18355204b421c638030303` |
| FE branch | `dev` |
| FE mode | `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`, `VITE_BFF_REAL_WRITES=false` |
| BFF health | `/healthz` returned `status=ok`, `live=true`, `ready=true` |
| BFF version | `0.2.0` |

## Current BFF Endpoint Presence

Captured from `/openapi.json` on 2026-06-30:

| Endpoint | OpenAPI |
|---|---|
| `/bff/management/data-sources` | `get` |
| `/bff/management/permissions` | `get` |
| `/bff/management/memory-governance` | `get` |
| `/bff/management/consult-rules` | `get` |
| `/bff/lineage` | `get` |
| `/bff/workflows` | `get` |
| `/bff/hooks` | `get` |
| `/bff/knowledge` | `get` |
| `/bff/management/readiness/bff-ha` | `get` |
| `/bff/management/readiness/strict-publish` | `get` |

Conclusion: the old "missing endpoint" gap for these routes is superseded.
The live gap is now FE wiring, DTO contract semantics, write command truth, and
production acceptance.

Update: `MGMT-GAP-003` closed the DTO/OpenAPI/contract semantics slice in
`ajoe734/pantheon` PR #2649 and deployed BFF commit
`0f3fc3ff60ad408d390f36244d3f9f465372457c` to dev. See
`archive/mgmt-gap-003-closeout-2026-07-01.md` for hosted curl evidence.

## Source Inventory

Active FE source checked from `/home/lupin/code/pantheon/.fe-ep`
`origin/dev` at `f53176db20a477331e18355204b421c638030303`.

Visible management nav count:

```text
58
```

Visible management nav groups:

- Oversight
- Performance & League
- Live Readiness
- Advanced Registry
- Operations
- Capabilities
- System

Management route evidence from `src/App.tsx`:

- `/management` index redirects to `/management/cockpit`.
- `control-room`, `one-ring`, `overview`, `overview-legacy`, `command-center`,
  and `ask` redirect to cockpit.
- `control-room-legacy` still renders `ControlRoomPage`.
- `deployment` and `deployment/:id` still render deployment list/detail instead
  of redirecting to `deployments`.
- `capital-pools`, `ranking-formulas`, `rebalances`, and `research` keep legacy
  aliases or redirects.
- `studios/formula` and `studios/skill-sandbox` are still mounted.

## Current Miswiring Evidence

The following source checks were taken from active FE `origin/dev`.

| Page | Current source behavior | Production target |
|---|---|---|
| Data Sources | `DataSourceManagement.tsx` calls `mgmt.personaFleet.get()` | call `/bff/management/data-sources` |
| Permission Matrix | `PermissionMatrixPage.tsx` calls `bff.permissionMatrices.list()` | call `/bff/management/permissions` |
| Memory Governance | `MemoryGovernancePage.tsx` calls `bff.memoryUpdates.list()` and mutates local state with toast | call `/bff/management/memory-governance`; writes use commands |
| Consult Rules | `ConsultRulesPage.tsx` calls `bff.consultRules.list()` and submits through mutation helper | call `/bff/management/consult-rules`; writes use commands |
| Lineage | `LineageExplorer.tsx` calls `bff.strategies.list()` and synthesizes nodes/edges | call `/bff/lineage` |
| Ranking Dashboard | `RankingDashboard.tsx` computes scores client-side from strategies/personas/pools/formulas | use BFF ranking read model or mark analytical-only |
| Formula Studio | source comment says "run mock backtest"; run button only toasts | real backtest job/readback or demote from nav |
| Skill Sandbox | source comment says "mock execute"; local trace/output | real skill runner trace/readback or demote from nav |
| Workflows | `withLiveOrMock` falls back to `SEED`; run button only toasts | live registry plus command receipt |
| Hooks | `withLiveOrMock` falls back to local `CRONS`/`HOOKS`; switches use `defaultChecked` | live registry plus command receipt |

## Hidden Route Probe Summary

Hosted route probe results from the re-audit:

Update: `MGMT-GAP-001` closed the route/IA defects from this section in
`ajoe734/execute-plans` PR #120 and deployed FE commit
`6218e67d4119bcfc663681935d2a98e5af73e55a`. See
`archive/mgmt-gap-001-closeout-2026-06-30.md` for the post-merge hosted probe.

| Route | Final route | Observed page | Action |
|---|---|---|---|
| `/management` | `/management/cockpit` | Pathreon Management cockpit | keep redirect |
| `/management/control-room` | `/management/cockpit` | Pathreon Management cockpit | keep redirect |
| `/management/control-room-legacy` | `/management/control-room-legacy` | old Control Room | remove or redirect |
| `/management/one-ring` | `/management/cockpit` | Pathreon Management cockpit | keep redirect |
| `/management/overview` | `/management/cockpit` | Pathreon Management cockpit | keep redirect |
| `/management/overview-legacy` | `/management/cockpit` | Pathreon Management cockpit | keep redirect |
| `/management/command-center` | `/management/cockpit` | Pathreon Management cockpit | keep redirect |
| `/management/risk-center` | `/management/risk` | Risk Center | keep redirect |
| `/management/broker-live` | `/management/readiness/broker-live` | Broker Live Activation | keep redirect |
| `/management/capital-live` | `/management/readiness/capital-binding-live` | Capital Binding Live | keep redirect |
| `/management/system/bff-ha` | `/management/readiness/bff-ha` | BFF HA readiness | keep redirect |
| `/management/system/strict-publish` | `/management/readiness/strict-publish` | Strict Publish Audit | keep redirect |
| `/management/openclaw-llm-auth` | `/management/llm-provider-auth` | LLM Provider Auth | keep redirect |
| `/management/ask` | `/management/cockpit` | Pathreon Management cockpit | keep redirect |
| `/management/capital-pools` | `/management/capital` | Capital Pools | keep redirect |
| `/management/ranking-formulas` | `/management/ranking/formulas` | Ranking Formulas | keep redirect |
| `/management/rebalances` | `/management/rebalance` | Rebalance | keep redirect |
| `/management/research` | `/management/experiments` | Research & Experiments | keep redirect |
| `/management/deployment` | `/management/deployment` | Deployments | redirect to `/management/deployments` |
| `/management/deployment/dep-042` | `/management/deployment/dep-042` | degraded/detail miss | redirect to `/management/deployments/dep-042` |
| `/management/agent` | `/management/cockpit` | cockpit with floating panel | keep redirect |

## Production-Level Acceptance Signals Needed

The current audit is enough to dispatch work, but not enough to close production.
The final closeout must capture:

1. hosted FE `/deployment.json` for the final merged commit;
2. BFF `/healthz` and `/openapi.json` for required endpoints;
3. Playwright route manifest probe for all visible nav pages and hidden aliases;
4. endpoint-capture proof that intended canonical endpoints are called;
5. write-CTA proof that no toast/mock/local-only mutation is presented as a live
   success;
6. residual risk list with owner and expiry.
