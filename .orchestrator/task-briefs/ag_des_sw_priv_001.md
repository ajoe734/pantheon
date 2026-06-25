# Task Brief: AG-DES-SW-PRIV-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Agora private-content storage/encryption/retention/redaction contract
- Status: review_approved
- Owner: Claude
- Reviewer: Codex
- Next: Review approved after reviewer fix ad583335: dev DEK unwrap is invertible, privacy contract tests pass, v3 schemas parse, frozen bundles untouched. Owner Claude should perform review_approved closeout.

## Summary
依 sw001-deep-closure §3 + §9 定義私有內容契約:PrivateContentStore 介面(put/get_for_owner/delete_for_owner/expire_due,**禁止 list 方法**)、opaque ref pcnt_<ULID>(不編碼 tenant/user/workshop/object path)、envelope 加密(AES-256-GCM,每物件一把隨機 DEK,KMS/HSM KEK,AAD=tenant_id/owner_user_id/workshop_id/event_id/content_type/schema_version;只存 encrypted DEK/key version/nonce+tag/ciphertext URI/ciphertext sha256,**不存 plaintext hash**)、retention classes(§3.5)、owner-only 解密授權 + 每次 decrypt 稽核(§3.6)、fail-closed redaction(§3.8,503 PRIVATE_CONTENT_REDACTION_UNAVAILABLE)、create/message write sequence(§3.9)、§9 error 碼。dev 用 AGORA_PRIVATE_CONTENT_DEV_KEK(非 production、不入庫);production KMS provisioning 為 infra 依賴另開 ops,不在本任務。 【有疑問一定要 STOP 開 blocker,不要自己亂做】動工前先讀完權威設計docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md(該文件為準)+ contract-closure/ + 既有 frozen bundle。鐵律:不可改動或重雜湊 frozen v1(specs/agora/bundle_index.json、openapi/agora_v1.openapi.yaml)與 v1.1(bundle_index.v1_1.json、openapi/agora_v1_1.openapi.yaml);一律 additive 到 specs/agora/v3/ 與 agora_v1_2.openapi.yaml / bundle_index.v1_2.json(後者必須 extends 並 hash v1_1 的精確 bytes)。raw 私有內容永不進 log/audit/trace/error/DB 明文。加密/金鑰/保留/狀態/映射語意一律照設計稿,不臆測、不自創 schema/route/enum/capability。
