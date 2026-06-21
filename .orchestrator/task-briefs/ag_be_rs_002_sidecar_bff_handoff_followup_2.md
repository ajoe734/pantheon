# Task Brief: AG-BE-RS-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare AG-BE-RS-002 BFF and frontend handoff packet
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Residual schema-drift fixes applied (2026-06-21): (1) outcome field summary in ResearchRunProjection table corrected from pending/succeeded/failed/cancelled to pending/pass/fail/inconclusive, matching v4 schema and the TS sketch; (2) section 5 title renamed from "Plan List Returns Plans Directly (Not Run Projections)" to "Run List Returns Full ResearchRunProjection Objects", accurately describing GET /research-plans/{plan_id}/runs behavior. Packet now consistent across field summary, TS sketch, and v4 schema. Ready for Codex re-review.

## Summary
平行支援 AG-BE-RS-002，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。
