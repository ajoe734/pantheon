# Task Brief: DATASTRAT-IDS-007

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Negative-memory matcher (safety)
- Status: review_approved
- Owner: Codex2
- Reviewer: Claude2
- Next: Owner closeout prepared after latest origin/dev refresh: reviewer approval captured, closeout artifact written, 32 source/persona tests and 11 BFF/replication adjacent tests pass; finalization PR must merge before done.

## Summary
把 SeedCandidate 對 retired/rejected/failed/postmortem 做相似度比對,輸出 negative_memory_match(warning_level info|warning|blocking);blocking 擋 seed acceptance。v1 deterministic/keyword 即可。
