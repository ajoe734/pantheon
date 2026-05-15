# Review: SVC-OPENCLAW-LIVE-GATE-HARNESS-SIDECAR-BFF-HANDOFF

Reviewer: Codex
Owner: Claude
Reviewed at: 2026-04-30T09:33:27Z
Disposition: approved

## Findings

No blocking findings remain.

## Resolved Finding

The prior review requested Section 4 coverage for six missing live gate error codes:

- `CAPITAL_BINDING_MISMATCH`
- `RUNTIME_BINDING_CHECK_FAILED`
- `RUNTIME_BINDING_SCHEMA_ERROR`
- `SAFE_MODE_CHECK_FAILED`
- `SAFE_MODE_CHECK_ERROR`
- `SAFE_MODE_SCHEMA_ERROR`

The revised packet now includes all six rows in Section 4, adds the Gate column, and keeps the catalogue ordered by gate check sequence. The fixed adapter and route-level error codes observed in `live_gate_adapter.py` and `main.py` are represented in the table.

## Verified Accurate

- The BFF read-only routes exist at `services/control-plane/bff/main.py:8349` and `services/control-plane/bff/main.py:8376`.
- `OpenClawOpsClient` has `get_live_gate_status()` and `list_live_gate_audit()` at `services/control-plane/bff/openclaw_ops_client.py:198` and `services/control-plane/bff/openclaw_ops_client.py:202`.
- The BFF inventory gap is accurate: `BFF_SURFACE_INVENTORY.md` has no live gate entries.
- Dedicated BFF tests for these routes are not present under `services/control-plane/bff/test*`.

## Verification Commands

```bash
rg -n "live-gate|live/gate|live/orders|live_gate|dry-handoff|X-Human-Approval-Token|LIVE_GATE|HUMAN_APPROVAL|RUNTIME_MANAGER|KILL_SWITCH|BINDING_" services/openclaw-gateway-adapter services/control-plane/bff
rg -n "SAFE_MODE_CHECK_FAILED|SAFE_MODE_CHECK_ERROR|SAFE_MODE_SCHEMA_ERROR|CAPITAL_BINDING_MISMATCH|RUNTIME_BINDING_CHECK_FAILED|RUNTIME_BINDING_SCHEMA_ERROR" support/sidecars/SVC-OPENCLAW-LIVE-GATE-HARNESS/SVC-OPENCLAW-LIVE-GATE-HARNESS-SIDECAR-BFF-HANDOFF.md services/openclaw-gateway-adapter/test_live_gate_adapter.py
rg -n '"(LIVE_GATE_DISABLED|HUMAN_APPROVAL_NOT_CONFIGURED|HUMAN_APPROVAL_TOKEN_MISSING|HUMAN_APPROVAL_TOKEN_INVALID|CAPITAL_POOL_REQUIRED|RUNTIME_MANAGER_NOT_CONFIGURED|RUNTIME_BINDING_CHECK_FAILED|RUNTIME_MANAGER_UNAVAILABLE|RUNTIME_MANAGER_ERROR|RUNTIME_BINDING_SCHEMA_ERROR|LIVE_RUNTIME_BINDING_NOT_FOUND|CAPITAL_BINDING_MISMATCH|LIVE_RUNTIME_BINDING_REQUIRED|SAFE_MODE_CHECK_FAILED|SAFE_MODE_CHECK_ERROR|SAFE_MODE_SCHEMA_ERROR|KILL_SWITCH_UNSAFE_STATE|BINDING_IN_UNSAFE_STATE|BINDING_NOT_ACTIVE|OPERATOR_REQUIRED|LIVE_EXECUTION_DISABLED)"' services/openclaw-gateway-adapter/live_gate_adapter.py services/openclaw-gateway-adapter/main.py
python3 -m pytest services/openclaw-gateway-adapter/test_live_gate_adapter.py -q
```

Result: `41 passed in 1.95s`.
