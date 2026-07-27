# Task Brief: OPS-L12-TELEMETRY-DISCOVERY-IMPORT-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Eliminate telemetry unittest discovery loader errors
- Status: in_progress
- Owner: Claude
- Reviewer: Codex
- Next: Human/Ops recorded Codex reviewer findings because the actual Codex reviewer command failed with PANTHEON_COMMAND_RUNTIME_SHA mismatch (worker expected 87166a352c0b90a26a6e35c138acfaea195fa4ee; command root was 741f6ec8a6c75a2505d534016335a896e59bc101). Codex independent review of PR #4273 head f6d340ff018cc178bcf2023b7fae00cde77ebb2c was NOT approved. Functional acceptance passed: baseline 197 tests/2 loader errors/1 skip reproduced; exact head full telemetry unittest 342 OK/1 skip/0 loader errors; discovery regression 20 OK; evidence gate 12 OK; checksum OK; production capture.py and feedback_adapter.py diff empty; PR #4273 eight Branch CI checks green. Required repair: canonical row is Owner=Claude / Reviewer=Codex, but committed task brief, README, AC2 proof, evidence.json task/two-person/AC6 fields, and scripts/test_ops_l12_telemetry_discovery_import_evidence.py still bind Reviewer=Codex2 and evidence still lacks current PR #4273/head/check binding. Re-cut current-task reviewer bindings to Codex while keeping historical packaging dependency reviewer Codex2 separately; refresh evidence.sha256 and commit with LLM-Agent Claude / Reviewer Codex trailers.

## Summary
修正 telemetry 完整 unittest discovery 的兩個裸模組 import error，讓乾淨 repo-root 與 package discovery 都能零 loader error。

## Coordination Root
- Auto workers inherit `PANTHEON_STATUS_ROOT`, `PANTHEON_COMMAND_ROOT`, and `PANTHEON_COMMAND_RUNTIME_SHA` from the supervisor.
- Run `$PANTHEON_COMMAND_ROOT/scripts/ai-status.sh` for governed status changes; git, tests, and product edits continue in this task worktree while canonical status, activity, archive and lock writes are routed to the validated central root.
