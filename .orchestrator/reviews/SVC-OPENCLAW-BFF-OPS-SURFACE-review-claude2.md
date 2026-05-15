# Review: SVC-OPENCLAW-BFF-OPS-SURFACE

Reviewer: Claude2
Date: 2026-04-30
Status: approved

## Verdict

**Approved.** All 5 acceptance criteria are met. The implementation is correct, fail-closed, and well-tested.

## Acceptance Criteria Check

### 1. BFF reads OpenClaw status and sessions through service client ✅

`openclaw_ops_client.py` is a clean service client exclusively wrapping calls to the Pantheon-owned `openclaw-gateway-adapter`. The BFF never calls upstream OpenClaw directly. `read_store.get_openclaw_ops_snapshot()` calls five adapter surfaces (capabilities, upstream_status, lifecycle/sessions, tools/policy, audit/invocations) via this client.

### 2. BFF exposes tool workflow audit and degraded reason ✅

- `data.tool_workflow.audit.entries[]` — full invocation audit with policy decisions, outcomes, and `args_hash`/`context_hash`
- `data.degradation.reasons[]` — backend-owned degraded reasons aggregated from all surface failures plus per-session degraded state
- `data.tool_workflow.bridge_posture` — documents fail-closed posture (`unknown_tools: "fail_closed"`, `bff_tool_invocation_commands: "not_exposed"`)

### 3. BFF displays paper and live gate state without enabling them ✅

- `gate_state.paper_adapter.enabled` and `gate_state.live_adapter.enabled` are read from adapter capabilities payload and are `False` in deferred state
- `allowedActions.canEnablePaper: False` and `canEnableLive: False` are hardcoded in `get_openclaw_ops_snapshot()`
- `bff_activation_command: "not_exposed"` set for every gate field
- `blocked_commands.enable_paper_adapter: "activation_gate_required_not_available_in_bff"` explicitly documented in response

### 4. Operator commands require auth and idempotency ✅

- `_require_openclaw_command_role()` enforces `operator` or `admin` role; viewer returns 403
- `_require_openclaw_idempotency_key()` enforces `X-Idempotency-Key` header; missing key returns 400 with `precondition_failed: "idempotency_key"`
- `_authorized_openclaw_operator_filter()` prevents non-admin operators from filtering by other operators' sessions
- `X-operator-id` and `X-idempotency-key` are forwarded to the adapter on session create/cancel

### 5. Tests cover healthy, degraded, and denied states ✅

`test_openclaw_ops_surface.py` includes:
- `test_openclaw_ops_surface_aggregates_status_sessions_gates_and_audit` — healthy state: verifies `overall_status == "ok"`, upstream reachability, session counts, gate state, audit counts, bridge posture, meta surfaces
- `test_openclaw_ops_surface_degrades_when_adapter_is_not_configured` — unavailable/degraded state: `overall_status == "unavailable"`, `production_activation == "disabled"`, `OPENCLAW_ADAPTER_URL_NOT_CONFIGURED` reason
- `test_openclaw_session_commands_require_auth_role_and_idempotency` — 401 (missing auth), 403 (viewer), 400 (missing idempotency key)
- `test_openclaw_session_create_forwards_operator_and_idempotency` — session creation with header forwarding verification

## Additional Notes

- BFF API contract (`BFF_API_CONTRACT.md §10.1`) correctly registers both `GET /api/v1/operator/openclaw/ops` and the alias `GET /api/v1/operator/openclaw/tool-workflow-bridge`
- Frontend handoff spec at `docs/pantheon-handoffs/SVC-OPENCLAW-BFF-OPS-SURFACE/FRONTEND_CHANGE_SPEC.md` is complete with query params, response shape, command contract, and constraints
- `_openclaw_gate_enabled()` handles string/bool/sentinel values correctly — "deferred" is not in the truthy set, so gates are correctly `False`
- Error handling in `openclaw_ops_client.py` covers `URLError`, `HTTPError`, and `OSError`
- No canonical L1 docs were modified beyond the formal BFF contract update

## Follow-up for Owner

Create a task-scoped commit (includes `openclaw_ops_client.py`, updated `main.py`, updated `read_store.py`, updated `BFF_API_CONTRACT.md`, `test_openclaw_ops_surface.py`, `FRONTEND_CHANGE_SPEC.md`) and run `scripts/ai-status.sh done`.
