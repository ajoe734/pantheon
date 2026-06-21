# Task Brief: AG-DES-E2E-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Winner-branch E2E + cross-repo/cross-user isolation acceptance (v1.3)
- Status: review_approved
- Owner: Claude2
- Reviewer: Claude
- Next: Review approved: 146 tests cover all §F1–§F7 assertions; iron rule enforced by real assertions in Steps 5/6/9/11 and ISO-M05/M06; 7 tautological tests noted for follow-up but not blocking

## Summary
依 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/06_winner_branch_e2e_and_isolation.md 凍結 canonical winner-branch E2E(§F1 11 步:ensure servant→workshop→重建/缺口/下個問題→首個 StrategySpec draft+link→ResearchPlan propose/approve→governed stages 含進度/證據→patch propose/accept→version compare+evidence-based readiness→選 candidate→candidate pool+dashboard+Trading Room→decision/TradingIntent + shadow 或 request-only handoff;全程 Agora 不下單)與 §F2 隔離驗收矩陣(跨 repo hash 相容、User A/B 隔離、Agora vs Management route、redacted-only projection、最小 ContextBundle、無資金/runtime/broker 權限、私有儲存/redaction/retention、idempotency/concurrency/replay)。 【有疑問一定要 STOP 開 blocker】動工前讀完權威設計 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/ (MASTER_SD_RESPONSE.md + 對應 0X 文件 + schemas/*.json + 08 OpenAPI delta)。鐵律:不可改動或重雜湊 frozen v1/v1.1/v1.2(bundle_index.json/.v1_1/.v1_2、agora_v1*.openapi.yaml);一律 additive 到 services/control-plane/specs/agora/v4/ 與 agora_v1_3.openapi.yaml / bundle_index.v1_3.json(後者 extends 並 hash v1.2 精確 bytes)。schema 內容以 docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure-round2/schemas/ 同名檔為準逐欄落地,不自創欄位/route/enum;Agora 永不下單/綁資金/寫 RuntimeBinding(governed handoff 只建 request)。遇任何不確定先 blocker。
