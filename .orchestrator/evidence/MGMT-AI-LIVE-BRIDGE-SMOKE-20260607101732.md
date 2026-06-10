# MGMT-AI-LIVE-BRIDGE-SMOKE-20260607101732 Evidence

## Scope

Evidence-only live smoke for the Management AI dev bridge. This task does not
change canonical architecture, BFF route code, dispatcher code, supervisor
logic, or status schema.

## Live Packet Receipt

Live status root:

```text
/home/lupin/code/pantheon
```

Packet paths:

```text
/home/lupin/code/pantheon/.orchestrator/assistant-dev-packets/processed/bridge_smoke_20260607101732.json
/home/lupin/code/pantheon/.orchestrator/assistant-dev-packets/receipts/bridge_smoke_20260607101732.json
```

Receipt facts:

- `packetId`: `bridge_smoke_20260607101732`
- `queuedAt`: `2026-06-07T10:17:32Z`
- `drainedAt`: `2026-06-07T10:17:43Z`
- `lastDrainAt`: `2026-06-07T10:17:46Z`
- `status`: `processed`
- `processedCount`: `1`
- `errorCount`: `0`
- `replayRejected`: `false`
- task record: `MGMT-AI-LIVE-BRIDGE-SMOKE-20260607101732` / `dispatched`
- actor: `codex-live-smoke`
- mode: `kernel_debug`
- intent: `live_supervisor_bridge_smoke`
- signature: `HMAC-SHA256`, key id `assistant-bridge-dev`

The processed packet carried the required constraints:

- `allowedRepos`: `pantheon`
- `noDirectShellFromWeb`: `true`
- `requiresBranchPrMerge`: `true`

## Readback

Command:

```bash
PYTHONPATH=services/control-plane/bff python3 -c 'import json, os; from assistant.orchestrator_status import read_orchestrator_status; s=read_orchestrator_status(os.environ["PANTHEON_STATUS_ROOT"]); b=s.assistant_dev_bridge; print(json.dumps({"status": b.get("status"), "lastDrainAt": b.get("lastDrainAt"), "lastPacketId": (((b.get("lastResult") or {}).get("packets") or [{}])[0].get("packetId")), "processedCount": (b.get("lastResult") or {}).get("processedCount"), "errorCount": (b.get("lastResult") or {}).get("errorCount"), "recentReceiptIds": [r.get("packetId") for r in (b.get("recentReceipts") or [])[:3]]}, indent=2, sort_keys=True))'
```

Output:

```json
{
  "errorCount": 0,
  "lastDrainAt": "2026-06-07T10:17:46Z",
  "lastPacketId": "bridge_smoke_20260607101732",
  "processedCount": 1,
  "recentReceiptIds": [
    "bridge_smoke_20260607101732"
  ],
  "status": "idle"
}
```

## Regression Verification

Command:

```bash
pytest -q scripts/test_assistant_dev_packet_inbox_supervisor_contract.py services/control-plane/bff/assistant/tests/test_dev_bridge_inbox.py services/control-plane/bff/assistant/tests/test_dev_bridge_inbox_cli.py services/control-plane/bff/assistant/tests/test_dev_bridge_dispatch_cli.py services/control-plane/bff/assistant/tests/test_orchestrator_status.py
```

Result:

```text
14 passed in 3.42s
```

## Reviewer Approval

Codex2 approved this evidence-only smoke after confirming:

- PR #1126 merged the task evidence into `dev`.
- Live readback still exposed packet `bridge_smoke_20260607101732` with
  `processedCount=1`, `errorCount=0`, and bridge status `idle`.
- Focused bridge regression rerun passed with `16 passed in 3.63s`.

## Owner Closeout Verification

Command:

```bash
PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon PYTHONPATH=services/control-plane/bff python3 -c 'import json, os; from assistant.orchestrator_status import read_orchestrator_status; s=read_orchestrator_status(os.environ["PANTHEON_STATUS_ROOT"]); b=s.assistant_dev_bridge; print(json.dumps({"status": b.get("status"), "lastDrainAt": b.get("lastDrainAt"), "lastPacketId": (((b.get("lastResult") or {}).get("packets") or [{}])[0].get("packetId")), "processedCount": (b.get("lastResult") or {}).get("processedCount"), "errorCount": (b.get("lastResult") or {}).get("errorCount"), "recentReceiptIds": [r.get("packetId") for r in (b.get("recentReceipts") or [])[:3]]}, indent=2, sort_keys=True))'
```

Output:

```json
{
  "errorCount": 0,
  "lastDrainAt": "2026-06-07T10:17:46Z",
  "lastPacketId": "bridge_smoke_20260607101732",
  "processedCount": 1,
  "recentReceiptIds": [
    "bridge_smoke_20260607101732"
  ],
  "status": "idle"
}
```

Command:

```bash
pytest -q scripts/test_assistant_dev_packet_inbox_supervisor_contract.py services/control-plane/bff/assistant/tests/test_dev_bridge_inbox.py services/control-plane/bff/assistant/tests/test_dev_bridge_inbox_cli.py services/control-plane/bff/assistant/tests/test_dev_bridge_dispatch_cli.py services/control-plane/bff/assistant/tests/test_orchestrator_status.py
```

Result:

```text
16 passed in 3.94s
```

## Conclusion

PASS. The signed Management AI DevTaskPacket reached the repo-local supervisor
bridge inbox, was drained through the supervisor-recorded assistant dev bridge
path, materialized this smoke task, and is visible through BFF orchestrator
status readback with zero drain errors.
