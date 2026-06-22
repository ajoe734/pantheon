# Task Brief: AG-XR-CP-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Candidate pool BFF routes contract (additive §17.3)
- Status: todo
- Owner: Claude2
- Reviewer: Codex
- Next: Auto-reassigned ownership from Claude to Claude2 after repeated Claude terminal: {"type":"rate_limit_event","rate_limit_info":{"status":"allowed_warning","resetsAt":1782129600,"rateLimitType":"seven_day","utilization":0.81,"isUsingOverage":false,"surpassedThres. Task returned to todo until Claude2 starts a fresh run.

## Summary
依 SD §8/§17.3 與既有 services/control-plane/specs/agora/candidate_pool.schema.json + design-closure A2(candidate_scoring_recipe.schema.json + winner_branch.default.json)補上 candidate pool 的 BFF 路由契約(目前 agora_v1*.openapi.yaml 皆無):candidate pool/member/discussion/monitoring CRUD + score/review endpoint。score 必須由 A2 recipe 計算(score_components 對齊 recipe)、rejected 候選保留為 negative example。以 additive 方式加入(優先延伸 v1.3 或新增 v1.4 fragment,依 repo 慣例;不得改動或重雜湊 frozen v1/v1.1/v1.2)。重用既有 candidate_pool.schema.json,不自創欄位/route。 【有疑問先 STOP 開 blocker,Agora 不下單】
