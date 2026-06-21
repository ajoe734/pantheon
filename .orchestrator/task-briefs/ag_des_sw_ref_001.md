# Task Brief: AG-DES-SW-REF-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Strategy Registry reference + workshop-version mapping contract
- Status: todo
- Owner: Claude2
- Reviewer: Claude
- Next: Assignment created

## Summary
依 sw001-deep-closure §5 + §2.1 定義 strategy_id / strategy_spec_registry_id / active_strategy_spec_registry_id 三身分與 workshop version link 語意:workshop 永不複製 StrategySpec JSON/lifecycle/ExperimentRun/CandidateArtifact 真相;從既有 draft 建立(§5.2,record.strategy_id 不符回 409 STRATEGY_REFERENCE_MISMATCH,缺/未授權回 404/403 不洩漏跨 user 存在)、free-form 建立(§5.3,strategy_id/active 皆 NULL)、首個 accepted version 走既有 Strategy Registry draft-create 並建 workshop-version link(§5.3)、workshop version = 指向不可變 Registry version 的 link 非複製文件(§5.4)、deprecated alias strategy_spec_ref/selected_version_id(§5.5)、conclude 只記 final ref 不 promote lifecycle(§5.6)。workshop_event.schema 含 version-link 欄位。 【有疑問一定要 STOP 開 blocker,不要自己亂做】動工前先讀完權威設計docs/04/pantheon_agora_cross_repo_2026-06-20/sw001-deep-closure/AG-BE-SW-001_deep_design_closure_2026-06-21.md(該文件為準)+ contract-closure/ + 既有 frozen bundle。鐵律:不可改動或重雜湊 frozen v1(specs/agora/bundle_index.json、openapi/agora_v1.openapi.yaml)與 v1.1(bundle_index.v1_1.json、openapi/agora_v1_1.openapi.yaml);一律 additive 到 specs/agora/v3/ 與 agora_v1_2.openapi.yaml / bundle_index.v1_2.json(後者必須 extends 並 hash v1_1 的精確 bytes)。raw 私有內容永不進 log/audit/trace/error/DB 明文。加密/金鑰/保留/狀態/映射語意一律照設計稿,不臆測、不自創 schema/route/enum/capability。
