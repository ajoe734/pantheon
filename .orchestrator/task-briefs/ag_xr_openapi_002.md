# Task Brief: AG-XR-OPENAPI-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Additive Agora v1.2 OpenAPI / capability / schema bundle
- Status: review_approved
- Owner: Codex
- Reviewer: Claude2
- Next: Review approved — all 5 bundle tests pass, frozen v1/v1.1 immutable confirmed, hash verified, all §4/§6/§9 contract requirements met. Returned to Codex for finalization.

## Summary
依 sw001-deep-closure §4.4 + §6 把 PRIV/REF/DB 的 v3 schema 整合成 additive v1.2 bundle:agora_v1_2.openapi.yaml(workshops create/message 採 **server-side** private_content_ref、browser 不得提交 ref、owner vs management projection §4.2/§4.3、4 狀態 filter + status_group(active=>open+in_review)、§9 error)、capability_manifest_v1_2.json、bundle_index.v1_2.json(extends 並 hash v1_1 的精確 bytes)。**必須明示 v1.2 取代 v1.1 的 `active` 生命週期 filter 措辭(authority order),frozen v1/v1.1 檔案與 hash 不可動。** 【有疑問一定要 STOP 開 blocker,不要自己亂做】動工前先讀完權威設計docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md(該文件為準)+ contract-closure/ + 既有 frozen bundle。鐵律:不可改動或重雜湊 frozen v1(specs/agora/bundle_index.json、openapi/agora_v1.openapi.yaml)與 v1.1(bundle_index.v1_1.json、openapi/agora_v1_1.openapi.yaml);一律 additive 到 specs/agora/v3/ 與 agora_v1_2.openapi.yaml / bundle_index.v1_2.json(後者必須 extends 並 hash v1_1 的精確 bytes)。raw 私有內容永不進 log/audit/trace/error/DB 明文。加密/金鑰/保留/狀態/映射語意一律照設計稿,不臆測、不自創 schema/route/enum/capability。
