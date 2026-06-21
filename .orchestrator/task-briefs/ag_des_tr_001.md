# Task Brief: AG-DES-TR-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Trading Room aggregate + governed intent handoff (v1.3)
- Status: todo
- Owner: Claude
- Reviewer: Claude2
- Next: Auto-reassigned ownership from Codex2 to Claude after repeated Codex2 terminal: Codex usage limit reached. Task returned to todo until Claude starts a fresh run.

## Summary
依 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/04_trading_room_and_governed_intent.md 落地 v4 schema:trading_room_aggregate/trading_decision_event/governed_intent_handoff。Trading Room 只讀/呈現(§D1,不擁有 order/capital/RuntimeBinding);decision-event 欄位(§D2,confidence≠probability,含 no-order-route proof);decision lifecycle(§D3);trader 決策 approve/reject/defer/modify(§D4,approve/modify 建 TradingIntent 非 order);governed handoff(§D5 shadow/paper/canary/live 僅 request,只有既有 Governance/DeploymentPlan/RuntimeBinding/LEAN 能產生執行)。 【有疑問一定要 STOP 開 blocker】動工前讀完權威設計 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/ (MASTER_SD_RESPONSE.md + 對應 0X 文件 + schemas/*.json + 08 OpenAPI delta)。鐵律:不可改動或重雜湊 frozen v1/v1.1/v1.2(bundle_index.json/.v1_1/.v1_2、agora_v1*.openapi.yaml);一律 additive 到 services/control-plane/specs/agora/v4/ 與 agora_v1_3.openapi.yaml / bundle_index.v1_3.json(後者 extends 並 hash v1.2 精確 bytes)。schema 內容以 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/ 同名檔為準逐欄落地,不自創欄位/route/enum;Agora 永不下單/綁資金/寫 RuntimeBinding(governed handoff 只建 request)。遇任何不確定先 blocker。
