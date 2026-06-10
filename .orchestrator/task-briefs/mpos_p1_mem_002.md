# Task Brief: MPOS-P1-MEM-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Automate persona and sponsor Learn feedback writeback
- Status: todo
- Owner: Codex
- Reviewer: Claude
- Next: Auto-reassigned MPOS-P1-MEM-002 away from unavailable lane Codex2 (disabled, sidecar-only, or auth-down); owner Codex2 -> Codex.

## Summary
把 runtime telemetry、postmortem、evolution 結果自動寫回 persona memory 與 sponsor-attributed institutional memory。

## Owner Implementation Notes

- Added `/api/memory/writebacks/learn-feedback` to convert runtime telemetry, postmortem, or evolution outcomes into contributor persona memory entries plus a sponsor-attributed institutional memory entry.
- Added source-event lookup/idempotency so replaying the same `source_event_type` + `source_event_id` returns existing memory ids instead of creating duplicates.
- Added telemetry, incident, and evolution payload builders that produce the memory-service Learn feedback contract without coupling those source planes to the memory store.
- MPOS-P1-E2E-002 is still active, so this validates the event-level writeback contract with synthetic runtime telemetry evidence rather than a full paper LEAN runtime E2E packet.

Verification:

- `python3 -m unittest services/memory/test_persona_memory_store.py services/memory/test_institutional_memory_store.py services/memory/test_main.py`
- `python3 -m py_compile services/memory/learn_feedback_writeback.py services/telemetry/feedback_adapter.py services/incident/incident.py services/evolution/postmortem_bridge.py`
- `(cd services/telemetry && python3 -m unittest test_feedback_adapter.py)`
- `python3 -m unittest services/incident/test_incident.py services/evolution/test_postmortem_bridge.py`
- `git diff --check`
