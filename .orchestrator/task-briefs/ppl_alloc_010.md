# Task Brief: PPL-ALLOC-010

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Per-persona attribution identity chain (real telemetry not seed)
- Status: in_progress
- Owner: Antigravity
- Reviewer: Codex
- Next: Review rejected after merged PR #3537 (merge 9f91a63d). Required changes: 1) Remove the fabricated zero telemetry summary in _pm12_persona_telemetry_records: a missing/non-dict get_telemetry_summary result must keep telemetry_coverage_count=0, source unavailable, and ranking ineligible; only an observed explicit-zero dict may count. Extend the HTTP-adapter regression to assert coverage/eligibility and add the missing-summary negative case. 2) In Persona Fleet, do not reassign runtimes through shared capital_pool_id or untyped binding/id aliases; consume the canonical resolved owner chain and query telemetry only by execution runtime_id. Add shared-pool isolation plus genuine multiple-runtimes-per-persona conservation coverage. 3) Remove the global numeric blacklist for 18.2/14.0/9.5; isolate market-seed metadata by provenance/source so legitimate persona-owned values remain valid, and test both copied-seed rejection and own-value preservation. 4) Run all three authenticated hosted curls after the corrected dev deployment; PR #3537 has no post-merge deploy/curl evidence. Validation observed: 69 focused tests pass, but an independent probe with no telemetry returns telemetry_coverage_count=1, eligible=true, source_confidence=formal, proving the suite misses the fail-closed regression.

## Summary
修復 per-persona 績效歸因 identity chain：個別 persona 綁共用 canonical seed binding、真實 devloop 交易(6841筆)全落 unassigned，導致績效中心以 seed 值(24560/14%/5.7%)冒充 persona 績效。詳見 .orchestrator/task-briefs/ppl_alloc_010_persona_attribution_identity.md
