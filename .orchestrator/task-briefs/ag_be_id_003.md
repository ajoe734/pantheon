# Task Brief: AG-BE-ID-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Interactive/trainer/research session BFF facade
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved: servant session facade aligns with spec; all 6 audit fields present; prohibited authority enforced; degradation handled; SSE stream correct. Returned to Codex for closeout.

## Summary
依 SD §5.3/§17.1 實作 servant session BFF facade:POST sessions(interactive/trainer/research_task)、GET session、POST messages、terminate、GET stream(SSE)。session type 映射到既有 OpenClaw session,所有 read/write 帶 §8.2 audit 欄位(trace_id/request_id/actor_id/user_id/persona_id/session_id)。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。

## Closeout Finalization
- Implementation PR #2025 merged into `dev` on 2026-06-21T09:24:16Z at merge commit `aeceba68da1bf262c8c761446a37a196bc91a625`.
- Task implementation commit `605964b0d3d2ee0a994e9b5f8b44455c8c9a97d0` delivered the Agora servant session facade, OpenClaw lifecycle session lookup helper, and focused contract tests.
- Reviewer approval is recorded in central status for Claude: servant session facade aligns with the v1.2 spec, all six audit fields are present, prohibited authority is enforced, degradation handling returns `OPENCLAW_UPSTREAM_DEGRADED`, and SSE stream behavior is correct.
- Owner closeout validation rerun at 2026-06-21T09:34:36Z:
  `python3 -m py_compile services/control-plane/bff/agora/servant/router.py services/control-plane/bff/openclaw_ops_client.py services/control-plane/bff/tests/test_agora_servant_sessions.py`,
  `python3 -m pytest services/control-plane/bff/tests/test_agora_servant_sessions.py -q` (4 passed),
  `python3 -m pytest services/control-plane/bff/tests/test_agora_router.py -q` (18 passed),
  `python3 -m pytest services/control-plane/bff/test_ask_001_sessions_contract.py -q` (24 passed),
  `python3 -m pytest services/control-plane/bff/test_bff_agora_core_contract.py -q` (5 passed),
  `python3 -m pytest services/control-plane/bff/tests/test_assistant_agora_ask.py -q` (8 passed),
  and `git diff --check`.
