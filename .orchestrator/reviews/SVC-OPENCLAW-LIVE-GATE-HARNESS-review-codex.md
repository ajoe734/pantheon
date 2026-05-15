# Review: SVC-OPENCLAW-LIVE-GATE-HARNESS

Reviewer: Codex
Disposition: approved
Reviewed commits: 0c00888, 27c4fe8

## Re-review Approval

The blocking capital binding finding is resolved in `27c4fe8`. The live gate now rejects a RuntimeBinding whose `capital_pool_id` does not match the requested pool with `LiveGateError` code `CAPITAL_BINDING_MISMATCH` under the `active_live_runtime_binding` gate before continuing to deployment-mode, safe-mode, or rollback checks.

Verification run during re-review:

```bash
python3 -c 'import sys
sys.path.insert(0,"services/openclaw-gateway-adapter")
from live_gate_adapter import LiveGateAdapter, LiveGateAuditLog, LiveGateError
binding={"binding_id":"rb-wrong","capital_pool_id":"pool-b","artifact_id":"art-live","deployment_mode":"live","status":"active"}
adapter=LiveGateAdapter(enabled=True,human_approval_token="token",binding_resolver=lambda pool_id: binding,safe_mode_resolver=lambda pool_id: "normal",audit_log=LiveGateAuditLog(path="/tmp/live-gate-review-mismatch.jsonl"))
try:
    adapter.check_all_gates(capital_pool_id="pool-a",human_approval_token="token",operator_id="op-review")
except LiveGateError as exc:
    print(exc.error_code, exc.gate, exc.status_code, exc.details)
else:
    raise SystemExit("expected LiveGateError")'

PYTHONPATH=~/.local/lib/python3.12/site-packages python3 -m pytest services/openclaw-gateway-adapter/test_live_gate_adapter.py services/openclaw-gateway-adapter/test_main.py -q

PANTHEON_BFF_AUTH_STUB=true PYTHONPATH=~/.local/lib/python3.12/site-packages python3 -m pytest services/control-plane/bff/test_openclaw_ops_surface.py -q
```

Results: reproduction raised `CAPITAL_BINDING_MISMATCH`; 76 gateway tests passed; 4 BFF tests passed.

## Blocking Finding

1. `services/openclaw-gateway-adapter/live_gate_adapter.py:414` accepts the RuntimeBinding returned by the resolver/runtime-manager without verifying `binding["capital_pool_id"] == capital_pool_id`.

   This is fail-open for the capital binding gate. A mismatched active live binding can pass the live gate if it has `deployment_mode="live"`, `status="active"`, and safe mode is normal. The paper adapter already rejects this case with `CAPITAL_BINDING_MISMATCH` in `paper_broker_adapter.py:348`, so the live gate should mirror that defensive check before continuing to safe-mode and rollback checks.

   Reproduction used during review:

   ```bash
   python3 -c 'import sys; sys.path.insert(0,"services/openclaw-gateway-adapter"); from live_gate_adapter import LiveGateAdapter, LiveGateAuditLog; binding={"binding_id":"rb-wrong","capital_pool_id":"pool-b","artifact_id":"art-live","deployment_mode":"live","status":"active"}; adapter=LiveGateAdapter(enabled=True,human_approval_token="token",binding_resolver=lambda pool_id: binding,safe_mode_resolver=lambda pool_id: "normal",audit_log=LiveGateAuditLog(path="/tmp/live-gate-review-mismatch.jsonl")); result=adapter.check_all_gates(capital_pool_id="pool-a",human_approval_token="token",operator_id="op-review"); print(result)'
   ```

   Observed result:

   ```text
   {'gates_passed': True, 'capital_pool_id': 'pool-a', 'operator_id': 'op-review', 'binding_id': 'rb-wrong', 'artifact_id': 'art-live', 'safe_mode': 'normal', 'deployment_mode': 'live', 'checked_at': '2026-04-30T09:05:40.414681Z'}
   ```

   Expected fix: reject mismatched `capital_pool_id` with a structured `LiveGateError` under the `active_live_runtime_binding` gate, and add a focused unit test that a wrong-pool binding fails closed.

## Verification Run

```bash
python3 -m pytest services/openclaw-gateway-adapter/test_live_gate_adapter.py services/openclaw-gateway-adapter/test_main.py
PANTHEON_BFF_AUTH_STUB=true python3 -m pytest services/control-plane/bff/test_openclaw_ops_surface.py
```

Results: 75 passed; 4 passed.
