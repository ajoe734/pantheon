# Task Brief: MPOS-P1-MEM-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Automate persona and sponsor Learn feedback writeback
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved by Claude. All acceptance criteria met. 17 test_main.py tests pass (5 new learn-feedback writeback tests), 56 memory store tests, 29 feedback_adapter tests, 94 incident/postmortem_bridge tests — all green. Persona memory writebacks created per contributor; sponsor-attributed institutional entry carries sponsor+contributing persona ids; proposal_ids and runtime_telemetry_evidence linked in each contributor entry; idempotent by source_event_id (201 create, 200 replay); unauthorized write_authority rejected 403. Owner must finalize to done.

## Summary
把 runtime telemetry、postmortem、evolution 結果自動寫回 persona memory 與 sponsor-attributed institutional memory。
