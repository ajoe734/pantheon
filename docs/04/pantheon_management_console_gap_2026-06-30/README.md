# Pantheon Management Console Gap Development Spec - 2026-06-30

| Field | Value |
|---|---|
| Doc ID | `MGMT_CONSOLE_GAP_2026-06-30` |
| Status | Ready for execution task dispatch |
| Scope | Pantheon Management Console production-level gap closure |
| Audit basis | Hosted FE/BFF probes on 2026-06-30, plus active FE source checkout `.fe-ep` (`origin/dev`, historically tracked as `execute-plans`) |
| FE deployment checked | `pantheon-dev-fe`, deployed `20260630T134507Z`, commit `f53176db20a477331e18355204b421c638030303` |
| BFF checked | `pantheon-dev-bff`, `/healthz` live and ready, version `0.2.0` |
| Supersedes | `docs/04/pantheon_bff_console_gap_2026-06-15/README.md` for management-console gap status |
| Archive evidence | `archive/live-audit-2026-06-30.md` |
| Re-audit addendum | `archive/full-reaudit-addendum-2026-07-01.md` |
| Execution packet | `docs/bff/execution-tasks/2026-06-30-management-console-production-gap/INDEX.md` |

## 1. Purpose

The 2026-06-30 re-audit found that the management console is not blocked mainly by
missing BFF routes anymore. The old 2026-06-15 framing said several management
routes were 404. On 2026-06-30 the BFF OpenAPI exposes those routes:

- `GET /bff/management/data-sources`
- `GET /bff/management/permissions`
- `GET /bff/management/memory-governance`
- `GET /bff/management/consult-rules`
- `GET /bff/lineage`
- `GET /bff/workflows`
- `GET /bff/hooks`
- `GET /bff/knowledge`

The remaining production gap is now a product/contract integration gap:

1. legacy and duplicate FE routes are still mounted;
2. several pages still derive data from old helpers instead of canonical BFF
   management endpoints;
3. many write-like controls still use local state, toast, seed fallback, or mock
   runners;
4. the information architecture exposes too many first-class pages before their
   operational depth is production-level;
5. there is no single production acceptance harness proving every visible
   management page is live, honest, non-mock, and deploy-verified.

This document converts that gap into production-level execution work for the
fleet.

## 2. Evidence Snapshot

Source and hosted evidence captured on 2026-06-30:

- Hosted FE `/deployment.json` reports commit
  `f53176db20a477331e18355204b421c638030303`, source branch `dev`,
  `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
  `VITE_BFF_REAL_WRITES=false`.
- BFF `/healthz` reports `live=true`, `ready=true`, dependencies OK.
- BFF `/openapi.json` exposes the canonical management endpoints listed above.
- Active FE management nav has 58 visible `/management/*` entries across
  Oversight, Performance, Live Readiness, Advanced Registry, Operations,
  Capabilities, and System.
- Active FE management router has more than 100 management route entries,
  including compatibility redirects and detail aliases.
- Hidden route probe confirmed:
  - `/management/control-room-legacy` still renders old `ControlRoomPage`.
  - `/management/deployment` and `/management/deployment/:id` still render the
    deployments list/detail instead of redirecting to `/management/deployments`.
  - `/management/capital-pools`, `/management/ranking-formulas`,
    `/management/rebalances`, and `/management/research` redirect or alias to
    canonical list/detail families.

Detailed evidence is archived in
`docs/04/pantheon_management_console_gap_2026-06-30/archive/live-audit-2026-06-30.md`.

The 2026-07-01 full re-audit addendum is archived in
`docs/04/pantheon_management_console_gap_2026-06-30/archive/full-reaudit-addendum-2026-07-01.md`.
It adds hosted authenticated detail-route evidence, live-id BFF availability,
mock/unavailable flags, DTO honesty issues such as `status.undefined` and
`NaN%`, and updated recommendations for which surfaces should be adjusted,
hidden/deleted, or deeply developed.

For execution artifacts, `frontend-checkout:...` means the active frontend
source checkout audited at `/home/lupin/code/pantheon/.fe-ep`; it is not a
literal path under this orchestration repository.

## 3. Gap Matrix

| Gap | Severity | Area | Current evidence | Production requirement | Task |
|---|---:|---|---|---|---|
| G1 | P0 | Legacy route cleanup | `control-room-legacy` renders old Control Room; `deployment` aliases duplicate `deployments` | Legacy routes redirect or are removed; no hidden duplicate UI survives route smoke | `MGMT-GAP-001` |
| G2 | P0 | Canonical read wiring | Data Sources derives from `mgmt.personaFleet`; permissions uses `permissionMatrices`; memory/consult derive from old helpers; Lineage uses `strategies` | FE reads canonical management endpoints exposed by OpenAPI and shows explicit degraded envelopes | `MGMT-GAP-002` + `MGMT-GAP-003` |
| G3 | P0 | Command/receipt truth | Ranking, governance memory/consult, workflow run/edit, hook toggles, settings, and many detail panels still use toast/local state/mock overlay | Every write-like action either uses governed command/receipt/audit flow or is disabled with explicit not-production label | `MGMT-GAP-004` |
| G4 | P1 | Studios and capability depth | Formula Studio says mock backtest; Skill Sandbox says mock execute; Tools/MCP/Skills lists are often empty/degraded with create CTA | Studios become real backtest/skill-runner flows, or are demoted from first-level nav until backend runner exists | `MGMT-GAP-005` |
| G5 | P1 | IA over-expansion | 58 first-level management nav entries, including loops subpages, studios, governance subpages, and empty registries | Nav is cockpit-first and task-clustered; non-production surfaces are secondary, gated, or hidden | `MGMT-GAP-001` + `MGMT-GAP-006` |
| G6 | P1 | Production proof | No single hosted-management probe asserts all visible nav pages use intended BFF endpoints and do not silently mock | Release gate includes management route/endpoint/mock/CTA coverage and deployed-host evidence | `MGMT-GAP-006` + `MGMT-GAP-007` |

## 4. Development Principles

### 4.1 Do Not Delete Valid Operator Viewpoints

Some pages look repetitive because they share entities, but they represent
different operator jobs:

- `ranking`, `persona-league`, and `quarterly-ranking` are different ranking
  cadences and should stay if each has live truth.
- `human-inbox`, `sentinel`, `interventions`, `approvals`, and `governance`
  should be consolidated as a decision workbench, not blindly deleted.
- `portfolio-book`, `performance-attribution`, and readiness pages should stay
  because they map to operational review and release gates.

Deletion is only recommended for true route duplication or dead legacy surfaces.

### 4.2 A Page Is Production-Level Only If It Is Honest

A management page is production-level when all of the following are true:

1. It uses a canonical BFF endpoint or explicitly states the exact degraded
   reason.
2. It never presents seed/mock/local state as live truth.
3. Write-like controls return command ids, receipts, audit refs, or are disabled.
4. It has a live hosted browser probe proving the intended endpoint family was
   called.
5. It has test coverage for success, empty/degraded, auth, and old route
   compatibility.

### 4.3 Backward Compatibility Is Allowed, Hidden Duplication Is Not

Redirects may stay for old bookmarks when they land on a canonical route.
Rendering a whole old component behind a hidden legacy URL is not acceptable for
production operations.

## 5. Batch Plan

### Batch 0 - Archive and Dispatch

Owner: Codex.

Deliverables:

- this gap spec;
- audit archive;
- execution task packet;
- active task board entries for the fleet.

Production gate:

- docs and task board are committed, pushed, PR opened, reviewed, and merged.

### Batch 1 - Route and IA Cleanup

Owner fleet: Frontend implementation.

Deliverables:

- `control-room-legacy` redirects to `/management/cockpit` or is removed;
- `deployment` and `deployment/:id` redirect to canonical deployments URLs;
- nav reduces first-level clutter by demoting non-production studios, empty
  registries, and duplicate loop subpages;
- old aliases have explicit route tests.

Production gate:

- hosted route probe proves no hidden old component renders.

### Batch 2 - Canonical Read Wiring

Owner fleet: Frontend + BFF contract.

Deliverables:

- Data Sources uses `/bff/management/data-sources`;
- permissions uses `/bff/management/permissions`;
- memory governance uses `/bff/management/memory-governance`;
- consult rules uses `/bff/management/consult-rules`;
- lineage uses `/bff/lineage`;
- workflows/hooks consume live empty/degraded envelopes without seed fallback
  masquerading as live truth;
- ranking uses a BFF ranking/read model or labels its client computation as
  analytical-only.

Production gate:

- route-level browser probe captures intended endpoint calls on hosted FE.

### Batch 3 - Command Truth and Deep Operations

Owner fleet: BFF/control-plane + frontend integration.

Deliverables:

- ranking recalc/freeze/publish/compare returns command receipts;
- governance memory approve/reject/merge returns command receipts;
- consult rule add/edit/delete/submit returns command receipts;
- workflow run/edit/create and hook toggle/create return command receipts;
- settings break-glass and force-transition are fully governed or disabled.

Production gate:

- dry-run or real-write-off probes prove no silent local-only mutation is shown
  as production success.

### Batch 4 - Studios, Tools, MCP, Skills

Owner fleet: runtime worker operations + frontend integration.

Deliverables:

- Formula Studio is backed by a real backtest job/readback, or removed from
  first-level nav;
- Skill Sandbox is backed by a real skill-runner trace/readback, or removed
  from first-level nav;
- Tools/MCP/Skills create/import/publish/retire actions are governed or disabled.

Production gate:

- hosted probe proves no mock trace/backtest is labeled as a successful live run.

### Batch 5 - Production Acceptance

Owner fleet: integration/QA.

Deliverables:

- management route manifest includes visible routes, hidden aliases, expected
  canonical final paths, and intended BFF endpoints;
- Playwright hosted probe asserts no console CORS failures, no seed fallback
  claims, no mock-only success for write CTAs, and no stale legacy route render;
- release gate records FE commit, BFF health, OpenAPI endpoint presence, and
  route evidence.

Production gate:

- PRs merged to `dev`, Pantheon dev FE deployment updated, `/deployment.json`
  reports the target commit, and the management production probe is green.

## 6. Fleet Task Map

| Batch | Task | Owner | Reviewer | Fleet lane |
|---|---|---|---|---|
| 1 | `MGMT-GAP-001` | Codex2 | Claude | Frontend IA/route cleanup |
| 2 | `MGMT-GAP-002` | Claude | Codex | Frontend canonical read wiring |
| 2 | `MGMT-GAP-003` | Claude2 | Codex | BFF management DTO contract hardening |
| 3 | `MGMT-GAP-004` | Codex | Claude2 | Command receipt and write truth |
| 4 | `MGMT-GAP-005` | Gemini | Claude | Runtime-backed studios and capability runner |
| 5 | `MGMT-GAP-006` | Gemini2 | Codex | Hosted production acceptance harness |
| 5 | `MGMT-GAP-007` | Codex | Claude | Oversight closeout, archive proof, production gate tracking |

These are materialized by:

```sh
python3 scripts/dispatch_management_console_gap_2026-06-30.py
```

## 7. Completion Definition

The overall management-console gap is not complete until:

1. all `MGMT-GAP-*` tasks are terminal `done` or explicitly superseded by a
   reviewed equivalent;
2. every task has a branch, commit, PR, merge target, and reviewer approval
   recorded through the task board;
3. active FE `dev` deploys the final commit and `/deployment.json` confirms it;
4. BFF `/healthz` and OpenAPI confirm required endpoints;
5. the hosted management production probe passes;
6. the final archive includes route evidence, endpoint evidence, screenshots or
   probe output, and residual risks with owners and expiry dates.
