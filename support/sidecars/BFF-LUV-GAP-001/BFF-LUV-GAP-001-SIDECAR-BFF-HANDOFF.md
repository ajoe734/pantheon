# BFF-LUV-GAP-001 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-GAP-001-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-GAP-001
Helper kind: bff_handoff_packet
Owner: Codex
Reviewer: Codex2
Prepared: 2026-05-08

## Scope

This is a support-only sidecar for the BFF-LUV-GAP-001 parent registry task. It does not define canonical architecture, update L1 truth, or change runtime behavior. The parent owner should use it as a handoff packet for reviewing the execute-plans BFF route registry, coverage harness, and frontend cutover implications.

The source frontend repo for this gap set is `/home/lupin/code/execute-plans`. Do not use the legacy `front-ai-trading-system` repo when interpreting this packet.

## Current Evidence Snapshot

Commands run from `/home/lupin/code/pantheon`:

```bash
jq '.tasks[] | select(.id=="BFF-LUV-GAP-001-SIDECAR-BFF-HANDOFF")' ai-status.json
sed -n '1,260p' docs/bff/execution-tasks/2026-05-08-execute-plans-gap/BFF-LUV-GAP-001-contract-registry.md
sed -n '1,220p' docs/bff/execution-tasks/2026-05-08-execute-plans-gap/INDEX.md
jq '.metadata' services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json
python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py
sed -n '1,220p' services/control-plane/bff/test_execute_plans_contract_registry.py
rg -n "/bff|/health|EventSource|apiFetch|fetch\(" /home/lupin/code/execute-plans/src -g '*.ts' -g '*.tsx'
sed -n '1,220p' /home/lupin/code/execute-plans/README.md
git status --short
```

Findings from the current worktree:

- The parent registry packet names `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json` as the durable route matrix and `services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py` as the reviewer report command.
- The registry metadata records `/home/lupin/code/execute-plans` as the source repo, 169 audited active endpoint references, and status values `implemented`, `implemented_by_alias`, `missing`, `superseded_with_reason`, and `deferred_with_task`.
- The coverage report renders 178 route rows with no `Implemented Rows Not Live` section in the current worktree.
- Outstanding rows are currently concentrated in `strategy-persona` for BFF-LUV-GAP-002 plus three `execute-plans-cutover-smoke` deferred probes for BFF-LUV-GAP-012.
- The focused test file validates registry shape, duplicate route prevention, live FastAPI presence for implemented rows, final-contract route continuity, coverage report rendering, and concrete MCP alias action proof.
- The worktree already contains unrelated dirty/untracked parent implementation artifacts. This sidecar only adds this support packet.

## Coverage Readout

Current family-level report:

| Family | Implemented | Alias | Missing | Deferred | Superseded | Review note |
|---|---:|---:|---:|---:|---:|---|
| `health` | 1 | 0 | 0 | 0 | 0 | Final health probe remains live. |
| `final-contract` | 3 | 0 | 0 | 0 | 0 | `/bff/actions`, `/bff/approvals`, and `/bff/v1/commands` remain registered. |
| `v5-interventions` | 2 | 0 | 0 | 0 | 1 | `two-man-sign` is explicitly superseded for BFF-LUV-GAP-011 review. |
| `mcp-final` | 4 | 4 | 0 | 0 | 0 | Dynamic MCP action route is only accepted because the focused test proves `grant`, `revoke`, `disable`, and `test`. |
| `generic-actions` | 0 | 0 | 0 | 0 | 1 | Generic resource action route is intentionally superseded rather than treated as a catch-all. |
| `strategy-persona` | 0 | 0 | 24 | 1 | 0 | Remaining BFF query gap for BFF-LUV-GAP-002. |
| `capital-ranking-rebalance` | 17 | 0 | 0 | 0 | 0 | Mapped to BFF-LUV-GAP-003 implementation scope. |
| `evolution-experiment-jobs-events` | 19 | 0 | 0 | 0 | 0 | Mapped to BFF-LUV-GAP-004 implementation scope. |
| `governance-runtime-risk-audit` | 25 | 0 | 0 | 0 | 0 | Mapped to BFF-LUV-GAP-005 implementation scope. |
| `agora-core` | 26 | 0 | 0 | 0 | 0 | Mapped to BFF-LUV-GAP-006 implementation scope. |
| `agora-extended` | 4 | 8 | 0 | 0 | 5 | BFF-LUV-GAP-007 should confirm alias and supersession reasons remain intentional. |
| `tools-mcp-skills` | 17 | 0 | 0 | 0 | 0 | Mapped to BFF-LUV-GAP-008 implementation scope. |
| `session-auth-me` | 1 | 0 | 0 | 0 | 0 | BFF-LUV-GAP-009 should let frontend retire mock session copy. |
| `sse-compatibility` | 0 | 11 | 0 | 0 | 1 | BFF-LUV-GAP-010 aliases to existing stream routes; reviewer should check channel semantics, not just route shape. |
| `execute-plans-cutover-smoke` | 0 | 0 | 0 | 3 | 0 | BFF-LUV-GAP-012 must decide implementation, supersession, or smoke-only acceptance. |

## BFF Query Gap

The active query gap after the current registry/report run is `strategy-persona`.

Routes currently reported as missing for BFF-LUV-GAP-002:

- Strategy list/create/detail/update: `GET/POST /bff/strategies`, `GET/PATCH /bff/strategies/{strategyId}`.
- Strategy drilldowns: specs, experiments, artifacts, lineage, audit.
- Strategy actions: `POST /bff/strategies/{strategyId}/actions/{actionId}` and `POST /bff/strategies/{strategyId}/dry-run`.
- Persona list/create/detail/update: `GET/POST /bff/personas`, `GET/PATCH /bff/personas/{personaId}`.
- Persona drilldowns: route policy, activity, evaluations, memory, audit.
- Persona actions: `POST /bff/personas/{personaId}/actions/{actionId}` and `POST /bff/personas/{personaId}/test-prompt`.
- Global search: `GET /bff/search`.
- Deferred type catalog: `GET /bff/types`.

Deferred cutover probes for BFF-LUV-GAP-012:

- `GET /bff/v5/loop-runs`
- `GET /bff/v5/sentinel/findings`
- `GET /bff/v5/execution/persona-health`

These gaps should remain mapped to their owner tasks in the registry. BFF-LUV-GAP-001 should not silently mark them implemented unless the corresponding parent task has route code and focused proof.

## Operator Journey

The execute-plans app is a dual-surface operator workflow:

1. Operator lands in Management Console or Agora Workbench through the shared platform shell.
2. Health and final-contract probes can hit live Pantheon BFF through hybrid mode.
3. Operator reads Management entities such as strategies, personas, capital pools, rebalances, reviews, deployments, runtimes, risk alerts, incidents, tools, MCP servers, and skills.
4. Operator reads Agora surfaces such as daily brief, signals, watchlist, sessions, notes, journal, insights, memory, and training examples.
5. High-risk writes remain controlled through BFF action routes, command confirmations, idempotency keys, evidence, and audit events.
6. Realtime UI expects SSE compatibility routes or aliases for notifications, jobs, alerts, incidents, deployment events, review updates, Agora signals, and Agora sessions.
7. Cutover readiness is only credible when the registry report has no unmapped missing rows, focused BFF tests pass, and execute-plans hybrid fallback is no longer covering active route gaps by accident.

For operators, the biggest residual user-visible gap is strategy/persona navigation. Current frontend code references strategy/persona objects broadly across Management detail pages, Agora KPI formulas, governance/route-policy panels, and global search. If BFF-LUV-GAP-002 is not absorbed before cutover, hybrid mode must continue to retain mock fallback for those surfaces.

## Frontend Handoff Materials

Frontend repo: `/home/lupin/code/execute-plans`.

Important frontend files and implications:

| Frontend source | Handoff use |
|---|---|
| `README.md` | Documents `VITE_BFF_MODE=hybrid`, live final routes, and why non-final routes keep mock fallback until BFF completion. |
| `src/lib/bff/transport.ts` | Owns real BFF URL resolution, auth/headers, JSON parsing, and `/health` probe. |
| `src/lib/bff/client.ts` | Main read facade; registry reviewers should compare its active read methods against the route matrix before cutover. |
| `src/lib/bff/mutations.ts` | Write/action facade; real writes remain gated by frontend config and backend safety semantics. |
| `src/lib/bff/realtime.ts` | Realtime facade; compare its channels against `sse-compatibility` aliases before declaring route-live SSE readiness. |
| `src/lib/v3/agoraKpi.ts` | KPI sources include `/bff/strategies`, `/bff/agora/daily`, `/bff/agora/signals`, `/bff/research/tasks`, `/bff/alerts`, and `/bff/incidents`. |
| `src/lib/v3/medium-low/B1-platform.ts` | Global search helper references `/bff/search`, currently part of BFF-LUV-GAP-002. |
| `src/lib/v3/medium-low/B3-console.ts` | Static SSE endpoint inventory; use it to validate BFF-LUV-GAP-010 alias semantics. |
| `src/lib/v3/medium-low/B5-misc.ts` | Dry-run, test-prompt, sandbox-eval, memory quarantine, and audit export action endpoint references. |
| `src/management/pages/StrategyDetail.tsx` and `src/management/pages/PersonaDetail.tsx` | Representative pages most exposed to the remaining BFF-LUV-GAP-002 route gap. |
| `src/agora/pages/DailyBrief.tsx` and `src/agora/pages/SignalReview.tsx` | Representative Agora pages that combine Agora core routes with strategy/persona context. |

Frontend cutover guidance:

- Keep `VITE_BFF_MODE=hybrid` and `VITE_BFF_REAL_WRITES=false` until strategy/persona and BFF-LUV-GAP-012 cutover probes have explicit disposition.
- Do not replace mock fallback solely because a registry row is marked `implemented_by_alias`; first confirm the frontend method accepts the aliased response shape and channel semantics.
- For any route not present in the registry, publish a fresh `bff-gap` handoff instead of adding frontend-only local state.
- When `/bff/me` is live through BFF-LUV-GAP-009, remove or revise the frontend transitional copy that says the v5 control room is still using a mock session.

## Parent Absorption Checklist

The parent BFF-LUV-GAP-001 owner/reviewer can use this packet to check the registry before closing the parent:

1. Confirm `execute_plans_bff_routes.json` includes all routes from the named `.lovable` contract files and active `/home/lupin/code/execute-plans/src/**` references.
2. Confirm every `missing` or `deferred_with_task` row has a valid BFF-LUV-GAP-002..012 owner task.
3. Confirm every `implemented_by_alias` row includes `covered_by` and `proof`, and that the proof is not only route-shape matching.
4. Confirm every `superseded_with_reason` row has a clear reason that a frontend owner can understand.
5. Confirm `test_execute_plans_contract_registry.py` fails if an implemented row drifts out of the FastAPI route table.
6. Confirm the registry/report remains useful while other BFF-LUV-GAP tasks are still dirty or in progress.
7. Confirm the report output is copied or summarized in the parent artifact whenever the route matrix changes materially.

## Suggested Focused Verification

Minimum review commands:

```bash
python3 services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py
python3 -m pytest services/control-plane/bff/test_execute_plans_contract_registry.py -q
```

Optional frontend-facing cross-check:

```bash
rg -n "/bff|/health|EventSource|apiFetch|fetch\(" /home/lupin/code/execute-plans/src -g '*.ts' -g '*.tsx'
```

If a full BFF suite is blocked by unrelated in-progress task files, record that as a parent-task blocker rather than weakening the registry semantics.

## Reviewer Handoff

Reviewer should verify that this packet remains support-only and that every operational claim can be traced back to:

- `docs/bff/execution-tasks/2026-05-08-execute-plans-gap/BFF-LUV-GAP-001-contract-registry.md`
- `docs/bff/execution-tasks/2026-05-08-execute-plans-gap/INDEX.md`
- `services/control-plane/bff/contract_snapshots/execute_plans_bff_routes.json`
- `services/control-plane/bff/contract_snapshots/execute_plans_bff_contract.py`
- `services/control-plane/bff/contract_snapshots/report_execute_plans_bff_coverage.py`
- `services/control-plane/bff/test_execute_plans_contract_registry.py`
- `/home/lupin/code/execute-plans/README.md`
- `/home/lupin/code/execute-plans/src/lib/bff/*`
- `/home/lupin/code/execute-plans/src/lib/v3/*`

This packet is ready for Codex2 review and parent-owner absorption decisions.
