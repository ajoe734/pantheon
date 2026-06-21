# Task Brief: AG-DES-SW-DB-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Workshop tables + 4-state lifecycle + section-8 index migration
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved — all §6/§7/§8 requirements met, frozen bundles intact, 7 tests pass. Returned to Codex for finalization.

## Summary
依 sw001-deep-closure §6/§7/§8 在既有 store 慣例(Python 模組內嵌 CREATE TABLE IF NOT EXISTS,參 bff/assistant_conversation_store.py;**無 .sql migration**)產出 workshop 持久化:5 張表(strategy_workshop_session / strategy_workshop_event / strategy_workshop_version_link / strategy_completeness_snapshot / agora_private_content_object,欄位/型別/NOT NULL/FK/PK 完全照 §7)、4 狀態 lifecycle CHECK(open/in_review/concluded/archived)、event message 的 (private_content_ref+redacted_summary+redaction_policy_version) CHECK、§8 全部索引(含 partial/unique)。沿用既有 idempotency store(services/foundation/idempotency.py,aggregate type strategy_workshop),不另建 idempotency 子系統。 【有疑問一定要 STOP 開 blocker,不要自己亂做】動工前先讀完權威設計docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md(該文件為準)+ contract-closure/ + 既有 frozen bundle。鐵律:不可改動或重雜湊 frozen v1(specs/agora/bundle_index.json、openapi/agora_v1.openapi.yaml)與 v1.1(bundle_index.v1_1.json、openapi/agora_v1_1.openapi.yaml);一律 additive 到 specs/agora/v3/ 與 agora_v1_2.openapi.yaml / bundle_index.v1_2.json(後者必須 extends 並 hash v1_1 的精確 bytes)。raw 私有內容永不進 log/audit/trace/error/DB 明文。加密/金鑰/保留/狀態/映射語意一律照設計稿,不臆測、不自創 schema/route/enum/capability。
