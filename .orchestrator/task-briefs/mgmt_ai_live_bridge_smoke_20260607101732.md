# Task Brief: MGMT-AI-LIVE-BRIDGE-SMOKE-20260607101732

## Task

- Title: Live smoke: Management AI DevTaskPacket reaches supervisor bridge
- Status: review_approved
- Owner: Codex
- Reviewer: Codex2
- Next: Codex2 review approved: live bridge smoke evidence, live readback, and focused regression all pass; owner may finalize done.

## Summary

Live bridge smoke only: verify Management AI signed packet reaches supervisor drain path.

## Acceptance

- Supervisor drains the signed assistant DevTaskPacket from the live inbox.
- `scripts/ai_status.py` materializes this smoke task without replay or signature failure.
- BFF orchestrator status exposes `assistantDevBridge.lastResult` for the packet.

## Review Approval

Codex2 approved the evidence-only scope after verifying live readback and the
focused bridge regression suite. The implementation evidence commit was merged
through PR #1126 into `dev` at merge commit
`508d1b69c78d3936444bd9e7cf2cce5b1b01857a`.

## Owner Closeout Verification

- `PANTHEON_STATUS_ROOT=/home/lupin/code/pantheon PYTHONPATH=services/control-plane/bff python3 -c '...'`
  confirmed packet `bridge_smoke_20260607101732`, `processedCount=1`, and
  `errorCount=0`.
- `pytest -q scripts/test_assistant_dev_packet_inbox_supervisor_contract.py services/control-plane/bff/assistant/tests/test_dev_bridge_inbox.py services/control-plane/bff/assistant/tests/test_dev_bridge_inbox_cli.py services/control-plane/bff/assistant/tests/test_dev_bridge_dispatch_cli.py services/control-plane/bff/assistant/tests/test_orchestrator_status.py`
  passed with `16 passed in 3.94s`.

## Artifact

- `.orchestrator/evidence/MGMT-AI-LIVE-BRIDGE-SMOKE-20260607101732.md`
