# Review: SVC-OPENCLAW-PAPER-BROKER-ADAPTER

Reviewer: Claude
Date: 2026-04-30
Status: **APPROVED**

## Scope

Add gated OpenClaw paper broker adapter: default-disabled paper/broker gates, runtime-manager
active paper RuntimeBinding validation for capital/strategy binding, paper simulation
sidecar/audit, and live fail-closed coverage.

## Artifacts Reviewed

- `services/openclaw-gateway-adapter/paper_broker_adapter.py` — adapter gate, binding check, audit log, submit/reject paths
- `services/openclaw-gateway-adapter/main.py` — gateway integration with paper adapter
- `services/openclaw-gateway-adapter/test_paper_broker_adapter.py` — adapter unit tests
- `services/openclaw-gateway-adapter/test_compose_activation.py` — compose-level gate checks
- `services/broker/main.py` — paper/live broker endpoint routing
- `services/broker/paper_simulation.py` — order simulation fill logic
- `services/broker/test_broker.py` — broker endpoint tests
- `services/runtime-manager/main.py` and `service.py` — active RuntimeBinding query support
- `services/runtime-manager/test_runtime_manager.py` — runtime-manager tests
- `docker-compose.yml` — paper/broker gate env var defaults

## Acceptance Criteria Evaluation

### ✓ 1. Paper adapter is disabled by default and requires explicit gate

`paper_broker_adapter.py:130` — `OPENCLAW_PAPER_ADAPTER_ENABLED` defaults to empty string
(falsy); `_gate_check()` raises `PAPER_ADAPTER_DISABLED` unless the env var is explicitly
set. Broker-side gate `BROKER_PAPER_ENABLED` (`broker/main.py:26`) is symmetric.
Docker Compose sets both to `"false"` explicitly (compose `broker` service env block).
Test `TestPaperBrokerAdapterGate` (test_paper_broker_adapter.py:73–102) validates gate
enforcement for all order paths.

### ✓ 2. Paper orders route through simulation path not live broker

`broker/main.py:91–109` — POST `/api/broker/paper/orders` calls `simulate_paper_order()`.
`paper_simulation.py:131–173` fills synchronously with placeholder price (100.0 market,
limit_price for limit), setting `sim_fill_flag=True`, `is_real_order=False`,
`is_real_capital=False`, `deployment_stage="paper"`. Live endpoint (`broker/main.py:150–164`)
is hardcoded to return 403 `LIVE_ADAPTER_DISABLED` unconditionally (`_LIVE_ENABLED = False`).
`test_broker.py:109–123` confirms market orders fill at placeholder price.

### ✓ 3. Capital and strategy binding checks are enforced

`paper_broker_adapter.py:182–186` — `submit_paper_order()` calls `_binding_check()` before
any broker interaction. `_binding_check()` (lines 318–453) enforces:
- Capital pool presence (319–324)
- Active RuntimeBinding lookup via `/api/runtimes/{pool_id}/active` (337–347)
- Binding status must be `"active"` (359–365)
- Deployment mode must be `"paper"` (366–372)
- Strategy ID must match via artifact_id or metadata.strategy_id (373–385)

Tests `test_paper_broker_adapter.py:105–222` validate all binding check branches including
missing binding, inactive status, wrong deployment mode, and strategy mismatch.

### ✓ 4. Audit trail captures order intent and result

`PaperBrokerAuditLog` (`paper_broker_adapter.py:56–108`) is a thread-safe JSONL append-only
log. Intent is recorded before broker call (`event: paper_order_intent`, `outcome: pending`).
On success: `outcome: ok` with order_id, fill_price, fill_qty, status, sim_fill_flag.
On error: `outcome: error` with error_code and message. Metadata includes operator_id,
capital_pool_id, strategy_id, runtime_binding_id, and persona_capital_binding_id.
Live rejections also log (`event: live_order_rejected`, `is_real_order: False`,
`is_real_capital: False`). Tests `test_paper_broker_adapter.py:268–337` validate intent→ok
and intent→error audit flows end-to-end.

### ✓ 5. Tests prove live remains rejected

`TestPaperBrokerAdapterLiveRejection` (`test_paper_broker_adapter.py:224–265`):
- Live rejected when paper disabled (error code `LIVE_ADAPTER_DISABLED`, status 403)
- Live rejected even when paper is enabled (same error — unconditional)
- Live rejection always records audit entry with `is_real_order=False`, `is_real_capital=False`, `live_enabled=False`

`TestBrokerLiveOrderAlwaysRejected` (`test_broker.py:87–105`):
- Live rejected when paper disabled (403, `LIVE_ADAPTER_DISABLED`)
- Live rejected when paper enabled (403, same — hard gate, not conditional)
- Response body includes `deployment_stage: "paper"`

## Verification Commands Run

```
python3 -m pytest services/openclaw-gateway-adapter services/broker services/runtime-manager/test_runtime_manager.py -q --tb=short
→ 220 passed in 3.24s

python3 -m py_compile services/openclaw-gateway-adapter/paper_broker_adapter.py services/openclaw-gateway-adapter/main.py services/broker/main.py services/broker/paper_simulation.py services/runtime-manager/main.py services/runtime-manager/service.py
→ ALL COMPILE OK
```

## Notes

- Gate is fully symmetric: both adapter-side (`OPENCLAW_PAPER_ADAPTER_ENABLED`) and
  broker-side (`BROKER_PAPER_ENABLED`) default fail-closed. Neither path trusts the other's
  gate alone.
- `_LIVE_ENABLED = False` in broker is a hard constant, not an env var — correct; live
  activation requires a separate SVC-OPENCLAW-LIVE-GATE-HARNESS task.
- Audit log path defaults to `/tmp/paper_broker_audit.jsonl` — acceptable for paper stage;
  a persistent volume mount would be needed before canary/live.
- No canonical architecture docs mutated beyond task scope.

## Decision

**APPROVED** — all five acceptance criteria met, 220 tests pass, all modules compile.
Returned to Codex (owner) for closeout finalization.
