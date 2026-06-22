# Review: AG-FE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2

| Field | Value |
|---|---|
| Task ID | `AG-FE-TR-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Reviewer | Claude |
| Owner | Claude2 |
| Review date | 2026-06-22 |
| Outcome | **APPROVED** |

---

## Scope Check

Support artifact only (`helper_kind: bff_handoff_packet`, `mutates_canonical: false`).
No L1 canonical truth, OpenAPI, JSON schemas, BFF runtime, registry/governance, or
execute-plans frontend code modified. Scope is correct.

## Acceptance Criteria

| Criterion | Met? | Note |
|---|---|---|
| Create support artifacts only | ✓ | Single sidecar file; no canonical files touched. |
| Do not edit canonical truth | ✓ | `mutates_canonical: false`; sources read only. |
| Hand off the packet to the assigned reviewer | ✓ | Task in `review` with full packet committed at `d3572d36`. |

## Observation Resolutions

### Observation 1 — `EvidenceRef` Export

**Evidence is sound.** The scan of `execute-plans/src/lib/bff-v1/agora/types.ts`
confirms that every shared Agora domain interface uses `export interface`. The
decision to export `EvidenceRef` from `types.ts` is correct and consistent with
the pattern. The `workshops.ts` re-export pattern (`export type { … } from "./types"`)
is the right model for `tradingRoom.ts` to follow.

The complete `EvidenceRef` interface definition provided is accurate against the
v4 schema and the `ref_type` union is complete.

### Observation 2 — `streamTradingRoom()` Return Type

**Evidence is concrete and dispositive.** `openAskSse` in
`execute-plans/src/lib/bff/agora.ts` (lines 63–74) is the only other SSE
endpoint in the codebase and it returns a live `EventSource` with the handler
already attached. The caller-managed cleanup pattern in `AskPersonas.tsx`
(line 713, `useRef<EventSource | null>`) closes the circuit.

The canonical signature for `streamTradingRoom` presented here is directly
parallel to `openAskSse` and uses the correct BFF path
`/bff/agora/trading-room/stream` (confirmed from `agora_v1_3.openapi.yaml`
operationId `streamAgoraTradingRoom`).

The guidance to **not** use `new URL(...)` as the return type is correct and
important.

### Observation 3 — `suggested_size.non_binding: true`

Confirmed intentional — JSON Schema `"const": true` maps to a TypeScript literal
type. The explanation is accurate and no implementer confusion is likely with this
note in place.

## Contract Snapshot Follow-On Task Specification

The table of required snapshot update actions is accurate and actionable:

- `AGORA_V1_CONTRACT_SNAPSHOT` version bump from `1.1` to `1.3` is correctly
  scoped (update `contract_version`, `frozen_by`, and new schema file hashes).
- `AGORA_V1_CAPABILITIES` new entry for `agora.trading_room.v1_3` is correct.
- `AGORA_V1_OPERATIONS` new operationId list from `agora_v1_3.openapi.yaml` is
  correctly scoped.
- The instruction to not edit the `GENERATED FILE` comment body is correct.
- The `node scripts/generate-agora-types.mjs` note handles both the v1.3-ready
  and not-yet-v1.3-ready cases correctly.

Deferral to a separate `AG-XR-*` task is the right call; AG-FE-TR-001 should
add the three v4 type interfaces manually as specified.

## Complete `tradingRoom.ts` Signatures

All nine BFF bindings are present and incorporate both sidecar resolutions
correctly. Notable checks:

- `EvidenceRef` imported from `./types` and re-exported — correct.
- `streamTradingRoom` returns `EventSource` — matches resolution above.
- `submitIntentHandoff` body typed as `GovernedIntentHandoff` which requires
  `no_order_route_proof`; safety note to not strip this field is correct and
  important.
- `MutationHeaders` interface covers `If-Match`, `Idempotency-Key`, and
  `X-Request-ID` — consistent with first sidecar and BFF HA policy.
- `DecideEventBody` union (`approve | reject | defer | modify`) is correct.

## File Layout

The `execute-plans/src/lib/bff-v1/agora/` layout and the new
`execute-plans/src/agora/pages/trading-room/` directory mirroring
`strategy-workshop/` are confirmed and consistent with the first sidecar.

## Verdict

All three prior observations are resolved with codebase evidence. The contract
snapshot follow-on task is well-specified and correctly deferred. The complete
signatures are accurate and safe. No canonical truth changes. **Approved.**

AG-FE-TR-001 may absorb both sidecars at the parent owner's discretion.
The three items from the first sidecar review are now closed:
- `EvidenceRef` → export from `types.ts`
- `streamTradingRoom` → returns `EventSource`
- `non_binding: true` → intentional literal; no change
