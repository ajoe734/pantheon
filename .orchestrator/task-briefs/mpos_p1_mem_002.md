# Task Brief: MPOS-P1-MEM-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Automate persona and sponsor Learn feedback writeback
- Status: review_approved
- Owner: Codex
- Reviewer: Claude
- Next: Review approved. Implementation verified: 17 test_main.py tests pass (including 5 new learn-feedback writeback tests), 56 memory store tests pass, 29 feedback_adapter tests pass, 94 incident/postmortem_bridge tests pass. All acceptance criteria met: persona and institutional memory writebacks created from telemetry/postmortem/evolution outcomes; sponsor persona id propagated to institutional entries; contributor entries carry proposal_ids and runtime_telemetry_evidence; idempotent replay returns HTTP 200 on duplicate source_event_id; unauthorized authority rejected 403. Owner must finalize.

## Summary
把 runtime telemetry、postmortem、evolution 結果自動寫回 persona memory 與 sponsor-attributed institutional memory。
