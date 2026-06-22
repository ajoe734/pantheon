# Task Brief: AG-BE-TR-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Trading room aggregate and event queues
- Status: in_progress
- Owner: Claude2
- Reviewer: Codex
- Next: Closeout finalization commit pushed. PR #2191 open (task/AG-BE-TR-001 -> dev) with auto-merge enabled. 24/24 tests passed. Waiting for PR to merge before running done.

## Summary
依 SD §12/§13.1 與 specs/agora/trading_event.schema.json 做 trading room aggregate 與 entry/add/reduce/exit/review 事件佇列,§17.4 endpoint;事件欄位(confidence/probability/EV/rationale/riskNotes/evidenceRefs/invalidation)對齊 schema。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。
