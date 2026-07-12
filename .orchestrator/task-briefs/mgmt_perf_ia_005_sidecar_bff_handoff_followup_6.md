# Task Brief: MGMT-PERF-IA-005-SIDECAR-BFF-HANDOFF-FOLLOWUP-6

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Prepare MGMT-PERF-IA-005 BFF and frontend handoff packet
- Status: review
- Owner: Codex
- Reviewer: Claude
- Next: Independently verified all 9 route/behavior claims against services/control-plane/bff/main.py (recommendations GET L44208, submit POST L42621, promotion-reviews GET L42729/L42817, decisions POST L42860, formula GET L43872, rebalances GET L24393/L24548, apply POST L24515). Apply route returns 202 only and requires approval_ref before a live capital increase — fail-closed confirmed. `git show --stat cd70cf2c5` confirms only the task-brief and support packet changed; no canonical/runtime/registry/governance file was touched. `AI_NAME=Claude ./scripts/ai-status.sh approve` was denied again by the auto-mode classifier as self-approval (Claude reviewer + automated-pipeline owner Codex); `ai-status.sh note` is a silent no-op because this sidecar task id has no entry in ai-status.json. Formal `review_approved` needs a human or a non-Claude reviewer identity to run `approve`.

## Summary
平行支援 MGMT-PERF-IA-005，先整理 BFF query gap、operator journey 與前端 handoff materials，不改 canonical truth。
