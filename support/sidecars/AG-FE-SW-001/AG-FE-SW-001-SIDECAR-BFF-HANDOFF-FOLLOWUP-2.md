# AG-FE-SW-001 Sidecar Follow-up 2: BFF and Frontend Handoff Delta

| Field | Value |
|---|---|
| Task ID | `AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-SW-001` - TradingDeskShell + Strategy Workshop tab |
| Parent owner / reviewer | `Claude` / `Codex` |
| Sidecar owner / reviewer | `Codex` / `Claude` |
| Date | 2026-06-22 |
| Pantheon dev base inspected | `4652e26ab1e2dc8b478642431df8528b2f6af941` |
| Prior packet | `support/sidecars/AG-FE-SW-001/AG-FE-SW-001-SIDECAR-BFF-HANDOFF.md` |
| Mutates canonical truth | `false` |
| Status | Ready for reviewer handoff |

This is a support-only follow-up to the already merged AG-FE-SW-001 handoff
packet. It does not edit L1 truth, OpenAPI, JSON schemas, BFF runtime,
registry/governance code, or execute-plans frontend code. The parent owner
decides whether to absorb this delta into the main AG-FE-SW-001 implementation.

---

## Sources Rechecked

| Source | Follow-up finding |
|---|---|
| `AI_COLLABORATION_GUIDE.md` | Sidecar packets remain L0/L3-style support records and do not override canonical architecture or policy truth. |
| `.orchestrator/task-briefs/ag_fe_sw_001_sidecar_bff_handoff_followup_2.md` | Scope is support-only BFF query gap, operator journey, and frontend handoff material. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` | Active task is `in_progress`, owner `Codex`, reviewer `Claude`, artifact target is this file. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001` | Parent remains `todo`; dependencies include `AG-FE-ID-001` and `AG-XR-OPENAPI-004`; acceptance forbids invented fields/routes/widgets and direct page fetches. |
| `AI_NAME=Codex python3 scripts/ai_status.py show AG-FE-SW-001-SIDECAR-BFF-HANDOFF` | Prior packet is archived `done`; PR #2206 merged at `4652e26ab1e2dc8b478642431df8528b2f6af941`. |
| `services/control-plane/bff/agora/strategy_workshop/router.py` | Runtime-live workshop list/create/get/messages/events/completeness/stream routes remain present; versions, legacy research-runs, consultations, and conclude remain explicit 501 stubs. |
| `services/control-plane/bff/agora/research/router.py` | Plan-first research plan/run routes exist under `/bff/agora/workshops/{workshop_id}/research-plans`, `/bff/agora/research-plans/*`, and `/bff/agora/research-runs/*`. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | v1.3 contract includes cards, patch proposals, version comparisons, readiness, research plans/runs, and typed stream/card schemas. |
| `services/control-plane/specs/agora/v4/capability_manifest_v1_3.json` | `agora.workshop.v1` has `execution_authority: none`; `agora.research.v1` is `research_only`. |
| `services/control-plane/specs/agora/v4/workshop_card.schema.json` | 12 card types are constrained by `card_type`; frontend must not infer cards from markdown or event text. |
| `services/control-plane/specs/agora/v4/workshop_stream_event.schema.json` | Typed stream event catalog exists, but runtime stream currently emits the simpler SSE envelope from the router. |
| `/home/lupin/code/execute-plans` | Checkout is detached at `574cc541bf326e031a2f6bf9081e428a708b929a`. |
| `/home/lupin/code/execute-plans/src/App.tsx` | Agora still mounts legacy routes; no `/agora/trading-room`, `/agora/strategy-workshop`, or `/agora/strategy-performance` routes are present. |
| `/home/lupin/code/execute-plans/src/agora/AgoraLayout.tsx` | Current Agora shell is grouped side navigation, not the design-approved three-tab TradingDeskShell. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/paths.ts` | No workshop path builders exist. |
| `/home/lupin/code/execute-plans/src/lib/bff/agora.ts` | Live adapter covers daily/signals/inbox/journal/ask only; no workshop client exists. |
| `/home/lupin/code/execute-plans/src/lib/bff-v1/agora/types.ts` | Contract snapshot is still v1.0; no v1.3 `WorkshopCard`, `WorkshopStreamEvent`, readiness, patch, or version comparison types are present. |

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned.

---

## Delta From Prior Packet

The prior support packet remains valid on the inspected dev base. This
follow-up adds only a freshness check and a compact parent-owner checklist.

No new runtime implementation was found for:

- `GET /bff/agora/workshops/{workshop_id}/cards`
- `/bff/agora/workshops/{workshop_id}/patch-proposals*`
- `/bff/agora/workshops/{workshop_id}/version-comparisons`
- `/bff/agora/workshops/{workshop_id}/readiness*`

No Strategy Workshop frontend implementation was found in the detached
execute-plans checkout:

- no TradingDeskShell route family;
- no `/agora/strategy-workshop` route;
- no StrategyWorkshop page/component tree;
- no `src/lib/bff-v1/agora/workshops.ts`;
- no workshop path builders;
- no v1.3 generated Agora types.

---

## Parent-Ready Runtime Surface

`AG-FE-SW-001` can safely build a strict client for these runtime-live BFF
routes:

| Need | Route | Parent rule |
|---|---|---|
| List workshops | `GET /bff/agora/workshops` | Use for landing and picker state; include user/tenant-scoped query keys. |
| Create workshop | `POST /bff/agora/workshops` | Require `Idempotency-Key`; do not persist raw `initial_message` in frontend storage. |
| Load detail | `GET /bff/agora/workshops/{workshop_id}` | Capture the HTTP `ETag` and `meta.etag`. |
| Append message | `POST /bff/agora/workshops/{workshop_id}/messages` | Require latest `If-Match` plus fresh `Idempotency-Key`; refetch on 409. |
| Event history | `GET /bff/agora/workshops/{workshop_id}/events` | Render redacted/private-ref event records only; do not reconstruct raw text. |
| Completeness baseline | `GET /bff/agora/workshops/{workshop_id}/completeness` | Render empty/unassessed state when `data` is null-like; do not invent grades. |
| SSE stream | `GET /bff/agora/workshops/{workshop_id}/stream` | Expect first `workshop.connected`; dedupe by SSE event id; use `Last-Event-ID` for reconnect. |

Plan-first research routes are runtime-live, but should remain a separate
client module or later slice unless AG-FE-SW-001 only needs shell-level links:

- `GET|POST /bff/agora/workshops/{workshop_id}/research-plans`
- `GET /bff/agora/research-plans/{plan_id}`
- `POST /bff/agora/research-plans/{plan_id}/approve`
- `POST /bff/agora/research-plans/{plan_id}/cancel`
- `GET|POST /bff/agora/research-plans/{plan_id}/runs`
- `GET /bff/agora/research-runs/{run_id}`
- `POST /bff/agora/research-runs/{run_id}/cancel`
- `GET /bff/agora/research-runs/{run_id}/artifacts`

---

## Stop Lines To Preserve

These stop lines should carry into the parent implementation unchanged.

```text
Do not render live typed WorkshopCard projections until a runtime
GET /bff/agora/workshops/{workshop_id}/cards handler exists. The v1.3 schema is
contract truth, but the inspected BFF runtime does not expose that route.
```

```text
Do not implement live patch proposal, version comparison, readiness reassess,
consultation, conclude, or workshop-level legacy research-runs actions in
AG-FE-SW-001. The inspected runtime either lacks handlers or returns explicit
501 stubs for those edges.
```

```text
Do not depend on v1.3 frontend types until execute-plans regenerates or mirrors
the v1.3 bundle. The inspected checkout still carries a v1.0 Agora contract
snapshot and lacks WorkshopCard/WorkshopStreamEvent/readiness/patch/version
types.
```

```text
Do not route pages directly through fetch(), internal /api/v1/* endpoints,
Management clients, broker routes, RuntimeBinding writes, capital binding, or
local seed fallback in strict live mode. AG-FE-SW-001 must use
src/lib/bff-v1/agora/* client boundaries only.
```

---

## Minimum Frontend Checklist For Parent

The parent owner can use this as the minimum slice for AG-FE-SW-001:

1. Add the IA routes and redirects:
   - `/agora/trading-room`
   - `/agora/strategy-workshop`
   - `/agora/strategy-workshop/:workshopId`
   - `/agora/strategy-performance`
   - legacy redirects from `daily`, `watchlist`, `signals`, and `notebook` per the IA decision.
2. Add `src/lib/bff-v1/agora/workshops.ts` with strict live methods for the seven runtime-live workshop routes.
3. Add path builders in `src/lib/bff-v1/paths.ts` only for canonical `/bff/agora/workshops*` routes that exist at runtime.
4. Implement Strategy Workshop shell with conversation history, composer, SSE connection state, and completeness empty state.
5. Keep card projections, patch/version/readiness actions, and research execution cards behind explicit blockers or later tasks.
6. Add tests that prove pages do not call `fetch()` directly, strict live mode has no local seed fallback, mutation headers are required, and no Management/broker/capital route enters the Agora bundle.

---

## Reviewer Checklist

- Support artifact only; no canonical/runtime/execute-plans changes.
- Prior packet validity is preserved and this file only adds a follow-up delta.
- Runtime-live route list matches `strategy_workshop/router.py` and `research/router.py`.
- Stop lines prevent invented card/action/type surfaces.
- Parent handoff is narrow enough for Claude to absorb or reject without blocking unrelated AG-FE work.
