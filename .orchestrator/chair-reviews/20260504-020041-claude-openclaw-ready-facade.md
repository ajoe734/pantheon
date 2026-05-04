# Review: SVC-BLUEPRINT-OPENCLAW-READY-FACADE

**Reviewer:** Claude
**Reviewed at:** 2026-05-04T02:00:41Z
**Task:** SVC-BLUEPRINT-OPENCLAW-READY-FACADE — Make OpenClaw adapter activation-ready while live broker remains gated
**Owner:** Codex
**Status:** Approved

---

## Acceptance Criteria Review

### 1. Adapter degrades deterministically without live broker ✅

- `_probe_upstream()` in `services/openclaw-gateway-adapter/main.py` returns a structured degraded envelope when upstream is absent or unhealthy.
- `_CAPABILITY_SNAPSHOT` is a static dict that serves the capabilities endpoint in full regardless of upstream reachability — `activation_state` flips between `upstream_client_ready` / `upstream_client_degraded` but the adapter itself never fails.
- `/livez` probe is self-only; `/readyz` correctly 503s only when upstream is degraded.

### 2. BFF projects capability and gate_reason ✅

- `_project_openclaw_gate_state()` in `read_store.py` (line ~5369) iterates all gate keys and builds `enabled`, `activation_gate`, and `gate_reason` per gate.
- `canEnableCanary: False` is hardcoded at line 5702 — correctly reflects that no BFF action can enable the canary path.
- `get_openclaw_ops_snapshot()` aggregates gate_state, session counts, and tool/workflow audit into a single surface.

### 3. Live / paper / canary execution paths fail-closed ✅

- **Canary:** `payload["canary_adapter"] = "deferred"` at line 619 is hardcoded — even if `OPENCLAW_CANARY_ADAPTER_ENABLED=true`, the capabilities response still says deferred. The `reject_canary_order` route always returns HTTP 403 / `CANARY_EXECUTION_DISABLED`.
- **Live:** `reject_live_order` delegates to `_LIVE_GATE.reject_live_order()` which always raises; fallback line is marked `# pragma: no cover`.
- **Paper:** gated by `_PAPER_ADAPTER_ENABLED` — disabled by default in all compose configs.
- compose pins all four flags to `"false"` (lines 380, 1228 for canary; verified via `test_compose_activation.py`).

### 4. Tests cover gated posture ✅

- `test_main.py` — `test_canary_adapter_disabled_by_default`, `test_canary_order_always_rejected`, broker capability assertions.
- `test_openclaw_ops_surface.py` — verifies `gate_state.canary_adapter.gate_reason == "OPENCLAW_CANARY_ADAPTER_ENABLED is not enabled"` and `canEnableCanary: False`.
- `test_smoke_openclaw_activation_ready_e2e.py` — verifies `default:canary-denied` returns `CANARY_EXECUTION_DISABLED`, `default:live-denied` returns `LIVE_EXECUTION_DISABLED`, `default:paper-denied` returns `PAPER_ADAPTER_DISABLED`.
- Codex's handoff reports 209 tests passing. Syntax check (`py_compile`) on main.py and read_store.py passes clean.

## OPENCLAW_RUNTIME_CONTRACT.md Update ✅

The contract doc now includes the broker execution gate surfaces section (§2.2) documenting:
- `POST /api/openclaw-adapter/broker/canary/orders` — permanent fail-closed
- `OPENCLAW_CANARY_ADAPTER_ENABLED` scope note: display-only gate, does not enable the canary order route

## Verdict

**Approved.** All acceptance criteria met. Implementation correctly separates the canary display gate (`_CANARY_ADAPTER_ENABLED`) from the canary execution gate (hardcoded deny). No changes required.

Returned to Codex for closeout finalization.
