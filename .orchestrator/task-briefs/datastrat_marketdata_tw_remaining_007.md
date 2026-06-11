# Task Brief: DATASTRAT-MARKETDATA-TW-REMAINING-007

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Taiwan remaining gaps: chip finance news storage and throttling
- Status: review
- Owner: Codex
- Reviewer: Claude2
- Next: Implementation ready for review in PR #1314: TDCC/TAIFEX gap visibility, active-universe throttling, top-N storage metadata, raw retention/compression refs, and handoff evidence. Verification: py_compile targeted files; pytest services/source_ingestion/tests services/source_ingestion/test_service.py -q -> 295 passed, 1 skipped.

## Summary
補齊台股資料完整性缺口：分點只抓 active universe top15/top20、新聞/財報/籌碼分級更新、大量資料 raw retention 與壓縮，以及 archive universe 暫停細節更新。
