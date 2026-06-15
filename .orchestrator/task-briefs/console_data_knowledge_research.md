# Task Brief: CONSOLE-DATA-KNOWLEDGE-RESEARCH

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Populate /bff/knowledge,/bff/research-analyses,/bff/research/tasks
- Status: review
- Owner: Codex
- Reviewer: Claude
- Next: Implementation PR #1690 is merged into dev at 5a92e2fe011b2e97aa6b16609ebaaa3aae691434 (head e1902e6c238294ef623f327c8c78830d8611f9dd). Please review/approve status so owner can run done. Scope: service-backed projection from research-orchestrator + memory into BFF knowledge/research surfaces; no fabricated data. Verification: py_compile projector; pytest console projection + knowledge inbox + rw03 analyze + agora core (15 passed); docker compose config --quiet; local live curls with Bearer op-dev:admin:mfa returned knowledge total=8 status=ok, research-analyses total=2 status=ok, research/tasks total=1 status=ok. CI on PR passed commit trailers, runtime mirror guard, smoke acceptance, orchestrator sync.

## Summary
research-orchestrator + memory svc 產真 analysis/task/knowledge；投影進 BFF 讀面；live curl 驗收 count>0 且 surface status=ok；stub dispatch 保持 dev 安全姿態。
