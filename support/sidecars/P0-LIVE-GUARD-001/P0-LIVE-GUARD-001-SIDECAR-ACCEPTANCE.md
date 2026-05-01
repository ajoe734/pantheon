---
sidecar_id: P0-LIVE-GUARD-001-SIDECAR-ACCEPTANCE
parent_task: P0-LIVE-GUARD-001
helper_kind: acceptance_packet
owner: Claude2
reviewer: Codex
status: draft
created_at: 2026-05-01
mutates_canonical: false
---

# P0-LIVE-GUARD-001 — Acceptance Packet

## 1. Parent Task Summary

**Task:** Assert live fail-closed and bracket logged-only honesty  
**Owner:** Codex  
**Reviewer:** Claude  
**Depends on:** P0-BOOT-001 (done)

Parent task acceptance criteria (from `ai-status.json`):
1. Live role cannot broker connect or place order without activation.
2. `bracket_order_logged` is not treated as broker submitted order.

---

## 2. Current Implementation State (as of 2026-05-01)

### 2.1 Live Role — Fail-Closed Evidence

**File:** `services/execution/lean_runtime/runtime_bootstrap.py`

The entrypoint dispatches on `PANTHEON_RUNTIME_ROLE`. Only two roles start the paper runtime:
- `pantheon-paper-execution-runtime`
- `pantheon-lean-paper-runtime`

All other roles — including `live`, `canary`, and any unrecognised value — fall into the sidecar path. That sidecar starts a health-only HTTP server that serves:
- `/` → `{"status": "ok", ...sidecar_state}`
- `/__health__` → same
- `/health` → same
- Any other path → 404

The sidecar state payload includes `"adapter_mode": "mock"` and `"runtime_package": "execution_sidecar"`. It does **not** connect to a broker or place any orders.

**File:** `services/execution/lean_runtime/bootstrap_contract.py`

`_materialize_runtime_config` enforces:
```python
health_only = stage in {"canary", "live"}
live_broker_enabled = False  # always
```

Any request with `live_broker_enabled=True` raises `BootstrapContractError` immediately. This is tested in `test_live_broker_activation_flag_is_rejected`.

`to_runtime_env()` sets:
- `PANTHEON_HEALTH_ONLY=true` when `health_only` is True
- `PANTHEON_LIVE_BROKER_ENABLED=false` always in P0

**File:** `services/execution/lean_runtime/test_bootstrap_contract.py`

Existing test coverage for live fail-closed:
- `test_live_role_defaults_to_health_only`: asserts `health_only=True`, `live_broker_enabled=False`, `paper_mode=False`, `PANTHEON_HEALTH_ONLY=true`
- `test_live_broker_activation_flag_is_rejected`: asserts `BootstrapContractError` when `live_broker_enabled=True`

### 2.2 Bracket Order — Logged-Only Evidence

**File:** `services/execution/lean_runtime/executor.py` (lines ~150–160)

```python
# --- Risk: stop-loss / take-profit bracket ---
risk = (signal.get("metadata") or {}).get("risk_parameters") or {}
if risk.get("stop_loss_pct") or risk.get("take_profit_pct"):
    log.info(
        "[%s] Risk parameters present (stop=%.2f%%, tp=%.2f%%) — "
        "bracket order not yet implemented; log only",
        ...
    )
    # TODO (P3-001 follow-up): implement StopMarketOrder + LimitOrder bracket
    # after verifying broker support via algo.BrokerageModel
```

The code explicitly logs risk parameters but takes no broker action. No `StopMarketOrder`, `LimitOrder`, or broker submission is invoked when `stop_loss_pct` or `take_profit_pct` are present. This matches **INV-BOOT-010** from SD-P0-02: "bracket order behavior MUST remain logged_only until guarded broker execution is implemented."

**SD canonical invariant reference:**
> `INV-BOOT-010: bracket order behavior MUST remain logged_only until guarded broker execution is implemented.`

---

## 3. Acceptance Checklist

### AC-LIVE-001: Live role cannot broker connect or place order without activation

| Sub-check | Status | Evidence |
|---|---|---|
| `runtime_bootstrap.py` dispatches live role to sidecar path, not paper runtime | PASS | Line 84: only `pantheon-paper-execution-runtime` and `pantheon-lean-paper-runtime` trigger `main()` |
| Sidecar has no broker connection or order placement code | PASS | `_SidecarHandler` only serves HTTP health JSON |
| `bootstrap_contract.py` forces `health_only=True` for `live` stage | PASS | `_materialize_runtime_config`: `health_only=stage in {"canary","live"}` |
| `bootstrap_contract.py` forces `live_broker_enabled=False` unconditionally | PASS | Hard-coded `False` in `_materialize_runtime_config` |
| Attempting `live_broker_enabled=True` raises error | PASS | `test_live_broker_activation_flag_is_rejected` |
| `PANTHEON_HEALTH_ONLY=true` env var set for live runtime | PASS | `test_live_role_defaults_to_health_only` |
| No live/canary broker secret path exists in bootstrap request | PASS | `_reject_raw_secrets` in materializer; `broker_secret` test |

### AC-LIVE-002: bracket_order_logged is not treated as broker submitted order

| Sub-check | Status | Evidence |
|---|---|---|
| `executor.py` detects bracket risk parameters | PASS | Reads `stop_loss_pct`/`take_profit_pct` from signal metadata |
| Bracket path emits log only, no broker call | PASS | `log.info(...)` followed by comment; no `StopMarketOrder` or broker API call |
| No `BracketOrder`, `StopMarketOrder`, `SubmitOrderRequest` in executor | PASS | Grep confirms no such call exists in lean_runtime |
| `PaperExecutionAlgorithm` has no broker submission method | PASS | Only `SetHoldings`, `MarketOrder`, `LimitOrder`, `Liquidate` — all paper-mode only |

---

## 4. Dependency Map

```
P0-BOOT-001 (done)
  └─► P0-LIVE-GUARD-001 (todo → in_progress)
        ├─► bootstrap_contract.py  ← live fail-closed enforcement
        ├─► runtime_bootstrap.py   ← sidecar dispatch for non-paper roles
        └─► executor.py            ← bracket logged-only enforcement
```

**Runtime bootstrap chain for live:**
```
DeploymentPlan(target_stage=live)
  → RuntimeBinding(deployment_stage=live)
  → materialize_runtime_bootstrap_request()
    → health_only=True, live_broker_enabled=False
  → PANTHEON_RUNTIME_ROLE=live (or any non-paper role)
  → runtime_bootstrap.py → sidecar branch
  → _SidecarHandler (health only, no broker, no orders)
```

**Bracket order chain:**
```
Signal with metadata.risk_parameters.stop_loss_pct != 0
  → executor.py
  → log.info("bracket order not yet implemented; log only")
  → no StopMarketOrder / LimitOrder submitted
  → bracket remains logged_only (per INV-BOOT-010)
```

---

## 5. Gap Analysis

### Gaps that P0-LIVE-GUARD-001 should close

| Gap | Priority | Description |
|---|---|---|
| No explicit test asserting live role cannot call `MarketOrder` or `LimitOrder` | P0 | Current tests verify `health_only=True` and `live_broker_enabled=False` at the config level, but there is no behavioural test that actually runs the live sidecar and asserts zero order calls |
| No test for bracket logged-only | P0 | `executor.py` has the logged-only code path, but `test_bootstrap_contract.py` and `test_paper_runtime.py` do not include a case that confirms bracket risk params → log-only with no broker submission |
| `runtime_bootstrap.py` sidecar does not expose `/healthz` or `/readyz` | Observability gap | Only `/`, `/__health__`, and `/health` are served; SD-P0-02 specifies `/healthz` and `/readyz` |
| `not_activated` signal not in sidecar response | Minor | The payload has `adapter_mode: mock` but no explicit `not_activated` status field visible to operators |

### Gaps out of scope for P0-LIVE-GUARD-001

| Gap | Why out of scope |
|---|---|
| Full live broker SDK kernel | Non-goal of P0 per SD-P0-02 section 13 |
| Canary activation | Non-goal of P0 per SD-P0-02 section 13 |
| OpenClaw broker kernel | Non-goal per canonical boundary docs |

---

## 6. Suggested Test Cases for P0-LIVE-GUARD-001

The following test cases are suggested to satisfy the parent task acceptance criteria. They are non-binding guidance for the implementor (Codex).

### LIVE-TEST-001: Live sidecar serves health and returns `not_activated`
```python
# Set PANTHEON_RUNTIME_ROLE=live (or any non-paper role)
# Start runtime_bootstrap entrypoint
# GET /healthz → expect 200, body includes not_activated or health_only=true
# Assert no broker call was made
```

### LIVE-TEST-002: Live materializer → health_only=True, live_broker_enabled=False
```python
# Already covered by test_live_role_defaults_to_health_only
# Confirm PANTHEON_HEALTH_ONLY=true in env
# Confirm PANTHEON_LIVE_BROKER_ENABLED=false in env
```

### LIVE-TEST-003: Live materializer rejects live_broker_enabled=True
```python
# Already covered by test_live_broker_activation_flag_is_rejected
```

### LIVE-TEST-004: Bracket risk parameters → log-only, no order submitted
```python
# Create signal with metadata.risk_parameters.stop_loss_pct=0.02
# Call executor.execute_signal()
# Assert log contains "bracket order not yet implemented; log only"
# Assert algorithm received no StopMarketOrder or LimitOrder call
```

### LIVE-TEST-005: Live role runtime_bootstrap cannot route to paper runtime path
```python
# Set PANTHEON_RUNTIME_ROLE=live
# Confirm main() is NOT called (sidecar branch is taken)
# Confirm _SidecarHandler is started
```

---

## 7. Relevant Canonical References

| Document | Relevant Section |
|---|---|
| `PAPER_CANARY_LIVE_POLICY.md` | §9.3 Live runtime — broker required, no relaxation vs canary |
| `KILL_SWITCH_AND_SAFE_MODE_EXECUTION_POLICY.md` | §3.2 Hard emergency path — kill switch routes through runtime-manager |
| `SD-P0-02_DeploymentPlan_to_RuntimeBootstrap_Contract.md` | §10.3 live: health-only sidecar, fail-closed for broker; INV-BOOT-002, INV-BOOT-004, INV-BOOT-010 |
| `SA-20_v2_risk_register_corrected.md` | Rank 2: Production live runtime not implemented; Rank 6: Bracket order is log-only |
| `BINDING_AND_DEPLOYMENT_SEMANTICS.md` | Binding write-owner semantics for runtime-manager |

---

## 8. Handoff Notes for Reviewer (Codex)

This sidecar packet is a **read-only support artifact**. It does not modify any canonical truth or implementation.

Key questions for the reviewer to validate:

1. Are the existing tests (`test_live_role_defaults_to_health_only`, `test_live_broker_activation_flag_is_rejected`) sufficient for the parent task's acceptance criteria, or do new behavioural tests (LIVE-TEST-001, LIVE-TEST-004) need to be written before the parent task closes?
2. Should the sidecar explicitly return `not_activated` as a structured field in its health response, or is `adapter_mode=mock` sufficient for P0?
3. Is the bracket logged-only behaviour in `executor.py` sufficiently tested by existing tests, or does P0-LIVE-GUARD-001 need to add a regression test explicitly asserting no broker call is made?
4. The `/healthz` and `/readyz` endpoints are missing from the live sidecar (only `/health` and `/__health__` exist). Should this be fixed as part of P0-LIVE-GUARD-001 or tracked as a separate gap (P0-HEALTH-001 scope)?

---

*Sidecar prepared by Claude2 · 2026-05-01 · Task P0-LIVE-GUARD-001-SIDECAR-ACCEPTANCE*  
*This packet is a support artifact only. Reviewer: Codex. Parent owner: Codex.*
