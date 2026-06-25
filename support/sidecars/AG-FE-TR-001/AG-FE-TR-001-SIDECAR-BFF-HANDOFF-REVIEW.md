# Review: AG-FE-TR-001-SIDECAR-BFF-HANDOFF

| Field | Value |
|---|---|
| Task ID | `AG-FE-TR-001-SIDECAR-BFF-HANDOFF` |
| Reviewer | Claude |
| Owner | Claude2 |
| Review date | 2026-06-22 |
| Outcome | **APPROVED** |

---

## Scope Check

This is a support artifact (`helper_kind: bff_handoff_packet`). It does not
modify L1 canonical truth, OpenAPI, JSON schemas, BFF runtime, or
registry/governance implementation. Scope is correct.

## Acceptance Criteria

| Criterion | Met? | Note |
|---|---|---|
| Create support artifacts only | ✓ | Sidecar file only; no canonical files touched. |
| Do not edit canonical truth | ✓ | `mutates_canonical: false` confirmed; all sources read, none written. |
| Hand off the packet to the assigned reviewer | ✓ | Task moved to `review` with full packet present. |

## Packet Quality Assessment

### 1. BFF Route Coverage

All 9 v1.3 operationIds from `agora_v1_3.openapi.yaml` are mapped to
proposed `tradingRoom.ts` function signatures with correct return types.
Mutation-path functions include the optional `headers: MutationHeaders`
parameter, which is the right pattern to carry idempotency and etag headers.

### 2. TypeScript Interface Fidelity

`TradingRoomAggregate`, `TradingDecisionEvent`, and `GovernedIntentHandoff`
each mirror their v4 JSON schema (`additionalProperties: false` schemas
verified). Required fields are correctly typed as non-optional. Optional schema
fields that appear in the schemas' `properties` without being in `required` are
correctly typed as optional (`?`). No invented fields added.

`EvidenceRef` is defined once and shared — correct approach for two types that
both reference it.

### 3. Safety Constraints

Five constraints explicitly enumerated with enforcement columns:

- No order routing via `decideEvent` (result is `CommandResponse`, not a broker instruction).
- `no_order_route_proof: "agora_request_only_no_order_route"` injected by frontend on `submitIntentHandoff`.
- No capital binding or RuntimeBinding creation.
- `no_order_route_proof` field on `TradingDecisionEvent` must be rendered, not hidden.
- Typed degraded response required when `getTradingRoom` returns degraded envelope.

All five are substantive and correct per `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`.

### 4. Operator Journey

Nine-step journey covers land → strategy select → decision event browse →
event detail → trader decision → intent view → governed handoff → withdraw →
SSE stream. Maps cleanly to the nine BFF functions.

### 5. Dependency Verification

All four upstream dependencies (`AG-FE-SW-001`, `AG-BE-TR-001`,
`AG-BE-CP-001`, `AG-XR-OPENAPI-004`) confirmed done. AG-FE-TR-001 is
unblocked.

### 6. Contract Snapshot Gap

v1.1 → v1.3 gap is correctly identified. Packet instructs against touching the
generated file header comment. Follow-on snapshot update is correctly deferred
to a separate task. This is the right call.

### 7. Boundary Clarity

"Owns" / "Does not own" table is precise. Particularly important: layout file
`TradingDeskLayout.tsx` and existing v1 types (`TradingEvent`, `TradingIntent`)
are explicitly out of scope.

## Minor Non-Blocking Observations for Parent Task Implementer

1. `EvidenceRef` is not marked `export` in the packet's TypeScript snippet.
   AG-FE-TR-001 should decide whether to export it from `types.ts` or keep it
   module-local in `tradingRoom.ts`. Either is fine; the packet leaves this
   open correctly.

2. `streamTradingRoom()` return type is listed as `EventSource (or URL)`.
   AG-FE-TR-001 should align with the pattern used for any other SSE endpoints
   in the `workshops.ts` codebase before settling on a return type.

3. `suggested_size.non_binding: true` is typed as a literal boolean — correct
   TypeScript for a schema constraint that prohibits `false`. AG-FE-TR-001
   implementer should be aware this is intentional, not a typo.

None of these block the handoff packet or require changes to the sidecar.

## Verdict

The packet is accurate, complete, and safe to absorb into AG-FE-TR-001 at the
parent owner's discretion. All acceptance criteria met. No canonical truth
changes made. **Approved.**
