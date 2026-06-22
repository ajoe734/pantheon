# AG-FE-TR-001 BFF Handoff Packet — Followup 2

| Field | Value |
|---|---|
| Task ID | `AG-FE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Helper kind | `bff_handoff_packet` |
| Parent task | `AG-FE-TR-001` — Trading Room tab + multi-strategy switcher |
| Parent owner / reviewer | `Claude` / `Codex` |
| Prepared by | `Claude2` |
| Reviewer | `Claude` |
| Date | 2026-06-22 |
| Mutates canonical truth | `false` |
| Supersedes | none — supplements `AG-FE-TR-001-SIDECAR-BFF-HANDOFF` |
| Status | Ready for reviewer handoff |

This packet is a support artifact only. It does not modify L1 canonical truth,
OpenAPI, JSON schemas, BFF runtime, registry/governance implementation, or
execute-plans frontend code.

It resolves the three non-blocking reviewer observations from
`AG-FE-TR-001-SIDECAR-BFF-HANDOFF-REVIEW.md`, confirms the SSE implementation
pattern discovered in the codebase, and adds the contract snapshot follow-on
task specification deferred in the first sidecar.

---

## Purpose

`AG-FE-TR-001-SIDECAR-BFF-HANDOFF` was approved by Claude on 2026-06-22 with
three non-blocking observations left open for the parent task implementer:

1. `EvidenceRef` export decision.
2. `streamTradingRoom()` return type — `EventSource` or `URL`.
3. `suggested_size.non_binding: true` literal boolean was intentional.

Observation 3 was already self-explanatory. This followup resolves observations
1 and 2 with evidence from the live codebase, and adds the follow-on contract
snapshot task specification from the first sidecar's § Contract Snapshot Gap.

---

## Resolution: Observation 1 — `EvidenceRef` Export Decision

**Decision: export `EvidenceRef` from `types.ts`.**

Evidence from `execute-plans/src/lib/bff-v1/agora/types.ts`:
All shared domain interfaces in this file are exported (`export interface`).
The file is the single canonical location for all Agora BFF v1 types consumed
across multiple modules. Current exports include `TradingEvent`, `TradingIntent`,
`StrategyWorkshop`, `CandidatePool`, `DashboardRecipe`, `ResearchPlan`, etc.

`EvidenceRef` is referenced by both `TradingDecisionEvent` and
`GovernedIntentHandoff`. Keeping it module-local in `tradingRoom.ts` would break
this pattern and create a hidden cross-import dependency the moment any other
module needs evidence references.

**Correct placement:**

```ts
// In execute-plans/src/lib/bff-v1/agora/types.ts
// Add alongside the other v4 trading room interfaces.

export interface EvidenceRef {
  ref_type:
    | "evidence_bundle"
    | "evidence_item"
    | "source_record"
    | "citation"
    | "experiment_artifact"
    | "registry_entry"
    | "consult_memo"
    | "research_run"
    | "telemetry_snapshot"
    | "market_context";
  ref_id: string;
  summary?: string;
  data_cutoff?: string;
}
```

`TradingDecisionEvent` and `GovernedIntentHandoff` then import it from
`types.ts` or from `tradingRoom.ts` re-exporting it — the `workshops.ts`
pattern uses `export type { … } from "./types"` at the top of the module file.

---

## Resolution: Observation 2 — `streamTradingRoom()` Return Type

**Decision: return `EventSource`.**

Evidence from `execute-plans/src/lib/bff/agora.ts` (lines 63–74):

```ts
/** Open an EventSource subscribed to the ask SSE channel. Returns the EventSource. */
export function openAskSse(onMessage: (ev: MessageEvent) => void, baseUrl?: string): EventSource {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/events/stream?channel=ask`;
  const es = new EventSource(url);
  es.addEventListener("message", onMessage);
  es.addEventListener("error", (e) => {
    // allow callers to observe errors via the returned EventSource
  });
  return es;
}
```

This is the only other SSE endpoint in the codebase. It returns a live
`EventSource` object with the `message` handler already attached. Callers hold
the reference for cleanup.

**Canonical signature for `streamTradingRoom`:**

```ts
/** Open an EventSource for Trading Room real-time updates. Returns the EventSource. */
export function streamTradingRoom(
  onMessage: (ev: MessageEvent) => void,
  baseUrl?: string
): EventSource {
  const base = resolvedBase(baseUrl);
  const url = `${base}/bff/agora/trading-room/stream`;
  const es = new EventSource(url);
  es.addEventListener("message", onMessage);
  es.addEventListener("error", (_e) => {
    // callers observe errors via the returned EventSource
  });
  return es;
}
```

**Notes for AG-FE-TR-001:**
- BFF path is `/bff/agora/trading-room/stream` (from `agora_v1_3.openapi.yaml`
  operationId `streamAgoraTradingRoom`, path `GET /bff/agora/trading-room/stream`).
- `TradingRoomPage.tsx` holds the returned `EventSource` in a `useRef<EventSource | null>`
  and calls `.close()` in the `useEffect` cleanup — matching the pattern used
  in `AskPersonas.tsx` (`execute-plans/src/agora/pages/AskPersonas.tsx`, line 713).
- Do not use `new URL(...)` as the return value. The codebase SSE pattern
  returns the live `EventSource`, not just a constructed URL.

---

## Confirmation: Observation 3 — `suggested_size.non_binding: true`

`suggested_size.non_binding` is typed as a literal `true` (not `boolean`) because
the v4 JSON schema for `TradingDecisionEvent` defines this property with
`"const": true`. TypeScript literal types are the correct encoding of a JSON
Schema `const` constraint. Implementers should not widen it to `boolean`.

No change required.

---

## Contract Snapshot Follow-On Task Specification

The first sidecar (§ Contract Snapshot Gap) identified that `types.ts` was
generated from the v1.1 contract snapshot (`AG-XR-001`) and does not include
the v1.3 trading room routes or v4 schemas.

**Scope of the follow-on snapshot update task:**

| Item | Required action |
|---|---|
| `AGORA_V1_CONTRACT_SNAPSHOT` in `types.ts` | Update `contract_version` from `"1.1"` to `"1.3"`, update `frozen_by`, and add the new schema file hashes for `trading_room_aggregate`, `trading_decision_event`, `governed_intent_handoff`. |
| `AGORA_V1_CAPABILITIES` in `types.ts` | Add a new capability entry for `agora.trading_room.v1_3` covering path prefix `/bff/agora/trading-room` and `/bff/agora/trading-intents`. |
| `AGORA_V1_OPERATIONS` (if this array exists in the generated section) | Add the nine trading room operationIds from `agora_v1_3.openapi.yaml`. |
| Generated file header | Update `contract_version` reference only; do not edit the `GENERATED FILE` comment body or the regeneration instruction. |
| `node scripts/generate-agora-types.mjs` | Run this script after manual changes are reviewed — if the script already handles v1.3 bundle entries, its output must match the manual additions exactly. If the script does not yet support v1.3, the manual additions are the interim truth until the script is updated. |

**This follow-on task should be a separate `AG-XR-*` task**, not absorbed into
AG-FE-TR-001. AG-FE-TR-001 must add the three v4 type interfaces manually (as
specified in the first sidecar). The snapshot update that makes the generated
file authoritative is a separate contract-governance concern.

---

## Complete `tradingRoom.ts` Function Signatures

For AG-FE-TR-001 implementers, the full set of exports from `tradingRoom.ts`,
incorporating all resolutions from both sidecars:

```ts
import type {
  TradingRoomAggregate,
  TradingDecisionEvent,
  GovernedIntentHandoff,
  EvidenceRef,
} from "./types";

export type { TradingRoomAggregate, TradingDecisionEvent, GovernedIntentHandoff, EvidenceRef };

interface DetailEnvelope { data?: Record<string, unknown>; [key: string]: unknown }
interface CommandResponse { [key: string]: unknown }

export interface DecideEventBody {
  decision: "approve" | "reject" | "defer" | "modify";
  rationale?: string;
  modifications?: Record<string, unknown>;
}

export interface MutationHeaders {
  "If-Match"?: string;
  "Idempotency-Key"?: string;
  "X-Request-ID"?: string;
}

// --- Queries ---
export async function getTradingRoom(baseUrl?: string): Promise<TradingRoomAggregate>;
export async function getTradingRoomStrategy(strategyId: string, baseUrl?: string): Promise<DetailEnvelope>;
export async function listDecisionEvents(params?: { event_kind?: string; state?: string }, baseUrl?: string): Promise<TradingDecisionEvent[]>;
export async function getDecisionEvent(decisionEventId: string, baseUrl?: string): Promise<TradingDecisionEvent>;
export async function getTradingIntent(intentId: string, baseUrl?: string): Promise<DetailEnvelope>;

// --- Mutations ---
export async function decideEvent(decisionEventId: string, body: DecideEventBody, headers?: MutationHeaders, baseUrl?: string): Promise<CommandResponse>;
export async function submitIntentHandoff(intentId: string, body: GovernedIntentHandoff, headers?: MutationHeaders, baseUrl?: string): Promise<CommandResponse>;
export async function withdrawIntent(intentId: string, headers?: MutationHeaders, baseUrl?: string): Promise<CommandResponse>;

// --- SSE ---
export function streamTradingRoom(onMessage: (ev: MessageEvent) => void, baseUrl?: string): EventSource;
```

**Safety invariant for `submitIntentHandoff`:** The `body` parameter is typed as
`GovernedIntentHandoff`, which includes `no_order_route_proof: "agora_request_only_no_order_route"`
as a required field. TypeScript enforces this at the call site — the caller
cannot omit it. The `tradingRoom.ts` implementation must not strip or override
this field before sending.

---

## File Layout Confirmed

```
execute-plans/src/lib/bff-v1/agora/
  types.ts                   ← add EvidenceRef (exported), TradingRoomAggregate,
                               TradingDecisionEvent, GovernedIntentHandoff here
  tradingRoom.ts             ← NEW: all nine BFF bindings + re-export from types.ts
  workshops.ts               ← existing; do not modify

execute-plans/src/agora/pages/
  strategy-workshop/
    StrategyWorkshopPage.tsx   ← existing pattern
    StrategyWorkshopPage.test.tsx
  trading-room/              ← NEW directory
    TradingRoomPage.tsx        ← NEW: follow StrategyWorkshopPage.tsx structure
    TradingRoomPage.test.tsx   ← NEW: follow StrategyWorkshopPage.test.tsx structure
```

---

## Sources Read for This Followup

| Source | Finding |
|---|---|
| `support/sidecars/AG-FE-TR-001/AG-FE-TR-001-SIDECAR-BFF-HANDOFF-REVIEW.md` | Three non-blocking observations identified for resolution. |
| `execute-plans/src/lib/bff-v1/agora/types.ts` (exports scan) | All shared Agora domain interfaces are `export interface`; `EvidenceRef` must follow this pattern. |
| `execute-plans/src/lib/bff/agora.ts` lines 63–74 | `openAskSse` is the canonical SSE pattern: returns live `EventSource`, attaches `message` handler, returns for caller-managed cleanup. |
| `execute-plans/src/agora/pages/AskPersonas.tsx` line 713 | Caller holds `EventSource` in `useRef<EventSource | null>` for cleanup in `useEffect`. |
| `execute-plans/src/lib/bff-v1/agora/workshops.ts` | No SSE endpoints; `resolvedBase`, `recordFrom`, `parseJson` helper pattern confirmed. |
| `execute-plans/src/lib/bff-v1/paths.ts` line 167 | Global SSE path: `/bff/events/stream`. Trading Room uses its own route `/bff/agora/trading-room/stream`. |
| `execute-plans/src/agora/pages/strategy-workshop/` (directory listing) | Confirmed: one `.tsx` + one `.test.tsx` — `trading-room/` must mirror this. |
| `services/control-plane/openapi/agora_v1_3.openapi.yaml` | Path `GET /bff/agora/trading-room/stream` is operationId `streamAgoraTradingRoom` — no query params. |
