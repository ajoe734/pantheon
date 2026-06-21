# Task Brief: AG-BE-RS-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Evidence/result synthesis skill
- Status: done
- Owner: Claude
- Reviewer: Codex
- Next: Closeout complete. PR #2096 merged to dev. 28 result-synthesis tests pass. Evidence scope filter + VPP schema validation implemented per C1 SPEC.

## Summary
依 design-closure C1 result-synthesis SPEC 與 SD §7 做 evidence-grounded 結果整合 skill:把多個 ResearchRunSummary + ConsultMemo 整合成可討論卡片資料(VersionPatchProposal/EvidenceSummary),每個結論必須 grounded 在 evidence_refs,不得無根據生成。 【有疑問一定要提出,不要自己亂做】動工前先讀完引用的設計稿(SD 對應章節 + docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/ + canonical services/control-plane/specs/agora/*.schema.json / openapi/agora_v1.openapi.yaml / capability_manifest.json)。只要遇到任何疑問、不確定、設計稿沒寫到、與既有 code 對不上、依賴不清、無法重現或衝突,一律 STOP,用 blocker(或向 reviewer handoff)把問題具體寫出來並等待澄清,絕對不可自行臆測、補洞、繞過或先做再說。可動工的部分必須與引用 spec/schema 逐欄位一致:不得自創 schema/欄位/評分/widget/route、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。
