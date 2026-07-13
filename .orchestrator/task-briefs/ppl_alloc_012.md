# Task Brief: PPL-ALLOC-012

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Quarterly ranking projection stage/weight/evidence tuple
- Status: todo
- Owner: Codex2
- Reviewer: Codex
- Next: Sweep addendum (Human/Ops 2026-07-13): scope includes the rolling league projection too, not just quarterly - live persona-league/rankings rows carry eligible/exclusion_reason/evidence_coverage but NO stage field (spec requires stage-aware recommendations from ranking reads). Add stage + snapshot id to league rows alongside the quarterly tuple.

## Summary
quarterly ranking 投影曝露 stage/current_weight/evidence/snapshot tuple，使 proposal 可對回單一 immutable ranking response（009 ranking-join blocker）；詳見 .orchestrator/task-briefs/ppl_alloc_012_quarterly_ranking_projection.md
