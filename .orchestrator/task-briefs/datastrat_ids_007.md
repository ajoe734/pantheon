# Task Brief: DATASTRAT-IDS-007

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Negative-memory matcher (safety)
- Status: review
- Owner: Codex2
- Reviewer: Claude2
- Next: Handoff for review: PR #1346 https://github.com/ajoe734/pantheon/pull/1346 implements deterministic negative-memory matching. Blocking negative_memory_match now rejects StrategySpecSeed save/materialization and blocks seed-card promotion; warnings surface in persona seed-card metadata. Local validation passed: py_compile target modules; 32 source/persona focused tests; 11 BFF/replication adjacent tests; git diff --check. GitHub checks pass: Commit trailers, Runtime mirror guard, Smoke acceptance, Forward to orchestrator.

## Summary
把 SeedCandidate 對 retired/rejected/failed/postmortem 做相似度比對,輸出 negative_memory_match(warning_level info|warning|blocking);blocking 擋 seed acceptance。v1 deterministic/keyword 即可。
