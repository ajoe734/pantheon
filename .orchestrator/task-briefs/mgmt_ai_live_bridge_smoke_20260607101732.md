# Task Brief: MGMT-AI-LIVE-BRIDGE-SMOKE-20260607101732

## Task

- Title: Live smoke: Management AI DevTaskPacket reaches supervisor bridge
- Status at dispatch: todo
- Owner: Codex
- Reviewer: Claude
- Next: Assignment created

## Summary

Live bridge smoke only: verify Management AI signed packet reaches the
supervisor drain path.

## Acceptance

- Supervisor drains the signed assistant DevTaskPacket from the live inbox.
- `scripts/ai_status.py` materializes this smoke task without replay or
  signature failure.
- BFF orchestrator status exposes `assistantDevBridge.lastResult` for the
  packet.

## Artifact

- `.orchestrator/evidence/MGMT-AI-LIVE-BRIDGE-SMOKE-20260607101732.md`
