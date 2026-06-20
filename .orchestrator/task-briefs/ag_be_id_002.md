# Task Brief: AG-BE-ID-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: OpenClaw ensure/provision/reconcile servant
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude2
- Next: Review approved — servant ensure/provision/reconcile implementation correct; returning to Codex2 for closeout

## Summary
依 SD §5.2 實作 POST /bff/agora/servant/ensure；缺 user-private persona 時建立 agora_servant registry object，並透過既有 OpenClaw adapter 建/更新 agent；存在則 reconcile status/capabilities；回 ServantProfileDTO。

## Closeout Evidence
- Implementation PR: https://github.com/ajoe734/pantheon/pull/1855
- Implementation commit: 141f505def6b587dad47ef23fa7d84de09ffd2d8
- Merge commit: 985d9825ef7f65442f6a4b188641fb7055b9ed3e
- GitHub checks: Branch CI Gate and Orchestrator Sync passed on PR #1855.
- Local verification: `pytest services/control-plane/bff/tests/test_agora_router.py integrations/openclaw/test_persona_agent_sync.py services/control-plane/bff/tests/test_agora_identity_scope.py` passed, 35 tests.
