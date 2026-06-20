# Task Brief: AG-BE-ID-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: ContextBundle redaction and central persona boundary
- Status: todo
- Owner: Claude
- Reviewer: Claude2
- Next: Chair reassigned owner from Codex2 to Claude: Codex2 lane is paused due to quota exhaustion and cannot be re-dispatched. AG-BE-ID-004 is blocked on stale SD section references (§5.6/§21.3 not present in SD_2026-06-20.md); design-closure C1/expert-consult already documents the ContextBundle privacy rule and is sufficient authority for implementation. Claude is healthy, active, and listed as a rescue candidate target. Supervisor should return task to todo for fresh Claude dispatch. Task returned to todo for a blocked-owner rescue dispatch.

## Summary
依 SD §5.6/§21.3 實作中央 Persona consult 的 ContextBundle:只帶 strategy_spec_draft_ref/question/symbols/evidence_refs/data_cutoff/required_output_schema,raw_prompt_included=false、user_identity_included=false(除非該次 consult 使用者明確授權)。中央人格永遠拿不到原始私人對話與身份。違反時回 RAW_PRIVATE_CONTENT_FORBIDDEN。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。
