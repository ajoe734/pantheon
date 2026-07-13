# Task Brief: PPL-ALLOC-010

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Per-persona attribution identity chain (real telemetry not seed)
- Status: in_progress
- Owner: Codex2
- Reviewer: Codex
- Next: Sweep addendum (Human/Ops 2026-07-13): live /bff/management/persona-league/rankings shows the full blast radius of this identity chain break - ALL 20 personas eligible=false with exclusion 'No telemetry coverage', evidence_coverage=0.0, and scores collapse into two uniform clusters (53.875 x10 / 49.375 x10). The promotion pipeline is functionally dead until this lands: nobody can rank or qualify. Add to acceptance: after fix, personas with real paper runtime become eligible=true with nonzero evidence_coverage and differentiated scores on the league read.

## Summary
修復 per-persona 績效歸因 identity chain：個別 persona 綁共用 canonical seed binding、真實 devloop 交易(6841筆)全落 unassigned，導致績效中心以 seed 值(24560/14%/5.7%)冒充 persona 績效。詳見 .orchestrator/task-briefs/ppl_alloc_010_persona_attribution_identity.md
