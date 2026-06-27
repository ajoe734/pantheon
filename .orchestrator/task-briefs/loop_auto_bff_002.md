# Task Brief: LOOP-AUTO-BFF-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Add BFF downstream health monitor
- Status: review
- Owner: Claude
- Reviewer: Codex
- Next: Implementation complete: DownstreamHealthMonitor module added. 26 tests pass (0 regressions). Three acceptance criteria covered: telemetry emit via runtime_health events, incident creation for sustained failures (idempotent, threshold-gated), background task isolation ensuring BFF degraded mode never affects active runtimes. New route GET /bff/v5/downstream-health exposes probe state. Evidence note at docs/deployment/evidence/loop-auto-bff-002/evidence-note.md.

## Summary
新增 continuous BFF/downstream health monitor，把 probe 結果寫進 telemetry/incident pipeline。
