# Task Brief: AG-XR-OPENAPI-004

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Additive Agora v1.3 OpenAPI / capability / schema bundle (+ hashes)
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Review approved: 29 routes, 11 v4 schemas, full SHA256 chain verified; frozen files untouched; no_order_route_proof enforced. Returned to Claude2 for finalization.

## Summary
依 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/08_openapi_v1_3_delta.yaml + INDEX 把 6 個 AG-DES-* 的 v4 schema 整合成 additive v1.3 bundle:services/control-plane/openapi/agora_v1_3.openapi.yaml(納入 08 全部 26 條 patch-proposals/version-comparisons/readiness/cards/research-plans/research-runs/trading-room/decision-events/trading-intents 路由與 typed response)、services/control-plane/specs/agora/v4/capability_manifest_v1_3.json、services/control-plane/specs/agora/bundle_index.v1_3.json(extends 並 hash v1.2 精確 bytes,含全部 v4 schema+manifest+openapi 的 sha256,**merge 後生成、不可從 design pack template 照抄**)。frozen v1/v1.1/v1.2 不可動。前端 generated types 之後由 type-gen follow-up 從 v1.3 產出。 【有疑問一定要 STOP 開 blocker】動工前讀完權威設計 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/ (MASTER_SD_RESPONSE.md + 對應 0X 文件 + schemas/*.json + 08 OpenAPI delta)。鐵律:不可改動或重雜湊 frozen v1/v1.1/v1.2(bundle_index.json/.v1_1/.v1_2、agora_v1*.openapi.yaml);一律 additive 到 services/control-plane/specs/agora/v4/ 與 agora_v1_3.openapi.yaml / bundle_index.v1_3.json(後者 extends 並 hash v1.2 精確 bytes)。schema 內容以 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/ 同名檔為準逐欄落地,不自創欄位/route/enum;Agora 永不下單/綁資金/寫 RuntimeBinding(governed handoff 只建 request)。遇任何不確定先 blocker。
