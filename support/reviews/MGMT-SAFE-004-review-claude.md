# Review: MGMT-SAFE-004 — canary human gate smoke

Reviewer: Claude  
Task owner: Codex2  
Review date: 2026-05-15  
Status: **approved**

## Scope reviewed

Task-owned files:
- `scripts/run_ep5_canary_readiness.py`
- `scripts/test_run_ep5_canary_readiness.py`
- `scripts/run_canary_human_gate_smoke.py`
- `scripts/test_run_canary_human_gate_smoke.py`
- `support/evidence/MGMT-SAFE-004/README.md`
- `support/evidence/MGMT-SAFE-004/canary-human-gate-smoke.json`

## Findings

No blocking issues. The implementation is correct and complete.

### Gate enforcement

`CANARY_HUMAN_GATE_REQUIRED_FIELDS` correctly requires all 9 promotion gate refs:
`promotion_gate_decision_id`, `human_gate_packet_ref`, `broker_sandbox_smoke_ref`,
`risk_owner_approval_ref`, `operator_approval_ref`, `persona_capital_binding_id`,
`allowed_deployment_scope`, `capital_scale_pct`, `gross_scale_pct`.

A packet is `ready_for_review` only when all 9 refs are present, `target_stage == "canary"`,
and the broker sandbox smoke proves the fail-closed live boundary.

### Broker sandbox smoke enforcement

`broker_smoke_ok` correctly requires all of:
1. `status in {"pass", "passed"}`
2. `provider == "Shioaji"` (case-insensitive via `normalize_token`)
3. `reconciliation.status == "passed"`
4. `no_real_capital.real_capital_used is False`
5. `no_real_capital.production_live_order_submitted is False`
6. Live boundary fail-closed: `live_gate.status == "rejected"` + `error_code == "SHIOAJI_LIVE_DISABLED"`, or `production_live.enabled is False`

`packet_ready` requires `broker_smoke_ok` as a hard gate, so no broker smoke = incomplete packet.

### Smoke test cases

5 cases cover the critical invariants:
- `ready-with-explicit-human-gate-and-broker-smoke`: valid canary plan + valid broker smoke → `ready_for_review`
- `missing-operator-approval-keeps-packet-incomplete`: missing `operator_approval_ref` → `incomplete`
- `missing-broker-smoke-keeps-packet-incomplete`: no broker smoke → `incomplete`
- `live-target-is-not-accepted-as-canary-human-gate`: `target_stage=live` → `incomplete`
- `production-live-broker-smoke-is-rejected`: `live_gate.status=accepted` → `incomplete`

### Safety invariants confirmed

- `production_live_order_submitted: false`
- `real_capital_used: false`
- All smoke cases use fixture artifacts only; no real broker calls or capital binding mutations.

## Verification commands run

```
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/run_ep5_canary_readiness.py scripts/run_canary_human_gate_smoke.py scripts/test_run_ep5_canary_readiness.py scripts/test_run_canary_human_gate_smoke.py
=> passed

PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_canary_human_gate_smoke.py --json-out /tmp/canary-human-gate-smoke-review.json
=> 5/5 passed

PYTHONDONTWRITEBYTECODE=1 python3 -m pytest scripts/test_run_ep5_canary_readiness.py scripts/test_run_canary_human_gate_smoke.py -q
=> 10 passed in 2.34s
```

## Conclusion

All MGMT-SAFE-004 scope invariants verified. Returning to Codex2 for closeout.
