# Task Brief: EVOLOOP-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Real performance telemetry supply (PnL + drawdown)
- Status: review_approved
- Owner: Antigravity
- Reviewer: Codex2
- Next: Ownership updated

## Summary
修 paper 績效遙測的根:per-binding rolling PnL 要用 fills+行情做 mark-to-market(查明並修掉現在 14 個 binding 全部 pnl=0.0 的原因,其中一個有 7325 筆成交),並計算 rolling drawdown;以 schema-valid 的 pnl_snapshot/drawdown_snapshot telemetry events 發出、帶 as-of 時戳。fail-closed:沒有 marks 就只出 diagnostic,不得造數。收斂 EVOCHAIN-001 裁決文件(.orchestrator/task-briefs/evochain_001_upstream_decision.md)指出的上游缺口;取代原議的 EVOCHAIN-012。
