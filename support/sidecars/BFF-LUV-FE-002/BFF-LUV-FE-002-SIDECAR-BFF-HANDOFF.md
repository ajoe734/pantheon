# BFF-LUV-FE-002 Sidecar BFF Handoff Packet

Task ID: BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF
Parent Task: BFF-LUV-FE-002
Helper kind: bff_handoff_packet
Owner: Codex2
Reviewer: Claude
Prepared: 2026-05-09T17:52:40Z

## Scope

Support-only sidecar for BFF-LUV-FE-002. This packet does not define canonical
architecture, change route truth, or modify runtime/frontend implementation. It
organizes the approved Management Console read adapter surface, the remaining
live-query evidence gap, and the frontend handoff notes for the parent owner to
absorb or ignore.

Current parent state at packet time:

- Parent owner: Claude.
- Parent reviewer: Codex2.
- Parent status: `review_approved`.
- Parent artifact:
  `docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-002-management-read-adapters.md`.
- Review approval packet: `.orchestrator/reviews/BFF-LUV-FE-002-review-codex2.md`.
- Reviewed execute-plans commits: `890712d`, `124aa17`.

## Source Snapshot

| Surface | Current state | Source |
|---|---|---|
| Management read client | `managementClient.<family>.list()` exists for all 20 required Management Console families; most families also expose `get(id)`. | `/home/lupin/code/execute-plans/src/lib/bff/client.ts:237` |
| Family registry | `MANAGEMENT_FAMILIES` enumerates strategies, personas, capital pools, ranking formulas, rebalances, deployments, evolution, research, artifacts, tools, MCP servers/tools, skills, channels, jobs, runtimes, alerts, incidents, approvals, and audit. | `/home/lupin/code/execute-plans/src/lib/bff/client.ts:263` |
| List transport | `src/lib/bff-v1/lists.ts` wraps every family with `withLiveOrMock` and the canonical `/bff/*` list path. | `/home/lupin/code/execute-plans/src/lib/bff-v1/lists.ts:105` |
| List-class metadata | Exact registry/governance lists, loop-run lists, realtime feeds, and audit feeds carry distinct `totalCountExact` / `estimatedTotal` semantics. | `/home/lupin/code/execute-plans/src/lib/bff-v1/lists.ts:76` |
| Detail transport | Detail readers call `withLiveOrMock` directly against `/bff/<resource>/{id}` paths; audit remains list-only. | `/home/lupin/code/execute-plans/src/lib/bff/client.ts:91` |
| Ranking formula fix | Rev2 corrected `rankingFormulas.get(id)` to call `/bff/ranking-formulas/{id}` with `encodeURIComponent(id)`. | `/home/lupin/code/execute-plans/src/lib/bff/client.ts:124` |
| Fallback taxonomy | `mock`, `hybrid`, and `real` modes are explicitly detected from `VITE_BFF_MODE` and `VITE_BFF_FALLBACK`. | `/home/lupin/code/execute-plans/src/lib/bff/client.ts:55` |
| Live fallback behavior | `withLiveOrMock` propagates typed 4xx BFF errors, falls back on network/5xx in auto mode, and throws in strict mode. | `/home/lupin/code/execute-plans/src/lib/bff-v1/liveTransport.ts:47` |
| Contract route coverage | Final OpenAPI contains list and detail routes for the required Management families except audit, which is list-only. | `/home/lupin/code/execute-plans/.lovable/feedback/2026-05-07-final/Pantheon_BFF_OpenAPI_3_1.yaml:249` |

## Management Read Surface Matrix

| Family | Frontend key | BFF route | List class | Handoff note |
|---|---|---|---|---|
| Strategies | `strategies` | `/bff/strategies`, `/bff/strategies/{id}` | `entityRegistry` | Core entity registry. Detail smoke should use an id from the live list. |
| Personas | `personas` | `/bff/personas`, `/bff/personas/{id}` | `entityRegistry` | Same list/detail pattern as strategies. |
| Capital pools | `capitalPools` | `/bff/capital-pools`, `/bff/capital-pools/{id}` | `entityRegistry` | Read-only adapter; do not pair with allocation writes in this task. |
| Ranking formulas | `rankingFormulas` | `/bff/ranking-formulas`, `/bff/ranking-formulas/{id}` | `entityRegistry` | Rev2 URL coverage exists for detail path construction. |
| Rebalances | `rebalances` | `/bff/rebalances`, `/bff/rebalances/{id}` | `governanceQueue` | Exact queue semantics. Treat command decisions as FE-004 scope. |
| Deployments | `deployments` | `/bff/deployments`, `/bff/deployments/{id}` | `governanceQueue` | Read-only deployment state. No live create/patch smoke here. |
| Evolution programs | `evolution` | `/bff/evolution-programs`, `/bff/evolution-programs/{id}` | `entityRegistry` | Uses the shortened frontend key `evolution`. |
| Research experiments | `research` | `/bff/research-experiments`, `/bff/research-experiments/{id}` | `entityRegistry` | Uses the shortened frontend key `research`. |
| Artifacts | `artifacts` | `/bff/artifacts`, `/bff/artifacts/{id}` | `entityRegistry` | Detail returns the artifact DTO shape directly. |
| Tools | `tools` | `/bff/tools`, `/bff/tools/{id}` | `entityRegistry` | Operational catalog read surface only. |
| MCP servers | `mcpServers` | `/bff/mcp-servers`, `/bff/mcp-servers/{id}` | `entityRegistry` | Server import writes remain outside FE-002. |
| MCP tools | `mcpTools` | `/bff/mcp-tools`, `/bff/mcp-tools/{id}` | `entityRegistry` | Path uses hyphenated `/mcp-tools`, matching final OpenAPI. |
| Skills | `skills` | `/bff/skills`, `/bff/skills/{id}` | `entityRegistry` | Catalog read surface. |
| Channels | `channels` | `/bff/channels`, `/bff/channels/{id}` | `entityRegistry` | Catalog read surface. |
| Jobs | `jobs` | `/bff/jobs`, `/bff/jobs/{id}` | `loopRun` | Live detail can call BFF; mock detail returns `undefined` because the seed has no job detail loader. |
| Runtimes | `runtimes` | `/bff/runtimes`, `/bff/runtimes/{id}` | `entityRegistry` | Runtime status read surface. |
| Alerts | `alerts` | `/bff/alerts`, `/bff/alerts/{id}` | `realtimeFeed` | `estimatedTotal` is intentionally omitted for list metadata. Acknowledge is FE-004 scope. |
| Incidents | `incidents` | `/bff/incidents`, `/bff/incidents/{id}` | `governanceQueue` | Incident command/update actions are outside FE-002. |
| Approvals | `approvals` | `/bff/approvals`, `/bff/approvals/{id}` | `governanceQueue` | Decide writes are FE-004 scope. |
| Audit | `audit` | `/bff/audit` | `auditFeed` | List-only. UI callers must not expect `managementClient.audit.get`. |

## BFF Query Gap Matrix

These are not blockers for the approved FE-002 implementation. They are the
remaining evidence and integration seams that the parent owner or follow-up live
smoke work should absorb.

| Gap | Current FE-002 state | Why it matters | Suggested absorption |
|---|---|---|---|
| Authenticated live DTO evidence | Unit coverage uses mocked `fetch` plus mock-mode DTO assertions; no valid Bearer-token live probe was run for all Management families. | `VITE_BFF_MODE=live` route registration is not the same as proving 2xx authenticated DTO shape. | Route this to AUTHED-LIVE or a follow-up smoke packet. Record status codes and field-shape checks under `docs/bff/evidence/`. |
| Detail route breadth | Focused live URL test exists for `rankingFormulas.get("rank_1")`; other detail readers rely on shared path builders and unit coverage. | One broken detail path already escaped Rev1 because TypeScript allowed a zero-arg builder. | Add a small generated/focused test or live smoke that walks ids from each live list and calls detail for every non-audit family. |
| Jobs mock detail | `jobs.get(id)` uses live `/bff/jobs/{id}` but mock fallback returns `undefined`. | In hybrid fallback, a transport failure can look like a missing job detail if the UI does not inspect `liveStatus`. | UI should display fallback state when `liveStatus.effective === "mock"` and avoid interpreting `undefined` as a live 404. |
| Audit list-only behavior | `audit` exposes `list()` only, matching OpenAPI list-only route. | A detail drawer wired generically across all families would fail for audit. | Branch audit UI to event-row selection or add a separate follow-up only if a canonical audit detail route is introduced. |
| Estimated feed counts | Alerts omit `estimatedTotal`; audit emits `estimatedTotal` with `totalCountExact=false`. | Dashboards that assume exact counts will misstate realtime/audit feed completeness. | Use `totalCountExact` to choose UI copy and pagination behavior; do not compare realtime count as exact. |
| Hybrid fallback visibility | Auto fallback returns mock data on network/5xx and records the reason through `liveStatus`. | Operators can otherwise mistake seed data for live BFF data. | Management Console pages should surface `getLiveStatusSnapshot()` or the existing live-status banner when in hybrid mode. |
| Strict-mode acceptance | `real` mode requires `VITE_BFF_MODE=live` plus `VITE_BFF_FALLBACK=strict`; transport failures surface as `BffError`. | Acceptance that says "real mode does not silently mock" only holds under strict fallback. | Live validation commands should set `VITE_BFF_FALLBACK=strict`; hybrid validation should be labeled as degraded/fallback-tolerant. |

## Operator Journey

Recommended read-only smoke path after a valid lupin-dev Bearer token is
available. This journey intentionally avoids all write and live-capital side
effects.

1. Session bootstrap: set `VITE_BFF_MODE=live`, `VITE_BFF_FALLBACK=strict`,
   and `VITE_BFF_BASE_URL` for the target BFF. Store a valid Bearer token in
   `sessionStorage["pantheon.bff.bearerToken"]` or use the established auth
   provider. Verify `/bff/me` returns an operator identity before Management
   reads.
2. List smoke: call `managementClient[family].list()` for every
   `MANAGEMENT_FAMILIES` entry. Assert the response has `items`, `cursor`,
   `pageSize`, and the expected `totalCountExact` class semantics.
3. Detail smoke: for every non-audit family with at least one list item, call
   `get(item.id)` and assert either a 2xx DTO with the same id or a typed BFF
   404 envelope. Treat unexpected 5xx, raw HTML, or untyped errors as failures.
4. Fallback-negative smoke: repeat one representative list with a deliberately
   unreachable BFF in `VITE_BFF_FALLBACK=auto`; verify mock data is returned
   and `liveStatus.lastError` / `fellBackAt` are populated. Then set
   `VITE_BFF_FALLBACK=strict` and verify the same failure throws `BffError`.
5. UI state smoke: confirm the Management Console live banner or equivalent
   status surface distinguishes `mock`, `hybrid`, and `real` using
   `getLiveStatusSnapshot()`. The operator should not need to infer live-vs-seed
   data from row contents.
6. Evidence capture: publish the smoke result as a narrow JSON/Markdown evidence
   file with target URL, timestamp, route list, actual status codes, and field
   names only. Do not store Bearer tokens, PII, or full sensitive payloads.

Do not run this FE-002 journey against mutation routes:

- strategy/persona/capital/deployment/rebalance actions;
- deployment create or patch;
- approval decisions, alert acknowledgements, intervention decisions;
- confirm-token lifecycle routes;
- any route that can emit a broker order or change live capital exposure.

## Frontend Handoff Notes

- Treat `/home/lupin/code/execute-plans/src/lib/bff/client.ts` as the primary
  Management Console read seam. Page components should import from
  `@/lib/bff/client` rather than reaching into `bff-v1/lists.ts` directly.
- Use `MANAGEMENT_FAMILIES` for broad coverage checks, but keep UI affordances
  family-aware. `audit` is list-only, and realtime/audit feeds have non-exact
  count semantics.
- For list views, honor `ListEnvelope` metadata instead of deriving exact totals
  from `items.length`. `alerts` intentionally omits `estimatedTotal`.
- For detail views, distinguish three outcomes: live DTO, typed BFF 404, and
  hybrid fallback to seed/undefined. The third case should surface fallback
  state rather than a definitive "not found".
- 4xx responses are real backend replies and should not fall back to mock.
  Display the typed `BffError` envelope where the UI already has error state.
- If backend DTO shapes drift from seed/domain shapes, add an explicit
  `adaptLive` callback at the read seam instead of normalizing ad hoc in page
  components.
- Keep command/write flows in FE-004. The read adapter may display approvals,
  alerts, deployments, and rebalances, but it should not own decide,
  acknowledge, create, patch, or action mutations.

## Parent Absorption Checklist

Before parent closeout or future live-smoke absorption, confirm:

- FE-002 finalization records the Rev2 ranking formula detail fix and the
  existing focused verification:
  `npm test -- --run src/lib/bff/__tests__/client.test.ts` and
  `npm run build`.
- Any authenticated live DTO smoke stores evidence under `docs/bff/evidence/`
  and labels whether it ran in strict real mode or hybrid auto-fallback mode.
- UI handoff keeps `audit` out of generic detail drawers unless a canonical
  audit detail route is added later.
- Operator-facing live/fallback status is visible when Management pages are fed
  by `managementClient`.
- No L1 canonical truth or backend route registry changes are inferred from this
  packet; it is advisory support material only.

## Verification Notes For This Sidecar

No runtime, canonical, or frontend implementation was changed by this sidecar.
Verification for the packet consisted of source inspection only:

```bash
jq '.tasks[] | select(.id=="BFF-LUV-FE-002" or .id=="BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF")' ai-status.json
sed -n '1,260p' docs/bff/execution-tasks/2026-05-09-execute-plans-frontend-live-completion/BFF-LUV-FE-002-management-read-adapters.md
sed -n '1,220p' .orchestrator/reviews/BFF-LUV-FE-002-review-codex2.md
sed -n '1,320p' /home/lupin/code/execute-plans/src/lib/bff/client.ts
sed -n '1,320p' /home/lupin/code/execute-plans/src/lib/bff-v1/lists.ts
sed -n '1,260p' /home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts
sed -n '1,280p' /home/lupin/code/execute-plans/src/lib/bff/__tests__/client.test.ts
sed -n '1,280p' /home/lupin/code/execute-plans/src/lib/bff-v1/liveTransport.ts
rg -n '^  /bff/(strategies|personas|capital-pools|ranking-formulas|rebalances|deployments|evolution-programs|research-experiments|jobs|runtimes|alerts|incidents|approvals|audit|artifacts|tools|mcp-servers|mcp-tools|skills|channels)(/\\{[^}]+\\})?:' /home/lupin/code/execute-plans/.lovable/feedback/2026-05-07-final/Pantheon_BFF_OpenAPI_3_1.yaml
git diff --check -- support/sidecars/BFF-LUV-FE-002/BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF.md
git status --short -- support/sidecars/BFF-LUV-FE-002/BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF.md
```

## Reviewer Handoff

Reviewer (Claude) should verify:

1. This packet is support-only and does not modify canonical truth, runtime
   implementation, registry state, or frontend implementation.
2. The Management read surface matrix matches the approved FE-002 adapter
   surface and final OpenAPI route paths.
3. The query gap matrix is framed as live-evidence/front-end absorption work,
   not as a contradiction of the approved FE-002 implementation.
4. The operator journey remains read-only and excludes live-capital side-effect
   smoke.
5. Parent owner can use this packet as advisory input without treating it as an
   approved replacement for the BFF-LUV-FE-002 implementation record.

## Finalization Status

Claude approved this sidecar packet on 2026-05-09 with no follow-up changes
required. Closeout verification remained support-scoped:

```bash
git diff --check -- support/sidecars/BFF-LUV-FE-002/BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF.md .orchestrator/reviews/BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF-review-claude.md
git status --short -- support/sidecars/BFF-LUV-FE-002/BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF.md .orchestrator/reviews/BFF-LUV-FE-002-SIDECAR-BFF-HANDOFF-review-claude.md
```

This packet is finalized as support-only advisory input for the parent owner.
