# Task Brief: AG-DES-VERS-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Strategy versioning: patch + version-compare + readiness contract (v1.3)
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Review approved — schemas byte-match design-closure-round2 authoritative sources; SHA256 verified; all acceptance criteria met; returning to owner Claude for closeout

## Summary
依 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/01_strategy_versioning_patch_readiness.md 落地 v4 schema:version_patch_proposal/version_compare/strategy_readiness。patch 用 **restricted RFC 6902**(只允許 add/remove/replace/test,禁 move/copy),只允許 §A1 列的 StrategySpec roots,system-owned 欄位不可改;patch 套用走 §A2(解析 base Registry version→驗 SHA-256+scope→驗 path→in-memory apply→驗 canonical schema+policy→建新 immutable draft+workshop-version link,絕不就地改 Registry);VersionPatchProposal 身分/lifecycle(§A3)、version compare(1 base + ≤4 candidate,§A4,predicted 不得渲染成 observed)、三 readiness gate(preliminary/full-validation/trading-room + 狀態 not_assessed/blocked/conditional/ready/stale,§A5)。 【有疑問一定要 STOP 開 blocker】動工前讀完權威設計 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/ (MASTER_SD_RESPONSE.md + 對應 0X 文件 + schemas/*.json + 08 OpenAPI delta)。鐵律:不可改動或重雜湊 frozen v1/v1.1/v1.2(bundle_index.json/.v1_1/.v1_2、agora_v1*.openapi.yaml);一律 additive 到 services/control-plane/specs/agora/v4/ 與 agora_v1_3.openapi.yaml / bundle_index.v1_3.json(後者 extends 並 hash v1.2 精確 bytes)。schema 內容以 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/ 同名檔為準逐欄落地,不自創欄位/route/enum;Agora 永不下單/綁資金/寫 RuntimeBinding(governed handoff 只建 request)。遇任何不確定先 blocker。
