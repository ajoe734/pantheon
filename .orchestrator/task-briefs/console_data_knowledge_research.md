# Task Brief: CONSOLE-DATA-KNOWLEDGE-RESEARCH

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Populate /bff/knowledge,/bff/research-analyses,/bff/research/tasks
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Owner finalization prepared: implementation PR #1690 is merged into dev, Claude approval artifact is committed, and closeout verification passed. Merge the closeout PR into dev, then run `AI_NAME=Codex ./scripts/ai-status.sh done CONSOLE-DATA-KNOWLEDGE-RESEARCH "<checkpoint>"`.

## Summary
research-orchestrator + memory svc 產真 analysis/task/knowledge；投影進 BFF 讀面；live curl 驗收 count>0 且 surface status=ok；stub dispatch 保持 dev 安全姿態。

## Owner Finalization

- Date: 2026-06-15
- Owner: Codex
- Approved implementation: PR #1690 merged into dev at `5a92e2fe011b2e97aa6b16609ebaaa3aae691434`; implementation head `e1902e6c238294ef623f327c8c78830d8611f9dd`.
- Reviewer approval artifact: `.orchestrator/task-briefs/console_data_knowledge_research_review.md`, committed at `5fdaedcc4e5208eed541fe0d9397d2cf33cf6dc0`.
- Closeout scope: task brief status/finalization record only. No projection script, BFF route, docker-compose, service runtime, or test behavior changed during closeout.
- Validation rerun:
  - `python3 -m py_compile scripts/project_research_to_bff_surfaces.py`
  - `python3 -m pytest services/control-plane/bff/tests/test_console_research_projection.py -q` - 1 passed
  - `docker compose config --quiet`
- Pending terminal closeout: merge the closeout PR into `dev`, then run `AI_NAME=Codex ./scripts/ai-status.sh done CONSOLE-DATA-KNOWLEDGE-RESEARCH "<checkpoint>"`.
