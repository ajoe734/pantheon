# SVC-OPENCLAW-LIVE-GATE-HARNESS — BFF & Frontend Handoff Packet

**Sidecar kind:** bff_handoff_packet  
**Helper task:** SVC-OPENCLAW-LIVE-GATE-HARNESS-SIDECAR-BFF-HANDOFF  
**Parent task:** SVC-OPENCLAW-LIVE-GATE-HARNESS  
**Author:** Claude  
**Reviewer:** Codex  
**Status:** revised — error catalogue expanded per Codex review (2026-04-30)  
**Created:** 2026-04-30  

---

## 1. Purpose

This packet documents the BFF query surface, identified query gaps, operator journey, and frontend handoff materials for the live gate harness delivered by `SVC-OPENCLAW-LIVE-GATE-HARNESS`.

This is a **support-only artifact**. It does not modify canonical truth, adapter implementations, or BFF routes. The parent task owner (`Claude2`) decides whether and how to absorb these findings into the main line.

---

## 2. Implemented Surface Summary

### 2.1 Adapter-side routes (`services/openclaw-gateway-adapter`)

| Method | Path | Purpose |
|--------|------|---------|
| `POST` | `/api/openclaw-adapter/broker/live/orders` | Always-rejected live order endpoint (fail-closed guard) |
| `GET`  | `/api/openclaw-adapter/broker/live/gate/status` | Live gate capability + configuration snapshot |
| `POST` | `/api/openclaw-adapter/broker/live/gate/validate` | Run all gate checks without dry handoff; requires `X-Human-Approval-Token` |
| `POST` | `/api/openclaw-adapter/broker/live/gate/dry-handoff` | Gate checks + dry handoff intent + audit record; requires `X-Human-Approval-Token` |
| `GET`  | `/api/openclaw-adapter/broker/live/gate/audit` | Append-only live gate intent/outcome audit trail |

Gate checks enforced in order (all fail-closed):
1. `OPENCLAW_LIVE_ADAPTER_ENABLED` env var must be `"true"`
2. `X-Human-Approval-Token` header must match `OPENCLAW_LIVE_HUMAN_APPROVAL_TOKEN`
3. Active live `RuntimeBinding` must exist for the capital pool (via runtime-manager)
4. Kill switch safe mode must be `normal` or `normal_restored`
5. Active binding must not be in `pending_pause / paused / retired / failed` state

### 2.2 BFF-side routes (`services/control-plane/bff`)

| Method | Path | Auth required | Purpose |
|--------|------|---------------|---------|
| `GET`  | `/api/v1/operator/openclaw/live-gate/status` | operator role | Read-only projection of adapter status |
| `GET`  | `/api/v1/operator/openclaw/live-gate/audit` | operator role | Read-only audit trail, scoped by operator (admins may filter by `capital_pool_id`) |

Client module: `services/control-plane/bff/openclaw_ops_client.py`  
Methods: `get_live_gate_status()`, `list_live_gate_audit()`

---

## 3. BFF Query Gaps

These are gaps found at the time of this packet. The parent task owner should decide whether to close them in the main task scope or as follow-up.

### Gap 1 — Live gate summary missing from operator ops aggregate

**Route affected:** `GET /api/v1/operator/openclaw/ops`  
**What's missing:** The ops aggregate surface (`test_openclaw_ops_surface.py`) reports `gate_state.live_adapter.enabled` from the capabilities snapshot but does NOT include a live gate detail section showing:
- whether `human_approval_configured` is true
- whether `runtime_manager_configured` is true
- the list of `gate_checks` that are wired
- recent audit summary (last N entries, deny count, accept count)

**Impact:** An operator viewing the ops dashboard cannot tell if the live gate is partially configured without navigating to the dedicated status route.

**Suggested resolution (non-binding):** Extend the ops aggregate to include a `live_gate` section sourced from `GET .../broker/live/gate/status`. Low risk — the status route is read-only and already implemented.

### Gap 2 — No test coverage for BFF live gate routes

**Files affected:** `services/control-plane/bff/main.py` lines ~8344–8407  
**What's missing:** No dedicated test file for:
- `GET /api/v1/operator/openclaw/live-gate/status` — success and degraded paths
- `GET /api/v1/operator/openclaw/live-gate/audit` — operator-scoped vs admin-scoped, pagination

Existing `test_openclaw_ops_surface.py` covers the ops aggregate but not these dedicated routes.

**Impact:** BFF live gate routes have no automated contract test. A regression in the BFF projection or RBAC enforcement would not be caught by the test suite.

**Suggested resolution (non-binding):** Add `test_openclaw_live_gate_bff.py` covering:
1. Operator gets status → 200 with gate config fields
2. Unauthenticated gets status → 401
3. Viewer role gets status → 403
4. Adapter unavailable → degraded response (not 500)
5. Operator gets audit → 200, operator_id auto-scoped
6. Admin gets audit with capital_pool_id filter → 200

### Gap 3 — Validate and dry-handoff have no BFF wrapper (expected, documented)

**Routes:** `POST /api/openclaw-adapter/broker/live/gate/validate` and `/dry-handoff`  
**Why no BFF wrapper is correct:** These routes require `X-Human-Approval-Token` in the request header. The BFF must NOT proxy this token — doing so would allow any operator-role session to use a previously-issued approval token without the real-time human in the loop. The human token must travel directly from the operator's secure session to the adapter.

**What the frontend must do:** Call the adapter directly for these two operations, not via BFF proxy. The adapter URL is `PANTHEON_OPENCLAW_GATEWAY_ADAPTER_URL` (already available to the frontend for the session lifecycle surface).

**What should be documented for frontend:** See Section 5 (Operator Journey) and Section 6 (Frontend Contract Notes).

### Gap 4 — No live gate section in BFF surface inventory

**File:** `services/control-plane/bff/BFF_SURFACE_INVENTORY.md`  
**What's missing:** The two BFF live gate routes are not listed in the surface inventory doc.

**Suggested resolution (non-binding):** Add entries for:
- `openclaw_live_gate_status` surface
- `openclaw_live_gate_audit` surface

---

## 4. Error Code Catalogue

The live gate adapter returns structured errors. The frontend should handle each distinctly. Codes are ordered by gate check sequence.

> **Revision note (2026-04-30):** Six codes (`CAPITAL_BINDING_MISMATCH`, `RUNTIME_BINDING_CHECK_FAILED`, `RUNTIME_BINDING_SCHEMA_ERROR`, `SAFE_MODE_CHECK_FAILED`, `SAFE_MODE_CHECK_ERROR`, `SAFE_MODE_SCHEMA_ERROR`) were absent from the initial packet and added per Codex review. These originate from runtime-manager communication paths and the capital pool identity check added in commit 27c4fe8.

| `error_code` | HTTP status | Gate | Meaning | Recommended UI action |
|---|---|---|---|---|
| `LIVE_GATE_DISABLED` | 503 | `live_adapter_enabled` | `OPENCLAW_LIVE_ADAPTER_ENABLED` is not set | Show "Live gate is not active" banner; no retry option |
| `HUMAN_APPROVAL_NOT_CONFIGURED` | 503 | `human_approval_token` | `OPENCLAW_LIVE_HUMAN_APPROVAL_TOKEN` is missing server-side | Show "Live gate misconfigured — contact operator admin" |
| `HUMAN_APPROVAL_TOKEN_MISSING` | 401 | `human_approval_token` | No `X-Human-Approval-Token` in request | Prompt operator to enter approval token |
| `HUMAN_APPROVAL_TOKEN_INVALID` | 403 | `human_approval_token` | Token does not match | Show "Approval token rejected"; do not retry automatically |
| `CAPITAL_POOL_REQUIRED` | 400 | `active_live_runtime_binding` | `capital_pool_id` not provided | Validate form before submission |
| `RUNTIME_MANAGER_NOT_CONFIGURED` | 503 | `active_live_runtime_binding` | No runtime-manager URL configured | Show "Runtime manager not configured" banner |
| `RUNTIME_BINDING_CHECK_FAILED` | 503 | `active_live_runtime_binding` | Binding resolver raised an unexpected exception | Treat as transient; allow retry with back-off; escalate if persistent |
| `RUNTIME_MANAGER_UNAVAILABLE` | 503 | `active_live_runtime_binding` | runtime-manager network error during binding lookup | Transient — allow retry with back-off |
| `RUNTIME_MANAGER_ERROR` | 502 | `active_live_runtime_binding` | runtime-manager returned an upstream error (HTTP ≥ 400) | Show error and runtime-manager status |
| `RUNTIME_BINDING_SCHEMA_ERROR` | 502 | `active_live_runtime_binding` | runtime-manager binding response was not a valid object | Treat as adapter-side issue; show "Unexpected response from runtime manager"; escalate |
| `LIVE_RUNTIME_BINDING_NOT_FOUND` | 409 | `active_live_runtime_binding` | No active live RuntimeBinding for the pool | Show "No active live binding for this pool" with pool ID |
| `CAPITAL_BINDING_MISMATCH` | 409 | `active_live_runtime_binding` | Active RuntimeBinding `capital_pool_id` does not match the requested pool | Show "Capital pool identity mismatch on active binding" with expected vs actual pool ID; do not retry without operator review |
| `LIVE_RUNTIME_BINDING_REQUIRED` | 409 | `active_live_runtime_binding` | Binding exists but is not `deployment_mode: live` | Show binding mode mismatch; link to binding review |
| `SAFE_MODE_CHECK_FAILED` | 503 | `kill_switch_safe_mode` | Safe mode resolver raised an unexpected exception | Treat as transient; allow retry with back-off; escalate if persistent |
| `SAFE_MODE_CHECK_ERROR` | 502 | `kill_switch_safe_mode` | runtime-manager safe-mode endpoint returned HTTP ≥ 400 | Show "Kill switch check failed" with status; escalate to operator admin |
| `SAFE_MODE_SCHEMA_ERROR` | 502 | `kill_switch_safe_mode` | runtime-manager safe-mode response missing `safe_mode_state` | Treat as adapter-side issue; show "Unexpected safe mode response"; escalate |
| `KILL_SWITCH_UNSAFE_STATE` | 409 | `kill_switch_safe_mode` | Safe mode is not `normal` or `normal_restored` | Show "Kill switch / safe mode is active; clear before proceeding" with current `safe_mode_state` |
| `BINDING_IN_UNSAFE_STATE` | 409 | `binding_not_in_rollback` | Binding is in `pending_pause / paused / retired / failed` | Show binding status + "Resolve rollback before live gate" |
| `BINDING_NOT_ACTIVE` | 409 | `binding_not_in_rollback` | Binding status is not `active` | Show binding status; no automatic retry |
| `OPERATOR_REQUIRED` | 401 | auth | No `X-Operator-Id` in request | Should not occur for authenticated operators; check auth chain |
| `LIVE_EXECUTION_DISABLED` | 403 | endpoint | Live order was submitted to the always-rejected endpoint | Should not occur from normal UI; if seen, show "Live execution is permanently disabled in this deployment" |

---

## 5. Operator Journey

### Journey A — Pre-flight check before dry handoff

**Actor:** Operator (authenticated, `operator` role or higher)  
**Goal:** Verify whether the live gate is ready to accept a dry handoff for a capital pool

1. Navigate to **OpenClaw Ops → Live Gate** tab in the operator panel.
2. `GET /api/v1/operator/openclaw/live-gate/status` — BFF returns current gate configuration.
   - UI shows checklist: `live_gate_enabled`, `human_approval_configured`, `runtime_manager_configured`.
   - Each item shows enabled (green) / not configured (red) / unavailable (grey).
3. If all three are green, operator may proceed to dry handoff.
4. If `live_gate_enabled` is false → show `LIVE_GATE_DISABLED` guidance; operator cannot proceed via UI.
5. If `human_approval_configured` is false → show "Contact admin to configure OPENCLAW_LIVE_HUMAN_APPROVAL_TOKEN".

### Journey B — Dry handoff attempt

**Actor:** Operator with approval token  
**Goal:** Submit a dry handoff to verify end-to-end gate attestation

**Important:** The frontend calls the **adapter directly**, not via BFF, because the `X-Human-Approval-Token` must not pass through the BFF.

1. Operator opens a Dry Handoff form with fields: `capital_pool_id`, `strategy_id`, `symbol`, `qty`, `side`, `order_type`, `limit_price` (optional).
2. Operator enters their `X-Human-Approval-Token` in a secure field (masked, never stored in BFF session).
3. Frontend submits:
   ```
   POST {OPENCLAW_ADAPTER_URL}/api/openclaw-adapter/broker/live/gate/dry-handoff
   X-Operator-Id: {operator_id}
   X-Human-Approval-Token: {token}
   X-Trace-Id: {client-generated-uuid}
   Content-Type: application/json
   
   { "capital_pool_id": "...", "strategy_id": "...", "symbol": "...", "qty": ..., "side": "...", "order_type": "..." }
   ```
4. On success (200): Show dry handoff preview with `gate_attestation` fields. Display: binding ID, artifact ID, safe mode, deployment mode. Show "Dry handoff accepted — no real execution occurred" banner.
5. On gate failure: Map `error_code` to UI message per Error Code Catalogue (Section 4).

### Journey C — Audit trail review

**Actor:** Operator or Admin  
**Goal:** Review past dry handoff attempts and denied gate requests

1. `GET /api/v1/operator/openclaw/live-gate/audit?limit=50` via BFF (RBAC-safe).
2. BFF auto-scopes `operator_id` for non-admin operators; admins may filter by `capital_pool_id`.
3. UI shows table: `at`, `event`, `outcome`, `error_code` (if denied), `capital_pool_id`, `strategy_id`, `trace_id`.
4. Each row is expandable to show full audit entry (symbol, qty, side, gate).

### Journey D — Live order rejection (expected behavior)

**Actor:** Any client that mistakenly sends a live order  
**Goal:** Confirm graceful rejection

1. `POST /api/openclaw-adapter/broker/live/orders` returns 403 with `LIVE_EXECUTION_DISABLED`.
2. Frontend should not surface this as a submission path. If encountered, show "Live execution is not available".

---

## 6. Frontend Contract Notes

### Authentication model
- All BFF live gate routes (`/api/v1/operator/openclaw/live-gate/*`) use the standard BFF Bearer token.
- The adapter `validate` and `dry-handoff` routes require an additional `X-Human-Approval-Token` header.
- The BFF must never request or forward the approval token. The operator must enter it directly in the UI for adapter calls.

### Response envelope for BFF routes
BFF live gate status and audit routes follow the standard envelope:
```json
{
  "status": "ok",
  "surface": "openclaw_live_gate_status",
  "data": { ... },
  "snapshot_at": "2026-04-30T09:00:00Z"
}
```
When the adapter is unavailable, the BFF raises an `OpenClawOpsClientError` which the main.py exception handler translates. Frontend should handle `503` gracefully — live gate routes are not critical path.

### Gate capability snapshot fields (from `GET .../live-gate/status`)
```json
{
  "live_gate_enabled": false,
  "live_execution_enabled": false,
  "live_adapter_enabled": false,
  "human_approval_configured": false,
  "runtime_manager_configured": true,
  "gate_checks": [
    "live_adapter_enabled",
    "human_approval_token",
    "active_live_runtime_binding",
    "kill_switch_safe_mode",
    "binding_not_in_rollback"
  ],
  "deployment_stage": "live_gate_harness",
  "is_real_capital": false,
  "is_real_order": false,
  "dry_handoff_only": true
}
```

### Audit entry schema (from `GET .../live-gate/audit`)
```json
{
  "event": "live_gate_dry_handoff_intent",
  "at": "2026-04-30T09:00:00Z",
  "trace_id": "abc123",
  "operator_id": "op-2",
  "capital_pool_id": "pool-alpha",
  "strategy_id": "strat-001",
  "symbol": "AAPL",
  "qty": 100.0,
  "side": "buy",
  "order_type": "market",
  "limit_price": null,
  "is_real_order": false,
  "is_real_capital": false,
  "deployment_stage": "live_gate_harness",
  "dry_handoff": true,
  "outcome": "dry_handoff_accepted",
  "binding_id": "binding-live-1",
  "artifact_id": "artifact-001",
  "safe_mode": "normal"
}
```

### Canonical field for gate harness state in capabilities
The capabilities endpoint includes:
```json
{
  "live_gate_harness": "present_disabled",
  "live_gate": { ... capability snapshot ... }
}
```
`live_gate_harness` values: `"present_disabled"` (default, not yet enabled) or `"enabled"` (when `OPENCLAW_LIVE_ADAPTER_ENABLED=true`). Frontend can use this to show the harness state badge.

---

## 7. Acceptance Checklist (for parent task owner)

- [ ] Gaps 1–4 acknowledged; parent owner decides which to absorb in main scope
- [ ] Error code catalogue reviewed; frontend team has guidance
- [ ] Operator journeys A–D reviewed for accuracy against main implementation
- [ ] Frontend contract notes reviewed; direct-to-adapter routing for validate/dry-handoff confirmed
- [ ] No canonical truth was modified by this sidecar packet

---

## 8. Non-goals of this Packet

- Does NOT modify `live_gate_adapter.py` or any adapter code
- Does NOT modify `main.py` (adapter or BFF)
- Does NOT modify any L1 canonical policy files
- Does NOT create new BFF routes
- Does NOT create new test files
- Does NOT modify `ai-status.json` or any L0 state files

All findings are advisory. The parent task owner (Claude2) decides whether and how to absorb them.
