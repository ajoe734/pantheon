# Task Brief: AG-BE-CP-001-SIDECAR-BFF-HANDOFF

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare AG-BE-CP-001 BFF and frontend handoff packet
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Handoff packet updated 2026-06-21 to reflect AG-BE-RS-002 is `done` (archive, closeout merge 3566d9e6ee1f531e84c536fd3ff0d4b44e0744c4, impl PR #2092 merged to dev). Stale RS-002 gate references removed; remaining blockers for parent AG-BE-CP-001 are: missing candidate score/review HTTP route (§17.3 not defined in SD), missing schema extension for score/discussion/monitoring/negative-example fields (candidate_pool.schema.json has additionalProperties:false), and missing lifecycle_state transition map. Packet ready for reviewer re-handoff to Codex.

## Summary
平行支援 AG-BE-CP-001，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。
