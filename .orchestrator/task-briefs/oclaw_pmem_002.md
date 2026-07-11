# Task Brief: OCLAW-PMEM-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: OpenClaw persona agent reconciliation
- Status: in_progress
- Owner: Codex
- Reviewer: Claude
- Next: Operator-unblocked: re-attempt live evidence capture for model=openclaw/{persona_id} (gateway now healthy post-fixes); refresh stale owner bookkeeping to Codex.

## 2026-07-11 checkpoint

Merged implementation and focused tests are green. Live evidence capture is
blocked by unavailable non-interactive access to the current dev VM: the local
SSH alias is stale, the current VM private key is absent, and gcloud credential
refresh requires interactive login. Restore operator access, then capture one
real `model=openclaw/{persona_id}` response before review handoff.

## Summary
把 general persona create/update 接到 shared OpenClaw reconciler；既有 agent 要能同步 identity/workspace/model/SOUL，並消除 deploy script 與 library 的 SOUL drift。
