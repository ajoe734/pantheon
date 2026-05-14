# BFF-CONSOL-024 Sidecar: BFF and Frontend Handoff Packet

Task-ID: BFF-CONSOL-024-SIDECAR-BFF-HANDOFF
Parent: BFF-CONSOL-024 — Deprecate old action receipt
Author: Claude
Reviewer: Codex2
Helper Kind: bff_handoff_packet
Created: 2026-05-14
Status: ready for review

---

## 1. Purpose

This sidecar prepares the operator-facing and frontend-facing handoff materials for
BFF-CONSOL-024. It documents the BFF query gap analysis, the post-deprecation operator
journey, and the frontend migration state so the parent task reviewer (Codex) and the
final acceptance task (BFF-CONSOL-027) have a structured view of what changed and what
remains open.

This packet does **not** modify canonical truth. It is a support artifact only.

---

## 2. What BFF-CONSOL-024 Delivered

### 2.1 Deprecation Headers on `/bff/actions/*`

`POST /bff/actions/{entityType}/{entityId}/{actionId}` remains fully operational but
now returns the following headers on every accepted response:

| Header | Value |
|---|---|
| `Deprecation` | `true` |
| `Sunset` | `Mon, 15 Jun 2026 00:00:00 GMT` |
| `Link` | `</bff/v1/commands>; rel="successor-version"` |
| `Warning` | `299 - "/bff/v1/commands is the successor…"` |
| `X-Pantheon-Deprecated-Route` | `/bff/actions/*` |

Key constants in `services/control-plane/bff/main.py`:
- `_ACTIONS_DEPRECATION_SINCE` = `"2026-05-14"`
- `_ACTIONS_SUNSET_DATE` = `"2026-06-15"`
- `_ACTIONS_SUNSET_HTTP_DATE` = `"Mon, 15 Jun 2026 00:00:00 GMT"`

### 2.2 Deprecated Markers in Response Body

`/bff/actions/*` responses carry `deprecated: true` at three levels:

```json
{
  "status": "accepted",
  "data": {
    "deprecated": true,
    "deprecation": {
      "route": "/bff/actions/{entityType}/{entityId}/{actionId}",
      "replacement": "/bff/v1/commands",
      "deprecated_since": "2026-05-14",
      "sunset": "2026-06-15"
    },
    "receipt": {
      "deprecated": true,
      "deprecation": { "..." }
    }
  },
  "meta": {
    "deprecated": true,
    "deprecation": { "..." }
  }
}
```

Audit and replay tools that inspect `data.receipt` or `meta` will see the same deprecation
signal regardless of which field they consume.

### 2.3 Command Executor Audit Source Tracking

`services/control-plane/bff/command_executor.py` now records:

| Field | Value |
|---|---|
| `source_route` | `"POST /bff/actions/{entityType}/{entityId}/{actionId}"` |
| `deprecated_action_receipt` | `true` when routed through action adapter |
| `admission_route` | `"POST /bff/v1/commands"` (canonical) |

This lets audit consumers reconcile legacy action receipts against final command receipts
without a separate legacy receipt stream.

### 2.4 Frontend Default Caller Migration

`execute-plans/src/lib/bff/runAction.ts` live write path:

- **Before BFF-CONSOL-024**: defaulted to `/bff/actions/{entityType}/{entityId}/{actionId}`
- **After BFF-CONSOL-024**: live writes default to `/bff/v1/commands` via `commandClient.runAction()`
- **Legacy opt-in preserved**: `opts.route = "legacy-actions"` still routes through
  `/bff/actions/*` for explicit compatibility checks

The `KIND_TO_ENTITY_TYPE` map in `runAction.ts:50–66` covers all 15 current entity kinds:
`Strategy`, `Persona`, `CapitalPool`, `Rebalance`, `Deployment`, `Evolution`,
`Research`, `Artifact`, `RankingFormula`, `Tool`, `McpServer`, `McpTool`, `Skill`,
`Channel`, `Runtime`.

Unmapped kinds fall back to `kind.toLowerCase()` — this is intentional for forward
compatibility with future entity kinds.

### 2.5 Verification Evidence (from parent task handoff)

Commands run by Codex2 during BFF-CONSOL-024 implementation:

```bash
# Python suite: 59 passed
python3 -m pytest \
  services/control-plane/bff/tests/test_actions_to_commands_adapter.py \
  services/control-plane/bff/tests/test_command_replay_conflict.py \
  services/control-plane/bff/test_command_executor.py \
  services/control-plane/bff/test_governance_command_submission.py -q

# Frontend suite: 43 passed
cd /home/lupin/code/execute-plans
npx vitest run src/lib/bff/__tests__/runAction.test.ts src/lib/bff-v1/__tests__/writes.test.ts

# TypeScript typecheck: passed
npx tsc --noEmit --pretty false
```

---

## 3. BFF Query Gap Analysis

### 3.1 Gaps Closed by BFF-CONSOL-024

| Gap | Resolution |
|---|---|
| Old action receipts carry no deprecation signal | Fixed: `deprecated: true` at `data`, `data.receipt`, and `meta` |
| Frontend live writes still default to legacy path | Fixed: `runAction.ts` now defaults to `/bff/v1/commands` |
| No deprecation timeline in contract doc | Fixed: `BFF_COMMAND_API_CONTRACT.md §3` documents 2026-05-14 deprecation and 2026-06-15 sunset floor |
| Audit records mix legacy and final admission routes | Fixed: `command_executor.py` persists both `admission_route` (final) and `source_route` (legacy) |

### 3.2 Open Gaps (Post BFF-CONSOL-024)

| Gap | Owner | Tracking |
|---|---|---|
| Staging strict cutover blocked (no Lovable preview URL + credentials) | Gemini | BFF-CONSOL-022 blocker |
| Prod strict cutover depends on staging gate passing | Gemini2 | BFF-CONSOL-023 (todo, depends on 022) |
| Final acceptance packet not yet started | Copilot | BFF-CONSOL-027 (todo, depends on 001..026) |
| Audit/replay tooling still consuming legacy receipt shape | out-of-scope for BFF-CONSOL wave | Post-027 follow-up |
| Operator dashboard does not yet surface deprecation warning to end users | out-of-scope for BFF-CONSOL wave | Post-027 follow-up |

---

## 4. Operator Journey (Post-Deprecation)

### 4.1 Normal Operator Write (Updated Flow)

```
Operator UI (execute-plans)
  └─ runAction() / runCommandAction()
       ├─ [VITE_BFF_REAL_WRITES=false]  → mock mutations path (unchanged)
       └─ [VITE_BFF_REAL_WRITES=true]   → commandClient.runAction()
            └─ POST /bff/v1/commands
                 └─ BFF admission: RBAC / idempotency / audit / policy
                      └─ downstream authority dispatch
```

No change for operators; the UI change is transparent.

### 4.2 Explicit Legacy-Actions Compatibility Check

Callers that need to verify backward compatibility with the old action adapter:

```typescript
await runAction(input, { route: "legacy-actions" });
// ↓ routes to POST /bff/actions/{entityType}/{entityId}/{actionId}
// ↓ returns CommandResponse with deprecated:true markers
// ↓ response headers include Deprecation, Sunset, Warning
```

### 4.3 Audit Consumer Path

Audit consumers that read command receipts:

| Old consumer pattern | New canonical pattern |
|---|---|
| Reads `data.receipt_id` from `/bff/actions/*` response | Read `data.receipt_id` from `/bff/v1/commands` response |
| Treats `receipt.status` as authoritative | `receipt.status` still present; `admission_route` now identifies final vs legacy source |
| No deprecation signal | Check `meta.deprecated` or `data.deprecated` before treating as final receipt |

Deprecation marker check:
```json
{ "meta": { "deprecated": true } }
```
When `meta.deprecated` is `true`, the consumer should switch to `/bff/v1/commands` as the
primary write path and use `source_route` in the audit record for backward reference only.

---

## 5. Frontend Migration State Summary

| File | Migration State |
|---|---|
| `execute-plans/src/lib/bff/runAction.ts` | Live writes default to `/bff/v1/commands`; `legacy-actions` opt-in preserved |
| `execute-plans/src/lib/bff/commandClient.ts` | Canonical `commandClient.runAction()` for `/bff/v1/commands` |
| `execute-plans/src/lib/bff-v1/writes.ts` | BFF-v1 write layer; unaffected by BFF-CONSOL-024 |
| `execute-plans/src/lib/bff-v1/paths.ts` | Canonical path helpers; unaffected |

All 15 entity-kind entries in `KIND_TO_ENTITY_TYPE` have been validated by the adapter
mapping tables in `BFF_COMMAND_API_CONTRACT.md §8.1–§8.16`.

---

## 6. Handoff Notes for BFF-CONSOL-027 Acceptance

BFF-CONSOL-027 final acceptance packet (`support/sidecars/BFF-CONSOL-FINAL/ACCEPTANCE.md`)
must include the following BFF-CONSOL-024 evidence:

- [ ] Confirm `/bff/actions/*` returns `Deprecation: true` header in live smoke
- [ ] Confirm `meta.deprecated: true` present in sample command receipt from `/bff/actions/*`
- [ ] Confirm `data.receipt.deprecated: true` present in sample receipt
- [ ] Confirm `runAction()` live writes default to `/bff/v1/commands` (verify `admission_route` in audit record)
- [ ] Note 2026-06-15 as the sunset floor for `/bff/actions/*`
- [ ] Note that audit/replay tooling migration from legacy receipt shape is a post-wave follow-up

The contract doc reference: `services/control-plane/bff/BFF_COMMAND_API_CONTRACT.md §3`

---

## 7. Open Questions for Parent Owner / Reviewer

1. **Sunset enforcement**: BFF-CONSOL-024 sets the sunset floor at 2026-06-15 but does not
   add a 410-returning hard block after that date. Is a hard block task planned, or is the
   sunset floor advisory only until explicitly cut?

2. **Audit tooling migration**: The audit and replay tools in execute-plans currently read
   legacy receipt shapes from `/bff/actions/*`. Is this tracked as a follow-up ticket, or
   does it need to be gated before BFF-CONSOL-027 acceptance?

3. **Operator dashboard warning**: No UI-visible deprecation warning is shown to operators
   when calls go through the legacy path. Is a visual indicator in scope for the BFF
   consolidation wave or deferred?

---

*This packet is a sidecar support artifact. It does not modify L1 canonical truth, core
contract docs, or runtime/registry/governance implementation. Parent owner (Codex2) decides
whether to absorb any findings into main-line work.*
