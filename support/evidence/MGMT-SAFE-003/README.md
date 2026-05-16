# MGMT-SAFE-003 OpenClaw Broker Tool Denial Smoke

Scope:

- OpenClaw adapter effective tool listing excludes broker/live/paper/canary/capital/LEAN tool refs even when env allowlist and upstream metadata include them.
- Broker-like tool invocations return `BRIDGE_TOOL_DENIED` with `policy_class=always_blocked`.
- Broker-like workflow triggers return `BRIDGE_WORKFLOW_DENIED` with `policy_class=always_blocked`.
- Denied attempts are audited and never dispatched to upstream OpenClaw.
- Production broker, live execution, canary execution, and capital binding gates remain disabled.

Focused verification:

```bash
PYTHONDONTWRITEBYTECODE=1 python3 scripts/run_openclaw_broker_tool_denial_smoke.py --json-out support/evidence/MGMT-SAFE-003/openclaw-broker-tool-denial-smoke.json
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest scripts/test_run_openclaw_broker_tool_denial_smoke.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m unittest services/openclaw-gateway-adapter/test_tool_workflow_bridge.py -q
PYTHONDONTWRITEBYTECODE=1 python3 -m py_compile scripts/run_openclaw_broker_tool_denial_smoke.py scripts/test_run_openclaw_broker_tool_denial_smoke.py services/openclaw-gateway-adapter/tool_workflow_bridge.py services/openclaw-gateway-adapter/test_tool_workflow_bridge.py
```
