# SVC-OPENCLAW-SESSION-LIFECYCLE BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `SVC-OPENCLAW-SESSION-LIFECYCLE` — Add Pantheon-owned OpenClaw session lifecycle
**Parent Owner**: Claude2
**Parent Reviewer**: Codex
**Parent Status**: `in_progress` (review changes requested — see Blocking Issue §3)
**Sidecar Task**: `SVC-OPENCLAW-SESSION-LIFECYCLE-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-30

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or
> core runtime / registry / governance implementations. It packages the session lifecycle
> implementation into a BFF-ready operator handoff.

---

## 1. What the Parent Task Delivered

Commit `ab13ad0` (branch `backend-dev-publish-20260429`) adds Pantheon-owned durable session
lifecycle to `services/openclaw-gateway-adapter/`.

| Deliverable | Location | State |
|---|---|---|
| State machine + durable store | `session_lifecycle.py` | done |
| Lifecycle HTTP routes (create/get/list/cancel/audit) | `main.py:569–670` | done |
| Typed downstream lifecycle client | `lifecycle_client.py` | done |
| Idempotent create (`X-Idempotency-Key`) | `main.py:570–602` | done |
| Operator identity audit trail (`X-Operator-Id`) | `session_lifecycle.py:228–232` | done |
| Degraded upstream recovery (`lost` state) | `session_lifecycle.py:239–242` | done |
| 61 lifecycle tests | `test_session_lifecycle.py`, `test_lifecycle_client.py` | pass |

**Activation posture**: `session_lifecycle_state: "activation_ready"`. Broker, paper, live, and
capital-binding paths remain `deferred` and fail-closed.

---

## 2. Lifecycle Endpoint Inventory

The adapter exposes the following lifecycle surface at its own service address
(`OPENCLAW_GATEWAY_ADAPTER_URL`, default port from `docker-compose.yml`).

### 2.1 Session Lifecycle Routes

| Method | Path | Required Headers | Description |
|---|---|---|---|
| `POST` | `/api/openclaw-adapter/lifecycle/sessions` | `X-Operator-Id`, optional `X-Idempotency-Key` | Idempotent session create |
| `GET` | `/api/openclaw-adapter/lifecycle/sessions` | — | List sessions; supports `?operator_id=` and `?state=` filters |
| `GET` | `/api/openclaw-adapter/lifecycle/sessions/{session_id}` | — | Get single session; refreshes from upstream for non-terminal states |
| `POST` | `/api/openclaw-adapter/lifecycle/sessions/{session_id}/cancel` | `X-Operator-Id` | Operator-owned cancel; preserves state on degraded upstream |
| `GET` | `/api/openclaw-adapter/lifecycle/sessions/{session_id}/audit` | — | Append-only audit trail; no upstream refresh |

### 2.2 Session Record Shape

```json
{
  "session_id": "string",
  "agent_id": "string",
  "session_type": "interactive | trainer | research_task | consult | committee | red_team | background_job",
  "state": "pending | active | cancel_requested | canceled | failed | lost",
  "operator_id": "string",
  "created_at": "ISO-8601",
  "updated_at": "ISO-8601",
  "idempotency_key": "string | null",
  "request_hash": "string | null",
  "context_bundle": {},
  "upstream_session_id": "string | null",
  "last_upstream_payload": {} ,
  "last_error": {} ,
  "canceled_by": "string | null",
  "audit_log": [
    {
      "action": "string",
      "actor": "string",
      "at": "ISO-8601",
      "detail": {}
    }
  ]
}
```

### 2.3 Error Shape

```json
{
  "status": "lifecycle_error",
  "error_code": "LIFECYCLE_NOT_FOUND | LIFECYCLE_INVALID_TRANSITION | LIFECYCLE_OPERATOR_REQUIRED | LIFECYCLE_TERMINAL | LIFECYCLE_INVALID_REQUEST | LIFECYCLE_IDEMPOTENCY_CONFLICT",
  "message": "string",
  "details": {}
}
```

### 2.4 Create Response Codes

| Status | Meaning |
|---|---|
| `201` | New session created |
| `200` | Idempotency key replayed — same record returned |
| `401` | Missing `X-Operator-Id` header |
| `409` | Idempotency key reused with a different request payload |

---

## 3. Blocking Issue — Read-Path Transition Bug

**Status**: changes requested by Codex, review file at
`.orchestrator/reviews/SVC-OPENCLAW-SESSION-LIFECYCLE-review-codex.md`.

Two valid read-time cases currently raise `LIFECYCLE_INVALID_TRANSITION` (409) instead of
returning the correct updated state:

| Case | Current behavior | Expected behavior |
|---|---|---|
| `active → canceled` (upstream reports session already canceled during GET) | 409 LIFECYCLE_INVALID_TRANSITION | transition to `canceled` via terminal refresh |
| `cancel_requested → active` (concurrent GET while cancel is in flight, upstream still reports active) | 409 LIFECYCLE_INVALID_TRANSITION | return local `cancel_requested` record unchanged; do not push back to `active` |

**BFF impact**: Until this is resolved, the `GET /lifecycle/sessions/{id}` proxy route can return
a 409 for sessions in normal lifecycle observation. The BFF must not surface 409 as a state-read
error to operators; it should treat a 409 from the lifecycle service as a BFF-internal anomaly and
surface a degraded "state unavailable" panel until the parent task fix lands.

**Prerequisite**: The BFF proxy implementation for `GET /lifecycle/sessions/{id}` should be
written against the corrected lifecycle semantics. Do not attempt to work around the bug at the
BFF layer — the fix belongs in `session_lifecycle.py:_ALLOWED_TRANSITIONS` and `get_session()`.

---

## 4. BFF Query Gaps

The current BFF (`services/control-plane/bff/`) has no proxy or composed read surfaces for the
OpenClaw session lifecycle. The gaps below represent forward work for the BFF owner after the
parent task's blocking issue is resolved.

### 4.1 Missing Surface: Session List

**Current inventory**: Not present in `BFF_SURFACE_INVENTORY.md` main body. OpenClaw surfaces are
deferred to Appendix A (RS-04 is the closest but covers the OSS activation-ready ops view, not
session lifecycle state).

**Gap**: No route in the BFF proxies `GET /api/openclaw-adapter/lifecycle/sessions`.

**Proposed surface ID**: `OC-01` (Session List)

```
GET /api/v1/openclaw/sessions
  ?operator_id=<string>     # filter by creating operator
  ?state=<string>           # filter by lifecycle state
  ?page=<int>               # standard pagination
  ?page_size=<int>
```

**Fields required by frontend**: `session_id`, `agent_id`, `session_type`, `state`,
`operator_id`, `created_at`, `updated_at`.

### 4.2 Missing Surface: Session Detail

**Gap**: No route proxies `GET /api/openclaw-adapter/lifecycle/sessions/{id}`.

**Proposed surface ID**: `OC-02` (Session Detail)

```
GET /api/v1/openclaw/sessions/{session_id}
```

**Fields required by frontend**: full `SessionRecord` shape (see §2.2), plus derived badge label
and action availability (see §6).

### 4.3 Missing Surface: Session Audit Trail

**Gap**: No route proxies `GET /api/openclaw-adapter/lifecycle/sessions/{id}/audit`.

**Proposed surface ID**: `OC-03` (Session Audit)

```
GET /api/v1/openclaw/sessions/{session_id}/audit
```

**Fields required**: `session_id`, `operator_id`, `audit_log[]` (action, actor, at, detail).

### 4.4 Missing Command: Session Cancel

The BFF is read-oriented (`BFF_API_CONTRACT.md §2`). Session cancel is a write command and should
be routed through the BFF command queue (`CommandStore` / `execute_command_with_status`) rather
than proxied directly. The BFF must translate the operator cancel intent into a
`POST /api/openclaw-adapter/lifecycle/sessions/{id}/cancel` call, forwarding `X-Operator-Id`
from the authenticated operator context.

**Proposed command type**: `OPENCLAW_SESSION_CANCEL`

**Required operator role**: `operator` or `admin` (align with RBAC matrix in
`BFF_API_CONTRACT.md §6`).

**Write authority note**: The BFF is never the write authority for lifecycle state. It forwards
the cancel command to the adapter and reflects the resulting record on the read surface.

### 4.5 Missing Surface: Upstream Status Panel

**Gap**: No BFF read surface exposes `GET /api/openclaw-adapter/upstream/status` or
`GET /api/openclaw-adapter/capabilities`.

**Proposed surface ID**: `OC-00` (Adapter Status)

```
GET /api/v1/openclaw/adapter/status
```

**Fields required**: `activation_state`, `session_lifecycle_state`, `broker_execution` (deferred
flag), `paper_adapter`, `live_adapter`, `upstream.reachable`, `upstream.reason`.

---

## 5. Operator Journey: Session Lifecycle Monitoring

```
Operator → OC-00 (adapter status) → OC-01 (session list)
         → OC-02 (session detail) → OC-03 (audit trail)
         → [cancel action if state is not terminal]
```

### Step-by-step

1. **Check adapter status** (`OC-00`): Confirms the upstream gateway is reachable and the session
   lifecycle is `activation_ready`. Shows deferred gates so the operator knows broker/paper/live
   are intentionally off.

2. **List sessions** (`OC-01`): Default view shows all sessions, newest first. Operator can filter
   by `operator_id` (their own sessions) or `state` (e.g., `?state=lost` to find degraded
   sessions needing attention).

3. **Session detail** (`OC-02`): Shows full record including `upstream_session_id` (if the
   session crossed into the upstream gateway) and `last_error` (if the session is in a `failed` or
   `lost` state).

4. **Audit trail** (`OC-03`): Append-only timeline of every state transition — who acted, when,
   and what the upstream reported. Operators use this to verify session provenance and diagnose
   degraded states.

5. **Cancel action**: Available only when `state` is not in `{canceled, failed}`. Sends
   `OPENCLAW_SESSION_CANCEL` command with operator identity. After the command returns, the detail
   panel refreshes from `OC-02`.

---

## 6. Frontend Component Surface

### 6.1 Session List Table

| Column | Source field | Notes |
|---|---|---|
| Session ID | `session_id` | Short display, copy-to-clipboard |
| Agent | `agent_id` | |
| Type | `session_type` | Badge from session type enum |
| State | `state` | State badge (see §6.3) |
| Operator | `operator_id` | |
| Created | `created_at` | Relative + absolute on hover |
| Updated | `updated_at` | Relative |

### 6.2 Session Detail Panel

All fields from §2.2, plus:

- `context_bundle` rendered as collapsible JSON viewer
- `last_upstream_payload` rendered as collapsible JSON viewer (labeled "last upstream sync")
- `last_error` rendered as error banner when present
- `upstream_session_id` shown as a copy field when non-null
- Audit trail preview (last 3 entries); "View full audit" link opens `OC-03`

### 6.3 State Badge Map

| State | Badge color | Label | Operator action available |
|---|---|---|---|
| `pending` | yellow | Pending | Cancel |
| `active` | green | Active | Cancel |
| `cancel_requested` | orange | Canceling… | — (in flight) |
| `canceled` | gray | Canceled | — (terminal) |
| `failed` | red | Failed | — (terminal) |
| `lost` | amber | Degraded | Cancel |

### 6.4 Degraded State Guidance

When `state = lost`:
- Show amber banner: "Session state is degraded — upstream is unreachable. The session record
  is preserved. You can cancel this session locally."
- Show `last_error.reason` if present.
- Allow cancel (cancel completes locally when no upstream session id exists).

When `state = failed`:
- Show red banner: "Session terminated with an error."
- Show `last_error.message` and `last_error.error_code` if present.
- No cancel action.

### 6.5 BFF Adapter Status Panel

A read-only summary card visible in the OpenClaw operations section:

| Field | Label | Notes |
|---|---|---|
| `activation_state` | Activation state | Show as status badge |
| `session_lifecycle_state` | Session lifecycle | `activation_ready` → green badge |
| `broker_execution` | Broker execution | `deferred` → gray badge (intentional) |
| `upstream.reachable` | Upstream gateway | green/red indicator |
| `upstream.reason` | Upstream note | shown only when `reachable = false` |

---

## 7. Degraded Path Assumptions for the BFF

These assumptions are consistent with `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md §5.1` and
`BFF_SURFACE_INVENTORY.md §5`.

| Scenario | BFF behavior |
|---|---|
| Adapter service unreachable | Show "session data unavailable" with last-known timestamp. Never show "no sessions" — that is a false negative. |
| Adapter returns `lost`-state sessions | Surface them as-is with amber degraded banner. Do not hide or suppress `lost` records. |
| Adapter GET returns 409 (pre-fix, see §3) | Treat as BFF-internal anomaly. Show "state unavailable" for that session. Do not forward the 409 to the operator as a user-facing error. |
| Cancel command fails with upstream error | Return a `CommandReceipt` with `status: failed` and the upstream error detail. Show operator the last-known session state from `OC-02`. |
| Adapter `activation_state != activation_ready` | Show an informational banner in the session list: "OpenClaw adapter is not fully ready. Session functionality may be limited." |

---

## 8. Pre-Conditions for BFF Implementation

The following conditions must be true before the BFF owner starts building the proxy surfaces:

1. **Parent task fix merged**: The `active→canceled` and `cancel_requested→active` transition bug
   (§3) must be resolved in `session_lifecycle.py` and confirmed passing in CI.

2. **`OpenClawLifecycleClient` available**: The typed client in `lifecycle_client.py` is ready for
   use. The BFF proxy should use it instead of raw HTTP calls to the adapter.

3. **BFF surface inventory update**: Once the OC-0x surfaces above are accepted by the parent
   task owner, `BFF_SURFACE_INVENTORY.md §8 Appendix A` should be updated to graduate the
   session lifecycle surfaces from "future / task-level" into the main surface catalog or a
   dedicated OpenClaw appendix entry.

4. **RBAC role decision**: The BFF RBAC matrix (`BFF_API_CONTRACT.md §6`) does not yet include
   `OPENCLAW_SESSION_CANCEL`. The BFF owner must decide whether this action requires `operator`
   or `admin` role before wiring the command handler.

---

## 9. Files Referenced

| File | Role |
|---|---|
| `services/openclaw-gateway-adapter/session_lifecycle.py` | Lifecycle state machine and durable store |
| `services/openclaw-gateway-adapter/main.py:569–670` | Lifecycle HTTP route handlers |
| `services/openclaw-gateway-adapter/lifecycle_client.py` | Typed downstream client for BFF use |
| `services/control-plane/bff/BFF_API_CONTRACT.md` | BFF canonical contract |
| `services/control-plane/bff/BFF_SURFACE_INVENTORY.md §8 Appendix A` | Future surfaces list (RS-04, FB-*, RG-*) |
| `.orchestrator/reviews/SVC-OPENCLAW-SESSION-LIFECYCLE-review-codex.md` | Codex review with blocking transition bug details |

---

*Support artifact — owned by Claude for task `SVC-OPENCLAW-SESSION-LIFECYCLE-SIDECAR-BFF-HANDOFF`.
Not canonical truth. BFF owner should absorb the OC-0x surface proposals after the parent task
fix is confirmed.*
