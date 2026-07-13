# Task Brief: PPL-ALLOC-010

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Per-persona attribution identity chain (real telemetry not seed)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Review rejected after merged PR #3524 and live verification. Required changes: 1) enforce the non-alias identity chain: runtime_binding_id and binding_id must not stand in for persona_capital_binding_id; add an independent canonical-binding precedence test without registry fallback. 2) Fix the service-backed telemetry path so exact execution runtime IDs produce nonzero telemetry_coverage_count and eligible ranking rows even when all metrics are explicit zero; add an HTTP-adapter-shaped regression plus a mixed topology conservation test with assigned persona runtimes and unresolved devloop runtimes. 3) Remove market-seed training_improvement_pct before deriving Fleet perf_delta; absent persona-owned evidence must be null or unavailable, and test copied 18.2, 14.0, and 9.5 seed values. 4) Keep unprovable devloop telemetry fail-closed in unassigned; if authoritative identity must be added at the producer layer, create an explicit scope-split follow-up and retain the unmet live acceptance there rather than guessing ownership. Also cover multiple runtimes per persona instead of last-record-wins. Re-run the 47 focused tests and all three authenticated dev curls. Current local suite is green but hosted league coverage is 0 and Fleet seed values still leak.

## Summary
修復 per-persona 績效歸因 identity chain：個別 persona 綁共用 canonical seed binding、真實 devloop 交易(6841筆)全落 unassigned，導致績效中心以 seed 值(24560/14%/5.7%)冒充 persona 績效。詳見 .orchestrator/task-briefs/ppl_alloc_010_persona_attribution_identity.md
