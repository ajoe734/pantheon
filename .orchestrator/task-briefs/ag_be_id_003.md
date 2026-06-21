# Task Brief: AG-BE-ID-003

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Interactive/trainer/research session BFF facade
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: 依 AG-XR-OPENAPI-003 補 servant session_type 後自動解鎖;owner Codex/reviewer Claude。

## Summary
依 SD §5.3/§17.1 實作 servant session BFF facade:POST sessions(interactive/trainer/research_task)、GET session、POST messages、terminate、GET stream(SSE)。session type 映射到既有 OpenClaw session,所有 read/write 帶 §8.2 audit 欄位(trace_id/request_id/actor_id/user_id/persona_id/session_id)。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。
