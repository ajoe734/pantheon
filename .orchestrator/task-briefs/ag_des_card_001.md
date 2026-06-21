# Task Brief: AG-DES-CARD-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Workshop card projection contracts (v1.3)
- Status: review_approved
- Owner: Claude
- Reviewer: Claude2
- Next: Review approved: all 4 acceptance criteria pass. 10 companion schemas verbatim-match reference hashes; workshop_card.schema.json adds 12 typed discriminated-union payload defs with additionalProperties:false per type per E2-E13 spec; agora_v1_3.openapi.yaml is strictly additive over 08 delta; bundle_index.v1_3.json SHA256s verified against actual bytes; no frozen v1/v1.1/v1.2 mutations. Returned to Claude for closeout.

## Summary
依 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/05_workshop_card_contracts.md 落地 v4 schema workshop_card:12 種卡共用 typed envelope,從 BFF projection 綁定而非解析自由 LLM markdown(user_strategy_description/servant_reconstruction/completeness_update/missing_definition/next_question/research_plan_proposal/research_progress/research_result/consult_result/version_patch_proposal/version_compare/readiness_gate)。每卡 field-level payload 依 05;前端可在 typed field 內 render markdown,但不得由自由輸出推斷卡型/語意。 【有疑問一定要 STOP 開 blocker】動工前讀完權威設計 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/ (MASTER_SD_RESPONSE.md + 對應 0X 文件 + schemas/*.json + 08 OpenAPI delta)。鐵律:不可改動或重雜湊 frozen v1/v1.1/v1.2(bundle_index.json/.v1_1/.v1_2、agora_v1*.openapi.yaml);一律 additive 到 services/control-plane/specs/agora/v4/ 與 agora_v1_3.openapi.yaml / bundle_index.v1_3.json(後者 extends 並 hash v1.2 精確 bytes)。schema 內容以 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/ 同名檔為準逐欄落地,不自創欄位/route/enum;Agora 永不下單/綁資金/寫 RuntimeBinding(governed handoff 只建 request)。遇任何不確定先 blocker。
