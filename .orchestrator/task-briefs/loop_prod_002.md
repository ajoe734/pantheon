# Task Brief: LOOP-PROD-002

Generated in the worker workspace because the supervisor root did not have a task brief file.

## Task
- Title: Product evidence schema and anti-false-close gate
- Status: review_approved
- Owner: Antigravity
- Reviewer: Claude
- Next: Reviewed PR #3584 (merged into dev at add901ad8): lazy jsonschema import inside check_task guarded with try/except ImportError, appends fail-closed gap 'jsonschema library is not installed on this host (ImportError)' and returns early — consistent with existing early-return pattern for evidence-file-missing/parse-failure cases. Added regression test test_missing_jsonschema_dependency_returns_graceful_gap using sys.modules patch. jsonschema>=4.0,<5.0 added to requirements.txt. All 38 tests pass (python3 scripts/test_loop_done_guardrail.py). CI green (Commit trailers, Runtime mirror guard, Smoke acceptance all SUCCESS). Approved for owner finalization.

## Summary
建立 machine-readable product evidence schema 與 supervisor closeout guard，拒絕 phantom cross-repo delivery、mock-only live claim、缺 terminal readback/restart/hosted/security/reviewer 或 unsupported maturity。
