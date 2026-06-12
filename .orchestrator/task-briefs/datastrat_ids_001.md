# Task Brief: DATASTRAT-IDS-001

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: InteractionSourceRecord schema + store
- Status: review
- Owner: Codex2
- Reviewer: Claude2
- Next: PR #1333 opened for InteractionSourceRecord schema/store; local validation passed: focused 19 tests and adjacent source-ingestion 99 tests.

## Summary
新增 interaction_source_record contract + JSONL dev store(沿用 registry-split 模式);raw 內容只存 raw_ref 不 inline;visibility 與 redaction_status 必填。
