# Task Brief: AG-XR-001A

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Additive Agora contract extension bundle (v1.1)
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: Helper-claimed by idle Codex; previous owner Claude becomes reviewer.

## Summary
依 contract-closure 02_schema_coexistence_and_migration.md 建立 additive v2 bundle,frozen AG-XR-001 一律不可就地修改:新增 services/control-plane/specs/agora/v2/{widget_spec_v2,chart_spec_v1,dashboard_recipe_v2,compatibility_manifest}.schema.json 與 capability_manifest_v1_1.json,以及 services/control-plane/specs/agora/bundle_index.v1_1.json(extends frozen v1、記錄 base bundle_index_sha256)。A3 內容以 v2 檔名/ID 採納,不覆寫 v1。WidgetSpec v1 維持 legacy 可讀;v1->v2 投影失敗回 LEGACY_WIDGET_MAPPING_REQUIRED 不臆測。schema 內容取自 contract-closure 同名 v2 schema 檔。 【有疑問一定要提出,不要自己亂做】動工前先讀完 contract-closure 文件(docs/04/pantheon_agora_cross_repo_2026-06-20/contract-closure/ 01-07 + v2 schema + ARCHIVE_NOTES.md)與 frozen 基線(services/control-plane/specs/agora/ + openapi/agora_v1.openapi.yaml + bundle_index.json)。prose 03/04 為合約權威,agora_openapi_extension_v1_1.yaml 只是 seed(24/32 route)不可照抄當完整契約。只要遇到任何疑問、設計沒寫到、與既有 code 對不上、依賴不清或衝突,一律 STOP 開 blocker 等澄清,不可臆測、補洞、繞過。鐵律:不得改動 frozen AG-XR-001 檔案、不得讓其 bundle_index.json sha256 失效、不得擴張 capability allowlist、不得讓 Agora 直接下單/綁資金/寫 RuntimeBinding。
